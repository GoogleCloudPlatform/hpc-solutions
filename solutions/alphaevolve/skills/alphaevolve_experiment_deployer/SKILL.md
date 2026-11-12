---
name: alphaevolve-experiment-deployer
description: >-
  Deploy, validate, run, monitor, clean up, and tear down AlphaEvolve
  evolutionary code optimization experiments on Google Cloud using Cluster
  Toolkit (gcluster), Cloud Batch, and Vertex AI. Use when a user wants to
  deploy AlphaEvolve infrastructure, build and stage a custom or pre-packaged
  experiment, run headless validation and smoke tests without opening Colab,
  inspect or reset smoke-test artifacts, or destroy experiment and
  infrastructure deployments. Don't use for designing or scaffolding new
  experiment codebases, EVOLVE-BLOCK instrumentation, or offline Docker
  dry-runs before cloud deployment (use alphaevolve-experiment-creator).
---

# AlphaEvolve Experiment Deployer

Guide users through end-to-end cloud deployment, verification, headless smoke
testing, artifact cleanup, full execution, and resource teardown of AlphaEvolve
experiments on Google Cloud using the Cluster Toolkit (`gcluster` `v1.108.0+`)
[AlphaEvolve blueprints][ae-blueprints] (`blueprints/alphaevolve/`) and
[Google Cloud HPC Solutions][hpc-alphaevolve] (`solutions/alphaevolve/`).

[ae-blueprints]: https://github.com/GoogleCloudPlatform/hpc-solutions/tree/main/blueprints/alphaevolve
[hpc-alphaevolve]: https://github.com/GoogleCloudPlatform/hpc-solutions/tree/main/solutions/alphaevolve

## Repository & Directory Layout (`hpc-solutions`)

AlphaEvolve splits cloud deployment blueprints and the experiment framework into
two top-level directories inside `GoogleCloudPlatform/hpc-solutions` (plus
Cluster Toolkit `v1.108.0+` providing the `gcluster` CLI and bundled
`community/modules/...`). Always resolve paths relative to the appropriate
directory rather than assuming all files share the same directory as `SKILL.md`:

1.  **AlphaEvolve Blueprints (`hpc-solutions/blueprints/alphaevolve/`)**:

    -   **URL**:
        `https://github.com/GoogleCloudPlatform/hpc-solutions/tree/main/blueprints/alphaevolve`
    -   **Prerequisite**: Cluster Toolkit (`gcluster`) `v1.108.0` or later
        installed (`gcluster --version`).
    -   **Contents**:
        -   `alpha-evolve-deployment.yaml` (global environment variables)
        -   `alpha-evolve-infra.yaml` (Stage 1 shared base infrastructure
            blueprint)
        -   `alpha-evolve-experiment.yaml` (Stage 2 per-experiment software
            environment blueprint)
        -   `build/cloud-build.yaml.tfpl` (Cloud Build container build template)
    -   **Important**: Whenever executing `gcluster deploy` or `gcluster
        destroy`, always run from `<hpc-solutions-root>/blueprints/alphaevolve/`
        (cloning `https://github.com/GoogleCloudPlatform/hpc-solutions.git` or
        asking the user for their local `hpc-solutions` path if not already
        known).

2.  **AlphaEvolve Solution & Skills (`hpc-solutions/solutions/alphaevolve/`)**:

    -   **URL**:
        `https://github.com/GoogleCloudPlatform/hpc-solutions/tree/main/solutions/alphaevolve`
    -   **Contents**:
        -   `solutions/alphaevolve/skills/alphaevolve_experiment_creator/SKILL.md`
            (companion `alphaevolve-experiment-creator` skill for designing,
            scaffolding, and offline-testing custom experiments)
        -   `solutions/alphaevolve/skills/alphaevolve_experiment_deployer/SKILL.md`
            (this deployment skill)
        -   `solutions/alphaevolve/CREATE_EXPERIMENT.md`
            (`https://github.com/GoogleCloudPlatform/hpc-solutions/blob/main/solutions/alphaevolve/CREATE_EXPERIMENT.md`
            - detailed reference guide for authoring experiments)
        -   `solutions/alphaevolve/user_examples/<experiment_name>/`
            (pre-packaged experiment directories; custom experiments may also
            reside in any local directory on disk)
        -   `solutions/alphaevolve/google_framework/notebook/run_notebook.ipynb`
            (experiment runner notebook)
        -   `solutions/alphaevolve/infrastructure/batch_configs/validation-batch.yaml`
            and `controller-batch.yaml`
        -   `solutions/alphaevolve/tools/monitoring/ae_monitor.py` (CLI
            monitoring and post-processing tool)

## When to Use

-   The user wants to deploy an AlphaEvolve experiment (custom or pre-packaged)
    to Google Cloud.
-   The user wants to check if AlphaEvolve base infrastructure
    (`alpha-evolve-infra.yaml`) is already provisioned in their GCP project or
    needs to deploy and validate it.
-   The user wants to configure and run `gcluster deploy` for
    `alpha-evolve-experiment.yaml` with custom machine types, provisioning
    models, or local/remote code sources.
-   The user wants to validate their deployed experiment, run a headless test
    reproducing `run_notebook.ipynb` without opening Colab Enterprise, verify
    generated artifacts (`results.csv`, candidate code archives, logs, and
    plots), or clean smoke-test artifacts to start a fresh production run.
-   The user wants to tear down and destroy either a specific experiment
    deployment or both the experiment and shared base infrastructure.

## Workflow Overview

Follow these six sequential phases. Do not skip verification gates or run
mutating deployment, cleanup, or teardown commands without explicit user
confirmation.

1.  **Phase 1: Experiment Codebase Readiness Check** - Confirm whether the
    experiment code exists or delegate authoring first to the companion
    `alphaevolve-experiment-creator` skill
    (`solutions/alphaevolve/skills/alphaevolve_experiment_creator/SKILL.md` and
    `solutions/alphaevolve/CREATE_EXPERIMENT.md`).
2.  **Phase 2: Base Infrastructure Discovery & Deployment
    (`blueprints/alphaevolve/`)** - Check if `alpha-evolve-infra.yaml` is
    already deployed, inspect its 7 resource groups if the user is unsure, and
    if any resources are missing, summarize what `alpha-evolve-infra.yaml`
    provisions, present the `gcluster deploy alpha-evolve-infra.yaml` command
    (run from `blueprints/alphaevolve/`), ask for explicit user confirmation
    before deploying, and verify all 7 resource groups after deployment.
3.  **Phase 3: Experiment Configuration & Deployment
    (`blueprints/alphaevolve/`)** - Collect experiment parameters, explain what
    the `gcluster deploy alpha-evolve-experiment.yaml` command sets up, confirm
    with the user, and deploy.
4.  **Phase 4: Headless Post-Deployment Smoke Validation & Artifact Reset
    Check** - Reproduce the `run_notebook.ipynb` workflow directly from the
    terminal (query Compute Engine Project Metadata to confirm experiment
    registration, load merged env vars, run the `validation-batch.yaml` job, run
    a short `controller-batch.yaml` smoke test with a small
    `max_programs_evaluated` limit, verify all GCS artifacts, and ask the user
    whether to keep smoke-test results or clean all generated artifacts for a
    fresh start).
5.  **Phase 5: Full Production Run & Monitoring** - Launch the full optimization
    run and monitor progress via CLI
    (`solutions/alphaevolve/tools/monitoring/ae_monitor.py`) or Colab
    Enterprise.
6.  **Phase 6: Resource Teardown & Destruction (`blueprints/alphaevolve/`)** -
    Validate with the user whether to destroy only the experiment deployment
    (`alpha-evolve-experiment`) or both the experiment and the shared base
    infrastructure (`alpha-evolve-infra`), stop/delete any active Cloud Batch
    jobs belonging to the target AlphaEvolve experiment(s) and clean
    `current_experiment.json` **before** running `gcluster destroy`, and execute
    teardown in strict experiment-first order.

## Phase 1: Experiment Codebase Readiness Check

Ask the user whether they have already created their AlphaEvolve experiment
codebase (in a custom local directory or under
`solutions/alphaevolve/user_examples/`).

### Branch A: Experiment Codebase Is Not Created Yet

If the user has not created their experiment codebase yet, **pause cloud
deployment** (`do NOT run gcluster deploy`) and load/follow the companion
**`alphaevolve-experiment-creator`** skill
(`solutions/alphaevolve/skills/alphaevolve_experiment_creator/SKILL.md`, paired
with `solutions/alphaevolve/CREATE_EXPERIMENT.md`) to design, scaffold, and
locally test the experiment directory before deploying to the cloud:

1.  Create a dedicated experiment directory (any custom local directory path or
    `solutions/alphaevolve/user_examples/<experiment_name>/`).
2.  Author the seed source files listed in `seed_files`, ensuring **at least
    one** file wraps mutable target code in `#` / `//` / `*`
    `EVOLVE-BLOCK-START` and `EVOLVE-BLOCK-END` markers while keeping benchmark
    harnesses outside mutable blocks.
3.  Author `experiment.yaml` (`title`, `problem_description`,
    `program_language`, `primary_metric`, `initial_score`, `seed_files`,
    `evaluator`) and timeout-guarded `evaluator.py` returning structured scores
    and diagnostic insights.
4.  Author `evaluator.Dockerfile` inheriting from `ARG BASE_IMAGE`
    (`FROM ${BASE_IMAGE}`) and running under non-root user `evaluser` (`UID
    1000`), plus optional `Makefile`, `setup.sh`, `requirements.txt`, or
    `eval-batch.yaml`.

### Branch B: Experiment Codebase Already Exists

If the user already has an experiment directory, verify that the required files
exist (`experiment.yaml`, all files listed under `seed_files` with **at least
one** file across `seed_files` containing valid `#` / `//` / `*`
`EVOLVE-BLOCK-START` and `EVOLVE-BLOCK-END` markers around editable code,
`evaluator.py`, and `evaluator.Dockerfile`) before proceeding to Phase 2. Note
that additional files in `seed_files` may legitimately omit `EVOLVE-BLOCK`
markers as read-only context files (e.g., `user_examples/adaptive_sort_cpp`). If
any required file is missing or **no** file in `seed_files` contains a valid
`EVOLVE-BLOCK`, alert the user to fix those files first and do not run
`gcluster deploy`.

## Phase 2: Base Infrastructure Discovery, Deployment & Validation

AlphaEvolve uses a two-stage Cluster Toolkit blueprint architecture located in
`hpc-solutions` under `blueprints/alphaevolve/` (requiring `gcluster`
`v1.108.0` or later):

-   **Stage 1 (`alpha-evolve-infra.yaml`)**: Deployed once per GCP project to
    provision shared foundational resources.
-   **Stage 2 (`alpha-evolve-experiment.yaml`)**: Deployed per experiment
    (supports N concurrent isolated experiments on the same base
    infrastructure).

### Step 2.1: Ask the User About Base Infrastructure Status

Ask the user:

1.  Whether the shared base infrastructure (`alpha-evolve-infra.yaml`) is
    already deployed in their target GCP project.
2.  What their target `PROJECT_ID`, `REGION` (default `us-central1`), and
    `BUCKET_NAME` (GCS bucket name) are, along with `INFRA_DEPLOYMENT_NAME`
    (default `alpha-evolve-infra`).

For **Step 2.2** (inspecting all 7 GCP resource groups if the user is unsure or
validating existing infra) and **Step 2.3** (if any components are missing:
summarizing what `alpha-evolve-infra.yaml` provisions, presenting the
`gcluster deploy alpha-evolve-infra.yaml` command, waiting for explicit user
confirmation before executing it, and re-running the 7-point inspection
afterward), read and follow the
[infrastructure inspection and experiment deployment commands](references/infra_and_experiment_deploy.md).

## Phase 3: Experiment Configuration & Deployment

Once base infrastructure is verified, configure and deploy the experiment
software environment using `alpha-evolve-experiment.yaml` in
`<hpc-solutions-root>/blueprints/alphaevolve/`.

### Step 3.1: Gather Experiment Parameters From the User

Ask the user for the following details (proposing sensible defaults based on
their experiment codebase):

1.  **Experiment Name (`user_experiment_name`)**:
    -   Must use lowercase letters, numbers, and hyphens (e.g., `airfoil-opt`,
        `adaptive-sort-1`).
    -   Keep it concise (**15 characters or fewer**, `<= 15` chars) so the
        per-experiment Evaluator Service Account ID
        (`ae-exp-${user_experiment_name}-eval-sa`, `15 + len` characters with
        default `exp_deployment_name: ae-exp`) stays within GCP IAM's
        30-character `account_id` limit and generated Cloud Batch job IDs
        (`${deployment_name}-worker-<id>`) stay well within Cloud Batch's
        63-character job ID limit.
2.  **Experiment Code Location & Source Mode**:
    -   **Mode 1 - Pre-shipped or Remote Git Experiment
        (`is_example_dir_local=false`)**: `example_dir` is a relative path
        inside `solutions/alphaevolve/` in the `hpc-solutions` Git repository
        (e.g., `user_examples/circle_packing_cloud_batch`), paired with
        `repo_url` (default
        `https://github.com/GoogleCloudPlatform/hpc-solutions.git`) and
        `repo_ref` (default `main`).
    -   **Mode 2 - Custom Local Experiment (`is_example_dir_local=true`)**:
        `example_dir` is the **full local directory path** to the user's custom
        experiment folder (any directory on disk or a local `user_examples/...`
        folder not pushed to GitHub).
        -   *Important Blueprint Check for Mode 2*: In
            `<hpc-solutions-root>/blueprints/alphaevolve/alpha-evolve-experiment.yaml`,
            verify that the `stage-experiment-source` module under
            `group: experiment-setup` uses
            `community/modules/file-system/gcs-objects` (rather than
            `file-system/gcs-sync`) so Terraform uploads local files directly to
            `gs://${BUCKET_NAME}/${user_experiment_name}/source`:

```yaml
  - group: experiment-setup
    modules:
    - id: stage-experiment-source
      source: community/modules/file-system/gcs-objects
      settings:
        bucket_name: $(vars.existing_bucket_name)
        directories:
          - source_path: "$(vars.example_dir)"
            destination_path: $(vars.user_experiment_name)/source
```

-   **Mode 3 - Both Framework & Experiment Code Local**: In addition to
    `is_example_dir_local=true` and `example_dir`, pass `--vars repo_url="''"`
    (or `--vars repo_url=local`) and
    `--vars cloud_build_dir="<path/to/hpc-solutions/solutions/alphaevolve/>"`.

3.  **Hardware & Cloud Batch Configuration**:
    -   `evaluation_machine_type`: Default `n2-standard-4` for standard CPU
        tasks; suggest compute-optimized (`c2-standard-8`, `h3-standard-88`) for
        heavy numerical/CFD workloads or GPU machine types (`g2-standard-8`,
        `a2-highgpu-1g`) for ML/GPU workloads.
    -   `evaluation_provisioning_model`:
        -   `STANDARD` (Recommended default): Dedicated on-demand VMs. Prevents
            Spot VM preemptions from being reported as candidate evaluation
            failures.
        -   `SPOT`: Lower-cost preemptible VMs. Whenever a user requests `SPOT`,
            honor `SPOT` if they want cost savings, and **always explicitly
            explain the `SPOT` vs. `STANDARD` trade-off** in your pre-deployment
            summary (noting that mid-evaluation Spot VM preemptions can be
            recorded as candidate evaluation failures).
        -   `FLEX_START`: Dynamic Workload Scheduler queueing for supported
            accelerators/HPC VMs.
4.  **Evolutionary Search & Runtime Parameters**:
    -   `model`: Generation model (default `gemini-3.8-flash`; also supports
        `gemini-4-argon`, `gemini-3.7-flash`, `gemini-3.5-flash`, and
        `gemini-3.1-pro-preview`, or weighted two-model mixtures).
    -   `max_programs_evaluated`: Total evaluated candidates before the
        controller loop stops (default `20`).
    -   `max_programs_generated`: Maximum candidates generated by AlphaEvolve
        (default `100`).
    -   `concurrency`: Number of parallel candidates evaluated concurrently
        (default `4`).
    -   `max_duration` (hours, default `6`, valid range `1` to `24`) and
        `idle_timeout` (hours, default `5`, strictly less than `max_duration`).

For **Step 3.2** (explaining what `alpha-evolve-experiment.yaml` provisions and
running the Mode 1 or Mode 2 `gcluster deploy alpha-evolve-experiment.yaml`
command after user confirmation), read and follow the
[infrastructure inspection and experiment deployment commands](references/infra_and_experiment_deploy.md).

## Phase 4: Headless Post-Deployment Smoke Validation & Artifact Reset Check

After `gcluster deploy alpha-evolve-experiment.yaml` finishes, do **not** ask
the user to manually open Colab Enterprise just to verify the setup. Instead,
reproduce the exact execution cells of
`google_framework/notebook/run_notebook.ipynb` (in
`hpc-solutions/solutions/alphaevolve/`) directly from the CLI/environment to
validate the deployment end-to-end.

Read and follow the
[headless smoke validation and artifact reset commands](references/headless_smoke_validation.md)
for **Step 4.1** through **Step 4.5**.

## Phase 5: Launching Full Production Runs & Monitoring

Once the user has decided whether to resume from the smoke test or start fresh
after artifact cleanup:

1.  **Launch Full Run Directly From the Terminal (or Colab Enterprise)**:
    -   Reload `_MAX_PROGRAMS_EVALUATED` and `_MAX_PROGRAMS_GENERATED` from
        `ae_validation/variables-exp.env` (or set them to the user's desired
        production targets), re-render
        `ae_validation/controller-batch-resolved.yaml`, and submit the
        production controller job via `gcloud batch jobs submit`.
2.  **Monitor Progress**:
    -   **Headless CLI Monitoring**: Use `gcloud batch jobs list`, `gcloud
        storage cat
        gs://${_CLOUD_BUCKET_NAME}/${_USER_EXPERIMENT_NAME}/results.csv`, or run
        `python3 tools/monitoring/ae_monitor.py --action status` (and
        `--action postprocess` for lineage trees and code diffs) from
        `hpc-solutions/solutions/alphaevolve/`.
    -   **Colab Enterprise UI**: Import
        `gs://${_CLOUD_BUCKET_NAME}/notebook/run_notebook.ipynb` into Vertex AI
        Colab Enterprise and connect to the custom runtime
        (`${INFRA_DEPLOYMENT_NAME}`), where the bucket is auto-mounted at
        `/data`.
3.  **Stopping or Pausing an Active Run**:
    -   To stop a running experiment cleanly, delete
        `gs://${_CLOUD_BUCKET_NAME}/${_USER_EXPERIMENT_NAME}/current_experiment.json`
        (and cancel any active Cloud Batch controller/worker jobs via
        `gcloud batch jobs delete`).
    -   Remind the user that while Cloud Batch worker VMs only run during active
        evaluations, the Colab Enterprise runtime (`n2-standard-4`) bills while
        active (with a 180-minute idle shutdown timeout) and should be stopped
        when not in use.

## Phase 6: Resource Teardown & Destruction (`gcluster destroy`)

When the user asks to clean up, tear down, or destroy cloud resources, first
execute the read-only inspection commands in **Step 6.1** (listing the deployed
Cluster Toolkit workspaces in `../deployment/` and checking for active Cloud
Batch jobs via `gcloud batch jobs list`), and **always validate with the user
first** whether they want to:

-   **Scope 1: Destroy ONLY the Experiment Deployment
    (`alpha-evolve-experiment`)** while keeping the shared Base Infrastructure
    (`alpha-evolve-infra`) intact for other or future experiments.
-   **Scope 2: Destroy BOTH the Experiment Deployment(s) AND the Shared Base
    Infrastructure (`alpha-evolve-infra`)** for a full project teardown.

Read and follow the
[resource teardown and destruction commands](references/resource_teardown.md)
for **Step 6.1** and **Step 6.2**.

## Gotchas & Best Practices

-   **Blueprints vs. Solution Subdirectory Separation (`hpc-solutions`)**:
    `alpha-evolve-infra.yaml`, `alpha-evolve-experiment.yaml`, and
    `alpha-evolve-deployment.yaml` live under `blueprints/alphaevolve/`
    (requiring Cluster Toolkit `gcluster` `v1.108.0` or later), while
    `CREATE_EXPERIMENT.md`, `user_examples/`, `tools/`, and `skills/` live under
    `solutions/alphaevolve/`. Always `cd` into
    `<hpc-solutions-root>/blueprints/alphaevolve/` before running
    `gcluster deploy` or `gcluster destroy`.
-   **Cloud Batch 63-Character Job ID Limit**: Cloud Batch strictly enforces
    `^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$` (max 63 characters). Always truncate the
    deployment prefix before appending `-validation-<timestamp>` or
    `-controller-<timestamp>`.
-   **Local vs. Remote Experiment Staging**: When `is_example_dir_local=true`,
    passing a local path without switching `stage-experiment-source` in
    `<hpc-solutions-root>/blueprints/alphaevolve/alpha-evolve-experiment.yaml`
    to `community/modules/file-system/gcs-objects` will cause GCS staging to
    look for the path in the remote Git repo instead of the local disk.
-   **Preserve `source/` and `variables.env` When Cleaning Smoke-Test
    Artifacts**: When resetting an experiment after a smoke test (Step 4.5),
    only remove run outputs (`current_experiment.json`, `results.csv`,
    `archive/`, `program_candidates/`, `logs/`, `results/`). Never delete
    `gs://${_CLOUD_BUCKET_NAME}/${_USER_EXPERIMENT_NAME}/source/` or
    `variables.env`, or the controller will fail to find `experiment.yaml` and
    seed files on the full run.
-   **Avoid `envsubst` Stripping Container Variables**: `validation-batch.yaml`
    uses internal bash variables inside its inline script (or if `envsubst` is
    run without a variable list, it may strip unset variables). Using the Python
    regex substitution snippet in Step 4.2/4.3 ensures only defined environment
    variables (`_PROJECT_ID`, `_CLOUD_BUCKET_NAME`, etc.) are substituted
    cleanly.
-   **Provisioning Model Signal Quality**: Default to `STANDARD` for
    `evaluation_provisioning_model` unless the user explicitly wants `SPOT` for
    cost savings, as Spot preemptions during evaluation can report failed scores
    if a worker VM is reclaimed mid-benchmark.
