# Resource Teardown & Destruction Commands (`gcluster destroy`)

## Step 6.1: Inspect Active AlphaEvolve Jobs, Stop Them FIRST, and List Workspaces

> **STRICT PRE-DESTROY ORDERING & SCOPING GATE**:
>
> 1.  **Stop Active Jobs Before `gcluster destroy`**: You **must** check for
>     active Cloud Batch jobs (`QUEUED`, `SCHEDULED`, or `RUNNING`),
>     cancel/delete active AlphaEvolve controller or worker jobs via
>     `gcloud batch jobs delete`, and delete or warn about
>     `gs://${BUCKET_NAME}/${USER_EXPERIMENT_NAME}/current_experiment.json`
>     **BEFORE** executing any `gcluster destroy` command. Never run
>     `gcluster destroy` first and delete active Cloud Batch jobs afterward.
> 2.  **Delete ONLY AlphaEvolve Batch Jobs (Scoped by Deployment/Experiment
>     Prefix)**: Other unrelated workloads or other AlphaEvolve experiments may
>     be running in the same GCP project. Filter or match job names by the
>     target AlphaEvolve deployment/experiment prefix (`${EXP_DEPLOYMENT_NAME}`,
>     `ae-exp`, or `${USER_EXPERIMENT_NAME}`, matching
>     `*-controller-*`, `*-worker-*`, or `*-validation-*`):
>     -   In **Scope 1 (Experiment-Only Teardown)**: Only delete active Cloud
>         Batch jobs belonging to the specific `${USER_EXPERIMENT_NAME}` (or
>         `${EXP_DEPLOYMENT_NAME}`) being destroyed—do not touch jobs belonging
>         to other AlphaEvolve experiments or non-AlphaEvolve workloads.
>     -   In **Scope 2 (Full Teardown)**: Only delete active Cloud Batch jobs
>         belonging to the AlphaEvolve experiment deployment(s) being torn
>         down—never indiscriminately delete unrelated Cloud Batch jobs in the
>         project.

Immediately run the read-only inspection commands (steps 1 and 2 below) to list
deployed workspaces in `../deployment/` and check for active Cloud Batch jobs
**before** asking the user to confirm their teardown scope (or before running
any `gcluster destroy` command):

```bash
# 1. List deployed Cluster Toolkit workspaces to identify the exact deployment name(s)
cd <hpc-solutions-root>/blueprints/alphaevolve
ls -d ../deployment/*

# 2. List active Cloud Batch jobs in the project/region and filter for the target AlphaEvolve experiment/deployment prefix
#    (e.g., matching ${EXP_DEPLOYMENT_NAME} or ${USER_EXPERIMENT_NAME}, such as ae-exp-<exp>-controller-* / -worker-*)
gcloud batch jobs list \
  --location="${REGION}" \
  --project="${PROJECT_ID}" \
  --filter="status.state:(QUEUED OR SCHEDULED OR RUNNING) AND name:${USER_EXPERIMENT_NAME}" \
  --format="table(name.basename(),status.state,createTime)"

# 3. For each active AlphaEvolve job matching the target experiment(s) being torn down:
#    Cancel/delete it FIRST and remove current_experiment.json BEFORE running gcluster destroy!
gcloud batch jobs delete "${AE_ACTIVE_JOB_ID}" \
  --location="${REGION}" \
  --project="${PROJECT_ID}" \
  --quiet
gcloud storage rm "gs://${BUCKET_NAME}/${USER_EXPERIMENT_NAME}/current_experiment.json" || true
```

## Step 6.2: Execute Teardown Based on User's Confirmed Scope

Explain what resources will be destroyed under the selected scope and wait for
explicit user confirmation before executing `gcluster destroy` (unless the user
has already explicitly confirmed the scope and authorized immediate execution).

### Scope 1: Destroy Only the Experiment (`alpha-evolve-experiment`)

1.  **Explain What This Destroys**:
    -   Per-experiment Artifact Registry repository
        (`${exp_deployment_name}-${user_experiment_name}-repo`) and its
        container images.
    -   Evaluator Service Account (`eval-sa`) and its IAM bindings.
    -   Per-experiment Pub/Sub pull subscription
        (`${deployment_name}-subscription`).
    -   Staged `variables.env` object and the
        `${user_experiment_name}_USER_EXPERIMENT_NAME` entry in Compute Engine
        Project Metadata.
    -   *Preserved*: All shared base infrastructure (`alpha-evolve-infra`), any
        other running AlphaEvolve experiments, and the experiment's output
        artifacts in `gs://${BUCKET_NAME}/${USER_EXPERIMENT_NAME}/`
        (`results.csv`, `archive/`, `logs/`, `results/`).
2.  **Run Experiment Teardown (after active Batch jobs for this experiment are
    deleted)**:

```bash
# Run from <hpc-solutions-root>/blueprints/alphaevolve
cd <hpc-solutions-root>/blueprints/alphaevolve
gcluster destroy ../deployment/<experiment_workspace_dir> --auto-approve
```

3.  **Optional GCS Experiment Folder Cleanup**: Ask the user if they want to
    keep their archived results in
    `gs://${BUCKET_NAME}/${USER_EXPERIMENT_NAME}/` or delete the GCS folder as
    well (`gcloud storage rm -r "gs://${BUCKET_NAME}/${USER_EXPERIMENT_NAME}"`).

### Scope 2: Destroy Both the Experiment(s) and Base Infrastructure (`alpha-evolve-infra`)

1.  **Enforce Strict 3-Step Destruction Order (Active AlphaEvolve Jobs First ->
    Experiments Second -> Base Infra Third)**:
    -   **Step A**: Cancel/delete all active Cloud Batch jobs belonging to the
        AlphaEvolve experiment(s) being destroyed
        (`gcloud batch jobs delete ...`) and clean
        `gs://${BUCKET_NAME}/${USER_EXPERIMENT_NAME}/current_experiment.json`
        **first**, before any `gcluster destroy` call. Do not delete
        non-AlphaEvolve Cloud Batch jobs in the project.
    -   **Step B**: Destroy **all** `alpha-evolve-experiment` deployment
        workspaces **second**. Because experiment deployments reference the
        shared Pub/Sub topic, shared Artifact Registry base image, and shared
        GCS bucket resources, tearing down experiments before base
        infrastructure prevents dependency or IAM cleanup conflicts.
    -   **Step C**: Destroy `alpha-evolve-infra` **third**.
2.  **Explain What This Destroys**:
    -   Everything in Scope 1 for every deployed experiment.
    -   Shared Artifact Registry (`${infra_deployment_name}-repo`), including
        `alpha-evolve-base:latest`.
    -   Controller and Cloud Build Service Accounts (`ctrl-sa`, `build-sa`) and
        their IAM bindings.
    -   Shared Pub/Sub topic (`${infra_deployment_name}-batch-notifications`).
    -   AlphaEvolve Discovery Engine
        (`${infra_deployment_name}-experiment-engine`) and `default_assistant`.
    -   Vertex AI Colab Enterprise runtime, runtime template, and staged
        `config/` and `notebook/` files in GCS.
    -   *Preserved by Default*: The GCS bucket itself (`gs://${BUCKET_NAME}`) is
        passed as `existing_bucket_name` and is not deleted by
        `gcluster destroy` so historical experiment results remain safe.
3.  **Run Full Teardown in Strict Sequence**:

```bash
# Step A: Ensure active AlphaEvolve Batch jobs (matching the AE deployment/experiment prefix)
#         and current_experiment.json are removed FIRST (before gcluster destroy)
gcloud batch jobs delete "${AE_ACTIVE_JOB_ID}" --location="${REGION}" --project="${PROJECT_ID}" --quiet
gcloud storage rm "gs://${BUCKET_NAME}/${USER_EXPERIMENT_NAME}/current_experiment.json" || true

cd <hpc-solutions-root>/blueprints/alphaevolve

# Step B: Destroy all experiment workspaces SECOND
gcluster destroy ../deployment/<experiment_workspace_dir> --auto-approve

# Step C: Destroy the shared base infrastructure workspace THIRD
gcluster destroy ../deployment/${INFRA_DEPLOYMENT_NAME:-alpha-evolve-infra} --auto-approve
```

4.  **Post-Destroy Verification & Optional Bucket Deletion**:
    -   Verify that the Artifact Registry repositories, Pub/Sub
        topic/subscriptions, service accounts, and Colab runtime have been
        removed.
    -   Ask the user whether they want to retain `gs://${BUCKET_NAME}` for its
        saved experiment archives or permanently delete the bucket
        (`gcloud storage rm -r "gs://${BUCKET_NAME}"`).
