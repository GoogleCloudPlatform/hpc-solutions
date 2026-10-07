# AlphaEvolve Solution

AlphaEvolve is an agentic capability created by Google that leverages Large Language Models (LLMs like Gemini) to programmatically discover and optimize code. By wrapping targeted functions or classes in your code, AlphaEvolve discovers faster, more accurate, or more resource-efficient implementations through an evolutionary loop.

The **AlphaEvolve Solution** provides the Python framework, execution engine, evaluation pipelines, CLI tools, and problem examples for running scalable evolutionary optimization. Cloud infrastructure deployment is managed declaratively via Cluster Toolkit blueprints located in [`blueprints/alphaevolve`](../../blueprints/alphaevolve). The solution uses the AlphaEvolve API of Gemini Enterprise (see [Supported Models](https://docs.cloud.google.com/gemini/enterprise/docs/alphaevolve/reference-guide/api-reference#supported_models)).

---

## How AlphaEvolve works

Once you configure and deploy the solution, the actual AlphaEvolve optimization process runs as follows:
1.  **Generation**: The controller invokes the AlphaEvolve API (backed by Gemini) to propose code candidates based on a target metric and previous successful runs.
2.  **Evaluation**: Candidates are evaluated for correctness and performance in parallel as Cloud Batch jobs.
3.  **Storage**: Generated candidate files, source code revisions, and numeric test metrics are archived in a secure Google Cloud Storage (GCS) bucket.
4.  **Feedback**: Results and insight logs are fed back to the AlphaEvolve API, allowing it to learn from successes and failures and intelligently sample better code in the next generation.

![Alpha Evolve Execution Workflow](https://services.google.com/fh/files/misc/ae_execution_workflow.png)

### The crux of the AlphaEvolve Solution: Defining your problem and evaluation function

To run your own optimization, you need to provide a starting program and an evaluation function (along with any dependencies required to run them). The solution comes with pre-defined examples of optimization problems and their evaluation functions. For detailed instructions, see **[CREATE_EXPERIMENT.md](CREATE_EXPERIMENT.md)**.

## Repository Structure

```
solutions/alphaevolve/
├── google_framework/          # Core Python framework and execution engine
│   ├── alpha_evolve/          # Framework package (controller, workers, client, config)
│   │   ├── runner.py          # Unified entrypoint for controller & evaluator workers
│   │   ├── controller.py      # Experiment controller & evolutionary loop
│   │   ├── cloud_evaluator.py # Batch evaluator worker logic
│   │   ├── cloud_batch.py     # Cloud Batch client & job submission
│   │   └── ...
│   ├── notebook/              # Interactive Jupyter notebooks for Colab Enterprise
│   │   └── run_notebook.ipynb
│   └── tests/                 # Unit test suite
├── user_examples/             # Preconfigured optimization problems & domain examples
│   ├── circle_packing_cloud_batch/
│   ├── adaptive_sort_cpp/
│   ├── netlist_simulation/
│   ├── signal_processing/
│   ├── airfoil_optimization/
│   ├── nbody_molecular_dynamics/
│   ├── llm_fine_tuning_cloud_batch/
│   └── tpu_gemm_cloud_batch/
├── tools/                     # Observability and interactive tools
│   ├── ae_shell.py            # Interactive CLI shell for inspecting experiment runs
│   └── monitoring/            # Real-time web dashboard & lineage tree visualizer
├── infrastructure/            # Base Dockerfiles and Batch execution templates
└── CREATE_EXPERIMENT.md       # Guide for authoring custom optimization problems
```

## Preconfigured Examples

We prepackaged a set of examples to get you started. For an overview of the provided examples see the following table:

| Example | Language | Primary Metric | Notes |
|---|---|---|---|
| `circle_packing_cloud_batch` | Python/C++ | `sum_of_radii` | Multi-file, triggers compilation |
| `adaptive_sort_cpp` | C++ | Composite Score | Multi-file, triggers compilation |
| `netlist_simulation` | SPICE | Performance Score | Uses ngspice for simulation |
| `signal_processing` | Python | `overall_score` | Uses SciPy |
| `nbody_molecular_dynamics` | C++ | `simulation_speed_score` | Multi-node MPI orchestration |
| `airfoil_optimization` | Python | `lift_to_drag_ratio` | Uses OpenFOAM for 2D CFD simulation |
| `llm_fine_tuning_cloud_batch` | Python | `neg_eval_loss` | GPU training/evaluation (PyTorch/LoRA) on Cloud Batch |
| `tpu_gemm_cloud_batch` | Python / JAX | `tflops` | Uses TPU for high-performance matrix multiplication |

---

## Infrastructure & Deployment

Infrastructure provisioning for AlphaEvolve on Google Cloud is managed using [Cluster Toolkit (`gcluster`)](https://docs.cloud.google.com/cluster-toolkit/docs/setup/configure-environment).

For complete step-by-step instructions on deploying the base infrastructure (APIs, GCS bucket, Pub/Sub, Artifact Registry, Vertex AI Colab Enterprise runtime) and building problem container images, please refer to the **[AlphaEvolve Blueprints README](../../blueprints/alphaevolve/README.md)**.
## Running Experiments
 
### Run the experiment

Now you can connect to Vertex AI Colab Enterprise and run the experiment.

1.  **Open Vertex AI Colab Enterprise**:
    * Navigate to the Google Cloud Console and go to [**Vertex AI** -> **Colab Enterprise**](https://console.cloud.google.com/vertex-ai/colab/notebooks) (or direct link [here](https://console.cloud.google.com/vertex-ai/colab/notebooks)).
    * Make sure you have selected the correct **Project** and **Region** matching your deployment configuration.
2.  **Import the notebook from Cloud Storage**:
    * Click the **Import** button (represented by an upload icon) at the top of the page.
    * In the **Import notebooks** dialog, select **Cloud Storage** as the import source.
    * Browse your GCS bucket (configured as `BUCKET_NAME` in your deployment) and select the notebook file at `notebook/run_notebook.ipynb` (i.e., `gs://<YOUR_BUCKET_NAME>/notebook/run_notebook.ipynb`).
    * Click **Import**. The notebook will now appear under your **My notebooks** list. Click on it to open it.
3.  **Connect to your custom runtime**:
    * Open the notebook.
    * Click the **Connect** dropdown in the top-right corner of the Colab Enterprise interface and select **Connect to custom runtime**.
    * Select the custom runtime provisioned by your deployment (named after your `deployment_name`, e.g., `alpha-evolve-infra` or the custom name you configured).
    * Click **Connect**.
4.  **Run the controller**:
    * Run the cells in the notebook to start the controller container and begin your experiment. The notebook is configured to dynamically discover available experiments from GCP project metadata and load your environment variables from GCS via an interactive prompt.
5.  **Validate Successful Run**:
    * You can monitor candidate scores and progress directly in the notebook cell execution output, specifically by executing cells under the 'Process Results Plot Optimization' section.
6.  **Find Generated Code and Results**:
    * All candidate code files and corresponding JSON evaluation results are stored in the GCS bucket under the 'archive' folder, organized by individual program candidate. You can also view these from the Colab notebook by checking the `data/` directory.

### Security Sandbox & Data Staging

The AlphaEvolve platform enforces GCS volume isolation on worker VMs to maintain strict security boundaries and data integrity:

* **Job Workspace (`_CANDIDATE_DIR`)**: Mounted at `${_MOUNT_PATH}/${_USER_EXPERIMENT_NAME}/${_PROGRAMS_DIR}/${_JOB_ID}` (Read-Write). Candidate code can only read/write files within its own job directory.
* **Shared Experiment Data**: Staged at `gs://<bucket>/<user_experiment_name>/data/` and mounted at `${_MOUNT_PATH}/${_USER_EXPERIMENT_NAME}/data` with **Read-Only (`-o ro`)** permissions. Untrusted candidate code cannot modify or corrupt input datasets.
* **Evaluator Logs**: Output logs from the container runner are teed to `${_MOUNT_PATH}/${_USER_EXPERIMENT_NAME}/logs/`.

### Run additional pre-configured experiments

If you want to run any of the other pre-configured experiments (or experiment with more advanced configuration options) on the **same base infrastructure**, follow the instructions in the respective section of the Jupyter Notebook. Note that because each deployment is isolated by its unique `user_experiment_name`, you can run multiple experiments **simultaneously** in separate Colab Enterprise notebook tabs or sessions without them interfering with one another.

## Where to go from here?

**If you want to create your own experiment...**
See **[CREATE_EXPERIMENT.md#organize-your-own-experiment](CREATE_EXPERIMENT.md#organize-your-own-experiment)** for detailed instructions on how to create a new experiment.

## Monitoring & Observability

AlphaEvolve includes a dedicated tool for analyzing experiment progress:

* **Web Dashboard & Lineage Tree (`tools/monitoring/`)**: Real-time web UI and graph visualizer for tracking score progression across generations.