---
name: alphaevolve-experiment-creator
description: >-
  Designs, scaffolds, containerizes, and locally validates custom AlphaEvolve
  evolutionary code optimization experiments (in any custom local directory or
  under solutions/alphaevolve/user_examples/). Use when creating a new
  AlphaEvolve optimization experiment from scratch, adapting existing Python,
  C++, SPICE, MPI, CFD, or GPU code with EVOLVE-BLOCK markers, authoring
  experiment.yaml, evaluator.py, Makefile, or evaluator.Dockerfile, selecting a
  reference example template, or running offline Docker container dry-runs
  before cloud deployment. Don't use for provisioning or tearing down Google
  Cloud or Cluster Toolkit infrastructure, executing Cloud Batch smoke tests, or
  monitoring live cloud runs (use alphaevolve-experiment-deployer).
---

# AlphaEvolve Experiment Creator

Guide users through designing, scaffolding, containerizing, and offline-testing
custom evolutionary optimization experiments for
[Google Cloud HPC Solutions AlphaEvolve][hpc-alphaevolve]
(`solutions/alphaevolve/`). Once the experiment codebase passes offline
container verification, delegate cloud infrastructure deployment, Cloud Batch
smoke validation, monitoring, and teardown to the companion
`alphaevolve-experiment-deployer` skill.

[hpc-alphaevolve]: https://github.com/GoogleCloudPlatform/hpc-solutions/tree/main/solutions/alphaevolve
[create-exp]: https://github.com/GoogleCloudPlatform/hpc-solutions/blob/main/solutions/alphaevolve/CREATE_EXPERIMENT.md

## Architecture & Protected Directories Guardrail

AlphaEvolve evolves target code blocks by looping candidate generation through
containerized Cloud Batch worker evaluations (`Seed Program` -> `AlphaEvolve
API` -> `LLM Candidate Mutation` -> `Controller Container` -> `Evaluator
Container (Cloud Batch)` -> `Compile & Score` -> `Scores & Diagnostic
Insights`):

-   **Custom Experiment Directory (Any Local Path or `user_examples/<name>/`)**:
    Users can create their experiment in **any** self-contained directory on
    disk (e.g., `/absolute/path/to/<exp_name>/`), not necessarily under
    `solutions/alphaevolve/user_examples/<exp_name>/`. When deploying a custom
    directory outside the remote Git repository, pass the **full directory
    path** (`example_dir="/absolute/path/to/<exp_name>"` with
    `is_example_dir_local=true`) rather than a relative `user_examples/...`
    path.
-   **Protected Platform Directories (`google_framework/` and
    `infrastructure/`)**: Never modify files under
    `solutions/alphaevolve/google_framework/` or
    `solutions/alphaevolve/infrastructure/` when creating or adapting an
    experiment. Container customization belongs in per-experiment
    `evaluator.Dockerfile`, `Makefile`, `setup.sh`, and `requirements.txt`
    files, and custom Cloud Batch worker topologies belong in a per-experiment
    `eval-batch.yaml` override inside the experiment folder.

## Workflow Overview

Follow these five sequential phases, pausing at Confirmation Gate 1 before
writing experiment files:

1.  **Phase 1: Problem Formulation, Reference Template Selection & Spec
    Confirmation** - Map the workload to the closest reference example under
    `solutions/alphaevolve/user_examples/`, formulate the optimization
    objective, and get explicit user confirmation on `problem_description`,
    `program_language`, and `primary_metric` before creating files.
2.  **Phase 2: Seed Program & `EVOLVE-BLOCK` Instrumentation** - Structure
    initial source files and wrap only the mutable target logic in strict
    column-0 `EVOLVE-BLOCK` markers while isolating evaluation harnesses.
3.  **Phase 3: Declarative `experiment.yaml` & `evaluator.py` Authoring** -
    Define the declarative experiment spec and implement a timeout-guarded
    Python evaluator returning structured `AlphaEvolveProgramEvaluation` scores
    and actionable failure insights.
4.  **Phase 4: Worker Containerization & Build Customization** - Author
    `evaluator.Dockerfile` (inheriting from `BASE_IMAGE` under non-root UID
    `1000`), plus optional `Makefile`, `setup.sh`, `requirements.txt`, or custom
    per-experiment `eval-batch.yaml` Cloud Batch overrides (e.g., GPU flags and
    drivers, custom environment variables/volumes, or multi-node MPI).
5.  **Phase 5: Pre-Deployment Checklist, Offline Container Dry Run & Cloud
    Handoff** - Audit all files against the 8-point checklist, run the offline
    two-container schema dry run (`tools/test_container_communication.py`), and
    hand off cloud deployment to `alphaevolve-experiment-deployer`.

## Phase 1: Problem Formulation, Template Selection & Spec Confirmation

### Step 1.1: Select the Closest Reference Example

Inspect the matching reference directory under
`solutions/alphaevolve/user_examples/` to ground file layout and evaluator
patterns:

-   **Single-File Pure Python**: `user_examples/signal_processing` (single
    `main.py`, pure Python `evaluator.py` with `SIGALRM` / thread timeouts, no
    `Makefile`).
-   **Multi-File + Compiled Code (C++/Make)**:
    `user_examples/circle_packing_cloud_batch` or
    `user_examples/adaptive_sort_cpp` (`Makefile` compiling shared libraries or
    binaries at worker startup, with mutable and read-only `seed_files`).
-   **Multi-File Pure Python**: Combine the multi-file `seed_files` layout from
    `adaptive_sort_cpp` or `circle_packing_cloud_batch` with the pure Python
    evaluator pattern from `signal_processing` (no `Makefile` required).
-   **Multi-Node Distributed HPC / MPI**:
    `user_examples/nbody_molecular_dynamics` (custom per-experiment
    `eval-batch.yaml` with `taskCount > 1`, SSH daemon setup, and Cloud Batch
    `barrier` synchronization).
-   **External EDA/Physics Solvers & Post-Processing Plots**:
    `user_examples/netlist_simulation` (external `ngspice` simulator installed
    via `setup.sh`) or `user_examples/airfoil_optimization` (aerodynamic solver
    with a `postprocess(top_programs)` hook generating polar sweep plots).
-   **GPU-Accelerated ML / PyTorch Workloads**:
    `user_examples/llm_fine_tuning_cloud_batch` (GPU VM shapes such as
    `g2-standard-8`, per-experiment `eval-batch.yaml` enabling
    `installGpuDrivers: true` and `options: "--gpus all"`, build-time
    model/dataset caching, and VRAM error handling).

### Step 1.2: Confirmation Gate 1 (Confirm Core Spec Before Scaffolding)

**MANDATORY STOP BEFORE WRITING FILES**: Even if the user says "set this up" or
"create an experiment" alongside code and a high-level goal (such as "minimize
wirelength" or "optimize latency"), whenever you draft, infer, or refine the
exact `problem_description` text, `program_language`, `primary_metric` name, or
maximization negation formula, **do NOT create or write any files in the target
experiment directory yet**. Present your proposed specification and wait for
explicit user confirmation on all four items (plus the target directory path if
the user has not specified where to create the experiment — any local directory
path or `solutions/alphaevolve/user_examples/<exp_name>/`):

1.  **`problem_description`**: The optimization objective, domain invariants,
    and validity constraints sent to the LLM.
2.  **`program_language`**: Target language identifier (e.g., `python`, `cpp`,
    `spice`, `cuda`).
3.  **`primary_metric` and Maximization Direction**: Because AlphaEvolve always
    **maximizes** metric scores, confirm how minimization targets (such as
    latency, wirelength, runtime, or error) will be negated (e.g.,
    `-latency_ms`) or inverted so higher values strictly represent better
    candidates.
4.  **Target Mutable & Read-Only Scope (`seed_files`)**: Which files and code
    regions should sit inside `EVOLVE-BLOCK` markers (mutable) versus be listed
    in `seed_files` without markers as immutable read-only context (or kept
    evaluator-only outside `seed_files`).

Proceed to Phase 2 only after the user explicitly confirms these proposed
values (or when an existing `experiment.yaml` has already been confirmed).

## Phase 2: Seed Program & `EVOLVE-BLOCK` Instrumentation

Create the experiment folder at the user's chosen directory (any local path
`/path/to/<exp_name>/` or `solutions/alphaevolve/user_examples/<exp_name>/`)
and instrument the seed source files listed in `seed_files` (see the
[seed program, YAML, and evaluator templates](references/seed_and_evaluator_templates.md)):

1.  **Preserve Relative Subdirectory Paths**: If a seed file belongs in a
    subdirectory (e.g., `src/sort.hpp` and `src/benchmark.cpp`), place it at
    that relative path inside the experiment directory and list `"src/sort.hpp"`
    (never bare `"sort.hpp"`) in `experiment.yaml`. The framework preserves
    these exact relative paths when staging to GCS and copying candidates into
    `/app/experiment/`.
2.  **Mutable vs. Read-Only Context `seed_files` (Not Every Seed File Needs an
    `EVOLVE-BLOCK`)**:
    -   **At Least One Editable `EVOLVE-BLOCK` Overall**: Every file in
        `seed_files` must be non-empty, and **at least one** file across
        `seed_files` must contain an `EVOLVE-BLOCK-START` / `EVOLVE-BLOCK-END`
        region with **non-comment editable code** inside it (empty,
        whitespace-only, or comment-only blocks are rejected by the AlphaEvolve
        API).
    -   **Immutable Context Files Without Markers**: Additional files in
        `seed_files` **may omit `EVOLVE-BLOCK` markers entirely** to act as
        immutable, read-only context files (for example, `src/benchmark.hpp`
        and `src/benchmark.cpp` alongside mutable `src/sort.hpp` and
        `src/sort_impl.hpp` in `user_examples/adaptive_sort_cpp`).
    -   **Exposing Read-Only Context to the LLM
        (`include_full_program_in_prompt`)**: By default
        (`generation_settings.include_full_program_in_prompt: false`), only
        mutable `EVOLVE-BLOCK` regions are included in the LLM generation
        prompt. Set `generation_settings: { include_full_program_in_prompt:
        true }` in `experiment.yaml` whenever the LLM needs visibility into
        read-only `seed_files` (shared headers, data structures, or caller
        signatures) or surrounding boilerplate outside `EVOLVE-BLOCK`. Files
        omitted from `seed_files` remain purely local to `/app/experiment/` in
        the evaluator container.
3.  **Enforce Strict `EVOLVE-BLOCK` Marker Syntax**: On every file that marks
    mutable code sections, use language-appropriate line comments
    (`# EVOLVE-BLOCK-START` and `# EVOLVE-BLOCK-END` for Python and Python GPU
    kernels, `// EVOLVE-BLOCK-START` and `// EVOLVE-BLOCK-END` for C++ / CUDA
    C++, `* EVOLVE-BLOCK-START` for SPICE):
    -   **Column 0 Only**: Markers must sit on their own lines starting at
        column `0` with zero leading indentation, even when wrapping an
        indented method body.
    -   **No Inline Text or Comments on Marker Lines**: Never append
        descriptions or comments on the same line (for example,
        `// EVOLVE-BLOCK-START (helper)` is invalid and breaks block parsing).
    -   **No Blank Line or Trailing Whitespace After `EVOLVE-BLOCK-START`**: Do
        not leave empty lines or trailing spaces on or immediately following
        any `EVOLVE-BLOCK-START` line; start the mutable code on the very next
        line.
    -   **Multiple Blocks Supported**: A single file may contain multiple
        non-contiguous `EVOLVE-BLOCK` pairs to evolve several functions while
        freezing glue code between them.
4.  **Keep Evaluation & Benchmark Harnesses Immutable**: Never place scoring
    functions (`evaluate`), reference verification checks, timers, or test-case
    generators inside an `EVOLVE-BLOCK`. Allowing the LLM to mutate its own test
    harness enables trivial reward hacking (if the LLM should read the
    benchmark harness for context, include it without `EVOLVE-BLOCK` markers).

## Phase 3: Declarative `experiment.yaml` & `evaluator.py` Authoring

Read and follow the
[seed program, YAML, and evaluator templates](references/seed_and_evaluator_templates.md)
when authoring `experiment.yaml` and `evaluator.py`.

### Step 3.1: Create `experiment.yaml`

The universal runner (`alpha_evolve.runner`) reads `experiment.yaml` directly so
experiments require zero custom controller boilerplate. Verify all 7 required
top-level keys are populated: `title`, `problem_description`,
`program_language`, `primary_metric`, `initial_score` (float baseline score of
the unmodified seed program), `seed_files`, and `evaluator` (`script` filename
and `method` function name).

### Step 3.2: Implement `evaluator.py`

`evaluator.py` is the Python interface invoked inside the Evaluator Container
(working directory `/app/experiment`) to benchmark each candidate:

1.  **Enforce Strict Execution Timeouts**: Candidate programs generated by LLMs
    frequently introduce infinite loops, deadlocks, or pathological $O(N^3)$
    regressions. Guard every candidate execution with `signal.alarm(seconds)`,
    `concurrent.futures`, or `subprocess.run(..., timeout=seconds)` so an
    unresponsive candidate fails fast instead of stalling the Cloud Batch VM.
2.  **Follow Maximization & Failure Score Semantics**:
    -   **Negate Minimization Metrics**: AlphaEvolve maximizes `primary_metric`.
        When minimizing runtime, error, or resource counts, negate the metric
        (e.g., `-runtime_ms`) so higher numbers indicate improvement.
    -   **Return `score=None` on Invalid Candidates**: When compilation fails,
        verification assertions fail, `NaN`/`inf` appears, or execution times
        out, set `score=None` on `AlphaEvolveEvaluationScore` rather than
        returning `0.0` or arbitrary sentinel floats that corrupt parent
        selection.
3.  **Return Actionable Diagnostic `insights`**: The `insights` list is the
    LLM's only window into why a candidate failed or underperformed. Instead of
    generic `"Evaluation failed"` strings, include exact compiler stderr,
    failing test inputs, constraint violations, or bottleneck sub-scores (e.g.,
    `"Execution timed out (>10s) on N=10000 random_uniform input"`).
4.  **Validate Return Schema via `alpha_evolve.models`**: Construct results with
    `AlphaEvolveProgramEvaluation`, `AlphaEvolveEvaluationScores`,
    `AlphaEvolveEvaluationScore`, `AlphaEvolveEvaluationInsights`, and
    `AlphaEvolveEvaluationInsight` and return `.model_dump()`.
5.  **Optional `postprocess(top_programs)` Hook**: Define
    `postprocess(top_programs)` in `evaluator.py` if the user wants automatic
    end-of-run domain plots, comparison tables, or summary artifacts uploaded to
    GCS when the controller finishes.

## Phase 4: Worker Containerization & Build Customization

Read and follow the
[worker container, Makefile, and multi-node batch templates](references/container_and_batch_templates.md)
for **Step 4.1**, **Step 4.2**, and **Step 4.3**.

### Step 4.1: Author `evaluator.Dockerfile`

Every Cloud Batch experiment requires `evaluator.Dockerfile` inside the
experiment directory (`<exp_dir>/evaluator.Dockerfile`):

-   **Inherit from `ARG BASE_IMAGE` (Supports Custom Base Images)**: Always
    start `evaluator.Dockerfile` with `ARG BASE_IMAGE=python:3.12-slim-bookworm`
    followed by `FROM ${BASE_IMAGE}` — never hardcode `FROM <custom_image>`
    directly in `evaluator.Dockerfile` (which would strip `/opt/venv`, `uv`,
    `/app/src/alpha_evolve`, and the runner entrypoint). When a user needs a
    different underlying OS or CUDA base image (must be Debian/Ubuntu `apt-get`
    compatible, e.g., `nvidia/cuda:12.4.1-devel-ubuntu22.04`), keep
    `FROM ${BASE_IMAGE}` in `evaluator.Dockerfile` and pass
    `base_evaluator_image` (`--vars base_evaluator_image=<image>`) at deployment
    time so Cloud Build layers `base.Dockerfile` (`BASE_PYTHON_IMAGE`) onto that
    custom image as `alpha-evolve-base-custom` before building the evaluator.
-   **Install System & Python Dependencies at Build Time**:
    -   If extra OS packages/compilers are needed, place them in `setup.sh` and
        run `bash /app/experiment/setup.sh` during `docker build` so worker VMs
        never reinstall packages per candidate.
    -   If extra Python packages are needed, list them in `requirements.txt` and
        install via `uv pip install -r` on `/app/experiment/requirements.txt`
        (omit `--system` so packages install into `/opt/venv` configured by
        `base.Dockerfile`, and add `--require-hashes` **only** when
        `requirements.txt` contains `--hash=sha256:...` pins).
-   **Copy Experiment Files & Run Under Non-Root `evaluser` (UID `1000`)**: Use
    `COPY user_examples/<exp_name>/ /app/experiment/` (where `<exp_name>` is the
    basename of `<exp_dir>`, even when `<exp_dir>` lives outside
    `user_examples/`, because Cloud Build stages custom local `example_dir`
    directories into `user_examples/<exp_name>/` inside the build context), set
    `WORKDIR /app/src/alpha_evolve`, create `evaluser` (`UID 1000`) owning
    `/app/experiment`, and switch to `USER evaluser` so GCS FUSE volume mounts
    are writable.
-   **Never Override `ENTRYPOINT` and Never Run `RUN make` on Candidate Code**:
    `base.Dockerfile` defines `ENTRYPOINT` as
    `["python", "-m", "alpha_evolve.runner"]`. Do **not** add a custom
    `ENTRYPOINT`, and do **not** compile candidate code with `RUN make` in
    `evaluator.Dockerfile` — mutated candidate files overwrite
    `/app/experiment/` at container startup and are compiled dynamically per
    job.

### Step 4.2: Optional `Makefile` (Ahead-of-Time Compiled Languages Only)

-   **Python & Python GPU Workloads (Pure Python, PyTorch, Triton, CuPy, CUDA
    Python)**: Omit `Makefile` — Python GPU kernels are JIT-compiled or loaded
    dynamically at runtime without `make`.
-   **Ahead-of-Time Compiled Languages (C++, CUDA C++ `.cu`, Fortran)**: Create
    `Makefile` with an `all` target (e.g., `all: libkernel.so`). At worker
    startup, the runner copies the mutated candidate files into
    `/app/experiment/` and executes `make -C /app/experiment all` before calling
    `evaluator.py`.

### Step 4.3: Optional `eval-batch.yaml` (Custom Cloud Batch Worker Overrides)

If an experiment needs custom Cloud Batch job settings beyond the default
worker template (`infrastructure/batch_configs/eval-batch.yaml`), create
`eval-batch.yaml` **inside** the experiment directory
(`<exp_dir>/eval-batch.yaml`) rather than editing `infrastructure/`:

-   **GPU Runtime, Custom Environment Variables & Container Options** (see
    `user_examples/llm_fine_tuning_cloud_batch/eval-batch.yaml`): Set
    `installGpuDrivers: true` under `allocationPolicy.instances`, pass container
    runtime flags such as `options: "--gpus all"` (or `--shm-size`), add custom
    `environment.variables`, or mount extra volumes.
-   **Multi-Node Distributed HPC / MPI Topologies** (see
    `user_examples/nbody_molecular_dynamics/eval-batch.yaml`): Set `taskCount`
    > 1, enable `requireHostsFile: true` and `permissiveSsh: true`, and pair
    background SSH daemon runnables with Cloud Batch `barrier` synchronization
    before launching `mpirun`.

## Phase 5: Pre-Deployment Checklist, Offline Dry Run & Cloud Handoff

### Step 5.1: 8-Point Pre-Deployment Technical Audit

Verify every item before running containers or deploying to GCP:

1.  `experiment.yaml` defines `title`, `problem_description`,
    `program_language`, `primary_metric`, `initial_score`, `seed_files`, and
    `evaluator` (`script` + `method`), and user-confirmed metrics follow
    maximization semantics.
2.  Every file in `seed_files` exists at the exact relative path and is
    non-empty; **at least one** file across `seed_files` contains an
    `EVOLVE-BLOCK-START` / `EVOLVE-BLOCK-END` region with non-comment editable
    code (additional `seed_files` without markers are valid read-only context
    files, paired with `generation_settings.include_full_program_in_prompt:
    true` when the LLM needs to read them); and every `EVOLVE-BLOCK` marker
    starts at column `0` with no inline comments and no blank line or trailing
    whitespace immediately after `EVOLVE-BLOCK-START`.
3.  Benchmark harnesses and scoring functions sit strictly outside
    `EVOLVE-BLOCK` regions.
4.  `evaluator.py` enforces execution timeouts, returns `score=None` on invalid
    or failed candidates, populates diagnostic `insights`, and conforms to
    `AlphaEvolveProgramEvaluation`.
5.  `evaluator.Dockerfile` inherits `FROM ${BASE_IMAGE}`, copies files to
    `/app/experiment/`, sets `USER evaluser` (UID `1000`), preserves the base
    `ENTRYPOINT`, and does not run `RUN make` on candidate sources.
6.  Compiled workloads provide a `Makefile` with an `all` target (and pure
    Python workloads omit unnecessary `Makefile` rules).
7.  Extra OS/Python packages are installed at build time via `setup.sh` /
    `requirements.txt` inside `evaluator.Dockerfile` (`--require-hashes` used
    only when hashes are present).
8.  Zero files under `solutions/alphaevolve/google_framework/` or
    `solutions/alphaevolve/infrastructure/` were modified, and no temporary
    `__pycache__/` directories or scratch test scripts remain inside the
    experiment directory.

### Step 5.2: Offline Two-Container Schema & Seed Dry Run

Before consuming Cloud Batch quota or making AlphaEvolve API calls, read and
execute the
[offline two-container dry run commands](references/offline_container_dry_run.md)
from `solutions/alphaevolve/` (see [CREATE_EXPERIMENT.md][create-exp] for full
troubleshooting traces) to verify both the baseline seed score and
`test_container_communication.py` Pydantic schema compatibility, then clean up
`local_test_workspace/`.

-   **GPU & Accelerator Build-Machine Limitation**: Warn the user upfront if the
    experiment requires GPUs (`--gpus all`, CUDA kernels, or GPU-only libraries
    without a CPU fallback) because standard developer workstations and build
    machines typically do not have GPUs attached. On CPU-only build machines,
    validate `docker build` image compilation (and run the container dry run
    only if the evaluator supports a CPU fallback/surrogate mode), then defer
    real GPU runtime verification to Cloud Batch headless smoke testing in
    `alphaevolve-experiment-deployer`.

### Step 5.3: Handoff to `alphaevolve-experiment-deployer`

Once the experiment directory passes offline verification and the user is ready
to deploy to Google Cloud, load and follow the companion
`alphaevolve-experiment-deployer` skill to inspect or deploy base
infrastructure, stage the custom experiment via `gcluster deploy`, execute
headless Cloud Batch smoke tests, monitor runs, or tear down resources. When the
experiment lives in a custom local directory outside the remote Git repository
(rather than a committed `user_examples/<exp_name>` path), pass the **full
directory path** (`--vars is_example_dir_local=true --vars
example_dir="/absolute/path/to/<exp_name>"` with `file-system/gcs-objects`
source staging) rather than a relative `user_examples/...` path. Do not run
`gcluster deploy` unprompted during experiment authoring.
