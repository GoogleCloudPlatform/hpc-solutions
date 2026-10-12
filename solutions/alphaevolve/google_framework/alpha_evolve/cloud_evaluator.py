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

"""Cloud evaluator worker for circle packing, using Pub/Sub and Cloud Batch."""

import importlib
import json
import logging
import os
import sys
import time
import traceback

from typing import Any, Callable, Dict, Optional

from .config import EvaluatorConfig

from .utils import (
    get_program_candidate_result_path,
    sanitize_evaluation_scores,
)


logger = logging.getLogger(__name__)


def run_worker(evaluator: Callable[[], Dict[str, Any]], evaluator_config: Optional[EvaluatorConfig] = None):
  """Pulls an evaluation task, runs the evaluation, and stores the results.

  This function checks for a program candidate data file based on the job_id,
  loads the program, executes the evaluation callable,
  and writes the results back to a JSON file in the same job-specific directory.

  Args:
    evaluator: A callable that returns an evaluation dictionary
    evaluator_config: Configuration for the evaluator
  """
  logger.info("Worker started.")

  evaluator_config = evaluator_config or EvaluatorConfig.from_env()

  program_dir = evaluator_config.candidate_dir
  program_candidate_file_path = os.path.join(program_dir, "program_candidate_data.json")

  logger.info("Attempting to pull candidate program from %s...", program_candidate_file_path)

  if os.path.exists(program_candidate_file_path):
    with open(program_candidate_file_path, "r") as f:
      program_candidate = json.load(f)
    
    program_name = program_candidate.get("name", "unknown")
    logger.info("Successfully loaded program_candidate: %s (ID: %s)", program_name, evaluator_config.candidate_program_id)

    logger.info("Evaluating program %s...", evaluator_config.candidate_program_id)

    try:
      # RUN THE ACTUAL EVALUATION
      start_eval = time.time()
      evaluation = evaluator()
      eval_time = time.time() - start_eval

      logger.info("Evaling done for %s: %s", evaluator_config.candidate_program_id, evaluation)
      evaluation = sanitize_evaluation_scores(evaluation)

      valid_keys = {"scores", "insights"}
      submission_eval = {
          k: v for k, v in evaluation.items() if k in valid_keys
      }
    except Exception as e:
      eval_time = time.time() - start_eval if 'start_eval' in locals() else 0.0
      tb = traceback.format_exc()
      logger.exception("Exception during evaluation of program %s", evaluator_config.candidate_program_id)
      submission_eval = {
          "scores": {"scores": []},
          "insights": {
              "insights": [
                  {
                      "label": "RUNTIME_ERROR",
                      "text": f"Evaluation exception: {e}\n{tb}",
                  }
              ]
          },
      }

    # Prepare result payload
    payload = {
        "name": program_name,
        "lockToken": program_candidate["lockToken"],
        "evaluation": submission_eval,
        "eval_time": eval_time,
        "task_index": evaluator_config.job_id
    }
    result_file_path = get_program_candidate_result_path(program_dir)

    with open(result_file_path, "w") as f:
      json.dump(payload, f, indent=4)
    logger.info(
        "Result for %s successfully written to %s.",
        evaluator_config.candidate_program_id,
        result_file_path,
    )
  
  else:
    logger.info("No program candidate file found at %s.", program_candidate_file_path)
  
  logger.info("Worker finished.")


if __name__ == "__main__":
  evaluator_config = EvaluatorConfig.from_env()

  # Set up logging
  logging.basicConfig(
      level=logging.INFO,
      format=f"[Evaluator {evaluator_config.job_id}] %(levelname)s: %(message)s",
      stream=sys.stdout,
  )

  if not evaluator_config.client_evaluator_script or not evaluator_config.client_evaluator_method:
    logger.error("Missing _CLIENT_EVALUATOR_SCRIPT or _CLIENT_EVALUATOR_METHOD. Worker exiting.")
    sys.exit(1)

  evaluator_name = evaluator_config.client_evaluator_method

  try:
    # Add current directory to path to help find the module
    script_path = evaluator_config.client_evaluator_script
    module_dir = os.path.dirname(script_path)
    module_name = os.path.splitext(os.path.basename(script_path))[0]
    
    if module_dir:
      sys.path.insert(0, module_dir)
    else:
      sys.path.insert(0, os.getcwd())
      
    # Add /app/src to path if it exists (container environment)
    container_project_root = "/app/src"
    if os.path.exists(container_project_root) and container_project_root not in sys.path:
        sys.path.append(container_project_root)
        
    module = importlib.import_module(module_name)
    evaluator = getattr(module, evaluator_name)
    logger.info("Successfully loaded evaluator '%s' from module '%s'", evaluator_name, module_name)
  except ImportError:
    logger.exception("Failed to import module '%s'", module_name)
    sys.exit(1)
  except AttributeError:
    logger.exception("Failed to find evaluator function '%s' in module '%s'", evaluator_name, module_name)
    sys.exit(1)

  run_worker(evaluator, evaluator_config)

