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

import json
import logging
import os
from pathlib import Path
import shutil
import signal
import sys
from typing import Any, Mapping

from alpha_evolve.models import (
    AlphaEvolveEvaluationInsight,
    AlphaEvolveEvaluationInsights,
    AlphaEvolveEvaluationScore,
    AlphaEvolveEvaluationScores,
    AlphaEvolveProgramEvaluation,
)
import numpy as np

# Import the local Parameters from the copied src
from src.optimization.optimization import Parameters

logger = logging.getLogger(__name__)

AIRFOIL_EVALUATION_METRIC = "lift_to_drag_ratio"


class TimeoutError(Exception):
  pass


def handler(signum, frame):
  raise TimeoutError("Evaluation timed out")


signal.signal(signal.SIGALRM, handler)


def _load_initial_program():
  with open(os.path.join(os.path.dirname(__file__), "main.py"), "r") as f:
    return f.read()


INITIAL_PROGRAM_CODE = _load_initial_program()


def evaluate(eval_inputs: Mapping[str, Any] = None, timeout_seconds=300) -> dict:
  """Scoring interface called by the AlphaEvolve worker harness.

  This function loads the candidate 'main.py', runs its 'evaluate' method
  (which runs the evolved optimize_airfoil loop with a budget of 10
  simulations),
  and handles all scoring, logging, errors, and physical file cleanups.
  """
  # Read candidate metadata to get a unique ID (program_name)
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
  except Exception as e:
    logger.warning("Failed to read metadata: %s", e)
    program_name = "unknown"

  logger.info("STARTING EVALUATION: %s", program_name)

  main_py_path = "main.py"
  try:
    with open(main_py_path, "r") as f:
      code = f.read()
  except Exception as e:
    logger.error("Failed to read main.py: %s", e)
    raise e

  score_value: float | None = None
  insights_list: list[AlphaEvolveEvaluationInsight] = []

  # Configure isolated directories for this worker to prevent parallel crashes
  cases_folder = Path(f"ae_cases/{program_name}")
  csv_path = Path(f"ae_results/{program_name}.csv")

  run_parameters = Parameters(
      run_name=f"ae_{program_name}",
      cases_folder=cases_folder,
      is_debug=True,
      csv_path=csv_path,
      fluid_velocity=np.array(
          [99.6194698092, 8.7155742748, 0]
      ),  # 5-degree baseline
  )

  try:
    signal.alarm(timeout_seconds)
    # Execute candidate's code to load evaluate function
    exec_namespace = {
        "np": np,
        "Parameters": Parameters,
        "sys": sys,
        "Path": Path,
    }
    exec(code, exec_namespace)

    evaluate_func = exec_namespace.get("evaluate")

    if callable(evaluate_func):
      # Run the candidate's evaluate loop
      best_x, best_score = evaluate_func(
          run_parameters,
          max_successful_simulations=10,
          max_total_simulations=100,
      )

      # best_score is -abs(cl/cd). AlphaEvolve maximizes, so we return positive Cl/Cd.
      if (
          best_score != float("inf")
          and best_score != float("-inf")
          and best_score is not None
      ):
        score_value = float(-best_score)  # convert to positive Cl/Cd
        logger.info(f"Evaluation successful: Best Cl/Cd = {score_value}")

        # Save the best CST parameters to GCS share path for polar plotting post-experiment
        try:
          candidate_dir = os.environ.get("_CANDIDATE_DIR", ".")
          if candidate_dir:
            gcs_best_x_path = Path(candidate_dir) / f"{program_name}.json"

            with open(gcs_best_x_path, "w") as f:
              json.dump(
                  {
                      "program_name": program_name,
                      "score": score_value,
                      "best_x": best_x.tolist(),
                  },
                  f,
                  indent=2,
              )
            logger.info(f"Saved best CST parameters to {gcs_best_x_path}")
        except Exception as ex:
          logger.warning(f"Failed to save best CST parameters to GCS: {ex}")
      else:
        score_value = -1e12
        insights_list.append(
            AlphaEvolveEvaluationInsight(
                label="No Valid Airfoil Found",
                text=(
                    "The optimization algorithm failed to find any airfoil that"
                    " converged within the 10-simulation budget."
                ),
            )
        )
    else:
      insights_list.append(
          AlphaEvolveEvaluationInsight(
              label="Invalid Program Structure",
              text="The program is missing a callable 'evaluate' function.",
          )
      )

  except TimeoutError:
    error_message = (
        f"The program evaluation exceeded the time limit of "
        f"{timeout_seconds} seconds and was terminated.")
    logger.error(error_message)
    insights_list.append(
        AlphaEvolveEvaluationInsight(label="Execution Timeout", text=error_message))
  except Exception as e:
    error_message = (
        f"The program failed during execution with the following error: {e}"
    )
    logger.exception(error_message)
    insights_list.append(
        AlphaEvolveEvaluationInsight(label="Runtime Error", text=error_message)
    )
  finally:
    signal.alarm(0)
    # CLEANUP: Keep VM/host disk clean by removing intermediate case directories
    if cases_folder.exists():
      logger.info(f"Cleaning up cases directory: {cases_folder}")
      shutil.rmtree(cases_folder)
    if csv_path.exists():
      csv_path.unlink()

  if score_value is not None and (
      score_value == float("inf") or score_value == float("-inf")
  ):
    score_value = -1e12

  scores = [
      AlphaEvolveEvaluationScore(
          metric=AIRFOIL_EVALUATION_METRIC, score=score_value
      )
  ]

  if insights_list:
    insights = AlphaEvolveEvaluationInsights(insights=insights_list)
    program_evaluation = AlphaEvolveProgramEvaluation(
        scores=AlphaEvolveEvaluationScores(scores=scores), insights=insights
    )
  else:
    program_evaluation = AlphaEvolveProgramEvaluation(
        scores=AlphaEvolveEvaluationScores(scores=scores)
    )

  return program_evaluation.model_dump()

from alpha_evolve.visualization import get_score
from alpha_evolve.utils import get_job_id_from_program_name, read_file_from_gcs
from src.kulfan_converter.kulfan_to_coord import CST_shape


def upload_to_gcs(local_path: Path, bucket_name: str, blob_name: str):
  """Uploads a local file to a GCS bucket."""
  try:
    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(str(local_path))
    logger.info(f"Successfully uploaded {local_path.name} to GCS: gs://{bucket_name}/{blob_name}")
  except Exception as e:
    logger.warning(f"Failed to upload {local_path.name} to GCS: {e}")


def visualize_airfoil(x: np.ndarray, title: str, output_path: Path):
  """Reconstructs and plots the airfoil geometry from CST parameters and saves to output_path."""
  import matplotlib.pyplot as plt
  wu = x[0:3]
  wl = x[3:6]
  dz = 0
  N = 50

  try:
    airfoil_CST = CST_shape(wl, wu, dz, N)
    coords = airfoil_CST.airfoil_coor()

    plt.figure(figsize=(10, 4))
    plt.plot(coords[:, 0], coords[:, 1], "b-", linewidth=2, label="Airfoil Profile")
    plt.fill(coords[:, 0], coords[:, 1], "cyan", alpha=0.2)
    plt.title(title, fontsize=14, pad=15)
    plt.xlabel("x/c", fontsize=12)
    plt.ylabel("y/c", fontsize=12)
    plt.axis("equal")
    plt.grid(True, linestyle="--", alpha=0.7)
    plt.legend()
    plt.savefig(output_path, dpi=300)
    plt.close()
    logger.info(f"Saved airfoil visualization to {output_path}")
  except Exception as e:
    logger.warning(f"Failed to visualize airfoil: {e}")


def run_polar_sweep_and_plot(best_x: np.ndarray, output_dir: Path):
  """Runs an AoA sweep for the best airfoil geometry and plots performance curves."""
  import matplotlib.pyplot as plt
  import pandas as pd
  from src.optimization.optimization import funct, Parameters

  logger.info("\n=========================================\nSTARTING POLAR SWEEP FOR BEST AIRFOIL PROFILE\n=========================================")
  aoas = [0.0, 2.5, 5.0, 7.5, 10.0, 12.5, 15.0]
  sweep_cases_dir = Path("polar_sweep_cases")
  if sweep_cases_dir.exists():
    shutil.rmtree(sweep_cases_dir)
  sweep_cases_dir.mkdir(parents=True, exist_ok=True)

  temp_csv_path = output_dir / "polar_sweep_raw.csv"
  if temp_csv_path.exists():
    temp_csv_path.unlink()

  for aoa in aoas:
    alpha_rad = np.radians(aoa)
    velocity = np.array([100.0 * np.cos(alpha_rad), 100.0 * np.sin(alpha_rad), 0.0])
    run_params = Parameters(
        run_name=f"polar_aoa_{aoa}",
        cases_folder=sweep_cases_dir,
        is_debug=True,
        csv_path=temp_csv_path,
        fluid_velocity=velocity,
    )
    try:
      score = funct(best_x, run_params)
      logger.info(f"    Finished AoA = {aoa} with score: {score}")
    except Exception as e:
      logger.warning(f"    ERROR evaluating AoA = {aoa}: {e}")

  if temp_csv_path.exists():
    try:
      df = pd.read_csv(temp_csv_path)
      df["aoa"] = df["run_name"].apply(lambda name: float(name.split("_")[-1]))
      df = df.sort_values(by="aoa")
      df["failed"] = ~(df[["no_clipping", "block_mesh", "check_mesh", "simple"]]).all(axis=1)
      df_valid = df[df["failed"] == False].copy()

      if not df_valid.empty:
        df_valid["cl_cd"] = df_valid["cl"] / df_valid["cd"]
        df_valid.to_csv(output_dir / "polar_sweep_results.csv", index=False)

        # Plot 1: Lift and Drag vs AoA
        fig, ax1 = plt.subplots(figsize=(10, 6))
        color = "tab:blue"
        ax1.set_xlabel("Angle of Attack (degrees)", fontsize=12)
        ax1.set_ylabel("Lift Coefficient ($C_l$)", color=color, fontsize=12)
        line1 = ax1.plot(df_valid["aoa"], df_valid["cl"], "o-", color=color, linewidth=2, label="Lift ($C_l$)")
        ax1.tick_params(axis="y", labelcolor=color)
        ax1.grid(True, linestyle="--", alpha=0.5)

        ax2 = ax1.twinx()
        color = "tab:red"
        ax2.set_ylabel("Drag Coefficient ($C_d$)", color=color, fontsize=12)
        line2 = ax2.plot(df_valid["aoa"], df_valid["cd"], "s--", color=color, linewidth=2, label="Drag ($C_d$)")
        ax2.tick_params(axis="y", labelcolor=color)

        lines = line1 + line2
        labels = [l.get_label() for l in lines]
        ax1.legend(lines, labels, loc="upper left")
        plt.title("Aerodynamic Coefficients vs. Angle of Attack", fontsize=14, pad=15)
        fig.tight_layout()
        plot_coef_path = output_dir / "polar_coefficients.png"
        plt.savefig(plot_coef_path, dpi=300)
        plt.close()

        # Plot 2: Lift-to-Drag vs AoA
        plt.figure(figsize=(10, 6))
        plt.plot(df_valid["aoa"], df_valid["cl_cd"], "D-g", linewidth=2.5, label="Lift-to-Drag ($C_l/C_d$)")
        plt.title("Lift-to-Drag Ratio vs. Angle of Attack", fontsize=14, pad=15)
        plt.xlabel("Angle of Attack (degrees)", fontsize=12)
        plt.ylabel("Lift-to-Drag Ratio ($C_l/C_d$)", fontsize=12)
        plt.grid(True, linestyle="--", alpha=0.7)
        plt.legend()
        plot_ld_path = output_dir / "polar_lift_drag.png"
        plt.savefig(plot_ld_path, dpi=300)
        plt.close()
    except Exception as e:
      logger.warning(f"Failed to read and plot sweep results: {e}")
    finally:
      if temp_csv_path.exists():
        temp_csv_path.unlink()

  if sweep_cases_dir.exists():
    shutil.rmtree(sweep_cases_dir)


def postprocess(top_programs):
  """Postprocessing hook for airfoil optimization."""
  bucket_name = os.getenv("_CLOUD_BUCKET_NAME", "")
  user_exp = os.getenv("_USER_EXPERIMENT_NAME", "airfoil-optimization")
  if not top_programs or not bucket_name:
    return

  best_prog = top_programs[0]
  best_prog_id = best_prog.get("name", "").split("/")[-1]
  best_score = get_score(best_prog, AIRFOIL_EVALUATION_METRIC)

  logger.info(f"BEST PROGRAM FOUND: {best_prog_id} (Score: {best_score})")
  programs_dir = os.getenv("_PROGRAMS_DIR", "program_candidates")
  deployment_name = os.getenv("_DEPLOYMENT_NAME", "alpha-evolve")
  job_prefix = f"{deployment_name}-workers"
  job_id = get_job_id_from_program_name(best_prog_id, job_prefix)
  blob_name = f"{user_exp}/{programs_dir}/{job_id}/{best_prog_id}.json"

  best_x_data = read_file_from_gcs(bucket_name, blob_name)
  if not best_x_data:
    blob_name = f"{user_exp}/archive/{programs_dir}/{job_id}/{best_prog_id}.json"
    best_x_data = read_file_from_gcs(bucket_name, blob_name)

  if best_x_data:
    best_x = np.array(best_x_data["best_x"])
    output_dir = Path("results")
    output_dir.mkdir(parents=True, exist_ok=True)
    visualize_path = output_dir / "optimized_airfoil.png"
    visualize_airfoil(best_x, f"Optimized Airfoil Profile (Lift-to-Drag Score: {best_score:.2f})", visualize_path)
    upload_to_gcs(visualize_path, bucket_name, f"{user_exp}/results/{visualize_path.name}")
    run_polar_sweep_and_plot(best_x, output_dir)
    for fname in ["polar_coefficients.png", "polar_lift_drag.png", "polar_sweep_results.csv"]:
      fpath = output_dir / fname
      if fpath.exists():
        upload_to_gcs(fpath, bucket_name, f"{user_exp}/results/{fname}")
