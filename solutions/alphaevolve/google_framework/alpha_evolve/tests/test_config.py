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

"""Unit tests for ControllerConfig and its validation rules."""

import pytest
from alpha_evolve.config import ControllerConfig
from alpha_evolve.controller import AlphaEvolveController
from alpha_evolve.execution import DistributedEngine


@pytest.fixture
def valid_env():
    return {
        "_PROJECT_ID": "test-project-123",
        "_CLOUD_BUCKET_NAME": "test-bucket-alpha",
        "_USER_EXPERIMENT_NAME": "test-experiment-name",
        "_PUBSUB_SUBSCRIPTION": "projects/test-project-123/subscriptions/test-sub",
    }


def test_config_from_env_defaults(valid_env):
    cfg = ControllerConfig.from_env(valid_env)

    assert cfg.project_id == "test-project-123"
    assert cfg.bucket_name == "test-bucket-alpha"
    assert cfg.user_experiment_name == "test-experiment-name"
    assert cfg.pubsub_subscription == "projects/test-project-123/subscriptions/test-sub"
    assert cfg.location == "global"
    assert cfg.collection == "default_collection"
    assert cfg.engine == "alpha-evolve-infra-experiment-engine"
    assert cfg.assistant == "default_assistant"
    assert cfg.base_url == "discoveryengine.googleapis.com"
    assert cfg.evaluation_mode == "batch"
    assert cfg.programs_dir == "program_candidates"
    assert cfg.evaluation_provisioning_model == "STANDARD"
    assert cfg.evaluation_machine_type == "n2-standard-4"
    assert cfg.max_programs_generated == 100
    assert cfg.max_programs_evaluated == 20
    assert cfg.concurrency == 4
    assert cfg.num_samplers == 4
    assert cfg.poll_interval == 4
    assert cfg.delete_succeeded_jobs is True
    assert cfg.model == "GEMINI_V3P8_FLASH"
    assert cfg.deployment_name == "alpha-evolve"


def test_config_missing_mandatory_fields(valid_env):
    # Missing Project ID
    env = dict(valid_env)
    del env["_PROJECT_ID"]
    with pytest.raises(ValueError, match="Project ID not found in environment"):
        ControllerConfig.from_env(env)

    # Missing Bucket Name
    env = dict(valid_env)
    del env["_CLOUD_BUCKET_NAME"]
    with pytest.raises(ValueError, match="Bucket name not found in environment"):
        ControllerConfig.from_env(env)

    # Missing User Experiment Name
    env = dict(valid_env)
    del env["_USER_EXPERIMENT_NAME"]
    with pytest.raises(ValueError, match="User experiment name not found in environment"):
        ControllerConfig.from_env(env)

    # Missing PubSub Subscription
    env = dict(valid_env)
    del env["_PUBSUB_SUBSCRIPTION"]
    with pytest.raises(ValueError, match="Pub/Sub subscription not found in environment"):
        ControllerConfig.from_env(env)


def test_config_placeholder_values(valid_env):
    env = dict(valid_env, _PROJECT_ID="gcp-project-id")
    with pytest.raises(ValueError, match="Project ID not found in environment"):
        ControllerConfig.from_env(env)

    env = dict(valid_env, _CLOUD_BUCKET_NAME="my-bucket-name")
    with pytest.raises(ValueError, match="Bucket name not found in environment"):
        ControllerConfig.from_env(env)

    env = dict(valid_env, _USER_EXPERIMENT_NAME="my-experiment-name")
    with pytest.raises(ValueError, match="User experiment name not found in environment"):
        ControllerConfig.from_env(env)

    env = dict(valid_env, _PUBSUB_SUBSCRIPTION="my-pubsub-subscription")
    with pytest.raises(ValueError, match="Pub/Sub subscription not found in environment"):
        ControllerConfig.from_env(env)


def test_config_invalid_evaluation_mode(valid_env):
    env = dict(valid_env, _EVALUATION_MODE="dev")
    with pytest.raises(ValueError, match="Invalid evaluation mode: dev"):
        ControllerConfig.from_env(env)


def test_config_invalid_provisioning_model(valid_env):
    env = dict(valid_env, _EVALUATION_PROVISIONING_MODEL="INVALID_MODEL")
    with pytest.raises(ValueError, match="Invalid _EVALUATION_PROVISIONING_MODEL"):
        ControllerConfig.from_env(env)


def test_config_flex_start_machine_type_validation(valid_env):
    # Invalid machine type for FLEX_START
    env = dict(valid_env, _EVALUATION_PROVISIONING_MODEL="FLEX_START", _EVALUATION_MACHINE_TYPE="n2-standard-4")
    with pytest.raises(ValueError, match="Invalid _EVALUATION_MACHINE_TYPE 'n2-standard-4' for DWS FLEX_START"):
        ControllerConfig.from_env(env)

    # Valid machine type for FLEX_START
    env = dict(valid_env, _EVALUATION_PROVISIONING_MODEL="FLEX_START", _EVALUATION_MACHINE_TYPE="g2-standard-4")
    cfg = ControllerConfig.from_env(env)
    assert cfg.evaluation_provisioning_model == "FLEX_START"
    assert cfg.evaluation_machine_type == "g2-standard-4"


def test_config_n1_accelerator_validation(valid_env):
    # Invalid accelerator type for N1
    env = dict(valid_env, _EVALUATION_MACHINE_TYPE="n1-standard-4", _ACCELERATOR_TYPE="invalid-gpu")
    with pytest.raises(ValueError, match="Invalid _ACCELERATOR_TYPE 'invalid-gpu' for N1 machine type"):
        ControllerConfig.from_env(env)

    # Valid accelerator type for N1
    env = dict(valid_env, _EVALUATION_MACHINE_TYPE="n1-standard-4", _ACCELERATOR_TYPE="nvidia-tesla-v100", _ACCELERATOR_COUNT="2")
    cfg = ControllerConfig.from_env(env)
    assert cfg.accelerator_type == "nvidia-tesla-v100"
    assert cfg.accelerator_count == 2


def test_config_positive_integer_fields(valid_env):
    env = dict(valid_env, _MAX_PROGRAMS_EVALUATED="0")
    with pytest.raises(ValueError, match="max_programs_evaluated must be strictly positive"):
        ControllerConfig.from_env(env)

    env = dict(valid_env, _NUM_SAMPLERS="-2")
    with pytest.raises(ValueError, match="num_samplers must be strictly positive"):
        ControllerConfig.from_env(env)

    env = dict(valid_env, _POLL_INTERVAL="abc")
    with pytest.raises(ValueError, match="poll_interval must be a valid integer"):
        ControllerConfig.from_env(env)


def test_config_duration_settings_validation(valid_env):
    # Valid durations
    env = dict(valid_env, _MAX_DURATION="12", _IDLE_TIMEOUT="6")
    cfg = ControllerConfig.from_env(env)
    assert cfg.max_duration == 12
    assert cfg.idle_timeout == 6

    # max_duration out of range (> 24)
    env = dict(valid_env, _MAX_DURATION="25", _IDLE_TIMEOUT="5")
    with pytest.raises(ValueError, match="max_duration must be between 1 and 24 hours inclusive"):
        ControllerConfig.from_env(env)

    # idle_timeout out of range (> 24)
    env = dict(valid_env, _MAX_DURATION="24", _IDLE_TIMEOUT="25")
    with pytest.raises(ValueError, match="idle_timeout must be between 1 and 24 hours inclusive"):
        ControllerConfig.from_env(env)

    # idle_timeout > max_duration
    env = dict(valid_env, _MAX_DURATION="4", _IDLE_TIMEOUT="6")
    with pytest.raises(ValueError, match=r"idle_timeout \(6\) must be less than or equal to max_duration \(4\)"):
        ControllerConfig.from_env(env)


def test_controller_accepts_custom_config(valid_env):
    cfg = ControllerConfig(
        project_id="custom-proj",
        bucket_name="custom-bucket",
        user_experiment_name="custom-exp",
        pubsub_subscription="projects/custom-proj/subscriptions/custom-sub",
        num_samplers=8,
        poll_interval=2,
    )
    ctrl = AlphaEvolveController(config=cfg)
    assert ctrl.config.project_id == "custom-proj"
    assert ctrl.config.num_samplers == 8
    assert ctrl.config.poll_interval == 2
    assert ctrl.project_id == "custom-proj"
    assert ctrl.bucket_name == "custom-bucket"
    assert isinstance(ctrl.engine, DistributedEngine)


def test_base_experiment_config_validation(valid_env):
    from alpha_evolve.config import BaseExperimentConfig, EvaluatorConfig

    # Base class self-validates on instantiation
    with pytest.raises(ValueError, match="Project ID not found in environment"):
        BaseExperimentConfig(project_id="", bucket_name="base-bucket", user_experiment_name="base-exp")

    with pytest.raises(ValueError, match="Bucket name not found in environment"):
        BaseExperimentConfig(project_id="base-proj", bucket_name="", user_experiment_name="base-exp")

    with pytest.raises(ValueError, match="User experiment name not found in environment"):
        BaseExperimentConfig(project_id="base-proj", bucket_name="base-bucket", user_experiment_name="")

    # Base class instantiates cleanly when valid
    base_cfg = BaseExperimentConfig(
        project_id="base-proj",
        bucket_name="base-bucket",
        user_experiment_name="base-exp",
    )
    assert base_cfg.project_id == "base-proj"
    assert base_cfg.mount_path == "/mnt/disks/share"

    # Base class from_env
    base_from_env = BaseExperimentConfig.from_env(valid_env)
    assert base_from_env.project_id == "test-project-123"
    assert base_from_env.bucket_name == "test-bucket-alpha"
    assert base_from_env.user_experiment_name == "test-experiment-name"
    assert base_from_env.mount_path == "/mnt/disks/share"

    # EvaluatorConfig inherits from BaseExperimentConfig
    eval_cfg = EvaluatorConfig.from_env(valid_env)
    assert isinstance(eval_cfg, BaseExperimentConfig)
    assert eval_cfg.project_id == "test-project-123"
    assert eval_cfg.job_id == "0"
    assert eval_cfg.mount_path == "/mnt/disks/share"


def test_models_generation_settings_include_full_program():
    from alpha_evolve.models import AlphaEvolveGenerationSettings

    gen_default = AlphaEvolveGenerationSettings()
    assert gen_default.include_full_program_in_prompt is None

    gen_true = AlphaEvolveGenerationSettings(include_full_program_in_prompt=True)
    assert gen_true.include_full_program_in_prompt is True

    gen_false = AlphaEvolveGenerationSettings(include_full_program_in_prompt=False)
    assert gen_false.include_full_program_in_prompt is False


def test_models_pareto_sampling_config_validation():
    from pydantic import ValidationError
    from alpha_evolve.models import (
        AlphaEvolveParetoSamplingConfig,
        AlphaEvolveParentSamplingConfig,
        AlphaEvolveEvolutionSettings,
        AlphaEvolveExperimentConfig,
    )

    # Valid probability
    pareto_cfg = AlphaEvolveParetoSamplingConfig(pareto_sampling_probability=0.3)
    assert pareto_cfg.pareto_sampling_probability == 0.3

    # Out of bounds: > 1.0
    with pytest.raises(ValidationError):
        AlphaEvolveParetoSamplingConfig(pareto_sampling_probability=1.5)

    # Out of bounds: < 0.0
    with pytest.raises(ValidationError):
        AlphaEvolveParetoSamplingConfig(pareto_sampling_probability=-0.1)

    # Nested in evolution settings
    parent_cfg = AlphaEvolveParentSamplingConfig(pareto_sampling_config=pareto_cfg)
    evo_cfg = AlphaEvolveEvolutionSettings(
        parent_sampling_config=parent_cfg,
    )
    assert evo_cfg.parent_sampling_config.pareto_sampling_config.pareto_sampling_probability == 0.3

    # In full experiment config
    exp_cfg = AlphaEvolveExperimentConfig(
        title="Pareto Test",
        problem_description="Test multi-objective",
        program_language="python",
        evolution_settings=evo_cfg,
    )
    dumped = exp_cfg.model_dump(exclude_none=True)
    assert (
        dumped["evolution_settings"]["parent_sampling_config"]["pareto_sampling_config"][
            "pareto_sampling_probability"
        ]
        == 0.3
    )


def test_models_experiment_stats_tokens():
    from alpha_evolve.models import AlphaEvolveExperimentStats

    stats = AlphaEvolveExperimentStats(
        candidates_count=10,
        evaluated_candidates_count=8,
        input_token_count="150000",
        output_token_count=12000,
    )
    assert stats.candidates_count == 10
    assert stats.evaluated_candidates_count == 8
    assert stats.input_token_count == "150000"
    assert stats.output_token_count == 12000


def test_models_program_lineage_and_state():
    from alpha_evolve.models import AlphaEvolveProgram

    prog = AlphaEvolveProgram(
        name="projects/p/locations/l/collections/c/engines/e/sessions/s/alphaEvolveExperiments/exp/alphaEvolvePrograms/prog1",
        parent_programs=["projects/.../prog0"],
        state="EVALUATED",
    )
    assert prog.name.endswith("prog1")
    assert prog.parent_programs == ["projects/.../prog0"]
    assert prog.state == "EVALUATED"


