# AlphaEvolve Monitoring & Observability

A monitoring suite for tracking AlphaEvolve discovery experiments, visualizing evolutionary lineage trees, and inspecting generated candidate source code in real time.

---

## 1. Deploy to Google Cloud Run

```bash
# 1. Build the container image
gcloud builds submit --config=tools/monitoring/cloudbuild.yaml --project=${_PROJECT_ID}

# 2. Deploy to Cloud Run
gcloud run deploy ae-monitor-ui \
  --image=gcr.io/${_PROJECT_ID}/ae-monitor-ui \
  --service-account="alpha-evolve-infra-ctrl-sa@${_PROJECT_ID}.iam.gserviceaccount.com" \
  --region=us-central1 \
  --project=${_PROJECT_ID} \
  --set-env-vars="_CLOUD_BUCKET_NAME=${_CLOUD_BUCKET_NAME},_PROJECT_ID=${_PROJECT_ID}"
```

---

## 2. Access the Dashboard

Start an authenticated proxy tunnel:

```bash
gcloud run services proxy ae-monitor-ui --port=8080 --region=us-central1 --project=${_PROJECT_ID}
```

Open **[http://localhost:8080/](http://localhost:8080/)** in your browser:

- **Active Experiments Directory (`/`)**: Discover and switch between any active or completed experiment across sessions.
- **Live Progress & Trajectory Chart (`/status?exp=<ID>`)**: Real-time generation progress, token usage, and score trajectory over time.
- **Evolutionary Lineage Forest (`/lineage?exp=<ID>`)**:
  - Interactive ancestry graph (Vis.js) & directory table.
  - **`🏆 BEST` candidate program** distinctly highlighted in gold.
  - Slide-over drawer to inspect full source code and evaluation insights for any candidate.

---

## 3. Configuration Variables

All tools use the standard controller environment variables. If you use custom names instead of defaults, you can pass them via shell environment variables (`export _ENGINE=...`), CLI flags (`-b ${_CLOUD_BUCKET_NAME}`), or Cloud Run `--set-env-vars`:

| Variable | Description | Default |
| :--- | :--- | :--- |
| `_PROJECT_ID` | GCP Project ID | Auto-detected |
| `_CLOUD_BUCKET_NAME` | GCS Bucket for variables and data | `""` |
| `_ENGINE` | Discovery Engine ID | `alpha-evolve-infra-experiment-engine` |
| `_LOCATION` | Discovery API Location | `global` |
| `_COLLECTION` | Discovery API Collection | `default_collection` |

---

## 4. Alternative: CLI Monitoring (`ae_monitor.py`)

You can also inspect experiments directly from your terminal without deploying to Cloud Run.

### Experiment Resource Name (`-e`)
The `-e` / `--experiment-name` flag expects the full Discovery Engine experiment resource path:
```
projects/${_PROJECT_NUMBER}/locations/${_LOCATION}/collections/${_COLLECTION}/engines/${_ENGINE}/sessions/<SESSION_ID>/alphaEvolveExperiments/<EXPERIMENT_ID>
```

> **Tip**: If you do not have the full path, run `--action list` to enumerate all active experiment resource paths in your project:
> ```bash
> python3 tools/monitoring/ae_monitor.py -p ${_PROJECT_ID} --action list
> ```

### Common Commands

```bash
# Instant status check
python3 tools/monitoring/ae_monitor.py -p ${_PROJECT_ID} -e "<EXPERIMENT_RESOURCE_PATH>" --action status

# Continuous polling loop (refreshes every 30s)
python3 tools/monitoring/ae_monitor.py -p ${_PROJECT_ID} -e "<EXPERIMENT_RESOURCE_PATH>" --action monitor --poll-interval 30

# Post-processing (generate lineage graph & export top candidate code to ./results)
python3 tools/monitoring/ae_monitor.py -p ${_PROJECT_ID} -e "<EXPERIMENT_RESOURCE_PATH>" --action postprocess -o ./results
```

---

## 5. Teardown & Cleanup

When you are finished monitoring, you can delete the Cloud Run service and the container image:

```bash
# 1. Delete the Cloud Run service
gcloud run services delete ae-monitor-ui \
  --region=us-central1 \
  --project=${_PROJECT_ID} \
  --quiet

# 2. (Optional) Delete the built container image
gcloud container images delete gcr.io/${_PROJECT_ID}/ae-monitor-ui \
  --force-delete-tags \
  --quiet
```
