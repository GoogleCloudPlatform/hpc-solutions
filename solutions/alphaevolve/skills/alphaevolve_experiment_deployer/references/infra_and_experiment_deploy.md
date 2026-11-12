# Infrastructure Inspection and Experiment Deployment Commands

## Phase 2: Base Infrastructure Inspection & Deployment Commands

### Step 2.2: Inspect GCP Resources If the User Is Unsure (or to Validate Existing Infra)

If the user is unsure whether the base infrastructure is already deployed, or
after deploying `alpha-evolve-infra.yaml`, run the following non-destructive
inspection commands on a single line each to check all 7 resource groups created
by `alpha-evolve-infra.yaml`:

1.  **Required GCP APIs**: Verify all 11 platform APIs are enabled:

```bash
gcloud services list --enabled --project="${PROJECT_ID}" --filter="config.name:(cloudresourcemanager.googleapis.com OR serviceusage.googleapis.com OR iam.googleapis.com OR compute.googleapis.com OR batch.googleapis.com OR pubsub.googleapis.com OR storage.googleapis.com OR artifactregistry.googleapis.com OR cloudbuild.googleapis.com OR discoveryengine.googleapis.com OR aiplatform.googleapis.com)" --format="value(config.name)"
```

2.  **GCS Bucket & Staged Infrastructure Configuration Files**: Verify the
    bucket contains the baseline environment keystore
    (`config/variables-infra.env`), Batch job templates, and runner notebook
    staged by `alpha-evolve-infra.yaml`:

```bash
gcloud storage ls "gs://${BUCKET_NAME}/config/variables-infra.env" "gs://${BUCKET_NAME}/config/requirements.txt" "gs://${BUCKET_NAME}/config/controller-batch.yaml" "gs://${BUCKET_NAME}/config/validation-batch.yaml" "gs://${BUCKET_NAME}/notebook/run_notebook.ipynb"
```

3.  **Shared Artifact Registry Repository**: Check that the Docker repository
    `${INFRA_DEPLOYMENT_NAME}-repo` (default `alpha-evolve-infra-repo`) exists
    in `${REGION}`:

```bash
gcloud artifacts repositories describe "${INFRA_DEPLOYMENT_NAME:-alpha-evolve-infra}-repo" --location="${REGION}" --project="${PROJECT_ID}"
```

4.  **Service Accounts (Controller & Cloud Build)**: Verify the specialized
    service accounts (`ctrl-sa` and `build-sa`) exist:

```bash
gcloud iam service-accounts list --project="${PROJECT_ID}" --filter="email:(ctrl-sa OR build-sa)" --format="table(displayName,email)"
```

5.  **Pub/Sub Batch Notification Topic**: Verify the shared notification topic
    `${INFRA_DEPLOYMENT_NAME}-batch-notifications` exists:

```bash
gcloud pubsub topics describe "${INFRA_DEPLOYMENT_NAME:-alpha-evolve-infra}-batch-notifications" --project="${PROJECT_ID}"
```

6.  **AlphaEvolve Discovery Engine & Assistant**: Verify the Discovery Engine
    (`${INFRA_DEPLOYMENT_NAME}-experiment-engine`) and `default_assistant` exist
    under `locations/global/collections/default_collection`:

```bash
TOKEN=$(gcloud auth application-default print-access-token)
ENGINE_ID="${INFRA_DEPLOYMENT_NAME:-alpha-evolve-infra}-experiment-engine"
curl -s -H "Authorization: Bearer ${TOKEN}" -H "X-Goog-User-Project: ${PROJECT_ID}" "https://discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/global/collections/default_collection/engines/${ENGINE_ID}/assistants/default_assistant"
```

7.  **Vertex AI Colab Enterprise Runtime & Template**: Check for the provisioned
    Colab runtime template and runtime in `${REGION}`:

```bash
gcloud colab runtime-templates list --region="${REGION}" --project="${PROJECT_ID}"
gcloud colab runtimes list --region="${REGION}" --project="${PROJECT_ID}"
```

Report a clear status table to the user showing which of the 7 components are
present and healthy versus missing:

-   **If all 7 components are present and healthy**: Explicitly confirm that the
    base infrastructure is already deployed, skip
    `gcluster deploy alpha-evolve-infra.yaml`, and immediately transition to
    **Phase 3** by asking the user for (or confirming) their experiment
    deployment parameters (`user_experiment_name`, `example_dir`,
    `evaluation_machine_type`, `evaluation_provisioning_model`,
    `max_programs_evaluated`, `max_programs_generated`, and `concurrency`).
-   **If any components are missing**: Proceed to **Step 2.3** to deploy the
    base infrastructure.

### Step 2.3: Deploy Base Infrastructure (If Missing or Incomplete)

If the base infrastructure is not yet deployed:

1.  Ask the user for `USER_EMAIL` (`runtime_user_email`, e.g.,
    `user@example.com` for the Colab Enterprise runtime), verify Cluster Toolkit
    `v1.108.0` or later is installed (`gcluster --version`), and confirm the
    path to their local `blueprints/alphaevolve` directory in `hpc-solutions`
    (since `gcluster deploy` runs from `blueprints/alphaevolve/`, separate from
    `solutions/alphaevolve/`).
2.  Verify Application Default Credentials (ADC) are active
    (`gcloud auth application-default print-access-token`) and ensure the GCS
    bucket exists (creating it if needed):

```bash
gcloud storage buckets describe "gs://${BUCKET_NAME}" --project="${PROJECT_ID}" || \
  gcloud storage buckets create "gs://${BUCKET_NAME}" --project="${PROJECT_ID}" --location="${REGION}"
```

3.  **Summarize & Confirm Before Deploying**: Present the exact base
    infrastructure deployment command (run from
    `<hpc-solutions-root>/blueprints/alphaevolve/`) to the user,
    summarize what `alpha-evolve-infra.yaml` provisions (the 7 resource groups
    above), and **ask for explicit user confirmation before executing
    `gcluster deploy alpha-evolve-infra.yaml`**:

```bash
cd <hpc-solutions-root>/blueprints/alphaevolve
gcluster deploy alpha-evolve-infra.yaml \
  -d alpha-evolve-deployment.yaml \
  -o ../deployment \
  --vars project_id="${PROJECT_ID}" \
  --vars region="${REGION}" \
  --vars existing_bucket_name="${BUCKET_NAME}" \
  --vars runtime_user_email="${USER_EMAIL}" \
  -w -l IGNORE --auto-approve
```

Wait for the user's explicit confirmation before running the deployment command.

4.  **Post-Deployment Validation (After User Confirms & Deployment Completes)**:
    Immediately re-run the 7-point inspection checklist in Step 2.2 to confirm
    that `config/variables-infra.env`, the Artifact Registry repository, the
    Pub/Sub topic, the Discovery Engine assistant, and the Colab runtime were
    all created cleanly.

## Phase 3: Experiment Deployment Commands

### Step 3.2: Validate Command With the User & Explain What It Sets Up

Before executing `gcluster deploy`, display the exact command (run from
`<hpc-solutions-root>/blueprints/alphaevolve/`) to the user and
explain what `alpha-evolve-experiment.yaml` provisions under the hood:

1.  **Per-Experiment Artifact Registry & IAM**: Creates a dedicated Docker
    repository (`${exp_deployment_name}-${user_experiment_name}-repo`) and a
    least-privilege Evaluator Service Account (`eval-sa`) with bucket access.
2.  **Smart Container Builds via Cloud Build (`build/cloud-build.yaml.tfpl`)**:
    -   Checks if the shared vanilla base image (`alpha-evolve-base:latest`,
        containing Python 3.12, `uv`, build tools, and
        `google_framework/alpha_evolve`) already exists in the base Artifact
        Registry with a matching SHA-256 content hash; builds and caches it only
        on first run or when framework files change.
    -   Builds and pushes the controller image
        (`${user_experiment_name}-controller:latest`) and the
        experiment-specific evaluator image
        (`${user_experiment_name}-evaluator:latest` using
        `evaluator.Dockerfile`).
3.  **Source Staging to GCS**: Syncs the experiment directory
    (`experiment.yaml`, seed code, `evaluator.py`, `Makefile`) to
    `gs://${BUCKET_NAME}/${user_experiment_name}/source/`.
4.  **Isolated Pub/Sub Subscription**: Creates a dedicated pull subscription
    (`${deployment_name}-subscription`) on the shared notification topic,
    filtered by job prefix so concurrent experiments never cross-talk.
5.  **Experiment Environment Keystore (`variables.env`)**: Writes all resolved
    experiment settings, image URIs, and service account emails to
    `gs://${BUCKET_NAME}/${user_experiment_name}/variables.env`.
6.  **Project Metadata Registration**: Writes
    `${user_experiment_name}_USER_EXPERIMENT_NAME="${user_experiment_name}"` to
    Compute Engine Project Metadata for automatic experiment discovery.

If any user-supplied parameter triggers a naming guardrail or provisioning
trade-off (for example, `user_experiment_name` exceeding 15 characters or
`evaluation_provisioning_model="SPOT"`), explicitly surface both the naming fix
and the `SPOT` vs. `STANDARD` preemption trade-off alongside the deployment
command before waiting for confirmation.

#### Example Command (Mode 1 - Remote Git Example)

```bash
cd <hpc-solutions-root>/blueprints/alphaevolve
gcluster deploy alpha-evolve-experiment.yaml \
  -d alpha-evolve-deployment.yaml \
  -o ../deployment \
  --vars project_id="${PROJECT_ID}" \
  --vars region="${REGION}" \
  --vars existing_bucket_name="${BUCKET_NAME}" \
  --vars user_experiment_name="${USER_EXPERIMENT_NAME}" \
  --vars example_dir="${EXAMPLE_DIR}" \
  --vars evaluation_machine_type="${EVAL_MACHINE_TYPE}" \
  --vars evaluation_provisioning_model="${PROVISIONING_MODEL}" \
  --vars max_programs_evaluated="${MAX_EVALUATED}" \
  --vars max_programs_generated="${MAX_GENERATED}" \
  --vars concurrency="${CONCURRENCY}" \
  -w -l IGNORE --auto-approve
```

#### Example Command (Mode 2 - Custom Local Experiment)

```bash
cd <hpc-solutions-root>/blueprints/alphaevolve
gcluster deploy alpha-evolve-experiment.yaml \
  -d alpha-evolve-deployment.yaml \
  -o ../deployment \
  --vars project_id="${PROJECT_ID}" \
  --vars region="${REGION}" \
  --vars existing_bucket_name="${BUCKET_NAME}" \
  --vars user_experiment_name="${USER_EXPERIMENT_NAME}" \
  --vars example_dir="/absolute/path/to/custom_experiment" \
  --vars is_example_dir_local=true \
  --vars evaluation_machine_type="${EVAL_MACHINE_TYPE}" \
  --vars evaluation_provisioning_model="${PROVISIONING_MODEL}" \
  --vars max_programs_evaluated="${MAX_EVALUATED}" \
  --vars max_programs_generated="${MAX_GENERATED}" \
  --vars concurrency="${CONCURRENCY}" \
  -w -l IGNORE --auto-approve
```

Wait for the user's explicit confirmation before running the deployment command.
