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
"""Unit tests for AlphaEvolve Universal Runner (alpha_evolve.runner)."""

import json
import os
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import alpha_evolve.cloud_evaluator as cloud_evaluator
from alpha_evolve.config import BaseExperimentConfig, ControllerConfig
from alpha_evolve.runner import (
    locate_experiment_dir,
    load_experiment_spec,
    build_initial_program_payload,
    build_experiment_config,
    load_evaluator_function,
    run_controller,
    run_evaluator,
    main as runner_main,
)


@pytest.fixture
def sample_exp_dir(tmp_path):
    """Creates a mock experiment directory with experiment.yaml and seed files."""
    exp_yaml = """
title: "Test Experiment"
problem_description: "Optimize an algorithm in Python"
program_language: "python"
primary_metric: "score"
initial_score: 1.5
seed_files:
  - "main.py"
  - "helper.py"
evaluator:
  script: "evaluator.py"
  method: "test_eval"
"""
    (tmp_path / "experiment.yaml").write_text(exp_yaml, encoding="utf-8")
    (tmp_path / "main.py").write_text("# main code", encoding="utf-8")
    (tmp_path / "helper.py").write_text("# helper code", encoding="utf-8")
    (tmp_path / "evaluator.py").write_text(
        "def test_eval(*args, **kwargs):\n    return {'scores': {'scores': [{'metric': 'score', 'score': 10.0}]}}\ndef postprocess(programs):\n    pass\n",
        encoding="utf-8",
    )
    return tmp_path


def test_locate_experiment_dir_explicit(sample_exp_dir):
    cfg = BaseExperimentConfig(project_id="test-proj", bucket_name="test-bucket", user_experiment_name="test-exp")
    found = locate_experiment_dir(cfg, str(sample_exp_dir))
    assert found == sample_exp_dir.resolve()


def test_locate_experiment_dir_mount_path(tmp_path, monkeypatch):
    exp_dir = tmp_path / "my-exp" / "source"
    exp_dir.mkdir(parents=True, exist_ok=True)
    (exp_dir / "experiment.yaml").write_text("title: GCS Mount Test\n", encoding="utf-8")

    cfg = BaseExperimentConfig(project_id="test-proj", bucket_name="test-bucket", user_experiment_name="my-exp", mount_path=str(tmp_path))
    found = locate_experiment_dir(cfg)
    assert found == exp_dir.resolve()


def test_locate_experiment_dir_not_found(tmp_path):
    cfg = BaseExperimentConfig(project_id="test-proj", bucket_name="test-bucket", user_experiment_name="test-exp")
    with pytest.raises(FileNotFoundError, match="Could not find 'experiment.yaml'"):
        locate_experiment_dir(cfg, str(tmp_path / "non_existent"))


def test_load_experiment_spec_valid(sample_exp_dir):
    spec = load_experiment_spec(sample_exp_dir)
    assert spec["title"] == "Test Experiment"
    assert spec["program_language"] == "python"
    assert spec["primary_metric"] == "score"
    assert spec["initial_score"] == 1.5
    assert spec["seed_files"] == ["main.py", "helper.py"]
    assert spec["evaluator"]["method"] == "test_eval"


def test_load_experiment_spec_missing_required_fields(tmp_path):
    (tmp_path / "experiment.yaml").write_text("title: Only Title\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Missing required fields"):
        load_experiment_spec(tmp_path)


def test_build_initial_program_payload(sample_exp_dir):
    spec = load_experiment_spec(sample_exp_dir)
    payload = build_initial_program_payload(sample_exp_dir, spec)

    assert "content" in payload
    assert len(payload["content"]["files"]) == 2
    assert payload["content"]["files"][0]["path"] == "main.py"
    assert payload["content"]["files"][0]["content"] == "# main code"
    assert payload["evaluation"]["scores"]["scores"][0]["metric"] == "score"
    assert payload["evaluation"]["scores"]["scores"][0]["score"] == 1.5


def test_build_initial_program_payload_path_traversal(sample_exp_dir):
    spec = load_experiment_spec(sample_exp_dir)
    spec["seed_files"] = ["../secret.txt"]
    with pytest.raises(ValueError, match="Path traversal is not allowed"):
        build_initial_program_payload(sample_exp_dir, spec)


def test_build_initial_program_payload_missing_file(sample_exp_dir):
    spec = load_experiment_spec(sample_exp_dir)
    spec["seed_files"] = ["missing_file.py"]
    with pytest.raises(FileNotFoundError, match="Seed file not found"):
        build_initial_program_payload(sample_exp_dir, spec)


def test_build_experiment_config(sample_exp_dir, monkeypatch):
    monkeypatch.setenv("_MODEL", "GEMINI_V3P5_FLASH")
    monkeypatch.setenv("_MAX_PROGRAMS_GENERATED", "100")
    monkeypatch.setenv("_MAX_PROGRAMS_EVALUATED", "50")
    monkeypatch.setenv("_CONCURRENCY", "4")
    monkeypatch.setenv("_NUM_SAMPLERS", "8")
    monkeypatch.setenv("_MAX_DURATION", "12")
    monkeypatch.setenv("_IDLE_TIMEOUT", "10")
    monkeypatch.setenv("_PROJECT_ID", "test-proj")
    monkeypatch.setenv("_CLOUD_BUCKET_NAME", "test-bucket")
    monkeypatch.setenv("_USER_EXPERIMENT_NAME", "test-exp")
    monkeypatch.setenv("_PUBSUB_SUBSCRIPTION", "projects/test-proj/subscriptions/test-sub")

    ctrl_config = ControllerConfig.from_env()
    spec = load_experiment_spec(sample_exp_dir)
    config = build_experiment_config(spec, ctrl_config)

    assert config["title"] == "Test Experiment"
    assert config["run_settings"]["max_programs"] == 100
    assert config["run_settings"]["concurrency"] == 4
    assert config["run_settings"]["max_duration"] == 12
    assert config["run_settings"]["idle_timeout"] == 10


def test_build_experiment_config_with_generation_and_evolution_settings(sample_exp_dir, monkeypatch):
    monkeypatch.setenv("_PROJECT_ID", "test-proj")
    monkeypatch.setenv("_CLOUD_BUCKET_NAME", "test-bucket")
    monkeypatch.setenv("_USER_EXPERIMENT_NAME", "test-exp")
    monkeypatch.setenv("_PUBSUB_SUBSCRIPTION", "projects/test-proj/subscriptions/test-sub")

    ctrl_config = ControllerConfig.from_env()
    spec = load_experiment_spec(sample_exp_dir)
    spec["generation_settings"] = {
        "include_full_program_in_prompt": True,
        "context": "Focus on high throughput.",
    }
    spec["evolution_settings"] = {
        "parent_sampling_config": {
            "pareto_sampling_config": {
                "pareto_sampling_probability": 0.35,
            }
        },
    }

    config = build_experiment_config(spec, ctrl_config)

    assert config["generation_settings"]["include_full_program_in_prompt"] is True
    assert config["generation_settings"]["context"] == "Focus on high throughput."
    assert (
        config["evolution_settings"]["parent_sampling_config"][
            "pareto_sampling_config"
        ]["pareto_sampling_probability"]
        == 0.35
    )


def test_load_evaluator_function(sample_exp_dir):
    spec = load_experiment_spec(sample_exp_dir)
    func, mod = load_evaluator_function(sample_exp_dir, spec)
    assert callable(func)
    assert hasattr(mod, "postprocess")


def test_runner_main_flow(sample_exp_dir, monkeypatch):
    monkeypatch.setenv("_PROJECT_ID", "test-proj")
    monkeypatch.setenv("_CLOUD_BUCKET_NAME", "test-bucket")
    monkeypatch.setenv("_USER_EXPERIMENT_NAME", "test-user-exp")
    monkeypatch.setenv("_PUBSUB_SUBSCRIPTION", "projects/test-proj/subscriptions/test-sub")

    mock_controller = MagicMock()
    mock_controller.run_loop = AsyncMock()
    mock_controller.list_programs.return_value = {
        "alphaEvolvePrograms": [
            {"name": "programs/p1", "evaluation": {"scores": {"scores": [{"metric": "score", "score": 9.5}]}}}
        ]
    }

    with patch("alpha_evolve.runner.AlphaEvolveController", return_value=mock_controller):
        run_controller(str(sample_exp_dir))
        assert mock_controller.run_loop.called
        assert mock_controller.list_programs.called


def test_runner_evaluator_flow(sample_exp_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("_PROJECT_ID", "test-proj")
    monkeypatch.setenv("_CLOUD_BUCKET_NAME", "test-bucket")
    monkeypatch.setenv("_USER_EXPERIMENT_NAME", "test-exp")
    monkeypatch.setenv("_JOB_ID", "1")
    monkeypatch.setenv("_CANDIDATE_PROGRAM_ID", "cand_1")
    monkeypatch.setenv("_PROGRAMS_DIR", "evals")
    monkeypatch.setenv("_MOUNT_PATH", str(tmp_path))

    prog_dir = tmp_path / "test-exp" / "evals" / "1"
    prog_dir.mkdir(parents=True, exist_ok=True)

    candidate_data = {
        "name": "alphaEvolvePrograms/cand_1",
        "lockToken": "token-123",
        "content": {"files": [{"path": "main.py", "content": "# candidate code"}]}
    }
    (prog_dir / "program_candidate_data.json").write_text(json.dumps(candidate_data), encoding="utf-8")

    run_evaluator(str(sample_exp_dir))

    result_file = prog_dir / "program_candidate_result.json"
    assert result_file.is_file()
    with open(result_file, "r") as f:
        result = json.load(f)
    assert result["name"] == "alphaEvolvePrograms/cand_1"
    assert result["lockToken"] == "token-123"
    assert "scores" in result["evaluation"]


def test_runner_evaluator_makefile_compilation_failure(sample_exp_dir, tmp_path, monkeypatch):
    """Tests that a make compilation failure writes primary_metric with None and COMPILATION_ERROR insight."""
    monkeypatch.setenv("_PROJECT_ID", "test-proj")
    monkeypatch.setenv("_CLOUD_BUCKET_NAME", "test-bucket")
    monkeypatch.setenv("_USER_EXPERIMENT_NAME", "test-exp")
    monkeypatch.setenv("_JOB_ID", "2")
    monkeypatch.setenv("_CANDIDATE_PROGRAM_ID", "cand_2")
    monkeypatch.setenv("_PROGRAMS_DIR", "evals")
    monkeypatch.setenv("_MOUNT_PATH", str(tmp_path))

    prog_dir = tmp_path / "test-exp" / "evals" / "2"
    prog_dir.mkdir(parents=True, exist_ok=True)

    candidate_data = {
        "name": "alphaEvolvePrograms/cand_2",
        "lockToken": "token-456",
        "content": {"files": [{"path": "main.py", "content": "# candidate code"}]}
    }
    (prog_dir / "program_candidate_data.json").write_text(json.dumps(candidate_data), encoding="utf-8")

    # Create a Makefile that fails
    (sample_exp_dir / "Makefile").write_text("all:\n\t@exit 2\n", encoding="utf-8")

    run_evaluator(str(sample_exp_dir))

    result_file = prog_dir / "program_candidate_result.json"
    assert result_file.is_file()
    with open(result_file, "r") as f:
        result = json.load(f)
    assert result["name"] == "alphaEvolvePrograms/cand_2"
    assert result["lockToken"] == "token-456"
    assert result["evaluation"]["scores"]["scores"] == [{"metric": "score", "score": None}]
    assert result["evaluation"]["insights"]["insights"][0]["label"] == "COMPILATION_ERROR"
