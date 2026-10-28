#!/usr/bin/env -S uv run --script
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "google-auth",
#     "google-cloud-batch",
#     "google-cloud-logging",
#     "rich",
# ]
# ///

"""Benchmarking and profiling CLI for AlphaFold 3 MSA Cloud Batch array jobs.

Retrieves job execution metrics, sequence length statistics, throughput against
the 300 alignments/hour design target, task failures, and optional Cloud Logging
telemetry (Spot VM preemptions, sharded MSA database download times, per-VM
task completion counts, and hmmsearch/jackhmmer database search durations) and
renders them as formatted Rich tables. Supports exporting all collected data
to TSV files.

Usage:
  ./profile.py --project-id <PROJECT_ID> --job-id <JOB_ID>
  ./profile.py --project-id <PROJECT_ID> --job-id <JOB_ID> --with-logs
  ./profile.py --project-id <PROJECT_ID> --job-id <JOB_ID> --with-logs --export
  ./profile.py --project-id <PROJECT_ID> --job-id <JOB_ID> --export my_output_dir
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Sequence
import dataclasses
from datetime import datetime, timedelta, timezone
import json
import pathlib
import random
import re
import statistics
import subprocess
import sys
import time
from typing import Any

from google.api_core.exceptions import GoogleAPICallError, TooManyRequests
import google.auth
from google.auth import credentials as auth_credentials
from google.cloud import batch_v1
from google.cloud import logging as cloud_logging
from google.oauth2 import credentials as oauth2_credentials
from rich import box
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

DEFAULT_TARGET_ALIGNMENTS_PER_HOUR = 300.0
PREEMPTION_EXIT_CODE = 50001
_PREEMPTION_KEYWORDS = (
    "preempt",
    "spot",
    "50001",
    "instance was terminated",
    "vm was terminated",
)
_CANDIDATE_SEQUENCE_PATHS = (
    pathlib.Path(
        "third_party/hpc_solutions/solutions/alphafold3/msa/benchmarks/"
        "benchmark_sequences.jsonl"
    ),
    pathlib.Path(
        "solutions/alphafold3/msa/benchmarks/benchmark_sequences.jsonl"
    ),
    pathlib.Path(__file__).resolve().parent / "benchmark_sequences.jsonl",
    pathlib.Path("benchmark_sequences.jsonl"),
)

DEFAULT_TOOL_CORES = {
    "jackhmmer": 32,
    "nhmmer": 1,
    "hmmsearch": 32,
}

TOOL_BY_DB_TYPE = {
    "uniprot": "jackhmmer",
    "mgy": "jackhmmer",
    "uniref": "jackhmmer",
    "bfd": "jackhmmer",
    "pdb": "hmmsearch",
    "rfam": "nhmmer",
    "rnacentral": "nhmmer",
    "ntrna": "nhmmer",
}


def extract_tool_cores(job: batch_v1.Job | None) -> dict[str, int]:
  """Extracts --jackhmmer_n_cpu, --nhmmer_n_cpu, and --hmmsearch_n_cpu from Job."""
  cores = dict(DEFAULT_TOOL_CORES)
  if not job:
    return cores
  for tg in getattr(job, "task_groups", []):
    task_spec = getattr(tg, "task_spec", None)
    if not task_spec:
      continue
    for r in getattr(task_spec, "runnables", []):
      text = getattr(getattr(r, "script", None), "text", "") or ""
      if not text:
        continue
      m_j = re.search(r"--jackhmmer_n_cpu=(\d+)", text)
      if m_j:
        cores["jackhmmer"] = int(m_j.group(1))
      m_nh = re.search(r"--nhmmer_n_cpu=(\d+)", text)
      if m_nh:
        cores["nhmmer"] = int(m_nh.group(1))
      m_hs = re.search(r"--hmmsearch_n_cpu=(\d+)", text)
      if m_hs:
        cores["hmmsearch"] = int(m_hs.group(1))
  return cores


@dataclasses.dataclass(frozen=True)
class DistributionStats:
  """Summary statistics (count, min, max, mean, median) for a numeric sample."""

  count: int
  min_val: float
  max_val: float
  mean_val: float
  median_val: float

  @classmethod
  def from_values(
      cls, values: Sequence[float | int]
  ) -> DistributionStats | None:
    """Computes summary statistics from a non-empty numeric sequence."""
    if not values:
      return None
    vals = [float(v) for v in values]
    return cls(
        count=len(vals),
        min_val=min(vals),
        max_val=max(vals),
        mean_val=statistics.mean(vals),
        median_val=statistics.median(vals),
    )


@dataclasses.dataclass(frozen=True)
class SequenceStats:
  """Summary statistics for the benchmark sequences JSONL dataset."""

  path: pathlib.Path
  count: int
  avg_length: float
  min_length: int
  max_length: int
  names_by_index: dict[int, str]
  lengths_by_index: dict[int, int] = dataclasses.field(default_factory=dict)
  types_by_index: dict[int, str] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class TaskFailure:
  """Details of a failed Cloud Batch task."""

  task_index: int
  task_id: str
  input_name: str | None
  failed_at: datetime | None
  exit_code: int | None
  reason: str

  @property
  def target_name(self) -> str | None:
    return self.input_name


@dataclasses.dataclass(frozen=True)
class TaskRecord:
  """Execution details for a single task."""

  task_index: int
  task_id: str
  input_name: str | None
  sequence_length: int | None
  state: str
  start_time: datetime | None
  end_time: datetime | None
  duration_seconds: float | None
  exit_code: int | None
  reason: str
  entity_type: str = "unknown"
  task_group: str = "group0"

  @property
  def target_name(self) -> str | None:
    return self.input_name

  @property
  def target_type(self) -> str:
    return self.entity_type


@dataclasses.dataclass(frozen=True)
class DatabaseCallStats:
  """Summary statistics for tool calls (hmmsearch/jackhmmer/nhmmer) on a database."""

  db_type: str
  tool: str
  cores: int
  shard_count: int
  call_count: int
  min_seconds: float
  max_seconds: float
  mean_seconds: float
  median_seconds: float
  total_core_seconds: float = 0.0


@dataclasses.dataclass(frozen=True)
class RawDatabaseCall:
  """Individual hmmsearch / jackhmmer / nhmmer tool call record."""

  task_id: str
  vm_id: str
  timestamp: datetime | None
  tool: str
  db_type: str
  database_target: str
  shard_index: int | None
  shard_total: int
  duration_seconds: float
  cores: int = 1
  core_seconds: float = 0.0


@dataclasses.dataclass(frozen=True)
class EntityTypeThroughputStats:
  """Throughput and duration metrics for a specific target entity type."""

  entity_type: str
  display_name: str
  total_tasks: int
  succeeded_tasks: int
  failed_tasks: int
  avg_sequence_length: float | None
  duration_stats_min: DistributionStats | None
  avg_tasks_per_min: float
  alignments_per_hour: float
  alignments_per_vm_hour: float = 0.0
  avg_residues_per_min: float | None = None
  pct_of_total_tasks: float = 0.0


@dataclasses.dataclass(frozen=True)
class JobThroughputStats:
  """Throughput, duration, and failure metrics for a Cloud Batch array job."""

  job_id: str
  job_name: str
  job_uid: str
  job_state: str
  start_time: datetime | None
  end_time: datetime | None
  duration_seconds: float
  total_tasks: int
  tasks_succeeded: int
  tasks_failed: int
  avg_tasks_per_min: float
  avg_residues_aligned_per_min: float | None
  alignments_per_hour: float
  target_alignments_per_hour: float
  failures: list[TaskFailure]
  batch_preemptions: int
  tasks_records: list[TaskRecord] = dataclasses.field(default_factory=list)
  entity_type_stats: list[EntityTypeThroughputStats] = dataclasses.field(
      default_factory=list
  )
  overall_mean_duration_minutes: float = 0.0
  overall_median_duration_minutes: float = 0.0
  overall_alignments_per_vm_hour: float = 0.0


@dataclasses.dataclass(frozen=True)
class LogAnalysisStats:
  """Telemetry metrics extracted from Cloud Logging for a Batch job."""

  total_preemptions: int
  batch_preemptions: int
  gce_preemptions: int
  db_download_count: int
  db_download_duration_min: DistributionStats | None
  vm_count: int
  tasks_per_vm: DistributionStats | None
  db_cache_reuse_count: int = 0
  steady_state_alignments_per_hour: float = 0.0
  task_msa_durations_sec: dict[int, float] = dataclasses.field(
      default_factory=dict
  )
  msa_task_duration_min: DistributionStats | None = None
  db_call_stats: list[DatabaseCallStats] = dataclasses.field(default_factory=list)
  raw_db_calls: list[RawDatabaseCall] = dataclasses.field(default_factory=list)
  vm_task_counts: dict[str, int] = dataclasses.field(default_factory=dict)
  vm_download_durations_min: dict[str, float] = dataclasses.field(
      default_factory=dict
  )
  sampled_task_count: int | None = None
  total_succeeded_tasks: int | None = None
  inferred_task_entity_types: dict[int, str] = dataclasses.field(
      default_factory=dict
  )


def get_credentials(project_id: str) -> auth_credentials.Credentials:
  """Resolves Google Cloud credentials via ADC or gcloud CLI fallback."""
  scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  creds = None
  try:
    default_creds, _ = google.auth.default(scopes=scopes)
    sa_email = getattr(default_creds, "service_account_email", "") or ""
    if "colaboratory-playground" not in sa_email:
      creds = default_creds
  except Exception:  # pylint: disable=broad-exception-caught
    creds = None

  if creds is None:
    try:
      token = subprocess.check_output(
          ["gcloud", "auth", "print-access-token"],
          text=True,
          stderr=subprocess.DEVNULL,
      ).strip()
      creds = oauth2_credentials.Credentials(token=token)
    except Exception as err:
      raise RuntimeError(
          "Unable to resolve Google Cloud credentials. Run "
          "'gcloud auth application-default login' or 'gcloud auth login'."
      ) from err

  if hasattr(creds, "with_quota_project") and project_id:
    creds = creds.with_quota_project(project_id)
  return creds


def to_utc_datetime(value: Any) -> datetime | None:
  """Converts a protobuf Timestamp, datetime, or ISO-8601 string to UTC."""
  if not value:
    return None
  if isinstance(value, datetime):
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )
  if hasattr(value, "ToDatetime"):
    return value.ToDatetime(tzinfo=timezone.utc)
  if isinstance(value, str):
    cleaned = re.sub(
        r"(\.\d{6})\d+Z$", r"\1+00:00", value.replace("Z", "+00:00")
    )
    try:
      return datetime.fromisoformat(cleaned).astimezone(timezone.utc)
    except ValueError:
      return None
  return None


def format_duration(seconds: float) -> str:
  """Formats a duration in seconds into minutes and hours/seconds."""
  if seconds <= 0:
    return "0.00 min (0s)"
  minutes = seconds / 60.0
  hours = seconds / 3600.0
  whole_mins, rem_secs = divmod(int(round(seconds)), 60)
  if hours >= 1.0:
    return f"{minutes:.2f} min ({hours:.2f} hrs)"
  return f"{minutes:.2f} min ({whole_mins}m {rem_secs:02d}s)"


def format_core_time(seconds: float) -> str:
  """Formats total core time into human-readable duration."""
  if seconds >= 3600.0:
    return f"{seconds / 3600.0:,.2f} hrs ({seconds:,.0f}s)"
  if seconds >= 60.0:
    return f"{seconds / 60.0:,.2f} min ({seconds:,.0f}s)"
  return f"{seconds:.2f}s"


def resolve_sequences_path(
    explicit_path: str | None = None,
) -> pathlib.Path | None:
  """Finds the `benchmark_sequences.jsonl` file if it exists locally."""
  if explicit_path:
    path = pathlib.Path(explicit_path)
    return path if path.is_file() else None
  for candidate in _CANDIDATE_SEQUENCE_PATHS:
    if candidate.is_file():
      return candidate
  return None


def load_sequence_stats(jsonl_path: pathlib.Path) -> SequenceStats | None:
  """Loads and computes sequence length statistics from a JSONL file."""
  lengths: list[int] = []
  names_by_index: dict[int, str] = {}
  lengths_by_index: dict[int, int] = {}
  types_by_index: dict[int, str] = {}

  with jsonl_path.open("r", encoding="utf-8") as handle:
    for idx, line in enumerate(handle):
      stripped = line.strip()
      if not stripped:
        continue
      record = json.loads(stripped)
      if not isinstance(record, dict) or not record:
        continue
      if "sequences" in record and isinstance(record["sequences"], list):
        name = str(record.get("name", f"input_{idx}"))
        target_type = str(
            record.get("entity_type") or record.get("target_type", "")
        )
        total_len = 0
        has_protein = False
        has_rna = False
        chain_count = 0
        for seq_obj in record["sequences"]:
          if isinstance(seq_obj, dict):
            for entity_key, entity in seq_obj.items():
              if isinstance(entity, dict) and "sequence" in entity:
                total_len += len(str(entity["sequence"]))
                chain_count += 1
                if entity_key.lower() == "protein":
                  has_protein = True
                elif entity_key.lower() == "rna":
                  has_rna = True
        seq_len = total_len
        if not target_type or target_type == "heterogeneous":
          for prefix in (
              "protein_monomer__",
              "rna_monomer__",
              "protein_multimer__",
              "rnp_complex__",
          ):
            if name.startswith(prefix):
              target_type = prefix[:-2]
              break
          if not target_type or target_type == "heterogeneous":
            if has_protein and has_rna:
              target_type = "rnp_complex"
            elif has_rna and not has_protein:
              target_type = "rna_monomer"
            elif has_protein and not has_rna:
              target_type = (
                  "protein_multimer" if chain_count > 1 else "protein_monomer"
              )
            else:
              target_type = "unknown"
      elif "name" in record and "sequence" in record:
        name, seq = str(record["name"]), str(record["sequence"])
        seq_len = len(seq)
        target_type = str(record.get("target_type", "protein_monomer"))
      else:
        first_key, first_val = next(iter(record.items()))
        name, seq = str(first_key), str(first_val)
        seq_len = len(seq)
        target_type = "unknown"
      lengths.append(seq_len)
      names_by_index[idx] = name
      lengths_by_index[idx] = seq_len
      types_by_index[idx] = target_type

  if not lengths:
    return None

  return SequenceStats(
      path=jsonl_path,
      count=len(lengths),
      avg_length=statistics.mean(lengths),
      min_length=min(lengths),
      max_length=max(lengths),
      names_by_index=names_by_index,
      lengths_by_index=lengths_by_index,
      types_by_index=types_by_index,
  )


def fetch_job_and_tasks(
    project_id: str,
    job_id: str,
    credentials: auth_credentials.Credentials,
    region: str | None = None,
) -> tuple[batch_v1.Job, list[batch_v1.Task], str]:
  """Retrieves the Cloud Batch Job, all of its Task records, and its region.

  If `region` is omitted, infers the region from `job_id` (if a full resource
  name is provided) or by listing jobs across all regions via wildcard location
  `projects/{project_id}/locations/-`.

  Args:
    project_id: Google Cloud project ID.
    job_id: Short Batch job ID or full resource name.
    credentials: Google Cloud credentials.
    region: Optional Google Cloud region (auto-discovered if None).

  Returns:
    A tuple of `(job, tasks, resolved_region)`.

  Raises:
    ValueError: If the job cannot be found in the project.
  """
  client = batch_v1.BatchServiceClient(
      credentials=credentials,
      transport="rest",
      client_options={"quota_project_id": project_id},
  )

  job: batch_v1.Job | None = None
  if job_id.startswith("projects/"):
    job = client.get_job(name=job_id)
  elif region:
    job_name = f"projects/{project_id}/locations/{region}/jobs/{job_id}"
    job = client.get_job(name=job_name)
  else:
    parent = f"projects/{project_id}/locations/-"
    for candidate in client.list_jobs(parent=parent):
      if candidate.name.split("/")[-1] == job_id or candidate.uid == job_id:
        job = candidate
        break
    if job is None:
      raise ValueError(
          f"Batch job '{job_id}' not found in project '{project_id}' across "
          "any region. Verify --project-id and --job-id, or pass --region."
      )

  loc_match = re.search(r"/locations/([^/]+)/jobs/", job.name)
  resolved_region = loc_match.group(1) if loc_match else (region or "unknown")

  task_groups = [tg.name for tg in job.task_groups if tg.name] or [
      f"{job.name}/taskGroups/group0"
  ]
  tasks: list[batch_v1.Task] = []
  for tg_name in task_groups:
    tasks.extend(client.list_tasks(parent=tg_name))
  return job, tasks, resolved_region


def _state_name(state_val: Any) -> str:
  """Normalizes a protobuf enum or string state into its uppercase name."""
  if hasattr(state_val, "name"):
    return str(state_val.name)
  text = str(state_val)
  return text.split(".")[-1] if "." in text else text


def _is_preemption_event(description: str, exit_code: int | None) -> bool:
  """Returns True if a Batch status event represents a Spot VM preemption."""
  if exit_code == PREEMPTION_EXIT_CODE:
    return True
  lower_desc = description.lower()
  return any(keyword in lower_desc for keyword in _PREEMPTION_KEYWORDS)


def compute_throughput_and_failures(
    job: batch_v1.Job,
    tasks: Sequence[batch_v1.Task],
    sequence_stats: SequenceStats | None,
    target_alignments_per_hour: float = DEFAULT_TARGET_ALIGNMENTS_PER_HOUR,
) -> JobThroughputStats:
  """Computes job duration, throughput metrics, and task failure details."""
  task_start_times: list[datetime] = []
  task_end_times: list[datetime] = []
  failures: list[TaskFailure] = []
  task_records: list[TaskRecord] = []
  succeeded_count = 0
  failed_count = 0
  batch_preemptions = 0

  for task in tasks:
    match = re.search(r"/taskGroups/([^/]+)/tasks/(\d+)$", task.name or "")
    if match:
      task_group = match.group(1)
      task_idx = int(match.group(2))
    else:
      m_idx = re.search(r"/tasks/(\d+)$", task.name or "")
      task_idx = int(m_idx.group(1)) if m_idx else -1
      task_group = "group0"
    task_short_id = task.name.split("/")[-1] if task.name else str(task_idx)
    state_str = _state_name(task.status.state)

    running_times: list[datetime] = []
    assigned_times: list[datetime] = []
    terminal_time: datetime | None = None
    failure_time: datetime | None = None
    failure_exit_code: int | None = None
    failure_reason = ""

    for event in task.status.status_events:
      ev_time = to_utc_datetime(event.event_time)
      ev_state = _state_name(event.task_state)
      desc = event.description or ""
      exit_code = (
          event.task_execution.exit_code
          if event.task_execution and event.task_execution.exit_code
          else None
      )

      if _is_preemption_event(desc, exit_code):
        batch_preemptions += 1

      if ev_time is not None:
        if ev_state == "RUNNING" or "to RUNNING" in desc:
          running_times.append(ev_time)
        elif ev_state == "ASSIGNED" or "to ASSIGNED" in desc:
          assigned_times.append(ev_time)

        if ev_state in ("SUCCEEDED", "FAILED") or any(
            k in desc for k in ("to SUCCEEDED", "to FAILED")
        ):
          terminal_time = ev_time

        if ev_state == "FAILED" or "to FAILED" in desc:
          failure_time = ev_time
          if exit_code is not None:
            failure_exit_code = exit_code
          if desc:
            failure_reason = desc

    start_ts = (
        min(running_times)
        if running_times
        else (min(assigned_times) if assigned_times else None)
    )
    if start_ts is not None:
      task_start_times.append(start_ts)
    if terminal_time is not None:
      task_end_times.append(terminal_time)

    task_dur_sec = (
        (terminal_time - start_ts).total_seconds()
        if (start_ts and terminal_time and terminal_time >= start_ts)
        else None
    )
    input_name = (
        sequence_stats.names_by_index.get(task_idx)
        if sequence_stats
        else None
    )
    seq_len = (
        sequence_stats.lengths_by_index.get(task_idx)
        if sequence_stats
        else None
    )

    entity_type = (
        sequence_stats.types_by_index.get(task_idx, "unknown")
        if sequence_stats
        else "unknown"
    )
    if entity_type == "unknown" and input_name:
      for prefix in (
          "protein_monomer__",
          "rna_monomer__",
          "protein_multimer__",
          "rnp_complex__",
      ):
        if input_name.startswith(prefix):
          entity_type = prefix[:-2]
          break

    task_records.append(
        TaskRecord(
            task_index=task_idx,
            task_id=task_short_id,
            input_name=input_name,
            sequence_length=seq_len,
            state=state_str,
            start_time=start_ts,
            end_time=terminal_time,
            duration_seconds=task_dur_sec,
            exit_code=failure_exit_code,
            reason=failure_reason if state_str == "FAILED" else "",
            entity_type=entity_type,
            task_group=task_group,
        )
    )

    if state_str == "SUCCEEDED":
      succeeded_count += 1
    elif state_str == "FAILED":
      failed_count += 1
      failures.append(
          TaskFailure(
              task_index=task_idx,
              task_id=task_short_id,
              input_name=input_name,
              failed_at=failure_time or terminal_time,
              exit_code=failure_exit_code,
              reason=failure_reason or "Task entered FAILED state",
          )
      )

  failures.sort(key=lambda item: (item.task_index, item.task_id))
  task_records.sort(key=lambda item: (item.task_index, item.task_id))

  job_run_time: datetime | None = None
  job_end_time: datetime | None = None
  for ev in job.status.status_events:
    ev_time = to_utc_datetime(ev.event_time)
    desc = ev.description or ""
    if ev_time is None:
      continue
    if "RUNNING" in desc and job_run_time is None:
      job_run_time = ev_time
    if any(k in desc for k in ("SUCCEEDED", "FAILED")):
      job_end_time = ev_time

  start_time = (
      min(task_start_times)
      if task_start_times
      else (job_run_time or to_utc_datetime(job.create_time))
  )
  end_time = (
      max(task_end_times)
      if task_end_times
      else (job_end_time or to_utc_datetime(job.update_time))
  )

  if start_time and end_time and end_time > start_time:
    duration_seconds = (end_time - start_time).total_seconds()
  elif job_run_time and job_end_time and job_end_time > job_run_time:
    duration_seconds = (job_end_time - job_run_time).total_seconds()
  else:
    duration_seconds = 0.0

  duration_minutes = duration_seconds / 60.0
  avg_tasks_per_min = (
      succeeded_count / duration_minutes if duration_minutes > 0 else 0.0
  )
  alignments_per_hour = avg_tasks_per_min * 60.0
  avg_residues_aligned_per_min = (
      avg_tasks_per_min * sequence_stats.avg_length
      if sequence_stats is not None
      else None
  )

  total_tasks = len(tasks)
  if not total_tasks and job.task_groups:
    total_tasks = int(job.task_groups[0].task_count or 0)

  tasks_by_type: dict[str, list[TaskRecord]] = defaultdict(list)
  for tr in task_records:
    tasks_by_type[tr.target_type].append(tr)

  display_names = {
      "protein_monomer": "Protein Monomer",
      "protein_multimer": "Protein Multimer",
      "rna_monomer": "RNA Monomer",
      "rnp_complex": "RNP Complex",
      "unknown": "Unknown Entity",
  }

  entity_type_stats: list[EntityTypeThroughputStats] = []
  for e_type, e_tasks in sorted(tasks_by_type.items()):
    disp_name = display_names.get(e_type, e_type.replace("_", " ").title())
    tot = len(e_tasks)
    succ = sum(1 for t in e_tasks if t.state == "SUCCEEDED")
    fail = sum(1 for t in e_tasks if t.state == "FAILED")
    durations_m = [
        t.duration_seconds / 60.0
        for t in e_tasks
        if t.state == "SUCCEEDED" and t.duration_seconds is not None
    ]
    dur_stats = (
        DistributionStats.from_values(durations_m) if durations_m else None
    )
    t_per_min = succ / duration_minutes if duration_minutes > 0 else 0.0
    a_per_hr = t_per_min * 60.0
    lens = [
        t.sequence_length
        for t in e_tasks
        if t.state == "SUCCEEDED" and t.sequence_length is not None
    ]
    avg_l = statistics.mean(lens) if lens else None
    tot_l = sum(lens)
    res_per_min = (
        tot_l / duration_minutes if duration_minutes > 0 and tot_l > 0 else None
    )
    align_per_vm_hr = (
        (60.0 / dur_stats.mean_val)
        if (dur_stats and dur_stats.mean_val > 0)
        else 0.0
    )
    pct = (tot / total_tasks * 100.0) if total_tasks > 0 else 0.0

    entity_type_stats.append(
        EntityTypeThroughputStats(
            entity_type=e_type,
            display_name=disp_name,
            total_tasks=tot,
            succeeded_tasks=succ,
            failed_tasks=fail,
            avg_sequence_length=avg_l,
            duration_stats_min=dur_stats,
            avg_tasks_per_min=t_per_min,
            alignments_per_hour=a_per_hr,
            alignments_per_vm_hour=align_per_vm_hr,
            avg_residues_per_min=res_per_min,
            pct_of_total_tasks=pct,
        )
    )

  all_succeeded_durations_m = [
      t.duration_seconds / 60.0
      for t in task_records
      if t.state == "SUCCEEDED" and t.duration_seconds is not None
  ]
  overall_mean_dur_m = (
      statistics.mean(all_succeeded_durations_m)
      if all_succeeded_durations_m
      else 0.0
  )
  overall_median_dur_m = (
      statistics.median(all_succeeded_durations_m)
      if all_succeeded_durations_m
      else 0.0
  )
  overall_align_per_vm_hr = (
      (60.0 / overall_mean_dur_m) if overall_mean_dur_m > 0 else 0.0
  )

  return JobThroughputStats(
      job_id=job.name.split("/")[-1] if job.name else "",
      job_name=job.name,
      job_uid=job.uid,
      job_state=_state_name(job.status.state),
      start_time=start_time,
      end_time=end_time,
      duration_seconds=duration_seconds,
      total_tasks=total_tasks,
      tasks_succeeded=succeeded_count,
      tasks_failed=failed_count,
      avg_tasks_per_min=avg_tasks_per_min,
      avg_residues_aligned_per_min=avg_residues_aligned_per_min,
      alignments_per_hour=alignments_per_hour,
      target_alignments_per_hour=target_alignments_per_hour,
      failures=failures,
      batch_preemptions=batch_preemptions,
      tasks_records=task_records,
      entity_type_stats=entity_type_stats,
      overall_mean_duration_minutes=overall_mean_dur_m,
      overall_median_duration_minutes=overall_median_dur_m,
      overall_alignments_per_vm_hour=overall_align_per_vm_hr,
  )


def _extract_log_payload(entry: Any) -> str:
  """Extracts text message content from a Cloud Logging LogEntry."""
  payload = getattr(entry, "payload", None)
  if isinstance(payload, str):
    return payload
  if isinstance(payload, dict):
    return json.dumps(payload)
  api_repr = entry.to_api_repr() if hasattr(entry, "to_api_repr") else {}
  if "textPayload" in api_repr:
    return str(api_repr["textPayload"])
  if "jsonPayload" in api_repr:
    return json.dumps(api_repr["jsonPayload"])
  return str(payload or "")


def normalize_database_name(target: str) -> str:
  """Normalizes a database target filename to a canonical type key."""
  lower = target.lower()
  if "uniprot" in lower:
    return "uniprot"
  if "mgy" in lower or "mgnify" in lower:
    return "mgy"
  if "uniref" in lower:
    return "uniref"
  if "pdb" in lower:
    return "pdb"
  if "bfd" in lower:
    return "bfd"
  if "rfam" in lower:
    return "rfam"
  if "rnacentral" in lower:
    return "rnacentral"
  if "nt_rna" in lower or "ntrna" in lower:
    return "ntrna"
  return target.split(".")[0]


def _list_entries_resilient(
    logging_client: cloud_logging.Client,
    base_filter: str,
    max_entries: int | None = None,
    console: Console | None = None,
    description: str = "Streaming Cloud Logging entries...",
) -> list[Any]:
  """Fetches log entries with a Rich progress bar and automatic backoff for 429."""
  current_filter = base_filter
  seen_insert_ids: set[str] = set()
  last_timestamp = None
  results: list[Any] = []
  retries = 0
  max_retries = 15

  progress = Progress(
      SpinnerColumn(),
      TextColumn("[bold cyan]{task.description}[/bold cyan]"),
      BarColumn(),
      TextColumn("[bold green]{task.completed:,}[/bold green] entries"),
      TimeElapsedColumn(),
      console=console,
  )

  with progress:
    task = progress.add_task(
        description,
        total=max_entries,
    )
    while True:
      try:
        entries_iter = logging_client.list_entries(
            filter_=current_filter,
            order_by=cloud_logging.ASCENDING,
            page_size=1000,
        )
        for entry in entries_iter:
          iid = getattr(entry, "insert_id", None)
          if iid and iid in seen_insert_ids:
            continue
          if iid:
            seen_insert_ids.add(iid)
          last_timestamp = getattr(entry, "timestamp", None)
          results.append(entry)
          progress.update(task, completed=len(results))
          if max_entries is not None and len(results) >= max_entries:
            return results
        break
      except TooManyRequests:
        retries += 1
        if retries > max_retries:
          if console:
            console.print(
                "[yellow]Warning: Exceeded maximum retries for Cloud Logging"
                f" quota. Proceeding with {len(results):,} collected"
                " entries.[/yellow]"
            )
          break
        sleep_sec = 10
        progress.update(
            task,
            description=(
                f"[yellow]Rate limit reached (429); waiting {sleep_sec}s to"
                " resume...[/yellow]"
            ),
        )
        time.sleep(sleep_sec)
        progress.update(task, description=description)
        if last_timestamp is not None:
          ts_val = to_utc_datetime(last_timestamp)
          ts_str = ts_val.isoformat() if ts_val else str(last_timestamp)
          current_filter = f'{base_filter} AND timestamp>="{ts_str}"'
      except GoogleAPICallError as err:
        retries += 1
        if retries > max_retries:
          if console:
            console.print(
                f"[yellow]Warning: Cloud Logging error: {err}."
                f" Proceeding with {len(results):,} collected entries.[/yellow]"
            )
          break
        progress.update(
            task,
            description=f"[yellow]API error: {err}; retrying in 5s...[/yellow]",
        )
        time.sleep(5)
        progress.update(task, description=description)
        if last_timestamp is not None:
          ts_val = to_utc_datetime(last_timestamp)
          ts_str = ts_val.isoformat() if ts_val else str(last_timestamp)
          current_filter = f'{base_filter} AND timestamp>="{ts_str}"'

  return results


def analyze_job_logs(
    project_id: str,
    job_stats: JobThroughputStats,
    credentials: auth_credentials.Credentials,
    sample_tasks: int = 10,
    max_log_entries: int | None = None,
    console: Console | None = None,
    job: batch_v1.Job | None = None,
) -> LogAnalysisStats:
  """Downloads and analyzes Cloud Logging entries for the Batch job."""
  tool_cores = extract_tool_cores(job)
  logging_client = cloud_logging.Client(
      project=project_id,
      credentials=credentials,
      _use_grpc=False,
  )

  time_clauses: list[str] = []
  if job_stats.start_time is not None:
    t_min = (job_stats.start_time - timedelta(minutes=30)).isoformat()
    time_clauses.append(f'timestamp>="{t_min}"')
  if job_stats.end_time is not None:
    t_max = (job_stats.end_time + timedelta(minutes=30)).isoformat()
    time_clauses.append(f'timestamp<="{t_max}"')
  time_filter = (" AND " + " AND ".join(time_clauses)) if time_clauses else ""

  # Query 1: Task lifecycle milestones and database syncs across all VMs
  milestone_filter = (
      f'logName="projects/{project_id}/logs/batch_task_logs" '
      f'AND labels.job_uid="{job_stats.job_uid}" '
      'AND ("Syncing sharded databases" '
      'OR "Syncing databases" '
      'OR "Databases successfully synced" '
      'OR "Sharded databases successfully synced" '
      'OR "pdb_mmcif.squashfs successfully synced" '
      'OR "successfully synced" '
      'OR "Databases already present" '
      'OR "Sharded databases already present" '
      'OR "Starting AlphaFold 3 MSA Task" '
      f'OR "completed successfully"){time_filter}'
  )

  milestone_entries = _list_entries_resilient(
      logging_client,
      milestone_filter,
      max_entries=max_log_entries,
      console=console,
      description="Streaming task lifecycle & DB sync logs...",
  )

  # Query 2: Tool call durations (Jackhmmer / Hmmsearch / Nhmmer)
  succeeded_tasks = [
      r for r in job_stats.tasks_records if r.state == "SUCCEEDED"
  ]
  if not succeeded_tasks:
    succeeded_tasks = list(job_stats.tasks_records)

  tool_entries: list[Any] = []
  sampled_task_count: int | None = None
  total_succeeded = len(succeeded_tasks)

  tool_limit = None
  if max_log_entries is not None:
    tool_limit = max(0, max_log_entries - len(milestone_entries))

  if max_log_entries is None or tool_limit > 0:
    task_id_filter = ""
    if sample_tasks > 0 and succeeded_tasks:
      num_samples = min(sample_tasks, len(succeeded_tasks))
      sampled = random.sample(succeeded_tasks, num_samples)
      sampled.sort(key=lambda r: r.task_index)
      sampled_ids = [
          f'"{job_stats.job_uid}-{getattr(r, "task_group", "group0")}-{r.task_index}"'
          for r in sampled
      ]
      task_id_filter = f'AND labels.task_id=({" OR ".join(sampled_ids)}) '
      sampled_task_count = num_samples
      desc = (
          f"Streaming search tool duration logs ({num_samples} sampled"
          " tasks)..."
      )
    else:
      desc = "Streaming search tool duration logs (all tasks)..."

    tool_filter = (
        f'logName="projects/{project_id}/logs/batch_task_logs" '
        f'AND labels.job_uid="{job_stats.job_uid}" '
        f"{task_id_filter}"
        'AND ("Finished Jackhmmer" '
        'OR "Finished Hmmsearch" '
        f'OR "Finished Nhmmer"){time_filter}'
    )

    tool_entries = _list_entries_resilient(
        logging_client,
        tool_filter,
        max_entries=tool_limit,
        console=console,
        description=desc,
    )

  entries = milestone_entries + tool_entries

  active_sync_start: dict[str, datetime] = {}
  active_msa_start: dict[str, datetime] = {}
  download_durations_min: list[float] = []
  vm_download_durations_min: dict[str, float] = {}
  msa_task_durations_min: list[float] = []
  task_msa_durations_sec: dict[int, float] = {}
  db_cache_reuse_count = 0
  vm_completed_tasks: dict[str, set[str]] = {}
  completion_counter = 0

  db_tool_durations: dict[str, list[float]] = defaultdict(list)
  db_tool_shards: dict[str, set[int]] = defaultdict(set)
  db_tool_names: dict[str, str] = {}
  raw_db_calls: list[RawDatabaseCall] = []
  task_seen_tools: dict[int, set[str]] = defaultdict(set)
  task_seen_chains: dict[int, set[str]] = defaultdict(set)
  inferred_types: dict[int, str] = {}

  for entry in entries:
    ts = to_utc_datetime(getattr(entry, "timestamp", None))
    msg = _extract_log_payload(entry)
    labels = getattr(entry, "labels", None) or {}
    resource = getattr(entry, "resource", None)
    res_labels = getattr(resource, "labels", None) or {}

    vm_id = (
        labels.get("hostname")
        or res_labels.get("instance_id")
        or "unknown_vm"
    )
    task_label = (
        labels.get("task_id")
        or labels.get("batch.googleapis.com/task_id")
        or ""
    )
    m_task_idx = re.search(r"-(\d+)$", task_label) or re.search(
        r"/tasks/(\d+)$", task_label
    )
    task_idx_val = int(m_task_idx.group(1)) if m_task_idx else None

    if vm_id != "unknown_vm":
      vm_completed_tasks.setdefault(vm_id, set())

    if "Syncing sharded databases" in msg or "Syncing databases" in msg:
      if ts is not None:
        active_sync_start.setdefault(vm_id, ts)
    elif any(
        phrase in msg
        for phrase in (
            "Databases successfully synced",
            "Sharded databases successfully synced",
            "pdb_mmcif.squashfs successfully synced",
            "successfully synced",
        )
    ):
      start_ts = active_sync_start.pop(vm_id, None)
      if start_ts is not None and ts is not None and ts >= start_ts:
        dur_min = (ts - start_ts).total_seconds() / 60.0
        download_durations_min.append(dur_min)
        vm_download_durations_min[vm_id] = dur_min
    elif "Databases already present" in msg or "Sentinel files" in msg:
      db_cache_reuse_count += 1
    elif "Starting AlphaFold 3 MSA Task" in msg:
      match = re.search(r"Starting AlphaFold 3 MSA Task\s+(\d+)", msg)
      task_key = (
          f"task_{match.group(1)}" if match else (task_label or f"vm_{vm_id}")
      )
      if ts is not None:
        active_msa_start[task_key] = ts
    elif "completed successfully" in msg and "AlphaFold 3 MSA task" in msg:
      vm_completed_tasks.setdefault(vm_id, set())
      match = re.search(
          r"AlphaFold 3 MSA task\s+(\d+)\s+completed successfully", msg
      )
      if match:
        task_key = f"task_{match.group(1)}"
      elif task_label:
        task_key = task_label
      else:
        completion_counter += 1
        task_key = f"completion_{completion_counter}"
      vm_completed_tasks[vm_id].add(task_key)
      msa_start_ts = active_msa_start.pop(task_key, None) or (
          active_msa_start.pop(f"vm_{vm_id}", None)
      )
      if msa_start_ts is not None and ts is not None and ts >= msa_start_ts:
        dur_s = (ts - msa_start_ts).total_seconds()
        msa_task_durations_min.append(dur_s / 60.0)
        if match:
          task_msa_durations_sec[int(match.group(1))] = dur_s

    # Track chain executions and fold job names to infer entity types
    m_chain = re.search(
        r"Running data pipeline for chain\s+([A-Za-z0-9]+)", msg
    )
    if m_chain and task_idx_val is not None:
      task_seen_chains[task_idx_val].add(m_chain.group(1))

    m_fold = re.search(
        r"(?:Fold job|Running fold job)\s+(protein_monomer|protein_multimer|rna_monomer|rnp_complex)__",
        msg,
    )
    if m_fold and task_idx_val is not None:
      inferred_types[task_idx_val] = m_fold.group(1)

    # Database search call durations (Finished Jackhmmer / Hmmsearch / Nhmmer)
    m_tool = re.search(
        r"Finished\s+(Jackhmmer|Hmmsearch|Nhmmer)\s*(?:\(([^)]+)\))?\s+in\s+([0-9.]+)\s+seconds",
        msg,
        re.IGNORECASE,
    )
    if m_tool:
      tool_name = m_tool.group(1)
      if task_idx_val is not None:
        task_seen_tools[task_idx_val].add(tool_name.lower())
      target_file = m_tool.group(2) or "unknown"
      dur_sec = float(m_tool.group(3))
      m_shard = re.search(r"-(\d+)-of-(\d+)", target_file)
      shard_idx = int(m_shard.group(1)) if m_shard else None
      shards_total = int(m_shard.group(2)) if m_shard else 1

      db_type = normalize_database_name(target_file)
      db_tool_names[db_type] = tool_name
      db_tool_durations[db_type].append(dur_sec)
      db_tool_shards[db_type].add(shards_total)

      call_cores = tool_cores.get(
          tool_name.lower(),
          tool_cores.get(TOOL_BY_DB_TYPE.get(db_type, ""), 1),
      )
      raw_db_calls.append(
          RawDatabaseCall(
              task_id=task_label,
              vm_id=vm_id,
              timestamp=ts,
              tool=tool_name,
              db_type=db_type,
              database_target=target_file,
              shard_index=shard_idx,
              shard_total=shards_total,
              duration_seconds=dur_sec,
              cores=call_cores,
              core_seconds=dur_sec * call_cores,
          )
      )

  gce_preemptions = 0
  try:
    preempt_filter = (
        f'logName="projects/{project_id}/logs/'
        'cloudaudit.googleapis.com%2Fsystem_event" '
        'AND protoPayload.methodName="compute.instances.preempted"'
        f"{time_filter}"
    )
    known_tokens = set(vm_completed_tasks.keys()) | {
        job_stats.job_uid,
        job_stats.job_id,
        "af3-msa",
    }
    known_tokens.discard("unknown_vm")
    known_tokens.discard("")

    preempt_entries = logging_client.list_entries(
        filter_=preempt_filter,
        order_by=cloud_logging.ASCENDING,
        page_size=500,
    )
    preempted_instances: set[str] = set()
    for entry in preempt_entries:
      api_repr = entry.to_api_repr() if hasattr(entry, "to_api_repr") else {}
      proto_payload = api_repr.get("protoPayload", {})
      resource_name = str(proto_payload.get("resourceName", ""))
      instance_id = str(
          api_repr.get("resource", {}).get("labels", {}).get("instance_id", "")
      )
      combined = f"{resource_name} {instance_id}"
      if any(token in combined for token in known_tokens):
        preempted_instances.add(instance_id or resource_name)
    gce_preemptions = len(preempted_instances)
  except Exception:  # pylint: disable=broad-exception-caught
    gce_preemptions = 0

  total_preemptions = max(job_stats.batch_preemptions, gce_preemptions)
  counts_per_vm = [len(tasks) for tasks in vm_completed_tasks.values()]
  vm_task_counts = {vm: len(tasks) for vm, tasks in vm_completed_tasks.items()}

  db_call_stats: list[DatabaseCallStats] = []
  db_order = {
      "uniprot": 0,
      "mgy": 1,
      "uniref": 2,
      "pdb": 3,
      "bfd": 4,
      "rfam": 5,
      "rnacentral": 6,
      "ntrna": 7,
  }
  for db_type, durs in sorted(
      db_tool_durations.items(),
      key=lambda item: (db_order.get(item[0], 99), item[0]),
  ):
    shards = max(db_tool_shards[db_type]) if db_tool_shards[db_type] else 1
    tool = db_tool_names.get(db_type, TOOL_BY_DB_TYPE.get(db_type, "unknown"))
    cores = tool_cores.get(tool.lower(), 1)
    total_sec = sum(durs) * cores
    db_call_stats.append(
        DatabaseCallStats(
            db_type=db_type,
            tool=tool,
            cores=cores,
            shard_count=shards,
            call_count=len(durs),
            min_seconds=min(durs),
            max_seconds=max(durs),
            mean_seconds=statistics.mean(durs),
            median_seconds=statistics.median(durs),
            total_core_seconds=total_sec,
        )
    )

  for tidx, tools in task_seen_tools.items():
    if tidx in inferred_types:
      continue
    chains = task_seen_chains.get(tidx, set())
    has_prot = any(t in tools for t in ("jackhmmer", "hmmsearch", "protein"))
    has_r = any(t in tools for t in ("nhmmer", "rna"))
    if has_prot and has_r:
      inferred_types[tidx] = "rnp_complex"
    elif has_r and not has_prot:
      inferred_types[tidx] = "rna_monomer"
    elif has_prot and not has_r:
      inferred_types[tidx] = (
          "protein_multimer" if len(chains) > 1 else "protein_monomer"
      )

  steady_state_align_hr = 0.0
  if msa_task_durations_min and len(vm_completed_tasks) > 0:
    mean_msa_m = statistics.mean(msa_task_durations_min)
    if mean_msa_m > 0:
      steady_state_align_hr = (len(vm_completed_tasks) * 60.0) / mean_msa_m

  return LogAnalysisStats(
      total_preemptions=total_preemptions,
      batch_preemptions=job_stats.batch_preemptions,
      gce_preemptions=gce_preemptions,
      db_download_count=len(download_durations_min),
      db_download_duration_min=DistributionStats.from_values(
          download_durations_min
      ),
      vm_count=len(vm_completed_tasks),
      tasks_per_vm=DistributionStats.from_values(counts_per_vm),
      db_cache_reuse_count=db_cache_reuse_count,
      steady_state_alignments_per_hour=steady_state_align_hr,
      task_msa_durations_sec=task_msa_durations_sec,
      msa_task_duration_min=DistributionStats.from_values(
          msa_task_durations_min
      ),
      db_call_stats=db_call_stats,
      raw_db_calls=raw_db_calls,
      vm_task_counts=vm_task_counts,
      vm_download_durations_min=vm_download_durations_min,
      sampled_task_count=sampled_task_count,
      total_succeeded_tasks=total_succeeded,
      inferred_task_entity_types=inferred_types,
  )


def print_sequence_table(console: Console, stats: SequenceStats) -> None:
  """Prints a Rich table summarizing `benchmark_sequences.jsonl` statistics."""
  table = Table(
      title=f"Benchmark Sequences Summary ({stats.path.name})",
      box=box.ROUNDED,
      header_style="bold cyan",
  )
  table.add_column("Metric", style="bold")
  table.add_column("Value", justify="right")
  table.add_row("Number of Benchmark Inputs", f"{stats.count:,}")

  if stats.types_by_index:
    type_counts: dict[str, int] = defaultdict(int)
    for t in stats.types_by_index.values():
      type_counts[t] += 1
    for t_name, t_cnt in sorted(type_counts.items()):
      display_name = t_name.replace("_", " ").title()
      table.add_row(f"  • {display_name}", f"{t_cnt:,} inputs")

  table.add_row("Average Sequence Length", f"{stats.avg_length:,.2f} residues")
  table.add_row("Min Sequence Length", f"{stats.min_length:,} residues")
  table.add_row("Max Sequence Length", f"{stats.max_length:,} residues")
  console.print()
  console.print(table)


def print_throughput_table(
    console: Console,
    stats: JobThroughputStats,
    sequence_stats: SequenceStats | None,
) -> None:
  """Prints a Rich table summarizing job duration and alignment throughput."""
  table = Table(
      title=f"Throughput Summary — Job: {stats.job_id} ({stats.job_state})",
      box=box.ROUNDED,
      header_style="bold green",
  )
  table.add_column("Metric", style="bold")
  table.add_column("Value", justify="right")
  table.add_row("Total Job Duration", format_duration(stats.duration_seconds))
  table.add_row(
      "Tasks Succeeded",
      f"{stats.tasks_succeeded:,} / {stats.total_tasks:,}",
  )
  table.add_row(
      "Avg. Tasks / Minute",
      f"{stats.avg_tasks_per_min:,.2f} tasks/min",
  )

  target_hr = stats.target_alignments_per_hour
  target_min = target_hr / 60.0
  pct_of_target = (
      (stats.alignments_per_hour / target_hr) * 100.0 if target_hr > 0 else 0.0
  )
  delta_hr = stats.alignments_per_hour - target_hr
  color = "green" if stats.alignments_per_hour >= target_hr else "yellow"
  table.add_row(
      f"Design Target Comparison ({target_hr:,.0f}/hr | {target_min:.2f}/min)",
      f"[{color}]{stats.alignments_per_hour:,.1f} alignments/hr "
      f"({pct_of_target:.1f}% of {target_hr:,.0f}/hr target, "
      f"{delta_hr:+,.1f}/hr)[/{color}]",
  )
  console.print()
  console.print(table)


def print_entity_type_throughput_table(
    console: Console,
    stats: JobThroughputStats,
) -> None:
  """Prints a Rich table summarizing throughput and duration broken down by entity type."""
  if not stats.entity_type_stats:
    return

  table = Table(
      title=(
          "Throughput & Duration Breakdown by Entity Type — Job:"
          f" {stats.job_id}"
      ),
      box=box.ROUNDED,
      header_style="bold cyan",
  )
  table.add_column("Entity Type", style="bold")
  table.add_column("Tasks", justify="right")
  table.add_column("Avg Length", justify="right")
  table.add_column("Mean Duration", justify="right")
  table.add_column("Median Duration", justify="right")
  table.add_column("Alignments / VM Hour", justify="right")

  for et in stats.entity_type_stats:
    len_unit = (
        "nt"
        if et.entity_type == "rna_monomer"
        else ("residues" if et.entity_type == "rnp_complex" else "aa")
    )
    len_str = (
        f"{et.avg_sequence_length:,.0f} {len_unit}"
        if et.avg_sequence_length is not None
        else "N/A"
    )
    mean_dur = (
        f"{et.duration_stats_min.mean_val:.2f} min"
        if et.duration_stats_min
        else "N/A"
    )
    med_dur = (
        f"{et.duration_stats_min.median_val:.2f} min"
        if et.duration_stats_min
        else "N/A"
    )
    vm_hr_str = f"{et.alignments_per_vm_hour:,.1f} / VM hr"
    table.add_row(
        et.display_name,
        f"{et.succeeded_tasks:,} / {et.total_tasks:,}",
        len_str,
        mean_dur,
        med_dur,
        vm_hr_str,
    )

  if len(stats.entity_type_stats) > 1:
    overall_avg_len = (
        f"{stats.avg_residues_aligned_per_min / stats.avg_tasks_per_min:,.0f}"
        " residues"
        if (stats.avg_residues_aligned_per_min and stats.avg_tasks_per_min > 0)
        else "-"
    )
    overall_mean_str = (
        f"[bold]{stats.overall_mean_duration_minutes:.2f} min[/bold]"
        if stats.overall_mean_duration_minutes > 0
        else "-"
    )
    overall_med_str = (
        f"[bold]{stats.overall_median_duration_minutes:.2f} min[/bold]"
        if stats.overall_median_duration_minutes > 0
        else "-"
    )
    overall_vm_hr_str = (
        f"[bold]{stats.overall_alignments_per_vm_hour:,.1f} / VM hr[/bold]"
        if stats.overall_alignments_per_vm_hour > 0
        else "-"
    )
    table.add_section()
    table.add_row(
        "[bold]Overall / Combined[/bold]",
        f"[bold]{stats.tasks_succeeded:,} / {stats.total_tasks:,}[/bold]",
        f"[bold]{overall_avg_len}[/bold]",
        overall_mean_str,
        overall_med_str,
        overall_vm_hr_str,
    )

  console.print()
  console.print(table)


def print_failures_table(
    console: Console, failures: Sequence[TaskFailure]
) -> None:
  """Prints a Rich table of failed tasks when failures occurred."""
  if not failures:
    return

  table = Table(
      title=f"Task Failures ({len(failures)} failed)",
      box=box.ROUNDED,
      header_style="bold red",
  )
  table.add_column("Task Index", justify="right", style="bold")
  table.add_column("Input Name")
  table.add_column("Failed At (UTC)")
  table.add_column("Exit Code", justify="right")
  table.add_column("Details")

  for failure in failures:
    when_str = (
        failure.failed_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        if failure.failed_at
        else "Unknown"
    )
    exit_str = (
        str(failure.exit_code) if failure.exit_code is not None else "N/A"
    )
    table.add_row(
        str(failure.task_index if failure.task_index >= 0 else failure.task_id),
        failure.input_name or "-",
        when_str,
        exit_str,
        failure.reason,
    )

  console.print()
  console.print(table)


def print_log_analysis_tables(
    console: Console, log_stats: LogAnalysisStats
) -> None:
  """Prints Rich tables for preemptions, DB downloads, and tasks per VM."""
  overview = Table(
      title="Log Telemetry Overview (--with-logs)",
      box=box.ROUNDED,
      header_style="bold magenta",
  )
  overview.add_column("Metric", style="bold")
  overview.add_column("Value", justify="right")
  overview.add_row(
      "Total Preemption Interrupts",
      f"{log_stats.total_preemptions:,} "
      f"(Batch exit 50001: {log_stats.batch_preemptions:,}, "
      f"GCE audit: {log_stats.gce_preemptions:,})",
  )
  overview.add_row(
      "Initial MSA Database Downloads",
      f"{log_stats.db_download_count:,} (1 per worker VM)",
  )
  if log_stats.db_cache_reuse_count > 0:
    overview.add_row(
        "Database Cache Hits / Reuses",
        f"{log_stats.db_cache_reuse_count:,} tasks (skipped download via Hyperdisk cache)",
    )
  overview.add_row(
      "Total Worker VMs Observed in Logs",
      f"{log_stats.vm_count:,}",
  )
  if log_stats.steady_state_alignments_per_hour > 0:
    overview.add_row(
        "Estimated Steady-State Throughput",
        f"[bold green]{log_stats.steady_state_alignments_per_hour:,.1f} alignments/hr[/bold green] "
        f"({log_stats.vm_count} VMs, excluding initial DB download)",
    )
  console.print()
  console.print(overview)

  dist_table = Table(
      title=(
          "Database Download, Task Duration & Per-VM Task Completion Statistics"
      ),
      box=box.ROUNDED,
      header_style="bold blue",
  )
  dist_table.add_column("Metric", style="bold")
  dist_table.add_column("Count", justify="right")
  dist_table.add_column("Min", justify="right")
  dist_table.add_column("Max", justify="right")
  dist_table.add_column("Mean", justify="right")
  dist_table.add_column("Median", justify="right")

  db_dist = log_stats.db_download_duration_min
  if db_dist is not None:
    dist_table.add_row(
        "MSA Database Download Duration (min)",
        f"{db_dist.count:,}",
        f"{db_dist.min_val:.2f} min",
        f"{db_dist.max_val:.2f} min",
        f"{db_dist.mean_val:.2f} min",
        f"{db_dist.median_val:.2f} min",
    )
  else:
    dist_table.add_row(
        "MSA Database Download Duration (min)", "0", "N/A", "N/A", "N/A", "N/A"
    )

  task_dist = log_stats.msa_task_duration_min
  if task_dist is not None:
    dist_table.add_row(
        "MSA Task Duration (min)",
        f"{task_dist.count:,} tasks",
        f"{task_dist.min_val:.2f} min",
        f"{task_dist.max_val:.2f} min",
        f"{task_dist.mean_val:.2f} min",
        f"{task_dist.median_val:.2f} min",
    )
  else:
    dist_table.add_row(
        "MSA Task Duration (min)", "0 tasks", "N/A", "N/A", "N/A", "N/A"
    )

  vm_dist = log_stats.tasks_per_vm
  if vm_dist is not None:
    dist_table.add_row(
        "MSA Tasks Completed per VM",
        f"{vm_dist.count:,} VMs",
        f"{vm_dist.min_val:.0f}",
        f"{vm_dist.max_val:.0f}",
        f"{vm_dist.mean_val:.2f}",
        f"{vm_dist.median_val:.1f}",
    )
  else:
    dist_table.add_row(
        "MSA Tasks Completed per VM", "0 VMs", "N/A", "N/A", "N/A", "N/A"
    )

  console.print()
  console.print(dist_table)

  if log_stats.db_call_stats:
    sample_info = ""
    if (
        log_stats.sampled_task_count is not None
        and log_stats.total_succeeded_tasks
    ):
      sample_info = (
          f" (Sampled from {log_stats.sampled_task_count:,} of"
          f" {log_stats.total_succeeded_tasks:,} succeeded tasks)"
      )
    db_table = Table(
        title=(
            f"Database Search Durations (hmmsearch / jackhmmer / nhmmer){sample_info}"
        ),
        box=box.ROUNDED,
        header_style="bold cyan",
    )
    db_table.add_column("Database Type", style="bold")
    db_table.add_column("Tool", style="dim")
    db_table.add_column("Cores", justify="right")
    db_table.add_column("Count", justify="right")
    db_table.add_column("Min", justify="right")
    db_table.add_column("Max", justify="right")
    db_table.add_column("Mean", justify="right")
    db_table.add_column("Median", justify="right")
    db_table.add_column("Total Core-Time", justify="right")

    for s in log_stats.db_call_stats:
      shard_noun = "shard" if s.shard_count == 1 else "shards"
      count_str = f"{s.shard_count:,} {shard_noun}, {s.call_count:,} calls"
      db_table.add_row(
          s.db_type,
          s.tool,
          str(s.cores),
          count_str,
          f"{s.min_seconds:.2f}s",
          f"{s.max_seconds:.2f}s",
          f"{s.mean_seconds:.2f}s",
          f"{s.median_seconds:.2f}s",
          format_core_time(s.total_core_seconds),
      )

    console.print()
    console.print(db_table)


def generate_overview_markdown(
    throughput_stats: JobThroughputStats,
    sequence_stats: SequenceStats | None,
    log_stats: LogAnalysisStats | None,
) -> str:
  """Generates an agent-friendly, human-readable overview.md report without visual tables."""
  lines: list[str] = [
      "# AlphaFold 3 MSA Benchmark Overview",
      "",
      "## Job Information",
      f"- Job ID: {throughput_stats.job_id}",
      f"- Job Name: {throughput_stats.job_name}",
      f"- Job UID: {throughput_stats.job_uid}",
      f"- State: {throughput_stats.job_state}",
  ]

  if "/locations/" in throughput_stats.job_name:
    parts = throughput_stats.job_name.split("/")
    if len(parts) >= 4:
      lines.append(f"- Region: {parts[3]}")

  start_str = (
      throughput_stats.start_time.strftime("%Y-%m-%d %H:%M:%S UTC")
      if throughput_stats.start_time
      else "N/A"
  )
  end_str = (
      throughput_stats.end_time.strftime("%Y-%m-%d %H:%M:%S UTC")
      if throughput_stats.end_time
      else "N/A"
  )
  lines.extend([
      f"- Start Time: {start_str}",
      f"- End Time: {end_str}",
      (
          "- Total Duration:"
          f" {format_duration(throughput_stats.duration_seconds)}"
          f" ({throughput_stats.duration_seconds:.2f} seconds)"
      ),
      "",
  ])

  # Benchmark Sequences Summary
  lines.append("## Benchmark Sequences Summary")
  if sequence_stats is not None:
    lines.extend([
        f"- Source File: {sequence_stats.path.name}",
        f"- Total Benchmark Inputs: {sequence_stats.count:,}",
    ])
    if sequence_stats.types_by_index:
      type_counts: dict[str, int] = defaultdict(int)
      for t in sequence_stats.types_by_index.values():
        type_counts[t] += 1
      lines.append("- Input Composition:")
      for t_name, t_cnt in sorted(type_counts.items()):
        display_name = t_name.replace("_", " ").title()
        lines.append(f"  - {display_name}: {t_cnt:,} inputs")
    lines.extend([
        "- Sequence Length Statistics:",
        f"  - Average: {sequence_stats.avg_length:,.2f} residues",
        f"  - Minimum: {sequence_stats.min_length:,} residues",
        f"  - Maximum: {sequence_stats.max_length:,} residues",
    ])
  else:
    lines.append(
        "- Status: benchmark_sequences.jsonl not found locally; sequence summary"
        " skipped."
    )
  lines.append("")

  # Throughput Summary
  target_hr = throughput_stats.target_alignments_per_hour
  target_min = target_hr / 60.0
  pct_of_target = (
      (throughput_stats.alignments_per_hour / target_hr) * 100.0
      if target_hr > 0
      else 0.0
  )
  delta_hr = throughput_stats.alignments_per_hour - target_hr
  lines.extend([
      "## Throughput Summary",
      (
          "- Tasks Succeeded:"
          f" {throughput_stats.tasks_succeeded:,} /"
          f" {throughput_stats.total_tasks:,}"
      ),
      f"- Tasks Failed: {throughput_stats.tasks_failed:,}",
      (
          "- Total Job Duration:"
          f" {format_duration(throughput_stats.duration_seconds)}"
      ),
      (
          "- Average Throughput:"
          f" {throughput_stats.avg_tasks_per_min:,.2f} tasks/min"
          f" ({throughput_stats.alignments_per_hour:,.1f} tasks/hr)"
      ),
      (
          f"- Design Target: {target_hr:,.0f} alignments/hr"
          f" ({target_min:.2f} tasks/min)"
      ),
      (
          "- Target Comparison:"
          f" {throughput_stats.alignments_per_hour:,.1f} alignments/hr"
          f" ({pct_of_target:.1f}% of target, {delta_hr:+,.1f}/hr)"
      ),
      "",
  ])

  # Throughput & Duration Breakdown by Entity Type
  if throughput_stats.entity_type_stats:
    lines.append("## Throughput & Duration Breakdown by Entity Type")
    for et in throughput_stats.entity_type_stats:
      len_unit = (
          "nt"
          if et.entity_type == "rna_monomer"
          else ("residues" if et.entity_type == "rnp_complex" else "aa")
      )
      len_str = (
          f"{et.avg_sequence_length:,.0f} {len_unit}"
          if et.avg_sequence_length is not None
          else "N/A"
      )
      mean_dur = (
          f"{et.duration_stats_min.mean_val:.2f} min"
          if et.duration_stats_min
          else "N/A"
      )
      med_dur = (
          f"{et.duration_stats_min.median_val:.2f} min"
          if et.duration_stats_min
          else "N/A"
      )
      min_dur = (
          f"{et.duration_stats_min.min_val:.2f} min"
          if et.duration_stats_min
          else "N/A"
      )
      max_dur = (
          f"{et.duration_stats_min.max_val:.2f} min"
          if et.duration_stats_min
          else "N/A"
      )
      lines.extend([
          f"### {et.display_name}",
          f"- Entity Type Key: {et.entity_type}",
          f"- Tasks: {et.succeeded_tasks:,} / {et.total_tasks:,}",
          f"- Average Length: {len_str}",
          f"- Mean Duration: {mean_dur}",
          f"- Median Duration: {med_dur}",
          f"- Min Duration: {min_dur}",
          f"- Max Duration: {max_dur}",
          f"- Alignments / VM Hour: {et.alignments_per_vm_hour:,.1f} / VM hr",
          "",
      ])

    if len(throughput_stats.entity_type_stats) > 1:
      overall_avg_len = (
          f"{throughput_stats.avg_residues_aligned_per_min / throughput_stats.avg_tasks_per_min:,.0f}"
          " residues"
          if (
              throughput_stats.avg_residues_aligned_per_min
              and throughput_stats.avg_tasks_per_min > 0
          )
          else "N/A"
      )
      overall_mean = (
          f"{throughput_stats.overall_mean_duration_minutes:.2f} min"
          if throughput_stats.overall_mean_duration_minutes > 0
          else "N/A"
      )
      overall_med = (
          f"{throughput_stats.overall_median_duration_minutes:.2f} min"
          if throughput_stats.overall_median_duration_minutes > 0
          else "N/A"
      )
      overall_vm_hr = (
          f"{throughput_stats.overall_alignments_per_vm_hour:,.1f} / VM hr"
          if throughput_stats.overall_alignments_per_vm_hour > 0
          else "N/A"
      )
      lines.extend([
          "### Overall / Combined",
          (
              "- Tasks:"
              f" {throughput_stats.tasks_succeeded:,} /"
              f" {throughput_stats.total_tasks:,}"
          ),
          f"- Average Length: {overall_avg_len}",
          f"- Mean Duration: {overall_mean}",
          f"- Median Duration: {overall_med}",
          f"- Alignments / VM Hour: {overall_vm_hr}",
          "",
      ])

  # Task Failures
  lines.append("## Task Failures")
  if throughput_stats.failures:
    lines.append(f"- Total Failed Tasks: {len(throughput_stats.failures):,}")
    lines.append("- Failures List:")
    for failure in throughput_stats.failures:
      when_str = (
          failure.failed_at.strftime("%Y-%m-%d %H:%M:%S UTC")
          if failure.failed_at
          else "Unknown"
      )
      exit_str = (
          str(failure.exit_code) if failure.exit_code is not None else "N/A"
      )
      lines.extend([
          f"  - Task Index: {failure.task_index}",
          f"    - Task ID: {failure.task_id}",
          f"    - Input Name: {failure.input_name or '-'}",
          f"    - Failed At: {when_str}",
          f"    - Exit Code: {exit_str}",
          f"    - Reason: {failure.reason}",
      ])
  else:
    lines.append("- Status: No failed tasks detected across the job.")
  lines.append("")

  # Log Telemetry Overview
  if log_stats is not None:
    lines.extend([
        "## Log Telemetry & Infrastructure Overview",
        f"- Total Worker VMs Observed: {log_stats.vm_count:,}",
        (
            "- Total Preemption Interrupts:"
            f" {log_stats.total_preemptions:,} (Batch exit code 50001:"
            f" {log_stats.batch_preemptions:,}, GCE audit:"
            f" {log_stats.gce_preemptions:,})"
        ),
        (
            "- Number of Initial MSA Database Downloads:"
            f" {log_stats.db_download_count:,} (1 per worker VM)"
        ),
    ])
    if log_stats.db_cache_reuse_count > 0:
      lines.append(
          "- Database Cache Reuses (Download Skipped):"
          f" {log_stats.db_cache_reuse_count:,} tasks (Hyperdisk cache)"
      )
    if log_stats.steady_state_alignments_per_hour > 0:
      lines.append(
          "- Estimated Steady-State Throughput:"
          f" {log_stats.steady_state_alignments_per_hour:,.1f} alignments/hr"
          f" ({log_stats.vm_count} VMs, excluding initial DB download)"
      )
    lines.extend([
        "",
        "### Distribution Statistics",
    ])

    if log_stats.db_download_duration_min:
      db_dist = log_stats.db_download_duration_min
      lines.extend([
          "#### MSA Database Download Duration",
          f"- Count: {db_dist.count:,}",
          f"- Mean: {db_dist.mean_val:.2f} min",
          f"- Median: {db_dist.median_val:.2f} min",
          f"- Min: {db_dist.min_val:.2f} min",
          f"- Max: {db_dist.max_val:.2f} min",
          "",
      ])

    if log_stats.msa_task_duration_min:
      task_dist = log_stats.msa_task_duration_min
      lines.extend([
          "#### MSA Task Duration",
          f"- Count: {task_dist.count:,} tasks",
          f"- Mean: {task_dist.mean_val:.2f} min",
          f"- Median: {task_dist.median_val:.2f} min",
          f"- Min: {task_dist.min_val:.2f} min",
          f"- Max: {task_dist.max_val:.2f} min",
          "",
      ])

    if log_stats.tasks_per_vm:
      vm_dist = log_stats.tasks_per_vm
      lines.extend([
          "#### MSA Tasks Completed per Worker VM",
          f"- Count: {vm_dist.count:,} VMs",
          f"- Mean: {vm_dist.mean_val:.2f} tasks",
          f"- Median: {vm_dist.median_val:.1f} tasks",
          f"- Min: {vm_dist.min_val:.0f} tasks",
          f"- Max: {vm_dist.max_val:.0f} tasks",
          "",
      ])

    if log_stats.db_call_stats:
      sample_info = ""
      if (
          log_stats.sampled_task_count is not None
          and log_stats.total_succeeded_tasks
      ):
        sample_info = (
            f" (Sampled from {log_stats.sampled_task_count:,} of"
            f" {log_stats.total_succeeded_tasks:,} succeeded tasks)"
        )
      lines.append(
          "### Database Search Durations (hmmsearch / jackhmmer /"
          f" nhmmer){sample_info}"
      )
      for s in log_stats.db_call_stats:
        shard_noun = "shard" if s.shard_count == 1 else "shards"
        lines.extend([
            f"#### {s.db_type}",
            f"- Tool: {s.tool}",
            f"- CPU Cores: {s.cores}",
            f"- Shards: {s.shard_count:,} {shard_noun}",
            f"- Total Calls: {s.call_count:,}",
            f"- Mean Duration: {s.mean_seconds:.2f}s",
            f"- Median Duration: {s.median_seconds:.2f}s",
            f"- Min Duration: {s.min_seconds:.2f}s",
            f"- Max Duration: {s.max_seconds:.2f}s",
            f"- Total Core-Time: {format_core_time(s.total_core_seconds)}",
            "",
        ])

  return "\n".join(lines).rstrip() + "\n"


def export_benchmark_data(
    export_dir: pathlib.Path,
    throughput_stats: JobThroughputStats,
    sequence_stats: SequenceStats | None,
    log_stats: LogAnalysisStats | None,
) -> list[pathlib.Path]:
  """Exports all collected benchmark data as TSV files and an overview.md report into a subfolder."""
  export_dir.mkdir(parents=True, exist_ok=True)
  exported_files: list[pathlib.Path] = []

  # 1. overview.md (human-readable and agent-friendly structured report)
  overview_path = export_dir / "overview.md"
  overview_text = generate_overview_markdown(
      throughput_stats=throughput_stats,
      sequence_stats=sequence_stats,
      log_stats=log_stats,
  )
  overview_path.write_text(overview_text, encoding="utf-8")
  exported_files.append(overview_path)

  # 2. job_summary.tsv
  summary_path = export_dir / "job_summary.tsv"
  with summary_path.open("w", encoding="utf-8") as f:
    f.write("metric\tvalue\n")
    f.write(f"job_id\t{throughput_stats.job_id}\n")
    f.write(f"job_name\t{throughput_stats.job_name}\n")
    f.write(f"job_uid\t{throughput_stats.job_uid}\n")
    f.write(f"job_state\t{throughput_stats.job_state}\n")
    f.write(f"start_time_utc\t{throughput_stats.start_time or ''}\n")
    f.write(f"end_time_utc\t{throughput_stats.end_time or ''}\n")
    f.write(f"duration_seconds\t{throughput_stats.duration_seconds:.2f}\n")
    f.write(
        f"duration_minutes\t{throughput_stats.duration_seconds / 60.0:.2f}\n"
    )
    f.write(f"total_tasks\t{throughput_stats.total_tasks}\n")
    f.write(f"tasks_succeeded\t{throughput_stats.tasks_succeeded}\n")
    f.write(f"tasks_failed\t{throughput_stats.tasks_failed}\n")
    f.write(f"avg_tasks_per_min\t{throughput_stats.avg_tasks_per_min:.4f}\n")
    residues_pm = (
        f"{throughput_stats.avg_residues_aligned_per_min:.2f}"
        if throughput_stats.avg_residues_aligned_per_min is not None
        else ""
    )
    f.write(f"avg_residues_aligned_per_min\t{residues_pm}\n")
    f.write(
        f"alignments_per_hour\t{throughput_stats.alignments_per_hour:.2f}\n"
    )
    f.write(
        "target_alignments_per_hour\t"
        f"{throughput_stats.target_alignments_per_hour:.2f}\n"
    )
    pct_target = (
        (
            throughput_stats.alignments_per_hour
            / throughput_stats.target_alignments_per_hour
        )
        * 100.0
        if throughput_stats.target_alignments_per_hour > 0
        else 0.0
    )
    f.write(f"pct_of_design_target\t{pct_target:.1f}%\n")
    f.write(f"batch_preemptions\t{throughput_stats.batch_preemptions}\n")
  exported_files.append(summary_path)

  # 2. tasks.tsv
  tasks_path = export_dir / "tasks.tsv"
  with tasks_path.open("w", encoding="utf-8") as f:
    headers = [
        "task_index",
        "task_id",
        "input_name",
        "entity_type",
        "sequence_length",
        "state",
        "start_time_utc",
        "end_time_utc",
        "duration_seconds",
        "duration_minutes",
        "msa_duration_seconds",
        "msa_duration_minutes",
        "exit_code",
        "reason",
    ]
    f.write("\t".join(headers) + "\n")
    for tr in throughput_stats.tasks_records:
      dur_s = (
          f"{tr.duration_seconds:.2f}" if tr.duration_seconds is not None else ""
      )
      dur_m = (
          f"{tr.duration_seconds / 60.0:.2f}"
          if tr.duration_seconds is not None
          else ""
      )
      msa_dur_s = (
          log_stats.task_msa_durations_sec.get(tr.task_index)
          if log_stats
          else None
      )
      msa_s_str = f"{msa_dur_s:.2f}" if msa_dur_s is not None else ""
      msa_m_str = (
          f"{msa_dur_s / 60.0:.2f}" if msa_dur_s is not None else ""
      )
      row = [
          str(tr.task_index),
          tr.task_id,
          tr.input_name or "",
          tr.entity_type,
          str(tr.sequence_length or "")
          if tr.sequence_length is not None
          else "",
          tr.state,
          tr.start_time.isoformat() if tr.start_time else "",
          tr.end_time.isoformat() if tr.end_time else "",
          dur_s,
          dur_m,
          msa_s_str,
          msa_m_str,
          str(tr.exit_code) if tr.exit_code is not None else "",
          tr.reason.replace("\t", " ").replace("\n", " "),
      ]
      f.write("\t".join(row) + "\n")
  exported_files.append(tasks_path)

  # 2b. throughput_by_entity_type.tsv
  if throughput_stats.entity_type_stats:
    entity_path = export_dir / "throughput_by_entity_type.tsv"
    with entity_path.open("w", encoding="utf-8") as f:
      f.write(
          "entity_type\tdisplay_name\ttotal_tasks\tsucceeded_tasks\tfailed_tasks\t"
          "avg_sequence_length\tmean_duration_minutes\tmedian_duration_minutes\t"
          "min_duration_minutes\tmax_duration_minutes\t"
          "alignments_per_vm_hour\tresidues_aligned_per_min\n"
      )
      for et in throughput_stats.entity_type_stats:
        mean_d = (
            f"{et.duration_stats_min.mean_val:.2f}"
            if et.duration_stats_min
            else ""
        )
        med_d = (
            f"{et.duration_stats_min.median_val:.2f}"
            if et.duration_stats_min
            else ""
        )
        min_d = (
            f"{et.duration_stats_min.min_val:.2f}"
            if et.duration_stats_min
            else ""
        )
        max_d = (
            f"{et.duration_stats_min.max_val:.2f}"
            if et.duration_stats_min
            else ""
        )
        avg_l = (
            f"{et.avg_sequence_length:.1f}"
            if et.avg_sequence_length is not None
            else ""
        )
        res_m = (
            f"{et.avg_residues_per_min:.2f}"
            if et.avg_residues_per_min is not None
            else ""
        )
        vm_hr = f"{et.alignments_per_vm_hour:.2f}"
        f.write(
            f"{et.entity_type}\t{et.display_name}\t{et.total_tasks}\t"
            f"{et.succeeded_tasks}\t{et.failed_tasks}\t{avg_l}\t"
            f"{mean_d}\t{med_d}\t{min_d}\t{max_d}\t"
            f"{vm_hr}\t{res_m}\n"
        )
    exported_files.append(entity_path)

  # 3. benchmark_sequences.tsv
  if sequence_stats is not None:
    seq_path = export_dir / "benchmark_sequences.tsv"
    with seq_path.open("w", encoding="utf-8") as f:
      f.write("task_index\tinput_name\tentity_type\tlength_residues\n")
      for idx, name in sorted(sequence_stats.names_by_index.items()):
        length = sequence_stats.lengths_by_index.get(idx, "")
        t_type = sequence_stats.types_by_index.get(idx, "unknown")
        f.write(f"{idx}\t{name}\t{t_type}\t{length}\n")
    exported_files.append(seq_path)

  # 4. Log telemetry files
  if log_stats is not None:
    # 4a. database_search_summary.tsv
    if log_stats.db_call_stats:
      db_summary_path = export_dir / "database_search_summary.tsv"
      with db_summary_path.open("w", encoding="utf-8") as f:
        f.write(
            "database_type\ttool\tcores\tshard_count\tcall_count\t"
            "min_seconds\tmax_seconds\tmean_seconds\tmedian_seconds\ttotal_core_seconds\n"
        )
        for s in log_stats.db_call_stats:
          f.write(
              f"{s.db_type}\t{s.tool}\t{s.cores}\t{s.shard_count}\t{s.call_count}\t"
              f"{s.min_seconds:.4f}\t{s.max_seconds:.4f}\t"
              f"{s.mean_seconds:.4f}\t{s.median_seconds:.4f}\t"
              f"{s.total_core_seconds:.4f}\n"
          )
      exported_files.append(db_summary_path)

    # 4b. database_calls_raw.tsv
    if log_stats.raw_db_calls:
      db_raw_path = export_dir / "database_calls_raw.tsv"
      with db_raw_path.open("w", encoding="utf-8") as f:
        f.write(
            "task_id\tvm_id\ttimestamp_utc\ttool\tdatabase_type\t"
            "database_target\tshard_index\tshard_total\tduration_seconds\t"
            "cores\ttotal_core_seconds\n"
        )
        for c in log_stats.raw_db_calls:
          ts_str = c.timestamp.isoformat() if c.timestamp else ""
          shard_idx_str = (
              str(c.shard_index) if c.shard_index is not None else ""
          )
          f.write(
              f"{c.task_id}\t{c.vm_id}\t{ts_str}\t{c.tool}\t{c.db_type}\t"
              f"{c.database_target}\t{shard_idx_str}\t{c.shard_total}\t"
              f"{c.duration_seconds:.4f}\t{c.cores}\t{c.core_seconds:.4f}\n"
          )
      exported_files.append(db_raw_path)

    # 4c. vm_summary.tsv
    vm_path = export_dir / "vm_summary.tsv"
    with vm_path.open("w", encoding="utf-8") as f:
      f.write("vm_id\ttasks_completed\tdb_download_duration_min\n")
      all_vms = sorted(
          set(log_stats.vm_task_counts.keys())
          | set(log_stats.vm_download_durations_min.keys())
      )
      for vm in all_vms:
        tc = log_stats.vm_task_counts.get(vm, 0)
        dl = log_stats.vm_download_durations_min.get(vm, None)
        dl_str = f"{dl:.4f}" if dl is not None else ""
        f.write(f"{vm}\t{tc}\t{dl_str}\n")
    exported_files.append(vm_path)

    # 4d. telemetry_summary.tsv
    telemetry_path = export_dir / "telemetry_summary.tsv"
    with telemetry_path.open("w", encoding="utf-8") as f:
      f.write("metric\tvalue\n")
      f.write(f"total_preemptions\t{log_stats.total_preemptions}\n")
      f.write(f"batch_preemptions\t{log_stats.batch_preemptions}\n")
      f.write(f"gce_preemptions\t{log_stats.gce_preemptions}\n")
      f.write(f"db_download_count\t{log_stats.db_download_count}\n")
      f.write(f"db_cache_reuse_count\t{log_stats.db_cache_reuse_count}\n")
      f.write(f"worker_vm_count\t{log_stats.vm_count}\n")
      if log_stats.steady_state_alignments_per_hour > 0:
        f.write(
            "steady_state_alignments_per_hour\t"
            f"{log_stats.steady_state_alignments_per_hour:.2f}\n"
        )
      if log_stats.sampled_task_count is not None:
        f.write(f"sampled_task_count\t{log_stats.sampled_task_count}\n")
      if log_stats.total_succeeded_tasks is not None:
        f.write(f"total_succeeded_tasks\t{log_stats.total_succeeded_tasks}\n")
      if log_stats.db_download_duration_min:
        f.write(
            "db_download_min_minutes\t"
            f"{log_stats.db_download_duration_min.min_val:.4f}\n"
        )
        f.write(
            "db_download_max_minutes\t"
            f"{log_stats.db_download_duration_min.max_val:.4f}\n"
        )
        f.write(
            "db_download_mean_minutes\t"
            f"{log_stats.db_download_duration_min.mean_val:.4f}\n"
        )
        f.write(
            "db_download_median_minutes\t"
            f"{log_stats.db_download_duration_min.median_val:.4f}\n"
        )
      if log_stats.msa_task_duration_min:
        f.write(
            "msa_task_min_minutes\t"
            f"{log_stats.msa_task_duration_min.min_val:.4f}\n"
        )
        f.write(
            "msa_task_max_minutes\t"
            f"{log_stats.msa_task_duration_min.max_val:.4f}\n"
        )
        f.write(
            "msa_task_mean_minutes\t"
            f"{log_stats.msa_task_duration_min.mean_val:.4f}\n"
        )
        f.write(
            "msa_task_median_minutes\t"
            f"{log_stats.msa_task_duration_min.median_val:.4f}\n"
        )
    exported_files.append(telemetry_path)

  return exported_files


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
  """Parses command-line arguments for the benchmark profiler."""
  parser = argparse.ArgumentParser(
      description=(
          "Profile and benchmark an AlphaFold 3 MSA Google Cloud Batch array"
          " job."
      ),
      formatter_class=argparse.ArgumentDefaultsHelpFormatter,
  )
  parser.add_argument(
      "--project-id",
      required=True,
      help="Google Cloud project ID containing the Batch job.",
  )
  parser.add_argument(
      "--job-id",
      required=True,
      help="Cloud Batch array job ID (or full job resource name).",
  )
  parser.add_argument(
      "--region",
      default=None,
      help=(
          "Optional Google Cloud region (e.g. us-central1). If omitted, "
          "automatically inferred across all regions in the project."
      ),
  )
  parser.add_argument(
      "--with-logs",
      action="store_true",
      help=(
          "Download and analyze Cloud Logging entries to report preemption"
          " interrupts, MSA database download durations, tasks per VM, and"
          " hmmsearch / jackhmmer search durations per database."
      ),
  )
  parser.add_argument(
      "--export",
      dest="export_dir",
      nargs="?",
      const="",
      default=None,
      metavar="DIR",
      help=(
          "Export all collected benchmark data (overview.md report and TSV"
          " files) into a subfolder. If DIR is omitted, automatically"
          " generates a unique timestamped subfolder"
          " (e.g. benchmark_export_YYYYMMDD_HHMMSS)."
      ),
  )
  parser.add_argument(
      "--sample-tasks",
      type=int,
      default=10,
      help=(
          "Number of succeeded tasks to sample for tool call duration stats"
          " (0 for all tasks). Defaults to 10."
      ),
  )
  parser.add_argument(
      "--max-log-entries",
      type=int,
      default=None,
      help=(
          "Optional maximum number of Cloud Logging entries to process "
          "(processes all relevant entries by default)."
      ),
  )
  parser.add_argument(
      "--sequences-file",
      default=None,
      help=(
          "Optional path override to benchmark_sequences.jsonl (auto-detected"
          " by default)."
      ),
  )
  return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
  """Main entrypoint for the AlphaFold 3 MSA benchmark profiling script."""
  args = parse_args(argv)
  console = Console()

  region_display = args.region or "auto-detect"
  console.print(
      f"[bold cyan]==> Profiling AlphaFold 3 MSA Batch Job:[/bold cyan] "
      f"[bold]{args.job_id}[/bold] "
      f"(project=[cyan]{args.project_id}[/cyan], "
      f"region=[cyan]{region_display}[/cyan])"
  )

  console.print("[dim]-> Checking for benchmark_sequences.jsonl...[/dim]")
  seq_path = resolve_sequences_path(args.sequences_file)
  seq_stats = load_sequence_stats(seq_path) if seq_path is not None else None
  if seq_stats is not None:
    console.print(
        f"[green]✓[/green] Loaded {seq_stats.count:,} sequences from "
        f"[bold]{seq_stats.path}[/bold]."
    )
    print_sequence_table(console, seq_stats)
  else:
    console.print(
        "[yellow]![/yellow] benchmark_sequences.jsonl not found locally; "
        "skipping sequence length summary."
    )

  console.print(
      "\n[dim]-> Authenticating with Google Cloud "
      f"(project: {args.project_id})...[/dim]"
  )
  credentials = get_credentials(args.project_id)
  console.print("[green]✓[/green] Google Cloud credentials resolved.")

  if args.region:
    console.print(
        f"[dim]-> Fetching Cloud Batch job '{args.job_id}' and task records "
        f"in '{args.region}'...[/dim]"
    )
  else:
    console.print(
        f"[dim]-> Discovering region for job '{args.job_id}' across "
        f"project '{args.project_id}' and fetching task records...[/dim]"
    )
  job, tasks, resolved_region = fetch_job_and_tasks(
      project_id=args.project_id,
      job_id=args.job_id,
      credentials=credentials,
      region=args.region,
  )
  console.print(
      f"[green]✓[/green] Retrieved job [bold]{job.name.split('/')[-1]}[/bold] "
      f"in region [bold]{resolved_region}[/bold] (uid={job.uid}) and "
      f"[bold]{len(tasks):,}[/bold] task records."
  )

  console.print(
      "[dim]-> Computing throughput metrics and checking for task "
      "failures...[/dim]"
  )
  throughput_stats = compute_throughput_and_failures(
      job=job,
      tasks=tasks,
      sequence_stats=seq_stats,
  )
  print_throughput_table(console, throughput_stats, seq_stats)
  print_entity_type_throughput_table(console, throughput_stats)

  if throughput_stats.failures:
    console.print(
        f"\n[bold red]! Found {len(throughput_stats.failures):,} failed "
        "task(s):[/bold red]"
    )
    print_failures_table(console, throughput_stats.failures)
  else:
    console.print(
        "\n[green]✓[/green] No failed tasks detected across the job."
    )

  log_stats: LogAnalysisStats | None = None
  if args.with_logs or args.export_dir is not None:
    console.print(
        "\n[dim]-> Querying Cloud Logging (batch_task_logs, tool durations &"
        f" GCE preemptions) for job UID '{throughput_stats.job_uid}'...[/dim]"
    )
    log_stats = analyze_job_logs(
        project_id=args.project_id,
        job_stats=throughput_stats,
        credentials=credentials,
        sample_tasks=args.sample_tasks,
        max_log_entries=args.max_log_entries,
        console=console,
        job=job,
    )
    sample_desc = ""
    if log_stats.sampled_task_count is not None:
      sample_desc = f" [sampled {log_stats.sampled_task_count} tasks]"
    console.print(
        f"[green]✓[/green] Analyzed logs across "
        f"[bold]{log_stats.vm_count:,}[/bold] worker VMs "
        f"({log_stats.db_download_count:,} DB downloads, "
        f"{sum(s.call_count for s in log_stats.db_call_stats):,} database tool calls{sample_desc}, "
        f"{log_stats.total_preemptions:,} preemptions)."
    )
    print_log_analysis_tables(console, log_stats)

  if args.export_dir is not None:
    if not args.export_dir:
      now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
      export_path = pathlib.Path(f"benchmark_export_{now_str}")
    else:
      export_path = pathlib.Path(args.export_dir)

    console.print(
        f"\n[dim]-> Exporting benchmark data to: [bold]{export_path}[/bold]...[/dim]"
    )
    exported = export_benchmark_data(
        export_dir=export_path,
        throughput_stats=throughput_stats,
        sequence_stats=seq_stats,
        log_stats=log_stats,
    )
    console.print(
        f"[green]✓[/green] Successfully exported [bold]{len(exported)}[/bold]"
        f" benchmark file(s) (including overview.md) to: [bold]{export_path.resolve()}[/bold]"
    )
    for p in exported:
      console.print(f"  [dim]• {p.name}[/dim]")

  return 0


if __name__ == "__main__":
  sys.exit(main())
