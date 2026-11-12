# Headless Post-Deployment Smoke Validation & Artifact Reset Commands

## Step 4.1: Discover Experiment & Load Merged Environment Variables (Notebook Cell 1: `load_infra_and_experiment()`)

In `run_notebook.ipynb`, the first step queries Compute Engine Project Metadata
for keys ending in `_USER_EXPERIMENT_NAME`, loads
`gs://${BUCKET_NAME}/config/variables-infra.env`, and then loads
`gs://${BUCKET_NAME}/${USER_EXPERIMENT_NAME}/variables.env` on top of it.

> **MANDATORY VERIFICATION GATE (When Running Step 4.1)**: When executing
> initial post-deployment validation in Step 4.1 (if the user states
> post-deployment validation already passed, skip Steps 4.1–4.2 and proceed
> directly to **Step 4.3**), you **must** actually execute:
> 1. `gcloud compute project-info describe --project="${PROJECT_ID}"` and
>    observe the `_USER_EXPERIMENT_NAME` metadata entry in the command output,
> 2. **both** `gcloud storage cp` commands to download fresh
>    `gs://${BUCKET_NAME}/config/variables-infra.env` and
>    `gs://${BUCKET_NAME}/${USER_EXPERIMENT_NAME}/variables.env` from GCS (**even
>    if `ae_validation/` already contains files on disk**, so stale local files
>    never shadow freshly deployed settings), and
> 3. `gcloud storage ls` on
>    `"gs://${_CLOUD_BUCKET_NAME}/${_USER_EXPERIMENT_NAME}/source/"`.
>
> If an initial shell command fails before reaching any of these commands (for
> example, due to an unset variable or `set -e`), **re-run the skipped command
> explicitly**. Never skip downloading either GCS `.env` file or claim Project
> Metadata is verified without seeing its actual output.

Run the following steps in bash using a local `ae_validation/` directory:

```bash
# 1. Verify experiment registration in GCP Project Metadata (MUST succeed and return the _USER_EXPERIMENT_NAME entry)
gcloud compute project-info describe --project="${PROJECT_ID}" --format="json(commonInstanceMetadata.items)" | grep "_USER_EXPERIMENT_NAME"

# 2. Download both environment keystores from GCS into ./ae_validation
mkdir -p ae_validation
gcloud storage cp "gs://${BUCKET_NAME}/config/variables-infra.env" ae_validation/variables-infra.env
gcloud storage cp "gs://${BUCKET_NAME}/${USER_EXPERIMENT_NAME}/variables.env" ae_validation/variables-exp.env

# 3. Export merged variables into the current shell session
set -a
source ae_validation/variables-infra.env
source ae_validation/variables-exp.env
set +a

# 4. Verify critical variables and staged source files
echo "Project: ${_PROJECT_ID} | Bucket: ${_CLOUD_BUCKET_NAME} | Experiment: ${_USER_EXPERIMENT_NAME}"
echo "Controller Image: ${_CONTROLLER_IMAGE_URI}"
echo "Evaluator Image:  ${_EVALUATOR_IMAGE_URI}"
gcloud storage ls "gs://${_CLOUD_BUCKET_NAME}/${_USER_EXPERIMENT_NAME}/source/"
```

Verify that `experiment.yaml`, the seed files, and `evaluator.py` are present
under `gs://${_CLOUD_BUCKET_NAME}/${_USER_EXPERIMENT_NAME}/source/`.

## Step 4.2: Run the Environment Validation Batch Job (Notebook Cell 2: `validation-batch.yaml`)

In `run_notebook.ipynb`, the optional validation cell downloads
`gs://${_CLOUD_BUCKET_NAME}/config/validation-batch.yaml`, resolves all
`${_VAR}` placeholders, submits a Cloud Batch job, waits for `SUCCEEDED`, and
parses the `=== Validation Summary ===` table from Cloud Logging.

Run this check (re-sourcing `ae_validation/variables-infra.env` and
`ae_validation/variables-exp.env` so variables are present in the subshell) and
explain to the user that it verifies OCI container manifest access
(`Controller Image` and `Evaluator Image`), `GCS` read/write permissions,
`Pub/Sub` subscription access, and `AlphaEvolve API` allowlisting on a real
Cloud Batch VM:

```bash
set -a
source ae_validation/variables-infra.env
source ae_validation/variables-exp.env
set +a

# 1. Download validation-batch.yaml template from GCS
mkdir -p ae_validation
gcloud storage cp "gs://${_CLOUD_BUCKET_NAME}/config/validation-batch.yaml" ae_validation/validation-batch.yaml

# 2. Resolve environment variables safely using Python
python3 - << 'EOF'
import os, re
with open("ae_validation/validation-batch.yaml", "r") as f:
    content = f.read()
resolved = re.sub(r"\$\{([A-Za-z0-9_]+)\}", lambda m: os.environ.get(m.group(1), m.group(0)), content)
with open("ae_validation/validation-batch-resolved.yaml", "w") as f:
    f.write(resolved)
EOF

# 3. Construct a valid Cloud Batch Job ID (<= 63 chars, lowercase alphanumeric + hyphens)
SUFFIX="validation-$(date +%s)"
MAX_PREFIX=$((62 - ${#SUFFIX}))
SAFE_PREFIX=$(echo "${_DEPLOYMENT_NAME:-ae-exp}" | tr '[:upper:]_' '[:lower:]-' | cut -c1-${MAX_PREFIX} | sed 's/-$//')
VAL_JOB_ID="${SAFE_PREFIX}-${SUFFIX}"

# 4. Submit the validation job to Cloud Batch
gcloud batch jobs submit "${VAL_JOB_ID}" \
  --location="${_REGION}" \
  --project="${_PROJECT_ID}" \
  --config=ae_validation/validation-batch-resolved.yaml
```

Poll the validation job until it reaches `SUCCEEDED` (or `FAILED`), then fetch
the validation summary from Cloud Logging:

```bash
# Poll job state
while true; do
  STATE=$(gcloud batch jobs describe "${VAL_JOB_ID}" --location="${_REGION}" --project="${_PROJECT_ID}" --format="value(status.state)")
  echo "Validation job ${VAL_JOB_ID} state: ${STATE}"
  if [[ "${STATE}" == "SUCCEEDED" || "${STATE}" == "FAILED" ]]; then break; fi
  sleep 5
done

# Retrieve the Validation Summary table from Cloud Logging
JOB_UID=$(gcloud batch jobs describe "${VAL_JOB_ID}" --location="${_REGION}" --project="${_PROJECT_ID}" --format="value(uid)")
sleep 2
gcloud logging read "labels.job_uid=\"${JOB_UID}\"" \
  --project="${_PROJECT_ID}" \
  --limit=100 \
  --format="value(jsonPayload.message,textPayload)" | grep -A 12 "=== Validation Summary ==="
```

Confirm that all 5 components report `PASSED`:

-   `Controller Image | PASSED`
-   `Evaluator Image | PASSED`
-   `GCS | PASSED`
-   `Pub/Sub | PASSED`
-   `AlphaEvolve API | PASSED`

If any component reports `FAILED`, inspect the `Suggested Remediation` column in
the summary table and fix the underlying permission, image, or API issue before
running the controller.

## Step 4.3: Run a Short Controller Smoke Test (Notebook Cell 3: `controller-batch.yaml`)

Before launching a multi-hour evolutionary run, execute a quick end-to-end smoke
test of the controller and worker pipeline.

**Explain the Rationale to the User**: Explicitly inform the user that you are
overriding `_MAX_PROGRAMS_EVALUATED` to a small number (e.g., `2` or `4`) and
`_MAX_PROGRAMS_GENERATED` (e.g., `10`) for this validation run so the controller
evaluates just a few candidates and finishes within a few minutes. This verifies
that the controller container starts, creates an AlphaEvolve session, acquires
mutated candidates from Gemini, spawns Cloud Batch worker VMs, runs
`evaluator.py`, receives Pub/Sub completion events, and writes `results.csv` and
candidate archives without waiting for a full production run.

```bash
set -a
source ae_validation/variables-infra.env
source ae_validation/variables-exp.env
set +a

# 1. Download controller-batch.yaml template from GCS
mkdir -p ae_validation
gcloud storage cp "gs://${_CLOUD_BUCKET_NAME}/config/controller-batch.yaml" ae_validation/controller-batch.yaml

# 2. Override _MAX_PROGRAMS_EVALUATED for a fast smoke-test run (e.g., 2 or 4 candidates)
export _MAX_PROGRAMS_EVALUATED=2
export _MAX_PROGRAMS_GENERATED=10
export _MAX_DURATION_SECONDS="${_MAX_DURATION_SECONDS:-3600}"

# 3. Resolve controller-batch.yaml template
python3 - << 'EOF'
import os, re
with open("ae_validation/controller-batch.yaml", "r") as f:
    content = f.read()
resolved = re.sub(r"\$\{([A-Za-z0-9_]+)\}", lambda m: os.environ.get(m.group(1), ""), content)
with open("ae_validation/controller-batch-resolved.yaml", "w") as f:
    f.write(resolved)
EOF

# 4. Construct a valid 63-char-safe Controller Job ID and submit
CTRL_SUFFIX="controller-$(date +%s)"
MAX_PREFIX=$((62 - ${#CTRL_SUFFIX}))
SAFE_PREFIX=$(echo "${_DEPLOYMENT_NAME:-ae-exp}" | tr '[:upper:]_' '[:lower:]-' | cut -c1-${MAX_PREFIX} | sed 's/-$//')
CTRL_JOB_ID="${SAFE_PREFIX}-${CTRL_SUFFIX}"

gcloud batch jobs submit "${CTRL_JOB_ID}" \
  --location="${_REGION}" \
  --project="${_PROJECT_ID}" \
  --config=ae_validation/controller-batch-resolved.yaml
```

Monitor both the controller job and the spawned worker jobs (`*-worker-*`),
reproducing `list_batch_jobs_pretty()` from `run_notebook.ipynb`:

```bash
gcloud batch jobs list \
  --location="${_REGION}" \
  --project="${_PROJECT_ID}" \
  --filter="name:${SAFE_PREFIX}" \
  --format="table(name.basename(),status.state,createTime)"
```

Wait until the smoke-test controller job reaches `SUCCEEDED` (or stream its logs
via `gcloud logging read "labels.job_uid=\"${CTRL_UID}\""`).

## Step 4.4: Validate All Generated Experiment Artifacts (Notebook Analysis Cells)

Once the short controller job completes, verify every artifact generated under
`gs://${_CLOUD_BUCKET_NAME}/${_USER_EXPERIMENT_NAME}/`:

1.  **Results CSV (`results.csv`)**:
    -   Download and inspect
        `gs://${_CLOUD_BUCKET_NAME}/${_USER_EXPERIMENT_NAME}/results.csv`.
    -   Verify it is non-empty, contains header columns and evaluated candidate
        rows, and check that the primary metric scores are numeric and valid
        (not `None` or error sentinels).

```bash
gcloud storage cp "gs://${_CLOUD_BUCKET_NAME}/${_USER_EXPERIMENT_NAME}/results.csv" ae_validation/results.csv
head -n 20 ae_validation/results.csv
```

2.  **Archived Candidate Code & JSON Evaluation Results (`archive/` and
    `program_candidates/`)**:
    -   Verify that evaluated candidates were archived under
        `gs://${_CLOUD_BUCKET_NAME}/${_USER_EXPERIMENT_NAME}/archive/` (or
        `program_candidates/`), containing the mutated seed code files and
        `program_candidate_result.json` with scores and evaluation insights:

```bash
gcloud storage ls -r "gs://${_CLOUD_BUCKET_NAME}/${_USER_EXPERIMENT_NAME}/archive/" | head -n 30
```

3.  **Evaluator Execution Logs (`logs/`)**:
    -   Check `gs://${_CLOUD_BUCKET_NAME}/${_USER_EXPERIMENT_NAME}/logs/` (and
        Cloud Logging) to verify that evaluator containers executed cleanly
        without container startup errors, missing dependencies, or unhandled
        exceptions:

```bash
gcloud storage ls "gs://${_CLOUD_BUCKET_NAME}/${_USER_EXPERIMENT_NAME}/logs/"
```

4.  **Experiment-Specific Output Artifacts & Plots (`results/` or Custom
    Post-Processing Outputs)**:

    -   Inspect `evaluator.py` (including any `postprocess()` hook or custom
        save paths) in the experiment codebase to identify what domain-specific
        files or plots the experiment produces (for example,
        `results/optimized_airfoil.png` and `results/polar_coefficients.png` in
        `airfoil_optimization`, or custom benchmark tables/plots).
    -   Check `gs://${_CLOUD_BUCKET_NAME}/${_USER_EXPERIMENT_NAME}/results/` (or
        the experiment's configured output path) to confirm those artifacts were
        generated.
    -   Optionally generate or update the score progression plot from
        `results.csv` (reproducing `plot_metrics()` from `run_notebook.ipynb`).

5.  **Session State File (`current_experiment.json`)**:

    -   Verify whether
        `gs://${_CLOUD_BUCKET_NAME}/${_USER_EXPERIMENT_NAME}/current_experiment.json`
        was written.

## Step 4.5: Confirm With the User: Keep Smoke-Test Results or Clean Artifacts for a Fresh Experiment

After reporting the smoke-test verification results, **explicitly ask the user**
whether they want to:

-   **Option A - Keep the smoke-test results and resume from this checkpoint**:
    Retain `current_experiment.json`, `results.csv`, `archive/`, `logs/`, and
    `results/` so the next controller job continues evolving from the candidates
    already evaluated during the smoke test.
-   **Option B - Start a completely fresh experiment (Clean all smoke-test run
    artifacts)**: Purge all artifacts generated by the smoke test so the
    production run starts with a clean slate and a brand-new AlphaEvolve session
    ID.

### If the User Chooses Option B (Start a Fresh Experiment)

Clean all run artifacts under
`gs://${_CLOUD_BUCKET_NAME}/${_USER_EXPERIMENT_NAME}/` while **strictly
preserving** the deployed experiment configuration (`source/` and
`variables.env`):

1.  **List the artifacts to be removed** and confirm with the user:
    -   `current_experiment.json` (active AlphaEvolve session checkpoint)
    -   `results.csv` (smoke-test score table)
    -   `archive/` and `program_candidates/` (generated candidate programs and
        `program_candidate_result.json` files)
    -   `logs/` (smoke-test controller and evaluator execution logs)
    -   `results/` and any custom output plots/files generated by `evaluator.py`
2.  **Execute the cleanup commands**:

```bash
set -a
source ae_validation/variables-infra.env
source ae_validation/variables-exp.env
set +a

EXP_GCS_URI="gs://${_CLOUD_BUCKET_NAME}/${_USER_EXPERIMENT_NAME}"

# Remove session state, results.csv, candidate archives, logs, and output plots/files
for target in \
  "${EXP_GCS_URI}/current_experiment.json" \
  "${EXP_GCS_URI}/results.csv" \
  "${EXP_GCS_URI}/archive" \
  "${EXP_GCS_URI}/program_candidates" \
  "${EXP_GCS_URI}/logs" \
  "${EXP_GCS_URI}/results"; do
  if gcloud storage ls "${target}" >/dev/null 2>&1; then
    gcloud storage rm -r "${target}"
    echo "Cleaned: ${target}"
  fi
done

# Clean local temporary validation artifacts as well
rm -rf ae_validation/results.csv
```

3.  **Verify clean state**: Run `gcloud storage ls "${EXP_GCS_URI}/"` to confirm
    that only `source/` and `variables.env` remain in the experiment's GCS
    directory before launching the full production run.
