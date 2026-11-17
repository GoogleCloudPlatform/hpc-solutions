# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Evaluator module for OpenRADIOSS Aerospace Wing Bird-Strike optimization."""

from contextlib import contextmanager
import json
import logging
import math
import os
from pathlib import Path
import shutil
import signal
import threading
from typing import Any, Iterator, Mapping, Sequence

from alpha_evolve.config import (
    BaseExperimentConfig,
    ControllerConfig,
    EvaluatorConfig,
)
from alpha_evolve.models import (
    AlphaEvolveEvaluationInsight,
    AlphaEvolveEvaluationInsights,
    AlphaEvolveEvaluationScore,
    AlphaEvolveEvaluationScores,
    AlphaEvolveProgramEvaluation,
)
from alpha_evolve.utils import (
    get_job_id_from_program_name,
    read_file_from_gcs,
)
try:
  from alpha_evolve.utils import upload_to_gcs
except ImportError:

  def upload_to_gcs(local_path: Path, bucket_name: str, blob_name: str) -> None:
    from google.cloud import storage  # pylint: disable=g-import-not-at-top

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    bucket.blob(blob_name).upload_from_filename(str(local_path))

import numpy as np

from src.radioss_runner import (
    BIRD_BREACH_X_LIMIT_MM,
    BOUNDS,
    MAX_ERODED_SHELL_PCT,
    PART_STRAIN_LIMITS,
    SPAR_DEFLECTION_LIMIT_MM,
    SPAR_PEAK_FORCE_LIMIT_KN,
    plot_bird_strike_comparison,
    run_openradioss_simulation,
)

logger = logging.getLogger(__name__)

BIRD_STRIKE_EVALUATION_METRIC = "bird_strike_score"
_CANDIDATE_EXEC_TIMEOUT_SEC = 30


@contextmanager
def _candidate_timeout_guard(seconds: int = _CANDIDATE_EXEC_TIMEOUT_SEC) -> Iterator[None]:
  """Bounds untrusted candidate exec()/evaluate() execution via SIGALRM."""
  if not hasattr(signal, "SIGALRM") or threading.current_thread() is not threading.main_thread():
    yield
    return

  def _handle_alarm(signum: int, frame: Any) -> None:
    del signum, frame
    raise TimeoutError(f"Candidate design generation timed out after {seconds}s.")

  prev_handler = signal.signal(signal.SIGALRM, _handle_alarm)
  signal.alarm(seconds)
  try:
    yield
  finally:
    signal.alarm(0)
    signal.signal(signal.SIGALRM, prev_handler)


def _load_initial_program() -> str:
  with open(os.path.join(os.path.dirname(__file__), "main.py"), "r") as f:
    return f.read()


INITIAL_PROGRAM_CODE = _load_initial_program()


def evaluate(eval_inputs: Mapping[str, Any] | None = None) -> dict[str, Any]:
  """Scoring interface invoked by the AlphaEvolve Cloud Batch worker harness."""
  del eval_inputs  # Unused
  try:
    eval_cfg = EvaluatorConfig.from_env()
    program_name = eval_cfg.candidate_program_id
    candidate_dir = eval_cfg.candidate_dir
  except ValueError:
    program_name = ""
    candidate_dir = ""

  if not program_name:
    metadata_path = "program_candidate_data.json"
    try:
      with open(metadata_path, "r") as f:
        metadata = json.load(f)
      raw_program_name = metadata.get("name", "unknown")
      program_name = raw_program_name.split("/")[-1]
      logger.info(
          "Parsed program ID: %s (from raw name: %s)",
          program_name,
          raw_program_name,
      )
    except Exception as e:  # pylint: disable=broad-exception-caught
      logger.warning("Failed to read metadata: %s", e)
      program_name = "local_eval_run"

  logger.info("STARTING OPENRADIOSS WING BIRD-STRIKE EVALUATION: %s", program_name)

  main_py_path = "main.py"
  try:
    with open(main_py_path, "r") as f:
      code = f.read()
  except Exception as e:
    logger.error("Failed to read main.py: %s", e)
    raise e

  score_value: float | None = None
  insights_list: list[AlphaEvolveEvaluationInsight] = []
  run_dir = Path(f"ae_radioss_runs/{program_name}")

  try:
    exec_namespace: dict[str, Any] = {
        "np": np,
        "math": math,
        "Any": Any,
        "Mapping": Mapping,
        "BOUNDS": BOUNDS,
    }
    with _candidate_timeout_guard():
      exec(code, exec_namespace)  # pylint: disable=exec-used

    eval_func = exec_namespace.get("evaluate")
    if not callable(eval_func):
      insights_list.append(
          AlphaEvolveEvaluationInsight(
              label="Invalid Program Structure",
              text=(
                  "The candidate program is missing a callable 'evaluate'"
                  " function."
              ),
          )
      )
    else:
      with _candidate_timeout_guard():
        candidate_design = eval_func()
      sim_result = run_openradioss_simulation(
          raw_design=candidate_design,
          run_dir=run_dir,
          num_threads=4,
          cleanup=True,
      )

      raw_score = sim_result.get("score")
      if raw_score is not None and math.isfinite(raw_score):
        score_value = float(raw_score)
        m = sim_result["metrics"]
        sb = sim_result["score_breakdown"]
        part_strains = sim_result.get("part_strain_summary", {})

        logger.info(
            "OpenRADIOSS bird-strike evaluation succeeded for %s: score=%.2f,"
            " mass=%.3f kg, F_spar_peak=%.2f kN, spar_dx=%.2f mm,"
            " max_bird_x=%.1f mm, eroded=%.2f%%",
            program_name,
            score_value,
            m["structural_mass_kg"],
            m["peak_spar_force_kN"],
            m["max_spar_deflection_mm"],
            m["max_bird_x_mm"],
            m["eroded_shell_mass_pct"],
        )

        insights_list.append(
            AlphaEvolveEvaluationInsight(
                label="OpenRADIOSS Wing Bird-Strike Summary",
                text=(
                    f"bird_strike_score={score_value:.2f} | "
                    f"structural_mass={m['structural_mass_kg']:.3f} kg | "
                    f"peak_spar_force={m['peak_spar_force_kN']:.2f} kN "
                    f"(limit <= {SPAR_PEAK_FORCE_LIMIT_KN:.0f} kN) | "
                    f"spar_CFE={m['spar_cfe']:.3f} | "
                    f"max_spar_deflection={m['max_spar_deflection_mm']:.2f} mm "
                    f"(limit <= {SPAR_DEFLECTION_LIMIT_MM:.1f} mm) | "
                    f"max_bird_x={m['max_bird_x_mm']:.1f} mm "
                    f"(breach limit <= {BIRD_BREACH_X_LIMIT_MM:.0f} mm) | "
                    f"eroded_shell_mass={m['eroded_shell_mass_pct']:.2f}% "
                    f"(limit <= {MAX_ERODED_SHELL_PCT:.1f}%) | "
                    f"max_strain_ratio={m['max_strain_to_limit_ratio']:.2f} "
                    f"(limit <= 1.00)."
                ),
            )
        )
        insights_list.append(
            AlphaEvolveEvaluationInsight(
                label="Score & Penalty Breakdown",
                text=(
                    f"mass_term={sb['mass_term']:.2f}, "
                    f"load_efficiency_term=+{sb['load_efficiency_term']:.2f}, "
                    f"spar_force_penalty=-{sb['spar_force_penalty']:.2f}, "
                    f"spar_deflection_penalty=-{sb['spar_deflection_penalty']:.2f}, "
                    f"spar_breach_penalty=-{sb['spar_breach_penalty']:.2f}, "
                    f"rupture_penalty=-{sb['rupture_penalty']:.2f}, "
                    f"strain_penalty=-{sb['strain_penalty']:.2f}."
                ),
            )
        )
        insights_list.append(
            AlphaEvolveEvaluationInsight(
                label="Component Energy Partition (J)",
                text=(
                    f"nose_cap_IE={m['nose_cap_ie_max_J']:.1f} J, "
                    f"upper_lower_skins_IE={m['skins_ie_max_J']:.1f} J, "
                    f"splitter_wedge_IE={m['splitter_ie_max_J']:.1f} J, "
                    f"chordwise_ribs_IE={m['ribs_ie_max_J']:.1f} J, "
                    f"front_spar_IE={m['spar_ie_max_J']:.1f} J."
                ),
            )
        )
        if part_strains:
          strain_str = ", ".join(
              f"{k}: max_eps={v['max_eps']:.3f}/{PART_STRAIN_LIMITS.get(k, 0.11):.3f}"
              f" ({v['plastic_elem_pct']:.0f}% plastic)"
              for k, v in part_strains.items()
          )
          insights_list.append(
              AlphaEvolveEvaluationInsight(
                  label="Per-Part Plastic Strain vs 50% Safety Limit",
                  text=strain_str,
              )
          )

        try:
          if candidate_dir:
            out_json_path = Path(candidate_dir) / f"{program_name}.json"
            with open(out_json_path, "w") as jf:
              json.dump(
                  {
                      "program_name": program_name,
                      "score": score_value,
                      "metrics": m,
                      "score_breakdown": sb,
                      "params": sim_result["params"],
                      "time_history": sim_result["time_history"],
                      "part_strain_summary": part_strains,
                      "sampled_polys": sim_result.get("sampled_polys", []),
                      "bird_pts_xz": sim_result.get("bird_pts_xz", []),
                  },
                  jf,
                  indent=2,
              )
            logger.info("Saved simulation artifact to %s", out_json_path)
        except Exception as save_exc:  # pylint: disable=broad-exception-caught
          logger.warning("Failed to save candidate JSON to GCS: %s", save_exc)
      else:
        score_value = None
        insights_list.append(
            AlphaEvolveEvaluationInsight(
                label="Invalid Simulation Score",
                text="OpenRADIOSS simulation returned a non-finite score.",
            )
        )

  except Exception as e:  # pylint: disable=broad-exception-caught
    error_message = (
        f"The program failed during OpenRADIOSS evaluation with error: {e}"
    )
    logger.exception(error_message)
    score_value = None
    insights_list.append(
        AlphaEvolveEvaluationInsight(label="Runtime Error", text=error_message)
    )
  finally:
    if run_dir.exists():
      shutil.rmtree(run_dir, ignore_errors=True)

  scores = [
      AlphaEvolveEvaluationScore(
          metric=BIRD_STRIKE_EVALUATION_METRIC, score=score_value
      )
  ]
  if insights_list:
    program_evaluation = AlphaEvolveProgramEvaluation(
        scores=AlphaEvolveEvaluationScores(scores=scores),
        insights=AlphaEvolveEvaluationInsights(insights=insights_list),
    )
  else:
    program_evaluation = AlphaEvolveProgramEvaluation(
        scores=AlphaEvolveEvaluationScores(scores=scores)
    )
  return program_evaluation.model_dump()


bird_strike_evaluation = evaluate


def _get_default_bird_strike_baseline() -> dict[str, Any]:
  """Returns baseline seed telemetry for controller-side postprocessing."""
  baseline_json = Path(__file__).resolve().parent / "models" / "baseline_result.json"
  if baseline_json.is_file():
    try:
      return json.loads(baseline_json.read_text(encoding="utf-8"))
    except Exception:  # pylint: disable=broad-exception-caught
      pass
  t_ms = [round(0.15 * i, 2) for i in range(21)]
  spar_f = [
      round(255.16 * math.exp(-((t - 1.35) / 0.55) ** 2), 2) for t in t_ms
  ]
  spar_dx = [
      round(0.97 * math.exp(-((t - 1.45) / 0.65) ** 2), 3) for t in t_ms
  ]
  return {
      "program_name": "seed_baseline",
      "score": 22.57,
      "metrics": {
          "structural_mass_kg": 4.621,
          "peak_spar_force_kN": 255.16,
          "spar_cfe": 0.300,
          "max_spar_deflection_mm": 0.97,
          "max_bird_x_mm": 77.69,
          "eroded_shell_mass_pct": 0.0,
          "max_strain_to_limit_ratio": 0.673,
          "nose_cap_ie_max_J": 612.0,
          "skins_ie_max_J": 3560.0,
          "splitter_ie_max_J": 174.1,
          "ribs_ie_max_J": 180.2,
          "spar_ie_max_J": 8.2,
      },
      "params": {
          "t_nose_cap": 2.0,
          "t_upper_skin": 1.8,
          "t_lower_skin": 1.8,
          "t_splitter_upper": 1.8,
          "t_splitter_lower": 1.8,
          "t_ribs": 1.5,
          "t_front_spar": 2.5,
          "splitter_apex_x_shift_mm": 0.0,
          "splitter_apex_span_curve_mm": 0.0,
          "splitter_camber_mm": 0.0,
          "splitter_rear_z_scale": 1.0,
          "inner_rib_y_shift_mm": 0.0,
          "rib_corrugation_amp_mm": 0.0,
      },
      "time_history": {
          "time_ms": t_ms,
          "spar_force_kN": spar_f,
          "spar_deflection_mm": spar_dx,
      },
      "sampled_polys": [],
      "bird_pts_xz": [],
  }


def postprocess(top_programs: Sequence[Any]) -> None:
  """Exports best wing design summary and comparison plots to Cloud Storage."""
  try:
    ctrl_cfg = ControllerConfig.from_env()
    bucket_name = ctrl_cfg.bucket_name.strip()
    user_exp = ctrl_cfg.user_experiment_name.strip()
    programs_dir = ctrl_cfg.programs_dir
    deployment_name = ctrl_cfg.deployment_name
  except ValueError:
    try:
      base_cfg = BaseExperimentConfig.from_env()
      bucket_name = base_cfg.bucket_name.strip()
      user_exp = base_cfg.user_experiment_name.strip()
      programs_dir = base_cfg.programs_dir
    except ValueError:
      bucket_name = ""
      user_exp = "bird-strike"
      programs_dir = "program_candidates"
    deployment_name = "alpha-evolve"

  def _extract_prog_info(prog: Any) -> tuple[str, float | None]:
    raw_name = (
        prog.get("name", "")
        if isinstance(prog, Mapping)
        else getattr(prog, "name", "")
    )
    prog_id = str(raw_name).split("/")[-1] if raw_name else ""
    score_val: float | None = None
    scores_obj = (
        prog.get("evaluation", {}).get("scores", {}).get("scores", [])
        if isinstance(prog, Mapping)
        else getattr(
            getattr(getattr(prog, "evaluation", None), "scores", None),
            "scores",
            [],
        )
    )
    for entry in scores_obj or []:
      val = (
          entry.get("score")
          if isinstance(entry, Mapping)
          else getattr(entry, "score", None)
      )
      if val is not None:
        score_val = float(val)
        break
    return prog_id, score_val

  def _load_worker_json(prog_id: str) -> dict[str, Any] | None:
    if not prog_id or not bucket_name:
      return None
    job_prefix = f"{deployment_name}-workers"
    job_id = get_job_id_from_program_name(prog_id, job_prefix)
    for prefix in (
        f"{user_exp}/{programs_dir}",
        f"{user_exp}/archive/{programs_dir}",
    ):
      raw = read_file_from_gcs(bucket_name, f"{prefix}/{job_id}/{prog_id}.json")
      if isinstance(raw, str):
        try:
          raw = json.loads(raw)
        except json.JSONDecodeError:
          raw = None
      if isinstance(raw, dict) and "metrics" in raw and "time_history" in raw:
        return raw
    return None

  baseline_data = _get_default_bird_strike_baseline()
  best_data: dict[str, Any] | None = None
  best_score: float | None = None
  best_prog_id = ""
  if top_programs:
    best_prog_id, best_score = _extract_prog_info(top_programs[0])
    for prog in top_programs:
      prog_id, prog_score = _extract_prog_info(prog)
      cand_data = _load_worker_json(prog_id)
      if cand_data is not None:
        best_prog_id = prog_id
        best_score = prog_score
        best_data = cand_data
        break
    if best_prog_id:
      logger.info(
          "BEST BIRD STRIKE WING PROGRAM FOUND: %s (Score: %s)",
          best_prog_id,
          best_score,
      )

  if best_data is None:
    best_data = dict(baseline_data)
    if best_score is not None and math.isfinite(float(best_score)):
      best_data["score"] = float(best_score)

  results_env = os.getenv("_RESULTS_DIR")
  output_dir = Path(results_env) if results_env else Path("/tmp/results")
  output_dir.mkdir(parents=True, exist_ok=True)
  summary_path = output_dir / "best_wing_design.json"
  summary_path.write_text(json.dumps(best_data, indent=2), encoding="utf-8")

  saved_plots = plot_bird_strike_comparison(baseline_data, best_data, output_dir)
  if bucket_name:
    for fpath in [summary_path, *saved_plots]:
      if fpath.exists():
        upload_to_gcs(fpath, bucket_name, f"{user_exp}/results/{fpath.name}")


if __name__ == "__main__":
  print(json.dumps(evaluate(), indent=2))
