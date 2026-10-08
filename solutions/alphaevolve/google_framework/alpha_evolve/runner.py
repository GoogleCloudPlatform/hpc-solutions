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
"""Universal platform runner for AlphaEvolve controller and evaluator executions."""

import argparse
import asyncio
import importlib
import inspect
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
from typing import Any, Dict, List, Optional, Tuple

# Ensure package root is in sys.path when executed directly as a binary entrypoint.
_pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _pkg_root not in sys.path:
    sys.path.insert(0, _pkg_root)
import yaml

try:
    import nest_asyncio
except ImportError:
    nest_asyncio = None

from alpha_evolve.cloud_evaluator import run_worker
from alpha_evolve.config import ControllerConfig, EvaluatorConfig, BaseExperimentConfig
from alpha_evolve.controller import AlphaEvolveController
from alpha_evolve.models import parse_models_from_env
from alpha_evolve.utils import sanitize_evaluation_scores
from alpha_evolve.visualization import get_score

logger = logging.getLogger(__name__)


def locate_experiment_dir(base_config: BaseExperimentConfig, override_dir: Optional[str] = None) -> Path:
    """Locates the experiment directory based on CLI args, standard container mount, or current directory."""
    candidates = []

    # 1. Explicit override passed as argument
    if override_dir:
        candidates.append(Path(override_dir))

    # 2. Standard container experiment GCS mount path
    candidates.append(Path("/app/experiment"))

    # 3. Canonical GCS Direct Mount in Cloud Batch: /mnt/disks/share/<user_experiment_name>/source
    mount_path = base_config.mount_path
    user_exp = base_config.user_experiment_name
    if user_exp:
        candidates.append(Path(mount_path) / user_exp / "source")

    # 4. Current working directory (for local testing)
    candidates.append(Path.cwd())

    for candidate in candidates:
        if (candidate / "experiment.yaml").is_file():
            logger.info("Found experiment.yaml at: %s", candidate)
            return candidate.resolve()

    searched_paths = "\n - ".join(str(c / "experiment.yaml") for c in candidates)
    raise FileNotFoundError(
        f"Could not find 'experiment.yaml'. Searched locations:\n - {searched_paths}"
    )


def load_experiment_spec(exp_dir: Path) -> Dict[str, Any]:
    """Loads and strictly validates experiment.yaml from the experiment directory."""
    config_path = exp_dir / "experiment.yaml"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            spec = yaml.safe_load(f)
    except Exception as e:
        raise ValueError(f"Failed to parse YAML from {config_path}: {e}") from e

    if not isinstance(spec, dict):
        raise ValueError(f"Invalid experiment.yaml format in {config_path}: Expected a YAML dictionary.")

    # Validation
    required_fields = ["title", "problem_description", "program_language", "primary_metric", "seed_files", "evaluator"]
    missing = [field for field in required_fields if field not in spec]
    if missing:
        raise ValueError(f"Missing required fields in {config_path}: {missing}")

    if not isinstance(spec["seed_files"], list) or not spec["seed_files"]:
        raise ValueError(f"'seed_files' in {config_path} must be a non-empty list of file paths.")

    evaluator_spec = spec["evaluator"]
    if not isinstance(evaluator_spec, dict) or "method" not in evaluator_spec:
        raise ValueError(f"'evaluator' in {config_path} must be a dictionary containing 'method'.")

    return spec


def build_initial_program_payload(exp_dir: Path, spec: Dict[str, Any]) -> Dict[str, Any]:
    """Reads seed files from disk and builds the initial_program payload."""
    files_payload: List[Dict[str, str]] = []
    seed_files = spec["seed_files"]

    for rel_path in seed_files:
        normalized_path = Path(rel_path)
        if normalized_path.is_absolute() or ".." in normalized_path.parts:
            raise ValueError(f"Invalid seed file path '{rel_path}': Path traversal is not allowed.")

        full_path = (exp_dir / normalized_path).resolve()
        if not full_path.is_relative_to(exp_dir):
            raise ValueError(f"Security error: '{rel_path}' resolves outside the experiment directory root.")

        if not full_path.is_file():
            raise FileNotFoundError(f"Seed file not found: {rel_path} (searched at {full_path})")

        try:
            content = full_path.read_text(encoding="utf-8")
        except Exception as e:
            raise IOError(f"Failed to read seed file '{rel_path}': {e}") from e

        files_payload.append({
            "path": rel_path,
            "content": content,
        })

    initial_metric = spec["primary_metric"]
    initial_score = float(spec.get("initial_score", 0.0))

    return {
        "content": {"files": files_payload},
        "evaluation": {
            "scores": {
                "scores": [{"metric": initial_metric, "score": initial_score}]
            }
        },
    }


def build_experiment_config(spec: Dict[str, Any], controller_config: ControllerConfig) -> Dict[str, Any]:
    """Builds the AlphaEvolve experiment configuration from spec and ControllerConfig."""
    models = parse_models_from_env(controller_config.model)

    gen_settings = dict(spec.get("generation_settings", {}))
    if models:
        gen_settings["models"] = models

    exp_config: Dict[str, Any] = {
        "title": spec.get("title", "AlphaEvolve Optimization"),
        "problem_description": spec.get("problem_description", ""),
        "program_language": spec.get("program_language", "python"),
        "run_settings": {
            "max_programs": controller_config.max_programs_generated,
            "concurrency": controller_config.concurrency,
            "max_duration": controller_config.max_duration,
            "idle_timeout": controller_config.idle_timeout,
        },
        "generation_settings": gen_settings,
    }

    if "evolution_settings" in spec and spec["evolution_settings"]:
        exp_config["evolution_settings"] = spec["evolution_settings"]

    return exp_config


def load_evaluator_function(exp_dir: Path, spec: Dict[str, Any]) -> Tuple[Any, Any]:
    """Dynamically imports the evaluator module and returns the specified evaluation function."""
    eval_spec = spec["evaluator"]
    script_name = eval_spec.get("script", "evaluator.py")
    method_name = eval_spec["method"]

    module_name = script_name[:-3] if script_name.endswith(".py") else script_name

    for p in [str(exp_dir), str(exp_dir / "src")]:
        if p not in sys.path:
            sys.path.insert(0, p)

    try:
        eval_module = importlib.import_module(module_name)
    except Exception as e:
        raise ImportError(f"Failed to import evaluator module '{module_name}' from {exp_dir}: {e}") from e

    if not hasattr(eval_module, method_name):
        raise AttributeError(f"Evaluator module '{module_name}' has no function named '{method_name}'.")

    evaluator_func = getattr(eval_module, method_name)
    return evaluator_func, eval_module


def run_controller(experiment_dir: Optional[str] = None):
    """Runs AlphaEvolve in Controller mode."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
        force=True,
    )
    ae_logger = logging.getLogger("alpha_evolve")
    ae_logger.handlers.clear()
    ae_logger.propagate = True

    logger.info("Initializing AlphaEvolve Controller Runner...")

    # 1. Parse and validate ControllerConfig from environment
    controller_config = ControllerConfig.from_env()

    # 2. Locate and parse experiment.yaml
    exp_dir = locate_experiment_dir(controller_config, experiment_dir)
    spec = load_experiment_spec(exp_dir)

    # 3. Build initial program files payload
    initial_program = build_initial_program_payload(exp_dir, spec)

    # 4. Build experiment configuration with ControllerConfig integration
    exp_config = build_experiment_config(spec, controller_config)

    # 5. Dynamically import evaluator function
    evaluator_func, eval_module = load_evaluator_function(exp_dir, spec)

    # 6. Execute Controller evolutionary loop
    if nest_asyncio is not None:
        nest_asyncio.apply()
    controller = AlphaEvolveController(config=controller_config)

    logger.info("Starting AlphaEvolve controller run loop for experiment: %s", exp_config["title"])

    asyncio.run(
        controller.run_loop(
            exp_config=exp_config,
            initial_program=initial_program,
            evaluator_function=evaluator_func,
            num_samplers=controller_config.num_samplers,
        )
    )

    # 7. Report top programs
    primary_metric = spec["primary_metric"]
    try:
        response = controller.list_programs(params={"order_by": "score desc"})
        if response and "alphaEvolvePrograms" in response:
            top_programs = response["alphaEvolvePrograms"]
            top_programs.sort(key=lambda p: get_score(p, primary_metric), reverse=True)

            logger.info("\n=========================================")
            logger.info("TOP CANDIDATE PROGRAMS:")
            logger.info("=========================================")
            for i, prog in enumerate(top_programs[:3]):
                score = get_score(prog, primary_metric)
                logger.info("Rank %d: %s | Score (%s): %s", i + 1, prog.get("name", "Unknown"), primary_metric, score)
            logger.info("=========================================\n")

            # 8. Optional post-processing hook
            if hasattr(eval_module, "postprocess"):
                logger.info("Executing custom postprocess() hook from %s...", eval_module.__name__)
                eval_module.postprocess(top_programs)
    except Exception as e:
        logger.warning("Could not list or postprocess final programs: %s", e)


def run_evaluator(experiment_dir: Optional[str] = None):
    """Runs AlphaEvolve in Cloud Batch Evaluator Worker mode."""
    eval_config = EvaluatorConfig.from_env()
    job_id = eval_config.job_id
    candidate_id = eval_config.candidate_program_id

    # Configure stdout logging and optional GCS file logging
    handlers: List[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if eval_config.mount_path and eval_config.user_experiment_name:
        logs_dir = os.path.join(eval_config.mount_path, eval_config.user_experiment_name, "logs")
        try:
            os.makedirs(logs_dir, exist_ok=True)
            log_file = os.path.join(logs_dir, f"evaluator_{candidate_id}.log")
            handlers.append(logging.FileHandler(log_file, mode="a", encoding="utf-8"))
        except Exception:
            pass

    logging.basicConfig(
        level=logging.INFO,
        format=f"[Evaluator {job_id}] %(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
        force=True,
    )
    ae_logger = logging.getLogger("alpha_evolve")
    ae_logger.handlers.clear()
    ae_logger.propagate = True

    logger.info("Initializing AlphaEvolve Candidate Evaluator...")

    # 1. Locate and load experiment specification
    exp_dir = locate_experiment_dir(eval_config, experiment_dir)
    spec = load_experiment_spec(exp_dir)

    candidate_dir = eval_config.candidate_dir

    # 2. Copy candidate files to the experiment workspace if candidate_dir exists
    if os.path.isdir(candidate_dir):
        logger.info("Copying candidate generated code from %s to %s...", candidate_dir, exp_dir)
        for item in os.listdir(candidate_dir):
            if item != "program_candidate_result.json":
                src_item = os.path.join(candidate_dir, item)
                dst_item = exp_dir / item
                try:
                    if os.path.isdir(src_item):
                        shutil.copytree(src_item, dst_item, dirs_exist_ok=True)
                    else:
                        shutil.copy2(src_item, dst_item)
                except Exception as e:
                    logger.warning("Could not copy candidate file %s: %s", src_item, e)

    # 3. Switch working directory to exp_dir so relative file paths in evaluator and make work
    os.chdir(str(exp_dir))
    if str(exp_dir) not in sys.path:
        sys.path.insert(0, str(exp_dir))

    # 4. If Makefile exists in the experiment directory, compile the code
    makefile = exp_dir / "Makefile"
    if makefile.is_file():
        logger.info("Found Makefile in %s. Running 'make all'...", exp_dir)
        res = subprocess.run(["make", "-C", str(exp_dir), "all"], capture_output=True, text=True)
        if res.returncode != 0:
            logger.error("Build failed in %s:\nSTDOUT: %s\nSTDERR: %s", exp_dir, res.stdout, res.stderr)
            # Write build failure result payload
            program_candidate_file = os.path.join(candidate_dir, "program_candidate_data.json")
            program_name = "unknown"
            lock_token = ""
            if os.path.exists(program_candidate_file):
                with open(program_candidate_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    program_name = data.get("name", "unknown")
                    lock_token = data.get("lockToken", "")

            primary_metric = spec.get("primary_metric", "score")
            payload = {
                "name": program_name,
                "lockToken": lock_token,
                "evaluation": {
                    "scores": {"scores": [{"metric": primary_metric, "score": None}]},
                    "insights": {
                        "insights": [
                            {
                                "label": "COMPILATION_ERROR",
                                "text": f"Build failed with return code {res.returncode}:\n{res.stdout}\n{res.stderr}",
                            }
                        ]
                    },
                },
                "eval_time": 0.0,
                "task_index": job_id,
            }
            result_file_path = os.path.join(candidate_dir, "program_candidate_result.json")
            with open(result_file_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=4)
            logger.info("Compilation error written to %s. Worker exiting.", result_file_path)
            return

    # 5. Dynamically import evaluator function
    evaluator_func, _ = load_evaluator_function(exp_dir, spec)

    # 6. Execute evaluation via standard cloud_evaluator worker
    run_worker(evaluator_func, eval_config)


def main():
    """Main universal entrypoint for AlphaEvolve runner."""
    parser = argparse.ArgumentParser(description="AlphaEvolve Universal Platform Runner")
    parser.add_argument(
        "--mode",
        choices=["controller", "evaluator", "auto"],
        default="auto",
        help="Execution mode (default: auto-detected from environment)",
    )
    parser.add_argument(
        "experiment_dir",
        nargs="?",
        default=None,
        help="Path to experiment directory",
    )
    args, _ = parser.parse_known_args()

    mode = args.mode
    if mode == "auto":
        # Auto-detect mode: if candidate directory or candidate program ID is set in environment, run as evaluator
        if os.getenv("_CANDIDATE_DIR") or os.getenv("_CANDIDATE_PROGRAM_ID") or (os.getenv("_JOB_ID") and os.getenv("_JOB_ID") != "0"):
            mode = "evaluator"
        else:
            mode = "controller"

    if mode == "evaluator":
        run_evaluator(args.experiment_dir)
    else:
        run_controller(args.experiment_dir)


if __name__ == "__main__":
    main()
