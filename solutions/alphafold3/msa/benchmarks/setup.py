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
#     "google-cloud-storage",
#     "rich",
#     "rich-argparse",
# ]
# ///

"""Assembles, characterizes, and uploads AlphaFold 3 MSA benchmark inputs.

Generates benchmark inputs across supported MSA entity types:
  - 'heterogeneous_mix' (default): splits total inputs evenly across
    protein monomers, RNA monomers, protein multimers, and RNP complexes.
  - 'protein_monomer': monomeric protein chains (200-1,800 residues).
  - 'rna_monomer': monomeric RNA chains (30-500 nucleotides).
  - 'protein_multimer': multi-chain protein complexes (heterodimers).
  - 'rnp_complex': ribonucleoprotein complexes (protein + RNA).

Generates temporary {n}.json inputs in /tmp, uploads them to Cloud Storage via
the google-cloud-storage API, cleans up staging files, and characterizes input
length distributions.

Usage:
  ./setup.py gs://<bucket-name>
  ./setup.py --count 300 --type heterogeneous_mix gs://<bucket-name>
  ./setup.py --count 100 --type protein_monomer --no-upload
  ./setup.py --cached --no-upload
"""

from __future__ import annotations

import argparse
import atexit
from collections import defaultdict
import concurrent.futures
import json
import math
import os
import pathlib
import random
import shutil
import statistics
import sys
import tempfile
from typing import Any, TypedDict
import urllib.error
import urllib.parse
import urllib.request

from google.cloud import storage
from rich import box
from rich.console import Console
from rich.table import Table
from rich_argparse import RawDescriptionRichHelpFormatter

DEFAULT_INPUT_COUNT = 300
DEFAULT_TARGET_COUNT = DEFAULT_INPUT_COUNT
DEFAULT_INPUT_TYPE = "heterogeneous_mix"
DEFAULT_TARGET_TYPE = DEFAULT_INPUT_TYPE
DEFAULT_MIN_LEN = 200
DEFAULT_MAX_LEN = 1800
DEFAULT_SHUFFLE_SEED = 42

INPUT_TYPES = (
    "heterogeneous_mix",
    "protein_monomer",
    "rna_monomer",
    "protein_multimer",
    "rnp_complex",
)
TARGET_TYPES = INPUT_TYPES

HETEROGENEOUS_SUBTYPES = (
    "protein_monomer",
    "rna_monomer",
    "protein_multimer",
    "rnp_complex",
)


class ProteinCandidate(TypedDict):
  """Candidate protein sequence record for benchmark input assembly."""

  candidate_id: str
  name: str
  sequence: str
  length: int
  description: str


class RnaCandidate(TypedDict):
  """Candidate RNA sequence record for benchmark input assembly."""

  candidate_id: str
  name: str
  sequence: str
  length: int
  description: str
  rna_type: str


def load_protein_pool(
    file_path: pathlib.Path,
) -> list[ProteinCandidate]:
  """Loads candidate protein sequences from a JSONL file."""
  results: list[ProteinCandidate] = []
  if not file_path.is_file():
    return results

  with file_path.open("r", encoding="utf-8") as f:
    for line_num, line in enumerate(f, 1):
      line = line.strip()
      if not line:
        continue
      try:
        record = json.loads(line)
        if not isinstance(record, dict):
          continue
        if "name" in record and "sequence" in record:
          seq = str(record["sequence"]).upper()
          name = str(record["name"])
          candidate_id = str(
              record.get("candidate_id") or record.get("id") or name
          )
          results.append(
              ProteinCandidate(
                  candidate_id=candidate_id,
                  name=name,
                  sequence=seq,
                  length=len(seq),
                  description=str(record.get("description", "")),
              )
          )
        elif "sequences" in record and isinstance(record["sequences"], list):
          for item in record["sequences"]:
            if isinstance(item, dict) and "protein" in item:
              p = item["protein"]
              if isinstance(p, dict) and "sequence" in p:
                seq = str(p["sequence"]).upper()
                name = str(p.get("name") or record.get("name", ""))
                candidate_id = str(
                    p.get("candidate_id")
                    or p.get("id")
                    or record.get("candidate_id")
                    or record.get("id")
                    or name
                )
                results.append(
                    ProteinCandidate(
                        candidate_id=candidate_id,
                        name=name,
                        sequence=seq,
                        length=len(seq),
                        description=str(p.get("description", "")),
                    )
                )
      except Exception as e:
        raise ValueError(
            f"Failed to parse protein pool entry at {file_path}:{line_num}: {e}"
        ) from e
  return results


def load_rna_pool(
    file_path: pathlib.Path,
) -> list[RnaCandidate]:
  """Loads candidate RNA sequences from a JSONL file."""
  results: list[RnaCandidate] = []
  if not file_path.is_file():
    return results

  with file_path.open("r", encoding="utf-8") as f:
    for line_num, line in enumerate(f, 1):
      line = line.strip()
      if not line:
        continue
      try:
        record = json.loads(line)
        if not isinstance(record, dict):
          continue
        if "name" in record and "sequence" in record:
          seq = str(record["sequence"]).upper().replace("T", "U")
          name = str(record["name"])
          candidate_id = str(
              record.get("candidate_id") or record.get("id") or name
          )
          results.append(
              RnaCandidate(
                  candidate_id=candidate_id,
                  name=name,
                  sequence=seq,
                  length=len(seq),
                  description=str(record.get("description", "")),
                  rna_type=str(record.get("rna_type", "ncRNA")),
              )
          )
        elif "sequences" in record and isinstance(record["sequences"], list):
          for item in record["sequences"]:
            if isinstance(item, dict) and "rna" in item:
              r = item["rna"]
              if isinstance(r, dict) and "sequence" in r:
                seq = str(r["sequence"]).upper().replace("T", "U")
                name = str(r.get("name") or record.get("name", ""))
                candidate_id = str(
                    r.get("candidate_id")
                    or r.get("id")
                    or record.get("candidate_id")
                    or record.get("id")
                    or name
                )
                results.append(
                    RnaCandidate(
                        candidate_id=candidate_id,
                        name=name,
                        sequence=seq,
                        length=len(seq),
                        description=str(r.get("description", "")),
                        rna_type=str(r.get("rna_type", "ncRNA")),
                    )
                )
      except Exception as e:
        raise ValueError(
            f"Failed to parse RNA pool entry at {file_path}:{line_num}: {e}"
        ) from e
  return results


def fetch_uniprot_proteins(
    count_needed: int,
    min_len: int = DEFAULT_MIN_LEN,
    max_len: int = DEFAULT_MAX_LEN,
) -> list[ProteinCandidate]:
  """Queries UniProtKB REST API across uniform length bins, raising on query errors."""
  num_bins = min(20, max(1, count_needed))
  per_bin = math.ceil(count_needed / num_bins)
  span = max_len - min_len
  step = max(1, span // num_bins)

  proteins: list[ProteinCandidate] = []
  headers = {"User-Agent": "AlphaFold3-MSA-Benchmark/1.0"}

  for i in range(num_bins):
    start = min_len + i * step
    end = start + step - 1 if i < num_bins - 1 else max_len
    query = f"(length:[{start} TO {end}]) AND (reviewed:true)"
    url = (
        "https://rest.uniprot.org/uniprotkb/search?"
        + urllib.parse.urlencode({
            "query": query,
            "fields": "accession,id,length,sequence",
            "sort": "accession asc",
            "format": "json",
            "size": str(per_bin),
        })
    )
    req = urllib.request.Request(url, headers=headers)
    try:
      with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status != 200:
          raise RuntimeError(
              f"UniProtKB query returned HTTP status {resp.status} for URL {url}"
          )
        data = json.loads(resp.read().decode("utf-8"))
        results = data.get("results", [])
        if not results:
          raise RuntimeError(
              f"UniProtKB query returned 0 results for length bin [{start}..{end}]"
          )
        for entry in results:
          acc = entry.get("primaryAccession", "")
          entry_id = entry.get("uniProtkbId", "")
          seq_val = entry.get("sequence", {}).get("value", "")
          if acc and seq_val:
            name = f"{acc}_{entry_id}".lower()
            proteins.append(
                ProteinCandidate(
                    candidate_id=acc,
                    name=name,
                    sequence=seq_val.upper(),
                    length=len(seq_val),
                    description=f"UniProt {acc} ({len(seq_val)} aa)",
                )
            )
    except Exception as e:
      raise RuntimeError(
          f"Failed to query UniProtKB REST API for length bin [{start}..{end}]"
          f" via {url}: {e}"
      ) from e

  return proteins


def calculate_type_counts(
    target_count: int,
    target_type: str,
) -> dict[str, int]:
  """Splits target count as evenly as possible across types."""
  counts: dict[str, int] = {t: 0 for t in HETEROGENEOUS_SUBTYPES}

  if target_type == "heterogeneous_mix":
    num_types = len(HETEROGENEOUS_SUBTYPES)
    base = target_count // num_types
    remainder = target_count % num_types
    for idx, subtype in enumerate(HETEROGENEOUS_SUBTYPES):
      counts[subtype] = base + (1 if idx < remainder else 0)
  elif target_type in counts:
    counts[target_type] = target_count
  else:
    raise ValueError(f"Unknown target type: {target_type}")

  return counts


def create_protein_monomer_input(
    prot: ProteinCandidate,
) -> dict[str, Any]:
  """Constructs an AlphaFold 3 protein monomer input dictionary."""
  name = f"protein_monomer__{prot['name']}"
  return {
      "name": name,
      "entity_type": "protein_monomer",
      "target_type": "protein_monomer",
      "sequences": [
          {
              "protein": {
                  "id": "A",
                  "sequence": prot["sequence"],
                  "description": (
                      prot["description"]
                      or f"UniProt protein monomer {prot['name']} ({prot['length']} aa)"
                  ),
              }
          }
      ],
      "modelSeeds": [42],
      "dialect": "alphafold3",
      "version": 4,
  }


def create_rna_monomer_input(
    rna: RnaCandidate,
) -> dict[str, Any]:
  """Constructs an AlphaFold 3 RNA monomer input dictionary."""
  name = f"rna_monomer__{rna['name']}"
  return {
      "name": name,
      "entity_type": "rna_monomer",
      "target_type": "rna_monomer",
      "sequences": [
          {
              "rna": {
                  "id": "A",
                  "sequence": rna["sequence"],
                  "description": (
                      rna["description"]
                      or f"RNA monomer {rna['name']} ({rna['length']} nt)"
                  ),
              }
          }
      ],
      "modelSeeds": [42],
      "dialect": "alphafold3",
      "version": 4,
  }


def create_protein_multimer_input(
    prot_a: ProteinCandidate,
    prot_b: ProteinCandidate,
) -> dict[str, Any]:
  """Constructs an AlphaFold 3 protein-protein complex input dictionary."""
  name = f"protein_multimer__{prot_a['name']}_{prot_b['name']}_complex"
  return {
      "name": name,
      "entity_type": "protein_multimer",
      "target_type": "protein_multimer",
      "sequences": [
          {
              "protein": {
                  "id": "A",
                  "sequence": prot_a["sequence"],
                  "description": (
                      f"Chain A: {prot_a['name']} ({prot_a['length']} aa)"
                  ),
              }
          },
          {
              "protein": {
                  "id": "B",
                  "sequence": prot_b["sequence"],
                  "description": (
                      f"Chain B: {prot_b['name']} ({prot_b['length']} aa)"
                  ),
              }
          },
      ],
      "modelSeeds": [42],
      "dialect": "alphafold3",
      "version": 4,
  }


def create_rnp_complex_input(
    prot: ProteinCandidate,
    rna: RnaCandidate,
) -> dict[str, Any]:
  """Constructs an AlphaFold 3 protein-RNA complex input dictionary."""
  name = f"rnp_complex__{prot['name']}_{rna['name']}_rnp"
  return {
      "name": name,
      "entity_type": "rnp_complex",
      "target_type": "rnp_complex",
      "sequences": [
          {
              "protein": {
                  "id": "A",
                  "sequence": prot["sequence"],
                  "description": (
                      f"Protein chain: {prot['name']} ({prot['length']} aa)"
                  ),
              }
          },
          {
              "rna": {
                  "id": "B",
                  "sequence": rna["sequence"],
                  "description": f"RNA chain: {rna['name']} ({rna['length']} nt)",
              }
          },
      ],
      "modelSeeds": [42],
      "dialect": "alphafold3",
      "version": 4,
  }


# Backward-compatible aliases
create_protein_monomer_target = create_protein_monomer_input
create_rna_monomer_target = create_rna_monomer_input
create_protein_multimer_target = create_protein_multimer_input
create_rnp_complex_target = create_rnp_complex_input


def assemble_benchmark(
    target_count: int,
    target_type: str,
    output_path: pathlib.Path,
    protein_pool: list[ProteinCandidate],
    rna_pool: list[RnaCandidate],
    min_len: int = DEFAULT_MIN_LEN,
    max_len: int = DEFAULT_MAX_LEN,
    seed: int = DEFAULT_SHUFFLE_SEED,
) -> list[dict[str, Any]]:
  """Assembles and shuffles the benchmark dataset, raising on any missing candidate classes."""
  rng = random.Random(seed)
  type_counts = calculate_type_counts(target_count, target_type)

  needed_proteins = (
      type_counts["protein_monomer"]
      + 2 * type_counts["protein_multimer"]
      + type_counts["rnp_complex"]
  )
  needed_rnas = type_counts["rna_monomer"] + type_counts["rnp_complex"]

  # If protein candidates in the local pool are insufficient, query UniProtKB
  if len(protein_pool) < needed_proteins and needed_proteins > 0:
    missing_count = needed_proteins - len(protein_pool)
    fetched = fetch_uniprot_proteins(missing_count, min_len, max_len)
    seen = {p["name"] for p in protein_pool}
    for p in fetched:
      if p["name"] not in seen:
        seen.add(p["name"])
        protein_pool.append(p)

  # Check that sufficient candidates exist; fail loudly if not
  if len(protein_pool) < needed_proteins and needed_proteins > 0:
    raise RuntimeError(
        f"Insufficient protein candidates available: needed {needed_proteins},"
        f" but only {len(protein_pool)} found. Aborting to ensure full target"
        " class representation."
    )

  if len(rna_pool) < needed_rnas and needed_rnas > 0:
    raise RuntimeError(
        f"Insufficient RNA candidates available: needed {needed_rnas},"
        f" but only {len(rna_pool)} found. Aborting to ensure full target"
        " class representation."
    )

  shuffled_proteins = list(protein_pool)
  shuffled_rnas = list(rna_pool)
  rng.shuffle(shuffled_proteins)
  rng.shuffle(shuffled_rnas)

  inputs: list[dict[str, Any]] = []

  for _ in range(type_counts["protein_monomer"]):
    prot = shuffled_proteins.pop(0)
    inputs.append(create_protein_monomer_input(prot))

  for _ in range(type_counts["rna_monomer"]):
    rna = shuffled_rnas.pop(0)
    inputs.append(create_rna_monomer_input(rna))

  for _ in range(type_counts["protein_multimer"]):
    prot_a = shuffled_proteins.pop(0)
    prot_b = shuffled_proteins.pop(0)
    inputs.append(create_protein_multimer_input(prot_a, prot_b))

  for _ in range(type_counts["rnp_complex"]):
    prot = shuffled_proteins.pop(0)
    rna = shuffled_rnas.pop(0)
    inputs.append(create_rnp_complex_input(prot, rna))

  rng.shuffle(inputs)

  output_path.parent.mkdir(parents=True, exist_ok=True)
  with output_path.open("w", encoding="utf-8") as f:
    for inp in inputs:
      f.write(json.dumps(inp) + "\n")

  return inputs


def is_valid_benchmark_file(
    file_path: pathlib.Path,
    expected_count: int,
) -> bool:
  """Checks if a benchmark JSONL file exists with exactly expected_count valid lines."""
  if not file_path.is_file() or file_path.stat().st_size == 0:
    return False
  count = 0
  try:
    with file_path.open("r", encoding="utf-8") as f:
      for line in f:
        line = line.strip()
        if not line:
          continue
        json.loads(line)
        count += 1
  except Exception:
    return False
  return count == expected_count


ALPHAFOLD3_ALLOWED_KEYS: frozenset[str] = frozenset({
    "dialect",
    "version",
    "name",
    "modelSeeds",
    "sequences",
    "bondedAtomPairs",
    "userCCD",
    "userCCDPath",
})


def generate_temporary_json_inputs(
    benchmark_file: pathlib.Path,
    output_dir: pathlib.Path,
) -> int:
  """Expands benchmark_sequences.jsonl into sequential {idx}.json AlphaFold 3 inputs."""
  output_dir.mkdir(parents=True, exist_ok=True)
  count = 0

  with benchmark_file.open("r", encoding="utf-8") as f:
    for idx, line in enumerate(f):
      line = line.strip()
      if not line:
        continue
      record = json.loads(line)
      if "sequences" in record:
        input_dict = {
            k: v for k, v in record.items() if k in ALPHAFOLD3_ALLOWED_KEYS
        }
      elif "name" in record and "sequence" in record:
        input_dict = {
            "name": str(record["name"]),
            "sequences": [
                {
                    "protein": {
                        "id": "A",
                        "sequence": str(record["sequence"]),
                        "description": (
                            f"UniProt benchmark input {record['name']}"
                            f" ({len(str(record['sequence']))} aa)"
                        ),
                    }
                }
            ],
            "modelSeeds": [42],
            "dialect": "alphafold3",
            "version": 4,
        }
      else:
        first_key, first_val = next(iter(record.items()))
        input_dict = {
            "name": str(first_key),
            "sequences": [
                {
                    "protein": {
                        "id": "A",
                        "sequence": str(first_val),
                        "description": (
                            f"Benchmark input {first_key} ({len(str(first_val))} aa)"
                        ),
                    }
                }
            ],
            "modelSeeds": [42],
            "dialect": "alphafold3",
            "version": 4,
        }

      input_file = output_dir / f"{idx}.json"
      with input_file.open("w", encoding="utf-8") as out_f:
        json.dump(input_dict, out_f, indent=2)
        out_f.write("\n")
      count += 1

  return count


def upload_to_gcs(
    source_dir: pathlib.Path,
    target_gcs_uri: str,
    manifest_file: pathlib.Path | None = None,
    max_workers: int = 16,
) -> int:
  """Uploads generated JSON inputs and optional manifest to Cloud Storage."""
  if not target_gcs_uri.startswith("gs://"):
    raise ValueError(f"Target GCS URI must start with 'gs://', got '{target_gcs_uri}'")

  path_without_scheme = target_gcs_uri[5:].strip("/")
  parts = path_without_scheme.split("/", 1)
  bucket_name = parts[0]
  prefix = parts[1].rstrip("/") if len(parts) > 1 else ""

  client = storage.Client()
  bucket = client.bucket(bucket_name)

  json_files = sorted(source_dir.glob("*.json"))
  if not json_files:
    raise FileNotFoundError(f"No JSON input files found to upload in {source_dir}")

  def _upload_file(file_path: pathlib.Path) -> str:
    blob_name = f"{prefix}/{file_path.name}" if prefix else file_path.name
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(str(file_path), content_type="application/json")
    return blob_name

  with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
    futures = [executor.submit(_upload_file, p) for p in json_files]
    for future in concurrent.futures.as_completed(futures):
      future.result()

  if manifest_file and manifest_file.is_file():
    manifest_blob_name = f"{prefix}/manifest.jsonl" if prefix else "manifest.jsonl"
    bucket.blob(manifest_blob_name).upload_from_filename(
        str(manifest_file), content_type="application/x-ndjson"
    )

  return len(json_files)


def characterize_benchmark(
    benchmark_file: pathlib.Path,
    console: Console,
) -> None:
  """Characterizes input types and sequence length distributions."""
  lengths: list[int] = []
  type_counts: dict[str, int] = defaultdict(int)
  type_lengths: dict[str, list[int]] = defaultdict(list)

  with benchmark_file.open("r", encoding="utf-8") as f:
    for line in f:
      line = line.strip()
      if not line:
        continue
      record = json.loads(line)
      target_type = str(
          record.get("entity_type")
          or record.get("target_type", "protein_monomer")
      )
      total_len = 0
      if "sequences" in record and isinstance(record["sequences"], list):
        for seq_obj in record["sequences"]:
          if isinstance(seq_obj, dict):
            for entity in seq_obj.values():
              if isinstance(entity, dict) and "sequence" in entity:
                total_len += len(str(entity["sequence"]))
      elif "sequence" in record:
        total_len = len(str(record["sequence"]))
      else:
        first_val = next(iter(record.values()))
        total_len = len(str(first_val))

      lengths.append(total_len)
      type_counts[target_type] += 1
      type_lengths[target_type].append(total_len)

  if not lengths:
    console.print("[red]No sequences found in benchmark file.[/red]")
    return

  n = len(lengths)
  avg_len = statistics.mean(lengths)
  min_len = min(lengths)
  max_len = max(lengths)
  std_dev = statistics.stdev(lengths) if n > 1 else 0.0

  table = Table(
      title=f"Benchmark Characterization ({benchmark_file.name})",
      box=box.ROUNDED,
      header_style="bold cyan",
  )
  table.add_column("Metric", style="bold")
  table.add_column("Value", justify="right")

  table.add_row("Total Inputs", f"{n:,}")
  for t_name, t_cnt in sorted(type_counts.items()):
    disp = t_name.replace("_", " ").title()
    t_avg = statistics.mean(type_lengths[t_name])
    t_min = min(type_lengths[t_name])
    t_max = max(type_lengths[t_name])
    table.add_row(
        f"  • {disp}",
        f"{t_cnt:,} inputs (avg: {t_avg:,.1f}, range: {t_min}-{t_max})",
    )

  table.add_row("Average Length", f"{avg_len:,.2f} residues")
  table.add_row("Min Length", f"{min_len:,} residues")
  table.add_row("Max Length", f"{max_len:,} residues")
  table.add_row("Length Span", f"{max_len - min_len:,} residues")
  table.add_row("Standard Deviation", f"{std_dev:,.2f} residues")

  console.print()
  console.print(table)


def main() -> None:
  console = Console()
  parser = argparse.ArgumentParser(
      description=__doc__,
      formatter_class=RawDescriptionRichHelpFormatter,
  )
  parser.add_argument(
      "bucket",
      nargs="?",
      default=os.getenv("GCS_BUCKET", ""),
      help=(
          "Destination Cloud Storage bucket (e.g. gs://my-bucket or"
          " gs://my-bucket/msa-inputs). Required unless --no-upload is specified."
      ),
  )
  parser.add_argument(
      "-n",
      "--count",
      type=int,
      default=int(
          os.getenv(
              "INPUT_COUNT",
              os.getenv("TARGET_COUNT", str(DEFAULT_INPUT_COUNT)),
          )
      ),
      help=f"Total number of benchmark inputs to generate (default: {DEFAULT_INPUT_COUNT})",
  )
  parser.add_argument(
      "-t",
      "--type",
      dest="input_type",
      choices=INPUT_TYPES,
      default=os.getenv(
          "INPUT_TYPE",
          os.getenv("TARGET_TYPE", DEFAULT_INPUT_TYPE),
      ),
      help=f"Benchmark input category (default: {DEFAULT_INPUT_TYPE})",
  )
  parser.add_argument(
      "--cached",
      action="store_true",
      help="Skip generating new dataset if benchmark_sequences.jsonl already exists locally with matching input count",
  )
  parser.add_argument(
      "--no-upload",
      action="store_true",
      help="Assemble, save, generate and verify temporary {n}.json files in /tmp, clean up, and characterize without uploading to Cloud Storage",
  )
  parser.add_argument(
      "--benchmark-file",
      type=pathlib.Path,
      default=pathlib.Path(
          os.getenv(
              "BENCHMARK_FILE",
              str(pathlib.Path(__file__).resolve().parent / "benchmark_sequences.jsonl"),
          )
      ),
      help="Path to save the benchmark sequences JSONL file",
  )
  parser.add_argument(
      "--min-len",
      type=int,
      default=int(os.getenv("MIN_LEN", str(DEFAULT_MIN_LEN))),
      help=f"Minimum sequence length in residues (default: {DEFAULT_MIN_LEN})",
  )
  parser.add_argument(
      "--max-len",
      type=int,
      default=int(os.getenv("MAX_LEN", str(DEFAULT_MAX_LEN))),
      help=f"Maximum sequence length in residues (default: {DEFAULT_MAX_LEN})",
  )
  parser.add_argument(
      "--seed",
      type=int,
      default=int(os.getenv("SHUFFLE_SEED", str(DEFAULT_SHUFFLE_SEED))),
      help=f"Deterministic sequence order randomization seed (default: {DEFAULT_SHUFFLE_SEED})",
  )
  args = parser.parse_args()

  if args.count <= 0:
    console.print(
        f"[red]Error: --count must be a positive integer, got {args.count}.[/red]"
    )
    sys.exit(1)

  target_gcs_uri = ""
  if not args.no_upload:
    bucket = args.bucket
    if not bucket:
      console.print(
          "[red]Error: GCS bucket is required when uploading.[/red]\n"
          "Pass gs://<bucket-name> as an argument, set GCS_BUCKET, or pass"
          " --no-upload."
      )
      sys.exit(1)

    bucket = bucket.rstrip("/")
    if bucket.endswith("/msa-inputs"):
      target_gcs_uri = bucket
    else:
      target_gcs_uri = f"{bucket}/msa-inputs"

  tmp_dir = pathlib.Path(tempfile.mkdtemp(prefix="af3-msa-benchmarks."))
  atexit.register(lambda: shutil.rmtree(tmp_dir, ignore_errors=True))

  script_dir = pathlib.Path(__file__).resolve().parent
  rna_pool_path = script_dir / "reference_rnas.jsonl"
  prot_pool_path = script_dir / "benchmark_sequences.jsonl"

  console.print(
      f"[bold cyan]=== Step 1: Benchmark Dataset Preparation ({args.count} inputs, category: {args.input_type}) ===[/bold cyan]"
  )

  if args.cached and is_valid_benchmark_file(args.benchmark_file, args.count):
    console.print(
        f"[green]Using existing benchmark dataset at {args.benchmark_file} ({args.count} inputs)[/green]"
    )
  else:
    rna_pool = load_rna_pool(rna_pool_path)
    prot_pool = load_protein_pool(prot_pool_path)

    assembled_inputs = assemble_benchmark(
        target_count=args.count,
        target_type=args.input_type,
        output_path=args.benchmark_file,
        protein_pool=prot_pool,
        rna_pool=rna_pool,
        min_len=args.min_len,
        max_len=args.max_len,
        seed=args.seed,
    )
    console.print(
        f"[green]Saved {len(assembled_inputs)} randomized benchmark inputs to {args.benchmark_file}[/green]"
    )

  # Step 2: Generate temporary {n}.json files
  inputs_tmp_dir = tmp_dir / "msa-inputs"
  console.print(
      f"[bold cyan]=== Step 2: Generating AlphaFold 3 {{n}}.json input files in {inputs_tmp_dir} ===[/bold cyan]"
  )
  generated_count = generate_temporary_json_inputs(
      args.benchmark_file,
      inputs_tmp_dir,
  )
  if generated_count == 0:
    console.print("[red]Error: failed to generate input JSON files.[/red]")
    sys.exit(1)
  console.print(
      f"Created {generated_count} input files (0.json .. {generated_count - 1}.json)"
  )

  # Step 3: Upload to Cloud Storage
  if not args.no_upload:
    console.print(
        f"[bold cyan]=== Step 3: Uploading {generated_count} input files to {target_gcs_uri}/ (google-cloud-storage) ===[/bold cyan]"
    )
    uploaded = upload_to_gcs(
        inputs_tmp_dir, target_gcs_uri, manifest_file=args.benchmark_file
    )
    console.print(
        f"[green]Successfully uploaded {uploaded} benchmark inputs and manifest to {target_gcs_uri}/[/green]"
    )
  else:
    console.print(
        "[yellow]=== Step 3: Skipping Cloud Storage upload (--no-upload specified) ===[/yellow]"
    )

  # Step 4: Characterization
  console.print(
      "[bold cyan]=== Step 4: Characterizing benchmark inputs ===[/bold cyan]"
  )
  characterize_benchmark(args.benchmark_file, console)


if __name__ == "__main__":
  main()
