# Offline Two-Container Schema & Seed Dry Run Commands

Run these steps from `solutions/alphaevolve/` before deploying to Google Cloud
or consuming Cloud Batch quota:

```bash
# 1. Stage seed files and dummy candidate metadata in a local shared volume
EXP_NAME="<exp_name>"
EXP_DIR="${EXP_DIR:-user_examples/${EXP_NAME}}"  # Or full path /path/to/${EXP_NAME}
mkdir -p "local_test_workspace/${EXP_NAME}/program_candidates/test-job"
cat << 'EOF' > "local_test_workspace/${EXP_NAME}/program_candidates/test-job/program_candidate_data.json"
{
  "name": "alphaEvolvePrograms/seed-candidate",
  "lockToken": "dummy-token"
}
EOF
cp -r "${EXP_DIR}/"* "local_test_workspace/${EXP_NAME}/program_candidates/test-job/"
chmod -R a+rwX "local_test_workspace"
# If EXP_DIR is outside user_examples/, stage a temporary copy for Docker COPY context:
STAGED_TEMP_COPY=0
if [ "$(realpath -m "${EXP_DIR}")" != "$(realpath -m "user_examples/${EXP_NAME}")" ]; then
  mkdir -p "user_examples/${EXP_NAME}" && cp -r "${EXP_DIR}/." "user_examples/${EXP_NAME}/"
  STAGED_TEMP_COPY=1
fi

# 2. Build local base, evaluator, and controller test images from solutions/alphaevolve/
# (If using a custom Debian/Ubuntu base_evaluator_image, add: --build-arg BASE_PYTHON_IMAGE="<custom_image>" to test-base)
docker build -t test-base -f infrastructure/build/base.Dockerfile .
docker build -t test-evaluator --build-arg BASE_IMAGE=test-base -f "user_examples/${EXP_NAME}/evaluator.Dockerfile" .
docker build -t test-controller --build-arg BASE_IMAGE=test-base --build-arg EXAMPLE_DIR="user_examples/${EXP_NAME}" -f infrastructure/build/controller.Dockerfile .

# 3. Execute evaluator container against the seed candidate
docker run --rm \
  -v "$(pwd)/local_test_workspace:/mnt/disks/share" \
  -e _CLOUD_BUCKET_NAME=dummy-bucket \
  -e _USER_EXPERIMENT_NAME="${EXP_NAME}" \
  -e _PROJECT_ID=dummy-project \
  -e _JOB_ID=test-job \
  -e _PROGRAMS_DIR=program_candidates \
  -e _MOUNT_PATH=/mnt/disks/share \
  -e _CANDIDATE_DIR="/mnt/disks/share/${EXP_NAME}/program_candidates/test-job" \
  -e _CANDIDATE_PROGRAM_ID=seed-candidate \
  -e _CLIENT_EVALUATOR_SCRIPT=evaluator \
  -e _CLIENT_EVALUATOR_METHOD="<evaluator_method_name>" \
  test-evaluator

# 4. Validate Pydantic schema & controller ingestion offline (no external API calls)
docker run --rm --entrypoint python3 \
  -v "$(pwd)/local_test_workspace:/mnt/disks/share" \
  -v "$(pwd)/tools:/app/tools" \
  -e _USER_EXPERIMENT_NAME="${EXP_NAME}" \
  test-controller /app/tools/test_container_communication.py
```

## Expected Offline Verification Outcomes & Cleanup

1.  **Seed Baseline Score**: Inspect
    `local_test_workspace/${EXP_NAME}/program_candidates/test-job/program_candidate_result.json`
    (or container logs) and confirm the reported finite score matches
    `initial_score` in `experiment.yaml`.
2.  **Schema & Controller Ingestion**: Confirm
    `tools/test_container_communication.py` outputs both `[SUCCESS] Pydantic
    schema validation PASSED!` and `[SUCCESS] Controller Container validated and
    ingested Evaluator Container output!`.
3.  **Workspace Cleanup**: Remove the temporary `local_test_workspace/`
    directory (and `user_examples/${EXP_NAME}` **only** when
    `STAGED_TEMP_COPY=1` in Step 1) once verification succeeds so untracked
    dry-run artifacts are not staged or committed.

## GPU, Accelerator & Multi-Node Hardware Caveats

Most local developer workstations and Cloud Build / CLI build machines are
CPU-only and cannot execute GPU containers (`--gpus all`, CUDA kernels, or
GPU-only PyTorch/JAX paths):

-   **Always inform the user upfront** when an experiment targets GPUs or
    specialized accelerators that local `docker run` execution of GPU code paths
    is not possible on a CPU-only build machine.
-   **What to validate locally on CPU-only machines**:
    1.  Run `docker build` (`test-base`, `test-evaluator`, `test-controller`) to
        catch Dockerfile syntax, `setup.sh`, and `requirements.txt` errors
        offline.
    2.  If `evaluator.py` includes a CPU fallback or surrogate mode (such as
        `llm_fine_tuning_cloud_batch` when CUDA is absent), run `test-evaluator`
        and `test_container_communication.py` in CPU mode to verify Pydantic
        schema compatibility.
    3.  If `evaluator.py` strictly requires GPU hardware, skip local `docker
        run` of `test-evaluator` and validate runtime execution on Cloud Batch
        GPU worker VMs via `alphaevolve-experiment-deployer` headless smoke
        testing.
