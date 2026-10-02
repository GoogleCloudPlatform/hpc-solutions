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

"""Configuration data class and validation for AlphaEvolve Controller."""

from dataclasses import dataclass
import os
from typing import Any, Dict, Optional, Set


from .utils import create_full_programs_path


_SUPPORTED_PROVISIONING_MODELS: Set[str] = {"STANDARD", "SPOT", "FLEX_START"}
_SUPPORTED_FLEX_START_PREFIXES = ("g2-", "g4-", "a2-", "a3-", "a4-", "a4x-", "n1-", "h4d-")
_SUPPORTED_N1_ACCELERATORS: Set[str] = {
    "nvidia-tesla-t4",
    "nvidia-tesla-p4",
    "nvidia-tesla-v100",
    "nvidia-tesla-p100",
}

_INVALID_PLACEHOLDERS = {
    "project_id": {"gcp-project-id", "## SET GCP PROJECT ID HERE ##", "your-gcp-project-id"},
    "bucket_name": {
        "my-bucket-name",
        "alpha-evolve-existing-bucket",
        "## SET GCS BUCKET NAME HERE ##",
    },
    "user_experiment_name": {"my-experiment-name"},
    "pubsub_subscription": {"my-pubsub-subscription"},
}


def _get_positive_int(value: Any, field_name: str) -> int:
    try:
        int_val = int(value)
    except (ValueError, TypeError) as e:
        raise ValueError(f"{field_name} must be a valid integer, got {value!r}") from e
    if int_val <= 0:
        raise ValueError(f"{field_name} must be strictly positive, got {int_val}")
    return int_val


def _get_bool(value: Any, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes")


@dataclass
class BaseExperimentConfig:
    """Base configuration shared by all AlphaEvolve execution (Controller & Evaluator)."""

    # --- Mandatory Common Fields ---
    project_id: str
    bucket_name: str
    user_experiment_name: str

    # --- Optional Common Fields ---
    mount_path: str = "/mnt/disks/share"
    programs_dir: str = "program_candidates"

    def __post_init__(self):
        """Validates that common mandatory fields are present and not default placeholders."""
        self._validate_mandatory_fields()

    def _validate_mandatory_fields(self):
        """Validates that common mandatory fields are present and not default placeholders."""
        if not self.project_id or self.project_id.strip() in _INVALID_PLACEHOLDERS["project_id"]:
            raise ValueError("Project ID not found in environment (_PROJECT_ID).")

        if not self.bucket_name or self.bucket_name.strip() in _INVALID_PLACEHOLDERS["bucket_name"]:
            raise ValueError("Bucket name not found in environment (_CLOUD_BUCKET_NAME).")

        if (
            not self.user_experiment_name
            or self.user_experiment_name.strip() in _INVALID_PLACEHOLDERS["user_experiment_name"]
        ):
            raise ValueError(
                "User experiment name not found in environment (_USER_EXPERIMENT_NAME)."
            )

    @classmethod
    def _base_kwargs_from_env(cls, e: Dict[str, str]) -> Dict[str, Any]:
        """Extracts common base fields from environment dictionary."""
        kwargs: Dict[str, Any] = {
            "project_id": e.get("_PROJECT_ID", ""),
            "bucket_name": e.get("_CLOUD_BUCKET_NAME", ""),
            "user_experiment_name": e.get("_USER_EXPERIMENT_NAME", ""),
        }
        if e.get("_MOUNT_PATH"):
            kwargs["mount_path"] = e["_MOUNT_PATH"]
        if e.get("_PROGRAMS_DIR"):
            kwargs["programs_dir"] = e["_PROGRAMS_DIR"]
        return kwargs

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "BaseExperimentConfig":
        """Builds a BaseExperimentConfig by reading and parsing environment variables."""
        e = os.environ if env is None else env
        return cls(**cls._base_kwargs_from_env(e))


@dataclass
class ControllerConfig(BaseExperimentConfig):
    """Strongly-typed, validated configuration for AlphaEvolve Controller."""

    # --- Controller Specific Mandatory Fields ---
    pubsub_subscription: str = ""

    # --- Discovery Engine API Fields ---
    location: str = "global"
    collection: str = "default_collection"
    engine: str = "alpha-evolve-infra-experiment-engine"
    assistant: str = "default_assistant"
    base_url: str = "discoveryengine.googleapis.com"

    # --- Execution & Cloud Batch Fields ---
    evaluation_mode: str = "batch"
    evaluation_provisioning_model: str = "STANDARD"
    evaluation_machine_type: str = "n2-standard-4"
    accelerator_count: int = 1
    accelerator_type: str = "nvidia-tesla-t4"
    region: str = "us-central1"

    # --- Worker Tuning Fields ---
    max_programs_generated: int = 100
    max_programs_evaluated: int = 20
    concurrency: int = 4
    num_samplers: int = 4
    poll_interval: int = 4
    delete_succeeded_jobs: bool = True
    model: str = "GEMINI_V3P8_FLASH"
    deployment_name: str = "alpha-evolve"

    # --- Experiment Duration Settings (in Hours, valid range: 1 to 24) ---
    max_duration: int = 6  # Maximum wall-clock lifespan of the experiment run in hours
    idle_timeout: int = 5  # Maximum inactivity period in hours (must be <= max_duration)

    def __post_init__(self):
        """Validates all mandatory fields, allowed values, and constraints."""
        super().__post_init__()
        self._validate_controller_mandatory_fields()
        self._validate_evaluation_mode()
        self._validate_provisioning_and_machine()
        self._validate_accelerators()
        self._validate_numeric_fields()

    def _validate_controller_mandatory_fields(self):
        """Validates controller-specific mandatory fields."""
        if (
            not self.pubsub_subscription
            or self.pubsub_subscription.strip() in _INVALID_PLACEHOLDERS["pubsub_subscription"]
        ):
            raise ValueError(
                "Pub/Sub subscription not found in environment (_PUBSUB_SUBSCRIPTION)."
            )

    def _validate_evaluation_mode(self):
        """Ensures evaluation mode is supported."""
        if self.evaluation_mode.lower() != "batch":
            raise ValueError(f"Invalid evaluation mode: {self.evaluation_mode}. Must be 'batch'.")

    def _validate_provisioning_and_machine(self):
        """Validates Cloud Batch provisioning model and machine type compatibility."""
        prov_model = self.evaluation_provisioning_model.upper()
        if prov_model not in _SUPPORTED_PROVISIONING_MODELS:
            raise ValueError(
                f"Invalid _EVALUATION_PROVISIONING_MODEL '{self.evaluation_provisioning_model}'. "
                f"Valid values are: {', '.join(sorted(_SUPPORTED_PROVISIONING_MODELS))}."
            )
        self.evaluation_provisioning_model = prov_model

        if prov_model == "FLEX_START":
            if not self.evaluation_machine_type or not any(
                self.evaluation_machine_type.startswith(p) for p in _SUPPORTED_FLEX_START_PREFIXES
            ):
                raise ValueError(
                    f"Invalid _EVALUATION_MACHINE_TYPE '{self.evaluation_machine_type}' for DWS FLEX_START. "
                    f"FLEX_START on Cloud Batch requires GPU accelerator or H4D VM instances. "
                    f"Supported families include: G2, G4, A2, A3, A4, A4x, N1, and H4D."
                )

    def _validate_accelerators(self):
        """Validates GPU accelerator count and type for N1 machines."""
        self.accelerator_count = _get_positive_int(self.accelerator_count, "accelerator_count")
        if self.evaluation_machine_type and self.evaluation_machine_type.startswith("n1-"):
            if self.accelerator_type not in _SUPPORTED_N1_ACCELERATORS:
                raise ValueError(
                    f"Invalid _ACCELERATOR_TYPE '{self.accelerator_type}' for N1 machine type. "
                    f"Supported types are: {', '.join(sorted(_SUPPORTED_N1_ACCELERATORS))}."
                )

    def _validate_numeric_fields(self):
        """Validates worker tuning positive integer fields and duration settings."""
        self.max_programs_generated = _get_positive_int(
            self.max_programs_generated, "max_programs_generated"
        )
        self.max_programs_evaluated = _get_positive_int(
            self.max_programs_evaluated, "max_programs_evaluated"
        )
        self.concurrency = _get_positive_int(self.concurrency, "concurrency")
        self.num_samplers = _get_positive_int(self.num_samplers, "num_samplers")
        self.poll_interval = _get_positive_int(self.poll_interval, "poll_interval")
        self.max_duration = _get_positive_int(self.max_duration, "max_duration")
        self.idle_timeout = _get_positive_int(self.idle_timeout, "idle_timeout")

        if self.max_duration < 1 or self.max_duration > 24:
            raise ValueError(
                f"max_duration must be between 1 and 24 hours inclusive, got {self.max_duration}."
            )

        if self.idle_timeout < 1 or self.idle_timeout > 24:
            raise ValueError(
                f"idle_timeout must be between 1 and 24 hours inclusive, got {self.idle_timeout}."
            )

        if self.idle_timeout > self.max_duration:
            raise ValueError(
                f"idle_timeout ({self.idle_timeout}) must be less than or equal to max_duration ({self.max_duration})."
            )

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "ControllerConfig":
        """Builds a ControllerConfig by reading and parsing environment variables."""
        e = os.environ if env is None else env

        kwargs = cls._base_kwargs_from_env(e)
        kwargs["pubsub_subscription"] = e.get("_PUBSUB_SUBSCRIPTION", "")

        env_mappings = {
            "_LOCATION": "location",
            "_COLLECTION": "collection",
            "_ENGINE": "engine",
            "_ASSISTANT": "assistant",
            "_BASE_URL": "base_url",
            "_EVALUATION_MODE": "evaluation_mode",
            "_EVALUATION_PROVISIONING_MODEL": "evaluation_provisioning_model",
            "_EVALUATION_MACHINE_TYPE": "evaluation_machine_type",
            "_ACCELERATOR_COUNT": "accelerator_count",
            "_ACCELERATOR_TYPE": "accelerator_type",
            "_REGION": "region",
            "_MAX_PROGRAMS_GENERATED": "max_programs_generated",
            "_MAX_PROGRAMS_EVALUATED": "max_programs_evaluated",
            "_CONCURRENCY": "concurrency",
            "_NUM_SAMPLERS": "num_samplers",
            "_POLL_INTERVAL": "poll_interval",
            "_MAX_DURATION": "max_duration",
            "_IDLE_TIMEOUT": "idle_timeout",
            "_MODEL": "model",
            "_DEPLOYMENT_NAME": "deployment_name",
        }

        for env_var, field_name in env_mappings.items():
            val = e.get(env_var)
            if val is not None and val != "":
                kwargs[field_name] = val

        del_jobs = e.get("_DELETE_SUCCEEDED_JOBS")
        if del_jobs is not None and del_jobs != "":
            kwargs["delete_succeeded_jobs"] = _get_bool(del_jobs)

        return cls(**kwargs)


@dataclass
class EvaluatorConfig(BaseExperimentConfig):
    """Strongly-typed, validated configuration for AlphaEvolve Evaluator worker."""

    job_id: str = "0"
    candidate_program_id: str = ""
    candidate_dir: str = ""

    client_evaluator_script: str = ""
    client_evaluator_method: str = ""

    def __post_init__(self):
        """Resolves candidate directory and validates mandatory fields."""
        super().__post_init__()
        if not self.candidate_dir:
            base_dir = os.path.join(self.mount_path, self.user_experiment_name)
            self.candidate_dir = create_full_programs_path(
                base_dir, self.programs_dir, self.job_id
            )

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "EvaluatorConfig":
        """Builds an EvaluatorConfig by reading and parsing environment variables."""
        e = os.environ if env is None else env

        kwargs = cls._base_kwargs_from_env(e)

        env_mappings = {
            "_JOB_ID": "job_id",
            "_CANDIDATE_PROGRAM_ID": "candidate_program_id",
            "_CANDIDATE_DIR": "candidate_dir",
            "_CLIENT_EVALUATOR_SCRIPT": "client_evaluator_script",
            "_CLIENT_EVALUATOR_METHOD": "client_evaluator_method",
        }

        for env_var, field_name in env_mappings.items():
            val = e.get(env_var)
            if val is not None and val != "":
                kwargs[field_name] = val

        return cls(**kwargs)

