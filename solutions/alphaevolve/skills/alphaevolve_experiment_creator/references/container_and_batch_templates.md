# Worker Container, `Makefile`, and `eval-batch.yaml` Override Templates

## Step 4.1: `evaluator.Dockerfile` Template

Inherit from `ARG BASE_IMAGE` (`FROM ${BASE_IMAGE}`), install optional OS and
Python dependencies into `/opt/venv` at image build time (omit `--system`, and
pass `--require-hashes` **only** when `requirements.txt` contains
`--hash=sha256:...` pins), switch to non-root user `evaluser` (`UID 1000`)
owning `/app/experiment`, preserve the base image `ENTRYPOINT`
(`python -m alpha_evolve.runner`), and do **not** run `RUN make` on candidate
sources at Docker build time.

> **Custom Evaluator Base Images (`base_evaluator_image`)**: Do not replace
> `FROM ${BASE_IMAGE}` with a hardcoded image (such as `FROM nvidia/cuda:...`)
> in `evaluator.Dockerfile`, as doing so bypasses `base.Dockerfile` under
> `infrastructure/build/` and drops `/opt/venv`, `uv`, and
> `/app/src/alpha_evolve`. Instead, keep `FROM ${BASE_IMAGE}` and set
> `base_evaluator_image` (must be Debian/Ubuntu `apt-get` compatible) in
> `alpha-evolve-experiment.yaml` or `--vars base_evaluator_image=<custom_image>`
> during deployment; Cloud Build builds `base.Dockerfile` with `--build-arg
> BASE_PYTHON_IMAGE=<custom_image>` into `alpha-evolve-base-custom` and injects
> it into `evaluator.Dockerfile` via `BASE_IMAGE`:

```dockerfile
ARG BASE_IMAGE=python:3.12-slim-bookworm
FROM ${BASE_IMAGE}

ARG CLOUD_BUCKET_NAME
ARG PROJECT_ID
ARG MOUNT_PATH="/mnt/disks/share"

ENV _CLOUD_BUCKET_NAME=${CLOUD_BUCKET_NAME}
ENV _PROJECT_ID=${PROJECT_ID}
ENV _JOB_ID=""
ENV _CANDIDATE_PROGRAM_ID=""
ENV _MOUNT_PATH=${MOUNT_PATH}
ENV _PROGRAMS_DIR=""
ENV _CLIENT_EVALUATOR_SCRIPT=""
ENV _CLIENT_EVALUATOR_METHOD=""
ENV _CANDIDATE_DIR=""

WORKDIR /app
# Use user_examples/<exp_name>/ (matching the experiment folder's basename) even
# when <exp_dir> lives outside user_examples/: Cloud Build stages custom local
# example_dir paths into user_examples/<exp_name>/ inside the build context.
COPY user_examples/<exp_name>/ /app/experiment/

# Optional build-time OS and Python dependencies:
# RUN bash /app/experiment/setup.sh
# RUN uv pip install -r /app/experiment/requirements.txt

WORKDIR /app/src/alpha_evolve
RUN useradd -m -u 1000 evaluser && chown -R evaluser:evaluser /app/experiment
USER evaluser
```

## Step 4.2: Optional `Makefile` Template (Compiled Languages Only)

Omit `Makefile` for Python experiments, including Python GPU workloads (PyTorch
CUDA, Triton, CuPy, Numba, or CUDA Python) that JIT-compile kernels at runtime.
For ahead-of-time compiled C++, CUDA C++ (`.cu` via `nvcc`), or Fortran
workloads, define an `all` target so the worker container compiles each mutated
candidate dynamically via `make -C /app/experiment all` before invoking
`evaluator.py`:

```makefile
CXX = g++
CXXFLAGS = -O3 -std=c++17 -fPIC -Wall

all: libkernel.so

libkernel.so: kernel.cpp
	$(CXX) $(CXXFLAGS) -shared kernel.cpp -o libkernel.so

clean:
	rm -f libkernel.so *.o
```

## Step 4.3: Optional Per-Experiment `eval-batch.yaml` Overrides

Never edit `solutions/alphaevolve/infrastructure/batch_configs/eval-batch.yaml`.
When an experiment needs GPU drivers/flags, custom worker environment variables,
extra container options/volumes, or multi-node MPI synchronization, create
`<exp_dir>/eval-batch.yaml` inside the experiment directory.

### Pattern A: GPU Drivers, Container Options & Custom Env Vars

Add `options: "--gpus all"` on the evaluator container, custom entries under
`environment.variables`, and `installGpuDrivers: true` under
`allocationPolicy.instances`:

```yaml
taskGroups:
- taskSpec:
    runnables:
    - script:
        text: |
          mkdir -p ${_MOUNT_PATH}
          chown -f -R 1000:1000 ${_MOUNT_PATH} || true
    - container:
        imageUri: "${_EVALUATOR_IMAGE_URI}"
        options: "--gpus all"
        volumes:
        - "${_MOUNT_PATH}:${_MOUNT_PATH}"
    environment:
      variables:
        _PROJECT_ID: "${_PROJECT_ID}"
        OUTPUT_DIR: "${_MOUNT_PATH}"
        _JOB_ID: "${_JOB_ID}"
        _CANDIDATE_PROGRAM_ID: "${_CANDIDATE_PROGRAM_ID}"
        _CLIENT_EVALUATOR_SCRIPT: "${_CLIENT_EVALUATOR_SCRIPT}"
        _CLIENT_EVALUATOR_METHOD: "${_CLIENT_EVALUATOR_METHOD}"
        _PROGRAMS_DIR: "${_PROGRAMS_DIR}"
        _MOUNT_PATH: "${_MOUNT_PATH}"
        _CLOUD_BUCKET_NAME: "${_CLOUD_BUCKET_NAME}"
        _USER_EXPERIMENT_NAME: "${_USER_EXPERIMENT_NAME}"
        _CANDIDATE_DIR: "${_MOUNT_PATH}/${_USER_EXPERIMENT_NAME}/${_PROGRAMS_DIR}/${_JOB_ID}"
        REAL_TRAINING_ENABLED: "true"
  taskCount: 1
  parallelism: 1
allocationPolicy:
  instances:
  - installGpuDrivers: true
    policy:
      machineType: "${_EVALUATION_MACHINE_TYPE}"
      provisioningModel: "${_EVALUATION_PROVISIONING_MODEL}"
      bootDisk:
        image: "${_BOOT_DISK_IMAGE}"
  serviceAccount:
    email: "${_EVALUATOR_SERVICE_ACCOUNT_EMAIL}"
```
*(Include the standard GCS `volumes`, `maxRunDuration`, `lifecyclePolicies`,
`logsPolicy`, and `notifications` stanzas from
`user_examples/llm_fine_tuning_cloud_batch/eval-batch.yaml`.)*

### Pattern B: Multi-Node MPI & SSH Synchronization (`nbody_molecular_dynamics`)

Install `openmpi-bin` and `openssh-server` (with `/app/experiment/run-ssh.sh` as
in `user_examples/nbody_molecular_dynamics/evaluator.Dockerfile`), then set
`taskCount > 1`, `requireHostsFile: true`, and `permissiveSsh: true` with
background SSH setup, SSH/mount permission initialization, and Cloud Batch
`barrier` synchronization:

```yaml
taskGroups:
- taskSpec:
    runnables:
    - container:
        imageUri: "${_EVALUATOR_IMAGE_URI}"
        entrypoint: bash
        commands:
        - "/app/experiment/run-ssh.sh"
        volumes:
        - /etc/ssh:/etc/ssh
        - /var/tmp:/var/tmp
        options: "-u 0 -td --network host --uts=host"
      background: true
    - script:
        text: "until [ -f /etc/ssh/started ]; do sleep 1; done"
    - barrier:
        name: "vms-ready"
    - script:
        text: |
          mkdir -p ${_MOUNT_PATH}
          chown -f -R 1000:1000 ${_MOUNT_PATH} || true
          if [ -f /etc/ssh/id_cloud_batch ]; then
            chown 1000:1000 /etc/ssh/id_cloud_batch && chmod 600 /etc/ssh/id_cloud_batch
          fi
          if [ -f /etc/ssh/authorized_keys ]; then
            chown root:root /etc/ssh/authorized_keys && chmod 644 /etc/ssh/authorized_keys
          fi
    - container:
        imageUri: "${_EVALUATOR_IMAGE_URI}"
        entrypoint: bash
        commands:
        - -c
        - |
          cp -r ${_CANDIDATE_DIR}/* /app/experiment/ 2>/dev/null || true
          make -C /app/experiment all
        volumes:
        - "${_MOUNT_PATH}:${_MOUNT_PATH}"
        - /var/tmp:/var/tmp
        options: "--network host --uts=host"
    - barrier:
        name: "compile-ready"
    - container:
        imageUri: "${_EVALUATOR_IMAGE_URI}"
        entrypoint: bash
        commands:
        - -c
        - |
          if [ "${BATCH_TASK_INDEX:-0}" = "0" ]; then
            mpirun --hostfile "${BATCH_HOSTS_FILE}" /var/tmp/sim_bin > /app/experiment/simulation_output.txt 2>&1
            python -m alpha_evolve.runner
          fi
        volumes:
        - "${_MOUNT_PATH}:${_MOUNT_PATH}"
        - /etc/cloudbatch-taskgroup-hosts:/etc/cloudbatch-taskgroup-hosts
        - /etc/ssh:/etc/ssh
        - /var/tmp:/var/tmp
        options: "--network host --uts=host"
    - barrier:
        name: "finish-mpi"
  taskCount: 2
  taskCountPerNode: 1
  requireHostsFile: true
  permissiveSsh: true
```
*(Include the full `environment.variables` defining `_CANDIDATE_DIR`, GCS
`volumes`, `maxRunDuration`, `lifecyclePolicies`, `allocationPolicy`,
`logsPolicy`, and `notifications` stanzas from
`user_examples/nbody_molecular_dynamics/eval-batch.yaml`.)*
