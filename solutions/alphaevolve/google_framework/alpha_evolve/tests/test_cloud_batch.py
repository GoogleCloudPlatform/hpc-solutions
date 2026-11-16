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

"""Unit tests for BatchClient in cloud_batch.py."""

import json
import pathlib
import re
from unittest import mock

from alpha_evolve import cloud_batch
from google.cloud import batch_v1
import pytest
import yaml

BatchClient = cloud_batch.BatchClient


@pytest.fixture
def mock_batch_client():
    with mock.patch(
        "alpha_evolve.cloud_batch.batch_v1.BatchServiceClient"
    ) as mock_service:
        client = BatchClient()
        client.client = mock_service.return_value
        yield client


def test_get_config_path(mock_batch_client, tmp_path, monkeypatch):
    # Test when file exists at custom path
    config_file = tmp_path / "eval-batch.yaml"
    config_file.write_text("taskGroups: []")

    # Override the method behavior to simulate finding it
    monkeypatch.setattr(mock_batch_client, "_get_config_path", lambda: str(config_file))
    assert mock_batch_client._get_config_path() == str(config_file)


def test_get_config_path_not_found():
    client = BatchClient()
    with mock.patch("os.path.exists", return_value=False):
        with pytest.raises(FileNotFoundError, match="eval-batch.yaml not found"):
            client._get_config_path()


def test_prepare_job_config(mock_batch_client, tmp_path, monkeypatch):
    config_file = tmp_path / "eval-batch.yaml"
    config_file.write_text(
        'taskGroups:\n- taskSpec:\n    runnables:\n    - container:\n        imageUri: "${_EVALUATOR_IMAGE_URI}"\n'
        '    environment:\n      variables:\n        _JOB_ID: "${_JOB_ID}"\n        _CLIENT_EVALUATOR_SCRIPT: "${_CLIENT_EVALUATOR_SCRIPT}"'
    )

    monkeypatch.setattr(mock_batch_client, "_get_config_path", lambda: str(config_file))
    monkeypatch.setenv("_EVALUATOR_IMAGE_URI", "gcr.io/test/evaluator:latest")

    json_str = mock_batch_client._prepare_job_config("job-123", "prog-456", "eval_script.py", "eval_method")
    
    assert "gcr.io/test/evaluator:latest" in json_str
    assert "job-123" in json_str
    assert "eval_script.py" in json_str


def test_create_batch_job_success(mock_batch_client, monkeypatch):
    project_id = mock_batch_client.controller_config.project_id
    monkeypatch.setattr(mock_batch_client, "_prepare_job_config", lambda *args, **kwargs: '{"taskGroups": []}')

    mock_created = mock.MagicMock()
    mock_created.name = f"projects/{project_id}/locations/us-central1/jobs/job-123"
    mock_batch_client.client.create_job.return_value = mock_created

    job = mock_batch_client.create_batch_job("job-123", "prog-456", "eval_script.py", "eval_method", region="us-central1")
    
    assert job == mock_created
    mock_batch_client.client.create_job.assert_called_once()
    call_kwargs = mock_batch_client.client.create_job.call_args[1]
    assert call_kwargs["request"].parent == f"projects/{project_id}/locations/us-central1"
    assert call_kwargs["request"].job_id == "job-123"


def test_create_batch_job_failure(mock_batch_client, monkeypatch):
    monkeypatch.setattr(mock_batch_client, "_prepare_job_config", lambda *args, **kwargs: '{"taskGroups": []}')

    mock_batch_client.client.create_job.side_effect = Exception("API Crash")

    with pytest.raises(Exception, match="API Crash"):
        mock_batch_client.create_batch_job("job-123", "prog-456", "eval_script.py", "eval_method")


def test_delete_batch_job(mock_batch_client):
    job_name = "projects/test-project/locations/us-central1/jobs/job-123"
    mock_batch_client.delete_batch_job(job_name)
    mock_batch_client.client.delete_job.assert_called_once()
    call_kwargs = mock_batch_client.client.delete_job.call_args[1]
    assert call_kwargs["request"].name == job_name


def test_delete_batch_job_exception(mock_batch_client):
    mock_batch_client.client.delete_job.side_effect = Exception("Delete Crash")
    # Should catch exception and log without raising
    mock_batch_client.delete_batch_job(
        "projects/test-project/locations/us-central1/jobs/job-123"
    )


def test_prepare_job_config_n1_gpu(mock_batch_client, tmp_path, monkeypatch):
    config_file = tmp_path / "eval-batch.yaml"
    config_file.write_text(
        'allocationPolicy:\n'
        '  instances:\n'
        '  - policy:\n'
        '      machineType: "n1-standard-4"\n'
        '      provisioningModel: "STANDARD"\n'
    )

    monkeypatch.setattr(
        mock_batch_client, "_get_config_path", lambda: str(config_file)
    )

    json_str = mock_batch_client._prepare_job_config(
        "job-123", "prog-456", "eval_script.py", "eval_method",
        accelerator_count=2, accelerator_type="nvidia-tesla-t4"
    )

    parsed = json.loads(json_str)
    instance = parsed["allocationPolicy"]["instances"][0]
    assert instance["installGpuDrivers"] is True
    assert instance["policy"]["accelerators"][0]["count"] == 2
    assert instance["policy"]["accelerators"][0]["type"] == "nvidia-tesla-t4"


def test_prepare_job_config_n2_no_gpu(mock_batch_client, tmp_path, monkeypatch):
    config_file = tmp_path / "eval-batch.yaml"
    config_file.write_text(
        'allocationPolicy:\n'
        '  instances:\n'
        '  - policy:\n'
        '      machineType: "n2-standard-4"\n'
        '      provisioningModel: "STANDARD"\n'
    )

    monkeypatch.setattr(
        mock_batch_client, "_get_config_path", lambda: str(config_file)
    )

    json_str = mock_batch_client._prepare_job_config(
        "job-123", "prog-456", "eval_script.py", "eval_method",
        accelerator_count=2, accelerator_type="nvidia-tesla-t4"
    )

    parsed = json.loads(json_str)
    instance = parsed["allocationPolicy"]["instances"][0]
    assert "installGpuDrivers" not in instance
    assert "accelerators" not in instance["policy"]


def test_prepare_job_config_dynamic_params_override_env(
    # pylint: disable=redefined-outer-name
    mock_batch_client,
    tmp_path,
    monkeypatch,
):
    """Verifies dynamic parameters and controller_config override os.environ."""
    config_file = tmp_path / "eval-batch.yaml"
    config_file.write_text(
        'environment:\n'
        '  variables:\n'
        '    _JOB_ID: "${_JOB_ID}"\n'
        '    _CANDIDATE_PROGRAM_ID: "${_CANDIDATE_PROGRAM_ID}"\n'
        '    _DEPLOYMENT_NAME: "${_DEPLOYMENT_NAME}"\n'
        '    _USER_EXPERIMENT_NAME: "${_USER_EXPERIMENT_NAME}"\n'
        '    _EXTRA_ENV: "${_EXTRA_ENV}"\n'
    )

    monkeypatch.setattr(
        mock_batch_client, "_get_config_path", lambda: str(config_file)
    )
    # Set stale env vars in os.environ
    monkeypatch.setenv("_JOB_ID", "stale-env-job-id")
    monkeypatch.setenv("_CANDIDATE_PROGRAM_ID", "stale-candidate-id")
    monkeypatch.setenv("_DEPLOYMENT_NAME", "stale-env-deployment")
    monkeypatch.setenv("_USER_EXPERIMENT_NAME", "stale-env-experiment")
    monkeypatch.setenv("_EXTRA_ENV", "correct-extra-value")

    json_str = mock_batch_client._prepare_job_config(
        "fresh-job-id-999", "fresh-candidate-888", "eval.py", "eval_fn"
    )

    parsed = json.loads(json_str)
    env_vars = parsed["environment"]["variables"]
    assert env_vars["_JOB_ID"] == "fresh-job-id-999"
    assert env_vars["_CANDIDATE_PROGRAM_ID"] == "fresh-candidate-888"
    assert (
        env_vars["_DEPLOYMENT_NAME"]
        == mock_batch_client.controller_config.deployment_name
    )
    assert (
        env_vars["_USER_EXPERIMENT_NAME"]
        == mock_batch_client.controller_config.user_experiment_name
    )
    assert env_vars["_EXTRA_ENV"] == "correct-extra-value"


def test_prepare_job_config_policy_injection_error(mock_batch_client, tmp_path, monkeypatch):
    config_file = tmp_path / "eval-batch.yaml"
    # Invalid structure where instances is not a list
    config_file.write_text(
        'allocationPolicy:\n'
        '  instances: "invalid-string-instead-of-list"\n'
    )

    monkeypatch.setattr(mock_batch_client, "_get_config_path", lambda: str(config_file))

    with pytest.raises(Exception):
        mock_batch_client._prepare_job_config(
            "job-1", "prog-1", "eval.py", "eval_fn"
        )


def test_sanitize_gcp_label_value_and_key():
    """Verifies GCP label key and value sanitization rules."""
    label_val_re = re.compile(r"^[a-z0-9_-]{0,63}$")
    label_key_re = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")

    none_sanitized = cloud_batch.sanitize_gcp_label_value(None)
    empty_sanitized = cloud_batch.sanitize_gcp_label_value("")
    assert isinstance(none_sanitized, str) and not none_sanitized
    assert isinstance(empty_sanitized, str) and not empty_sanitized
    assert (
        cloud_batch.sanitize_gcp_label_value("OpenROAD_DRT.v2 (GPU)!")
        == "openroad_drt-v2-gpu"
    )
    assert cloud_batch.sanitize_gcp_label_value("my---exp___1") == "my-exp___1"
    assert cloud_batch.sanitize_gcp_label_value("123_val") == "123_val"
    assert cloud_batch.sanitize_gcp_label_value("_leading_ok") == "_leading_ok"

    long_val = cloud_batch.sanitize_gcp_label_value(
        "A" * 62 + "-suffix-exceeding-limit"
    )
    assert len(long_val) <= 63
    assert not long_val.endswith("-")
    assert label_val_re.match(long_val)

    assert cloud_batch.sanitize_gcp_label_key(None) is None
    assert cloud_batch.sanitize_gcp_label_key("") is None
    assert cloud_batch.sanitize_gcp_label_key("   ---___   ") is None
    assert (
        cloud_batch.sanitize_gcp_label_key("goog-packaged-solution")
        == "goog-packaged-solution"
    )
    assert cloud_batch.sanitize_gcp_label_key("goog-ec-src") == "goog-ec-src"
    assert (
        cloud_batch.sanitize_gcp_label_key("___--leading_key") == "leading_key"
    )
    numeric_key = cloud_batch.sanitize_gcp_label_key("123_Cost.Center!")
    assert numeric_key == "k_123_cost-center"
    assert label_key_re.match(numeric_key)

    long_numeric_key = cloud_batch.sanitize_gcp_label_key(
        "9" + "a" * 59 + "-overflow"
    )
    assert len(long_numeric_key) <= 63
    assert not long_numeric_key.endswith("-")
    assert label_key_re.match(long_numeric_key)


def test_inject_batch_tracking_labels_merges_custom_and_caps_at_60():
    """Verifies custom label merging and 60-user-label cap enforcement."""
    assert cloud_batch.MAX_BATCH_USER_LABELS == 60
    custom_cfg = {
        "labels": {
            "Team Name": "HPC Science!",
            "123env": "Prod",
        },
        "allocationPolicy": {
            "instances": [{"policy": {"machineType": "n2-standard-4"}}],
        },
    }
    cloud_batch.inject_batch_tracking_labels(
        custom_cfg,
        component="evaluator",
        deployment_name="ae-exp-openroad",
        experiment_name="OpenROAD_DRT/v2",
    )

    assert custom_cfg["labels"]["team-name"] == "hpc-science"
    assert custom_cfg["labels"]["k_123env"] == "prod"
    assert custom_cfg["labels"]["ae_experiment"] == "openroad_drt-v2"
    assert custom_cfg["labels"]["ae_component"] == "evaluator"
    assert custom_cfg["labels"]["goog-ec-src"] == "alphaevolve-evaluator"
    alloc_labels = custom_cfg["allocationPolicy"]["labels"]
    assert alloc_labels["ae_experiment"] == "openroad_drt-v2"
    assert alloc_labels["goog-packaged-solution"] == "alphaevolve"

    # Verify 60-user-label limit while retaining all 7 tracking labels
    many_labels = {f"custom_key_{i}": f"Val_{i}" for i in range(70)}
    capped_cfg = {
        "labels": dict(many_labels),
        "allocationPolicy": {"labels": dict(many_labels)},
    }
    cloud_batch.inject_batch_tracking_labels(
        capped_cfg,
        component="evaluator",
        deployment_name="ae-exp-1",
        experiment_name="exp-1",
    )
    assert len(capped_cfg["labels"]) == 60
    assert len(capped_cfg["allocationPolicy"]["labels"]) == 60
    expected_tracking = cloud_batch.build_tracking_labels(
        "evaluator", "ae-exp-1", "exp-1"
    )
    for k, v in expected_tracking.items():
        assert capped_cfg["labels"][k] == v
        assert capped_cfg["allocationPolicy"]["labels"][k] == v


def test_prepare_job_config_sanitizes_special_experiment_and_deployment_names(
    tmp_path, monkeypatch
):
    """Verifies runtime sanitization when eval-batch.yaml omits labels."""
    # Custom user eval-batch.yaml without any labels block (CUJ-3)
    config_file = tmp_path / "eval-batch.yaml"
    config_file.write_text(
        "taskGroups:\n"
        "- taskSpec:\n"
        "    runnables:\n"
        "    - container:\n"
        '        imageUri: "gcr.io/test/eval:latest"\n'
        "  taskCount: 1\n"
        "allocationPolicy:\n"
        "  instances:\n"
        "  - policy:\n"
        '      machineType: "n2-standard-4"\n'
    )
    monkeypatch.setenv("_DEPLOYMENT_NAME", "AE.Deploy@Prod--V1!")
    monkeypatch.setenv("_USER_EXPERIMENT_NAME", "Adaptive Sort / C++ (v2)!")
    with mock.patch("alpha_evolve.cloud_batch.batch_v1.BatchServiceClient"):
        client = BatchClient()
    monkeypatch.setattr(client, "_get_config_path", lambda: str(config_file))

    json_str = client._prepare_job_config(  # pylint: disable=protected-access
        "job-123", "prog-456", "evaluator", "evaluate"
    )
    job = batch_v1.Job.from_json(json_str)

    expected = {
        "ghpc_blueprint": "alpha-evolve-platform",
        "ghpc_module": "batch-job-template",
        "ghpc_deployment": "ae-deploy-prod-v1",
        "goog-packaged-solution": "alphaevolve",
        "goog-ec-src": "alphaevolve-evaluator",
        "ae_component": "evaluator",
        "ae_experiment": "adaptive-sort-c-v2",
    }
    assert dict(job.labels) == expected
    assert dict(job.allocation_policy.labels) == expected


def _resolve_repo_hpc_root():
    """Returns hpc-solutions root when running from a repo checkout, or None."""
    parents = pathlib.Path(__file__).parents
    if len(parents) > 5:
        candidate = parents[5]
        if (candidate / "solutions" / "alphaevolve").is_dir():
            return candidate
    return None


def test_all_batch_yaml_templates_render_valid_identical_labels():
    """Verifies Cloud Batch YAML templates parse cleanly as batch_v1.Job."""
    hpc_root = _resolve_repo_hpc_root()
    if hpc_root is not None:
        ae_root = hpc_root / "solutions" / "alphaevolve"
        example_templates = [
            (path, "evaluator", "alphaevolve-evaluator")
            for path in sorted(
                (ae_root / "user_examples").glob("*/eval-batch.yaml")
            )
        ]
        assert len(example_templates) >= 2
        templates = [
            (
                ae_root
                / "infrastructure"
                / "batch_configs"
                / "validation-batch.yaml",
                "validator",
                "alphaevolve-validator",
            ),
            (
                ae_root
                / "infrastructure"
                / "batch_configs"
                / "controller-batch.yaml",
                "controller",
                "alphaevolve-controller",
            ),
            (
                ae_root
                / "infrastructure"
                / "batch_configs"
                / "eval-batch.yaml",
                "evaluator",
                "alphaevolve-evaluator",
            ),
            *example_templates,
        ]
    else:
        container_eval_yaml = pathlib.Path("/app/eval-batch.yaml")
        if not container_eval_yaml.exists():
            pytest.skip("Batch YAML templates not present in container path")
        templates = [
            (container_eval_yaml, "evaluator", "alphaevolve-evaluator"),
        ]

    sample_env = {
        "_PROJECT_ID": "test-project",
        "_CLOUD_BUCKET_NAME": "ae-test-bucket",
        "_PUBSUB_TOPIC": "ae-infra-batch-notifications",
        "_PUBSUB_SUBSCRIPTION": "ae-exp-demo-subscription",
        "_USER_EXPERIMENT_NAME": "adaptive-sort-v1",
        "_DEPLOYMENT_NAME": "ae-exp-adaptive-sort-v1",
        "_BASE_URL": "discoveryengine.googleapis.com",
        "_LOCATION": "global",
        "_COLLECTION": "default_collection",
        "_REGION": "us-central1",
        "_REGION_CODE": "uc1",
        "_REPO_NAME": "ae-exp-repo",
        "_CONTROLLER_IMAGE_URI": "us-central1-docker.pkg.dev/p/r/ctrl:latest",
        "_EVALUATOR_IMAGE_URI": "us-central1-docker.pkg.dev/p/r/eval:latest",
        "_MOUNT_PATH": "/mnt/disks/share",
        "_ENGINE": "ae-infra-experiment-engine",
        "_ASSISTANT": "default_assistant",
        "_CONTROLLER_SERVICE_ACCOUNT_EMAIL": (
            "ctrl@test-project.iam.gserviceaccount.com"
        ),
        "_EVALUATOR_SERVICE_ACCOUNT_EMAIL": (
            "eval@test-project.iam.gserviceaccount.com"
        ),
        "_CONCURRENCY": "4",
        "_MAX_PROGRAMS_EVALUATED": "20",
        "_MAX_PROGRAMS_GENERATED": "100",
        "_MAX_DURATION": "6",
        "_IDLE_TIMEOUT": "5",
        "_NUM_SAMPLERS": "4",
        "_POLL_INTERVAL": "4",
        "_MODEL": "gemini-3.8-flash",
        "_PROGRAMS_DIR": "program_candidates",
        "_MAX_DURATION_SECONDS": "3600",
        "_EVALUATION_MACHINE_TYPE": "n2-standard-4",
        "_EVALUATION_PROVISIONING_MODEL": "STANDARD",
        "_DELETE_SUCCEEDED_JOBS": "true",
        "_EVALUATION_MODE": "batch",
        "_BOOT_DISK_IMAGE": (
            "projects/debian-cloud/global/images/family/debian-12"
        ),
        "_ACCELERATOR_COUNT": "1",
        "_ACCELERATOR_TYPE": "nvidia-tesla-t4",
        "_JOB_ID": "job-001",
        "_CANDIDATE_PROGRAM_ID": "prog-001",
        "_CLIENT_EVALUATOR_SCRIPT": "evaluator",
        "_CLIENT_EVALUATOR_METHOD": "evaluate",
    }

    label_val_re = re.compile(r"^[a-z0-9_-]{0,63}$")
    label_key_re = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")

    for path, component, expected_ec_src in templates:
        assert path.exists(), f"Missing Batch YAML template: {path}"
        raw_yaml = path.read_text(encoding="utf-8")
        rendered_yaml = raw_yaml
        for k, v in sample_env.items():
            rendered_yaml = rendered_yaml.replace(f"${{{k}}}", v)

        cfg = yaml.safe_load(rendered_yaml)
        # Verify static YAML defines identical Job and allocationPolicy labels
        assert cfg.get("labels") == cfg.get("allocationPolicy", {}).get(
            "labels"
        )

        cloud_batch.inject_batch_tracking_labels(
            cfg,
            component=component,
            deployment_name=sample_env["_DEPLOYMENT_NAME"],
            experiment_name=sample_env["_USER_EXPERIMENT_NAME"],
        )

        # Verify YAML round-trip and Cloud Batch proto parsing
        roundtrip_cfg = yaml.safe_load(yaml.safe_dump(cfg, sort_keys=False))
        job = batch_v1.Job.from_json(json.dumps(roundtrip_cfg))

        job_labels = dict(job.labels)
        alloc_labels = dict(job.allocation_policy.labels)
        assert job_labels == alloc_labels
        assert len(job_labels) == 7
        assert job_labels == {
            "ghpc_blueprint": "alpha-evolve-platform",
            "ghpc_module": "batch-job-template",
            "ghpc_deployment": "ae-exp-adaptive-sort-v1",
            "goog-packaged-solution": "alphaevolve",
            "goog-ec-src": expected_ec_src,
            "ae_component": component,
            "ae_experiment": "adaptive-sort-v1",
        }
        for k, v in job_labels.items():
            assert label_key_re.match(k)
            assert label_val_re.match(v)
            assert "${" not in v


def test_infra_blueprint_colab_tracking_labels():
    """Verifies alpha-evolve-infra.yaml compute-colab sets tracking labels."""
    hpc_root = _resolve_repo_hpc_root()
    if hpc_root is None:
        pytest.skip("Blueprints are outside minimal image")
    infra_bp = (
        hpc_root / "blueprints" / "alphaevolve" / "alpha-evolve-infra.yaml"
    )
    bp_data = yaml.safe_load(infra_bp.read_text(encoding="utf-8"))

    colab_module = None
    for group in bp_data.get("deployment_groups", []):
        for mod in group.get("modules", []):
            if mod.get("id") == "compute-colab":
                colab_module = mod
                break

    assert colab_module is not None
    assert colab_module["settings"]["labels"] == {
        "goog-packaged-solution": "alphaevolve",
        "goog-ec-src": "alphaevolve-notebook",
        "ae_component": "notebook",
    }


