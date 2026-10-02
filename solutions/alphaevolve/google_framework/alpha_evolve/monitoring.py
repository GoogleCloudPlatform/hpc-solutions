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

"""Monitoring and post-processing utilities for AlphaEvolve experiments.

This module provides tools to monitor live experiments, retrieve generated
and evaluated programs, compute comprehensive metrics and statistics, analyze
program lineage and evolution trees, and export artifacts to disk.
"""

import csv
import io
import json
import logging
import math
import os
import shutil
import statistics
import subprocess
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .client import AlphaEvolveClient
from .models import AlphaEvolveExperimentState
from .utils import fix_multi_files_program

logger = logging.getLogger(__name__)


def get_short_id(resource_name: str, resource_type: str = "") -> str:
    """Extracts the short ID from a full Google Cloud resource name.

    Args:
      resource_name: Full resource name (e.g., 'projects/.../alphaEvolvePrograms/prog_1').
      resource_type: Optional prefix string to strip (e.g., 'alphaEvolvePrograms/').

    Returns:
      The short ID identifier.
    """
    if not resource_name:
        return ""
    if resource_type and f"{resource_type}/" in resource_name:
        return resource_name.split(f"{resource_type}/")[-1].split("/")[0]
    if "alphaEvolvePrograms/" in resource_name:
        return resource_name.split("alphaEvolvePrograms/")[-1].split("/")[0]
    if "alphaEvolveExperiments/" in resource_name:
        return resource_name.split("alphaEvolveExperiments/")[-1].split("/")[0]
    return resource_name.split("/")[-1]


def extract_program_scores(prog: Dict[str, Any]) -> Dict[str, float]:
    """Extracts all metric scores from a program dictionary.

    Args:
      prog: The program dictionary as returned by the Discovery Engine API.

    Returns:
      A dictionary mapping metric names to float scores.
    """
    scores_map = {}
    try:
        scores_list = (
            prog.get("evaluation", {}).get("scores", {}).get("scores", [])
        )
        for s in scores_list:
            metric_name = s.get("metric")
            score_val = s.get("score")
            if metric_name and score_val is not None:
                try:
                    scores_map[metric_name] = float(score_val)
                except (ValueError, TypeError):
                    pass
    except (AttributeError, TypeError):
        pass
    return scores_map


def extract_program_insights(prog: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Extracts evaluation insights from a program dictionary.

    Args:
      prog: The program dictionary as returned by the Discovery Engine API.

    Returns:
      A list of (label, text) tuples representing evaluation insights.
    """
    insights_list = []
    try:
        raw_insights = (
            prog.get("evaluation", {}).get("insights", {}).get("insights", [])
        )
        for i in raw_insights:
            label = i.get("label", "GENERAL")
            text = i.get("text", "")
            if text:
                insights_list.append((label, text))
    except (AttributeError, TypeError):
        pass
    return insights_list


def load_config_from_bucket(bucket_name: str) -> dict:
    """Loads environment configuration from GCS bucket config/variables-infra.env without mutating os.environ."""
    blob_name = "config/variables-infra.env"
    logger.info(f"Loading configuration from GCS: gs://{bucket_name}/{blob_name}")
    content = None
    storage_err = None

    # 1. Attempt native google-cloud-storage Python SDK
    try:
        from google.cloud import storage
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        content = blob.download_as_text()
    except Exception as e:
        storage_err = e

    # 2. Fallback to gcloud CLI only if available in the environment
    if content is None:
        if shutil.which("gcloud"):
            try:
                content = subprocess.check_output(
                    ["gcloud", "storage", "cat", f"gs://{bucket_name}/{blob_name}"],
                    text=True,
                    stderr=subprocess.PIPE
                )
            except Exception as err:
                logger.warning("Failed to load config via gcloud CLI from gs://%s/%s: %s", bucket_name, blob_name, err)
        else:
            logger.debug("gcloud CLI not found in PATH; skipping CLI fallback.")

    if content is None:
        logger.warning(
            "Could not load configuration from GCS bucket '%s' (%s). Using environment defaults.",
            bucket_name,
            storage_err or "not found",
        )
        return {}

    config_vars = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" in line:
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            config_vars[key] = val

    logger.info(f"Successfully loaded {len(config_vars)} variables from GCS bucket '{bucket_name}'.")
    return config_vars


class AlphaEvolveMonitor:
    """Monitor and post-processor for AlphaEvolve experiments.

    Provides functions for polling experiment state, listing programs with
    pagination, calculating metric distributions, constructing lineage graphs,
    and exporting code and data artifacts locally.
    """

    def __init__(
        self,
        experiment_name: Optional[str] = None,
        session_name: Optional[str] = None,
        bucket_name: Optional[str] = None,
        project_id: Optional[str] = None,
    ):
        """Initializes the AlphaEvolveMonitor.

        Args:
          experiment_name: Full resource name of the target experiment.
          session_name: Full resource name of the target session.
          bucket_name: Optional GCS bucket name to load configuration from.
          project_id: Optional GCP project ID override.
        """
        config_vars: Dict[str, str] = {}
        b_name = bucket_name or os.environ.get("_CLOUD_BUCKET_NAME")
        if b_name:
            config_vars = load_config_from_bucket(b_name)

        pid = project_id or config_vars.get("_PROJECT_ID") or os.environ.get("_PROJECT_ID")
        if not pid or pid == "gcp-project-id":
            logger.warning(
                "Project ID not found or set to default in _PROJECT_ID. "
                "Client initialization may fail if not running in authenticated GCP context."
            )
        location = config_vars.get("_LOCATION") or os.environ.get("_LOCATION", "global")
        collection = config_vars.get("_COLLECTION") or os.environ.get("_COLLECTION", "default_collection")
        engine_name = config_vars.get("_ENGINE") or os.environ.get("_ENGINE", "alpha-evolve-infra-experiment-engine")
        assistant = config_vars.get("_ASSISTANT") or os.environ.get("_ASSISTANT", "default_assistant")
        base_url = config_vars.get("_BASE_URL") or os.environ.get("_BASE_URL", "discoveryengine.googleapis.com")

        self.client = AlphaEvolveClient(
            project_id=pid or "",
            location=location,
            collection=collection,
            engine=engine_name,
            assistant=assistant,
            base_url=base_url,
        )

        self.project_id = pid or getattr(self.client, "project_id", "")
        self.experiment_name = experiment_name or config_vars.get("_USER_EXPERIMENT_NAME") or os.environ.get("_USER_EXPERIMENT_NAME")
        self.session_name = session_name or config_vars.get("_SESSION_NAME") or os.environ.get("_SESSION_NAME")

    def _resolve_experiment_name(
        self, experiment_name: Optional[str] = None
    ) -> str:
        """Resolves the experiment name to use."""
        exp_name = experiment_name or self.experiment_name
        if not exp_name:
            raise ValueError(
                "Experiment name not provided and not set in environment (_USER_EXPERIMENT_NAME)."
            )
        return exp_name

    def list_all_experiments(self, page_size: int = 50) -> List[Dict[str, Any]]:
        """Lists all experiments across all accessible sessions."""
        return self.client.list_all_experiments(page_size=page_size)

    def get_experiment_status(
        self,
        experiment_name: Optional[str] = None,
        max_programs: Optional[int] = None,
        programs: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Retrieves and summarizes the status and configuration of an experiment.

        Args:
          experiment_name: Full resource name of the experiment.
          max_programs: Optional maximum number of candidate programs to retrieve
            for trajectory calculation (None for all).
          programs: Optional pre-fetched list of candidate programs to avoid duplicate API calls.

        Returns:
          A structured dictionary with experiment status, stats, and configuration.
        """
        exp_name = self._resolve_experiment_name(experiment_name)
        logger.info(f"Fetching status for experiment: {exp_name}")
        raw_exp = self.client.get_alpha_evolve_experiment(exp_name)
        if not raw_exp:
            raise RuntimeError(f"Failed to retrieve experiment: {exp_name}")

        state = raw_exp.get("state", AlphaEvolveExperimentState.STATE_UNSPECIFIED.name)
        create_time = raw_exp.get("createTime")
        stats = raw_exp.get("stats", {})
        config = raw_exp.get("config", {})

        candidates_count = stats.get("candidatesCount", stats.get("candidates_count", 0))
        evaluated_count = stats.get("evaluatedCandidatesCount", stats.get("evaluated_candidates_count", 0))
        input_token_count = stats.get("inputTokenCount", stats.get("input_token_count", 0))
        output_token_count = stats.get("outputTokenCount", stats.get("output_token_count", 0))

        best_program_id = None
        best_score = None
        main_metric = None
        best_scores_map = {}

        try:
            initial_prog_name = raw_exp.get("initialAlphaEvolveProgram")
            if initial_prog_name:
                try:
                    initial_prog = self.client.get_alpha_evolve_program(initial_prog_name)
                    if initial_prog:
                        scores_data = (
                            initial_prog.get("evaluation", {})
                            .get("scores", {})
                            .get("scores", [])
                        )
                        metrics_list = [s.get("metric") for s in scores_data if s.get("metric")]
                        if metrics_list:
                            main_metric = metrics_list[0]
                except Exception as e:
                    logger.warning("Could not extract main metric from initial program: %s", e)

            if programs is None:
                programs = self.list_programs(
                    experiment_name=exp_name, page_size=100, max_programs=max_programs
                )

            if not main_metric:
                for p in programs:
                    p_scores = extract_program_scores(p)
                    if p_scores:
                        main_metric = list(p_scores.keys())[0]
                        break

            prog_evals = []
            if main_metric:
                for p in programs:
                    p_scores = extract_program_scores(p)
                    if p_scores and main_metric in p_scores:
                        val = p_scores[main_metric]
                        if val is not None and isinstance(val, (int, float)) and not math.isnan(val) and not math.isinf(val):
                            c_time = p.get("createTime", p.get("create_time", ""))
                            u_time = p.get("updateTime", p.get("update_time", c_time))
                            pid = get_short_id(p.get("name", ""), "alphaEvolvePrograms")
                            f_val = float(val)
                            prog_evals.append({
                                "id": pid,
                                "create_time": c_time,
                                "update_time": u_time,
                                "score": f_val,
                                "scores_map": p_scores,
                            })
                            if best_score is None or f_val > best_score:
                                best_score = f_val
                                best_program_id = pid
                                best_scores_map = p_scores

            # Progression Series 1: Evolution Generation Order (Creation Timestamp)
            by_creation = sorted(prog_evals, key=lambda x: x["create_time"])
            running_best_gen = None
            progression_by_gen = []
            for idx, item in enumerate(by_creation):
                if running_best_gen is None or item["score"] > running_best_gen:
                    running_best_gen = item["score"]
                progression_by_gen.append({
                    "seq": idx + 1,
                    "id": item["id"],
                    "timestamp": item["create_time"],
                    "score": round(item["score"], 5),
                    "best_so_far": round(running_best_gen, 5),
                    "is_new_best": item["score"] == running_best_gen,
                })

            # Progression Series 2: Wall-Clock Completion Order (Update/Evaluation Completion Timestamp)
            by_upload = sorted(prog_evals, key=lambda x: x["update_time"])
            running_best_clock = None
            progression_by_clock = []
            for idx, item in enumerate(by_upload):
                if running_best_clock is None or item["score"] > running_best_clock:
                    running_best_clock = item["score"]
                progression_by_clock.append({
                    "seq": idx + 1,
                    "id": item["id"],
                    "timestamp": item["update_time"],
                    "score": round(item["score"], 5),
                    "best_so_far": round(running_best_clock, 5),
                    "is_new_best": item["score"] == running_best_clock,
                })
        except Exception as e:
            logger.warning(f"Could not compute best candidate score: {e}")
            progression_by_gen = []
            progression_by_clock = []

        summary = {
            "name": raw_exp.get("name", exp_name),
            "short_id": get_short_id(raw_exp.get("name", exp_name), "alphaEvolveExperiments"),
            "state": state,
            "create_time": create_time,
            "stats": {
                "candidates_count": int(candidates_count),
                "evaluated_candidates_count": int(evaluated_count),
                "input_token_count": int(input_token_count) if str(input_token_count).isdigit() else input_token_count,
                "output_token_count": int(output_token_count) if str(output_token_count).isdigit() else output_token_count,
                "best_score": best_score,
                "main_metric": main_metric,
                "best_program_id": best_program_id,
                "best_scores_map": best_scores_map,
                "progression_by_gen": progression_by_gen,
                "progression_by_clock": progression_by_clock,
            },
            "config": {
                "title": config.get("title", ""),
                "problem_description": config.get("problemDescription", config.get("problem_description", "")),
                "program_language": config.get("programLanguage", config.get("program_language", "")),
                "run_settings": config.get("runSettings", config.get("run_settings", {})),
                "generation_settings": config.get("generationSettings", config.get("generation_settings", {})),
            },
            "raw_experiment": raw_exp,
        }

        logger.info(
            f"Experiment Status: [{state}] | Generated: {candidates_count} | Evaluated: {evaluated_count}"
        )
        return summary

    def monitor_loop(
        self,
        experiment_name: Optional[str] = None,
        poll_interval_seconds: int = 60,
        timeout_seconds: Optional[int] = None,
        stop_on_completion: bool = True,
        max_programs: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Continuously monitors an experiment until completion or timeout.

        Args:
          experiment_name: Full resource name of the experiment.
          poll_interval_seconds: Polling interval in seconds.
          timeout_seconds: Maximum monitoring time in seconds (None for unlimited).
          stop_on_completion: Whether to stop loop when experiment state is COMPLETED or FAILED.
          max_programs: Optional maximum number of candidate programs to poll per cycle.

        Returns:
          The final experiment status summary dictionary.
        """
        exp_name = self._resolve_experiment_name(experiment_name)
        start_time = time.time()
        logger.info(f"Starting monitoring loop for: {exp_name} (Interval: {poll_interval_seconds}s)")

        while True:
            status = self.get_experiment_status(exp_name, max_programs=max_programs)
            state = status["state"]

            if stop_on_completion and state in [
                AlphaEvolveExperimentState.COMPLETED.name,
                AlphaEvolveExperimentState.FAILED.name,
            ]:
                logger.info(f"Experiment reached terminal state: {state}. Exiting monitoring loop.")
                return status

            elapsed = time.time() - start_time
            if timeout_seconds and elapsed >= timeout_seconds:
                logger.info(f"Monitoring timeout ({timeout_seconds}s) reached. Exiting loop.")
                return status

            time.sleep(poll_interval_seconds)

    def list_programs(
        self,
        experiment_name: Optional[str] = None,
        state_filter: Optional[str] = None,
        order_by: Optional[str] = None,
        max_programs: Optional[int] = None,
        page_size: int = 100,
    ) -> List[Dict[str, Any]]:
        """Retrieves programs for an experiment with automatic pagination and unflattening.

        Args:
          experiment_name: Full resource name of the experiment.
          state_filter: Optional state filter (e.g., 'COMPLETED', 'EVALUATING').
          order_by: Optional sorting criteria (e.g., 'score desc').
          max_programs: Maximum number of programs to retrieve across pages.
          page_size: Number of programs per API page request.

        Returns:
          A list of processed program dictionaries.
        """
        exp_name = self._resolve_experiment_name(experiment_name)
        logger.info(f"Listing programs for experiment: {exp_name}")

        programs: List[Dict[str, Any]] = []
        page_token: Optional[str] = None

        while True:
            params: Dict[str, Any] = {"pageSize": page_size}
            if page_token:
                params["pageToken"] = page_token
            if state_filter:
                params["stateFilter"] = state_filter
            if order_by:
                params["orderBy"] = order_by

            resp = self.client.list_alpha_evolve_programs(exp_name, params=params)
            if resp is None:
                logger.error("Error while listing programs from API.")
                break

            batch = resp.get("alphaEvolvePrograms") or resp.get("alpha_evolve_programs", [])
            for prog in batch:
                fix_multi_files_program(prog)
                programs.append(prog)
                if max_programs and len(programs) >= max_programs:
                    break

            if max_programs and len(programs) >= max_programs:
                logger.info(f"Reached max_programs limit ({max_programs}).")
                break

            page_token = resp.get("nextPageToken") or resp.get("next_page_token")
            if not page_token:
                break

        logger.info(f"Retrieved total of {len(programs)} programs.")
        return programs

    def analyze_metrics(
        self,
        programs: List[Dict[str, Any]],
        target_metric: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Analyzes evaluation metrics, distributions, leaderboard, and insights across programs.

        Args:
          programs: List of program dictionaries.
          target_metric: Metric name to use for ranking (auto-detects primary if None).

        Returns:
          A comprehensive analytics summary dictionary.
        """
        logger.info(f"Analyzing metrics for {len(programs)} programs...")
        state_counts: Dict[str, int] = {}
        metric_values: Dict[str, List[float]] = {}
        insights_counter: Dict[str, int] = {}
        evaluated_programs: List[Dict[str, Any]] = []

        for prog in programs:
            state = prog.get("state", "PROGRAM_STATE_UNSPECIFIED")
            state_counts[state] = state_counts.get(state, 0) + 1

            scores = extract_program_scores(prog)
            if scores:
                evaluated_programs.append(prog)
                for m_name, s_val in scores.items():
                    if m_name not in metric_values:
                        metric_values[m_name] = []
                    metric_values[m_name].append(s_val)

            for label, text in extract_program_insights(prog):
                key = f"[{label}] {text}"
                insights_counter[key] = insights_counter.get(key, 0) + 1

        # Determine target metric if not specified
        if not target_metric and metric_values:
            if "score" in metric_values:
                target_metric = "score"
            else:
                target_metric = list(metric_values.keys())[0]
        elif not target_metric:
            target_metric = "score"

        # Calculate statistics per metric
        metrics_stats: Dict[str, Dict[str, Any]] = {}
        for m_name, vals in metric_values.items():
            valid_vals = [v for v in vals if not math.isinf(v) and not math.isnan(v)]
            if not valid_vals:
                metrics_stats[m_name] = {"count": len(vals), "valid_count": 0}
                continue
            metrics_stats[m_name] = {
                "count": len(vals),
                "valid_count": len(valid_vals),
                "min": min(valid_vals),
                "max": max(valid_vals),
                "mean": statistics.mean(valid_vals),
                "median": statistics.median(valid_vals),
                "stdev": statistics.stdev(valid_vals) if len(valid_vals) > 1 else 0.0,
            }

        # Build Leaderboard
        def get_sort_key(p: Dict[str, Any]) -> float:
            scores = extract_program_scores(p)
            val = scores.get(target_metric, float("-inf"))
            return val if not math.isnan(val) else float("-inf")

        sorted_eval = sorted(evaluated_programs, key=get_sort_key, reverse=True)
        leaderboard: List[Dict[str, Any]] = []
        for idx, p in enumerate(sorted_eval):
            short_id = get_short_id(p.get("name", ""), "alphaEvolvePrograms")
            parent_ids = [
                get_short_id(parent, "alphaEvolvePrograms")
                for parent in p.get("parentPrograms", [])
            ]
            leaderboard.append(
                {
                    "rank": idx + 1,
                    "program_id": short_id,
                    "name": p.get("name", ""),
                    "state": p.get("state", ""),
                    "create_time": p.get("createTime", ""),
                    "scores": extract_program_scores(p),
                    "target_score": extract_program_scores(p).get(target_metric, None),
                    "parent_programs": parent_ids,
                    "lock_token": p.get("lockToken", ""),
                }
            )

        # Sort insights by frequency
        sorted_insights = sorted(
            [{"insight": k, "count": v} for k, v in insights_counter.items()],
            key=lambda x: x["count"],
            reverse=True,
        )

        return {
            "total_programs": len(programs),
            "evaluated_programs_count": len(evaluated_programs),
            "state_counts": state_counts,
            "target_metric": target_metric,
            "metrics_stats": metrics_stats,
            "leaderboard": leaderboard,
            "top_insights": sorted_insights[:20],
        }

    def analyze_lineage(self, programs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyzes evolutionary parent-child lineage trees across programs.

        Args:
          programs: List of program dictionaries.

        Returns:
          A dictionary with lineage mappings, seed programs, and evolution depth.
        """
        logger.info("Analyzing evolutionary program lineage...")
        parent_to_children: Dict[str, List[str]] = {}
        child_to_parents: Dict[str, List[str]] = {}
        all_ids: set = set()

        node_details: Dict[str, Dict[str, Any]] = {}
        for prog in programs:
            prog_id = get_short_id(prog.get("name", ""), "alphaEvolvePrograms")
            if not prog_id:
                continue
            all_ids.add(prog_id)
            parents = prog.get("parentPrograms", [])
            parent_ids = [get_short_id(p, "alphaEvolvePrograms") for p in parents]

            child_to_parents[prog_id] = parent_ids
            for p_id in parent_ids:
                if p_id not in parent_to_children:
                    parent_to_children[p_id] = []
                if prog_id not in parent_to_children[p_id]:
                    parent_to_children[p_id].append(prog_id)

            scores = extract_program_scores(prog)
            insights = [f"[{lbl}] {txt}" for lbl, txt in extract_program_insights(prog)]
            target_score = (
                scores.get("score")
                or scores.get("composite_score")
                or (next(iter(scores.values())) if scores else None)
            )

            node_details[prog_id] = {
                "program_id": prog_id,
                "name": prog.get("name", ""),
                "state": prog.get("state", "PROGRAM_STATE_UNSPECIFIED"),
                "create_time": prog.get("createTime", ""),
                "scores": scores,
                "target_score": target_score,
                "parent_ids": parent_ids,
                "insights": insights,
            }

        seed_programs = [
            pid for pid in all_ids if not child_to_parents.get(pid)
        ]

        # Calculate max depth from seed for each program
        depth_map: Dict[str, int] = {}
        for seed in seed_programs:
            depth_map[seed] = 0

        # Breadth-first traversal to compute depths
        queue = deque(seed_programs)
        visited = set(seed_programs)
        while queue:
            curr = queue.popleft()
            curr_depth = depth_map.get(curr, 0)
            for child in parent_to_children.get(curr, []):
                if child not in depth_map or depth_map[child] < curr_depth + 1:
                    depth_map[child] = curr_depth + 1
                if child not in visited:
                    visited.add(child)
                    queue.append(child)

        max_depth = max(depth_map.values()) if depth_map else 0

        # Inject child_ids into node_details and identify best candidate
        best_program_id = None
        best_target_score = None
        for pid, details in node_details.items():
            details["children_ids"] = parent_to_children.get(pid, [])
            details["depth"] = depth_map.get(pid, 0)
            t_score = details.get("target_score")
            if t_score is not None and isinstance(t_score, (int, float)):
                if best_target_score is None or t_score > best_target_score:
                    best_target_score = t_score
                    best_program_id = pid

        return {
            "total_nodes": len(all_ids),
            "seed_programs": seed_programs,
            "max_evolution_depth": max_depth,
            "best_program_id": best_program_id,
            "best_score": best_target_score,
            "parent_to_children": parent_to_children,
            "child_to_parents": child_to_parents,
            "program_depths": depth_map,
            "node_details": node_details,
        }

    def get_program_code(
        self,
        program_id: str,
        experiment_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Retrieves unflattened source code files and details for a specific program."""
        exp_name = self._resolve_experiment_name(experiment_name)
        prog_name = f"{exp_name}/alphaEvolvePrograms/{program_id}" if "/" not in program_id else program_id
        
        raw_prog = None
        try:
            raw_prog = self.client.get_alpha_evolve_program(prog_name)
        except Exception as e:
            logger.warning(f"Direct lookup failed for program {program_id}: {e}")
            raise RuntimeError(f"Failed to find program: {program_id} in {exp_name} ({e})") from e

        if not raw_prog:
            raise RuntimeError(f"Failed to find program: {program_id} in {exp_name}")

        fix_multi_files_program(raw_prog)
        content = raw_prog.get("content", {})
        files = content.get("files", [])
        scores = extract_program_scores(raw_prog)
        insights = [f"[{lbl}] {txt}" for lbl, txt in extract_program_insights(raw_prog)]

        return {
            "program_id": get_short_id(raw_prog.get("name", ""), "alphaEvolvePrograms"),
            "name": raw_prog.get("name", ""),
            "state": raw_prog.get("state", ""),
            "create_time": raw_prog.get("createTime", ""),
            "scores": scores,
            "parent_ids": [get_short_id(p, "alphaEvolvePrograms") for p in raw_prog.get("parentPrograms", [])],
            "insights": insights,
            "files": files,
        }


    def export_results(
        self,
        output_dir: Union[str, Path],
        experiment_status: Dict[str, Any],
        programs: List[Dict[str, Any]],
        metrics_analysis: Dict[str, Any],
        lineage_analysis: Dict[str, Any],
        top_k: int = 10,
        export_code: bool = True,
    ) -> str:
        """Exports post-processing summaries, CSV tables, and code artifacts to disk.

        Args:
          output_dir: Path to destination output directory.
          experiment_status: Summary from get_experiment_status.
          programs: Complete list of program dictionaries.
          metrics_analysis: Summary from analyze_metrics.
          lineage_analysis: Summary from analyze_lineage.
          top_k: Number of top performing programs to export code files for.
          export_code: Whether to save unflattened source code files.

        Returns:
          Absolute path to the created output directory.
        """
        out_path = Path(output_dir).resolve()
        out_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"Exporting post-processing artifacts to: {out_path}")

        # 1. Export JSON summaries
        with open(out_path / "experiment_status.json", "w", encoding="utf-8") as f:
            json.dump(experiment_status, f, indent=2, default=str)

        with open(out_path / "metrics_analysis.json", "w", encoding="utf-8") as f:
            json.dump(metrics_analysis, f, indent=2, default=str)

        with open(out_path / "lineage_analysis.json", "w", encoding="utf-8") as f:
            json.dump(lineage_analysis, f, indent=2, default=str)

        # 2. Export CSV Leaderboard / Programs Table
        csv_file = out_path / "programs_summary.csv"
        all_metrics = list(metrics_analysis.get("metrics_stats", {}).keys())
        header = [
            "rank",
            "program_id",
            "state",
            "create_time",
            "parent_ids",
        ] + all_metrics

        with open(csv_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(header)

            # Map rank by program_id from leaderboard
            rank_map = {
                item["program_id"]: item["rank"]
                for item in metrics_analysis.get("leaderboard", [])
            }

            for prog in programs:
                pid = get_short_id(prog.get("name", ""), "alphaEvolvePrograms")
                rank_val = rank_map.get(pid, "")
                state = prog.get("state", "")
                ctime = prog.get("createTime", "")
                parents = " | ".join(
                    [
                        get_short_id(p, "alphaEvolvePrograms")
                        for p in prog.get("parentPrograms", [])
                    ]
                )
                scores = extract_program_scores(prog)
                row = [
                    rank_val,
                    pid,
                    state,
                    ctime,
                    parents,
                ] + [scores.get(m, "") for m in all_metrics]
                writer.writerow(row)

        # 3. Export Code Files for Top K Programs
        if export_code and programs:
            code_dir = out_path / "programs_code"
            code_dir.mkdir(parents=True, exist_ok=True)

            prog_map = {
                get_short_id(p.get("name", ""), "alphaEvolvePrograms"): p
                for p in programs
            }
            leaderboard = metrics_analysis.get("leaderboard", [])
            top_items = leaderboard[:top_k] if top_k > 0 else leaderboard

            for item in top_items:
                pid = item["program_id"]
                rank = item["rank"]
                prog_data = prog_map.get(pid)
                if not prog_data:
                    continue

                prog_dir = code_dir / f"rank_{rank:03d}_prog_{pid}"
                prog_dir.mkdir(parents=True, exist_ok=True)

                # Save program metadata
                with open(prog_dir / "metadata.json", "w", encoding="utf-8") as f:
                    json.dump(item, f, indent=2, default=str)

                # Save individual source files
                files = (
                    prog_data.get("content", {}).get("files", [])
                )
                for f_info in files:
                    rel_path = f_info.get("path", "untitled.txt")
                    file_content = f_info.get("content", "")
                    file_path = prog_dir / rel_path
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(file_path, "w", encoding="utf-8") as fp:
                        fp.write(file_content)

        logger.info("Successfully completed all exports.")
        return str(out_path)

    def postprocess(
        self,
        experiment_name: Optional[str] = None,
        output_dir: Optional[Union[str, Path]] = None,
        target_metric: Optional[str] = None,
        top_k: int = 10,
        export_code: bool = True,
        state_filter: Optional[str] = None,
        order_by: Optional[str] = None,
        max_programs: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Runs the complete post-processing pipeline for an experiment.

        Args:
          experiment_name: Full resource name of the experiment.
          output_dir: Optional local path to export summary artifacts and code.
          target_metric: Target metric name for ranking programs.
          top_k: Number of top programs to export code files for.
          export_code: Whether to export unflattened program source files.
          state_filter: Optional API filter for program states.
          order_by: Optional API sorting parameter.
          max_programs: Maximum number of programs to fetch.

        Returns:
          A combined dictionary containing status, metrics analysis, and lineage.
        """
        exp_name = self._resolve_experiment_name(experiment_name)
        logger.info(f"=== Starting Post-Processing for: {exp_name} ===")

        programs = self.list_programs(
            exp_name,
            state_filter=state_filter,
            order_by=order_by,
            max_programs=max_programs,
        )
        status_summary = self.get_experiment_status(exp_name, programs=programs)

        metrics_summary = self.analyze_metrics(programs, target_metric=target_metric)
        lineage_summary = self.analyze_lineage(programs)

        exported_path = ""
        if output_dir:
            exported_path = self.export_results(
                output_dir=output_dir,
                experiment_status=status_summary,
                programs=programs,
                metrics_analysis=metrics_summary,
                lineage_analysis=lineage_summary,
                top_k=top_k,
                export_code=export_code,
            )

        return {
            "experiment_status": status_summary,
            "metrics_analysis": metrics_summary,
            "lineage_analysis": lineage_summary,
            "exported_directory": exported_path,
        }

