# Tutorial: Evolving an Adaptive OpenROAD Detailed Routing (`drt`) Schedule with AlphaEvolve

This tutorial shows how to deploy and run [OpenROAD](https://github.com/The-OpenROAD-Project/OpenROAD) TritonRoute (`drt`) ([`src/drt/src/dr/FlexDR.cpp`](https://github.com/The-OpenROAD-Project/OpenROAD/blob/master/src/drt/src/dr/FlexDR.cpp)) evaluation and 1-shot AlphaEvolve CLI evolution on a multi-core Slurm cluster, replacing OpenROAD's static 65-step open-loop lookup table (`DEFAULT[it]`) with a closed-loop adaptive Python policy `next_step(it, drvs, history)` driven by live Design Rule Violation (`DRV`) progress.

> [!IMPORTANT]
> **Prerequisites & Automated Cluster Toolkit (`gcluster`) Setup:**
>
> 1. **Sync `openroad_drt_schedule` Artifacts First**: Before running `gcluster deploy`, make sure the entire `openroad_drt_schedule/` directory is synced locally on your workstation. The Slurm blueprint [`openroad-ae-slurm.yaml`](openroad-ae-slurm.yaml) uses `$(ghpc_stage(...))` data runners to bundle and upload `setup.sh`, `evaluator.py`, `drt_config.py`, `main.py`, and `tutorial/*` from your local checkout directly onto the cluster during deployment.
> 2. **Software & Dataset Setup Runs Automatically During Cluster Toolkit Deployment**: You do **not** need to install OpenROAD, Apptainer, or the ISPD datasets manually. During `gcluster deploy openroad-ae-slurm.yaml`, the cluster startup script automatically runs [`../setup.sh --slurm`](../setup.sh), which installs Apptainer, pulls the OpenROAD `26Q3` container (`/tools/containers/openroad-2130.sif`), downloads and patches the ISPD benchmarks into `/scratch/ispd_bench`, and runs pre-flight verification (and also unpacks `/tools/ae_poc/bin/ae` when `--vars ae_cli_bundle_uri="gs://..."` is supplied). Steps 1–2 (baseline and pre-evolved policy evaluation) work out of the box with zero external dependencies.
> 3. **Step 3 (`ae` CLI Evolution) Prerequisites**: Running Step 3 (`run_openroad_ae_evolve.sbatch`) additionally requires: (a) the AlphaEvolve `ae` CLI installed at `/tools/ae_poc/bin/ae` (via `--vars ae_cli_bundle_uri="gs://YOUR_BUCKET/ae_cli_bundle.tar.gz"` or `AE=/path/to/ae`), and (b) `discoveryengine.googleapis.com` enabled with an AlphaEvolve Discovery Engine provisioned in your GCP project (for example via [`blueprints/alphaevolve/alpha-evolve-infra.yaml`](../../../../../blueprints/alphaevolve/alpha-evolve-infra.yaml), which creates `alpha-evolve-infra-experiment-engine` — the default `--vars ae_engine` / `AE_ENGINE` value — or via `ae engine create`). Users without `ae` CLI access can run the managed **Google Cloud Batch** workflow ([`../README.md`](../README.md)) or evaluate the baseline and pre-evolved policy directly in Step 2.
> 4. **Cloud Batch vs. Slurm**: The main AlphaEvolve solution in this repository ([`blueprints/alphaevolve`](../../../../../blueprints/alphaevolve/README.md) and [`solutions/alphaevolve`](../../../README.md)) runs on **Google Cloud Batch** (see [`../README.md`](../README.md)). This tutorial covers the standalone **Slurm cluster** workflow via [`openroad-ae-slurm.yaml`](openroad-ae-slurm.yaml).

### Two Tutorials Available

| Tutorial | Dataset (`RUN_PRESET`) | What It Covers |
| :--- | :--- | :--- |
| **Tutorial 1 — Tiny Dataset** | `mini6` (`6` designs: `3` training + `3` held-out) | Complete end-to-end walkthrough: `gcluster deploy` + automated software setup + baseline & pre-evolved policy eval + `20`-program evolution + winner validation. |
| **Tutorial 2 — Full 20-Design Suite** | `all` (`20` designs: `ispd18_test1..10`, `ispd19_test1..10`) | Stages all 20 benchmarks. Users can **evaluate the baseline and pre-evolved policy only (skipping evolution)**, or run the full `150`-program evolution + `20`-design validation. |
| **Section 3 — Evolution Setup** | `mini_train` (`3` designs) / `stage1` (`5` designs) | Seed program (`main.py`), benchmark splits, the 4 solver invariants for zero-shot generalization, `routing_qor_score` formulation, and key evolved mechanisms. |

---

## 1. Tutorial 1: Tiny 6-Design Dataset (`mini6`)

Tutorial 1 uses the default **6-design Mini Dataset (`mini6`)** staged automatically during `gcluster deploy`:

- **Training Split (`mini_train`, `3` designs)**: `ispd18_test1`, `ispd18_test2`, `ispd19_test5`
- **Held-Out Split (`mini_holdout`, `3` designs)**: `ispd19_test1`, `ispd18_test5`, `ispd19_test3`

### Workflow Overview (Tutorial 1)

| Step | Description |
| :--- | :--- |
| **Step 1a. `gcluster deploy`** | Provisions VPC, Filestore shares (`/home`, `/scratch`), Slurm controller, login node, and `c3-standard-176` compute nodeset |
| **Step 1b. Automated Software Setup (`setup.sh --slurm`)** | Runs automatically on login-node boot during Cluster Toolkit deployment (`/var/log/openroad_ae_setup.log`) |
| **Step 2. Job 1 — C++ Baseline (`run_openroad_baseline.sbatch`)** *(Optional)* | Runs stock C++ `detailed_route` on the 6 `mini6` designs *(can be skipped; see note below)* |
| **Step 2. Job 2 — Python Baseline + Cand #14 (`run_openroad_ae_cand14_py.sbatch`)** | Self-contained 6-design `mini6` run of both the **Python single-step baseline** and **evolved policy (`14_oneshot_r3_winner.py`)** |
| **Step 3. Job 3 — 1-Shot Evolution (`run_openroad_ae_evolve.sbatch`, `20` progs)** | Evolves `20` candidate programs (`4` workers x `5` programs) on the 3-design `mini_train` split and exports `evolved_winner.py` |
| **Step 4. Job 4 — Validate Evolved Winner (`run_openroad_ae_cand14_py.sbatch`)** | Validates the newly discovered `evolved_winner.py` across all 6 `mini6` designs (`3` train + `3` held-out), reusing the cached baseline |

### Step 1: Sync `openroad_drt_schedule` & Deploy the Slurm Cluster with Cluster Toolkit

Ensure `solutions/alphaevolve/user_examples/openroad_drt_schedule` is synced locally so `gcluster` can stage its artifacts, then deploy [`openroad-ae-slurm.yaml`](openroad-ae-slurm.yaml):

```bash
cd solutions/alphaevolve/user_examples/openroad_drt_schedule/tutorial
gcluster deploy openroad-ae-slurm.yaml \
  --vars project_id="${PROJECT_ID}" \
  --vars region="${REGION}" \
  --vars zone="${ZONE}" \
  --vars stage_all20="0" \
  -w --auto-approve
```

*(Optional: if you plan to run Step 3 `ae` CLI evolution on Slurm, pass `--vars ae_cli_bundle_uri="gs://YOUR_BUCKET/ae_cli_bundle.tar.gz"` — and `--vars ae_engine="YOUR_ENGINE_ID"` if your Discovery Engine is not named `alpha-evolve-infra-experiment-engine` — so `setup.sh --slurm` installs and configures `/tools/ae_poc/bin/ae` automatically during cluster startup.)*

Once `gcluster deploy` finishes, SSH into the login node (`openroadae-slurm-login-001`) and verify that the automated Cluster Toolkit software setup has completed:

```bash
gcloud compute ssh openroadae-slurm-login-001 \
  --project="${PROJECT_ID}" \
  --zone="${ZONE}" \
  --tunnel-through-iap

# On the login node, check the automated Cluster Toolkit startup log:
tail -n 25 /var/log/openroad_ae_setup.log
```

Wait until `/var/log/openroad_ae_setup.log` displays `=== [Slurm Mode] Setup Complete ===`.

### Step 2: Evaluate the Baseline & Pre-Evolved Policy (`14_oneshot_r3_winner.py`)

Submit **[`run_openroad_ae_cand14_py.sbatch`](run_openroad_ae_cand14_py.sbatch)** and optionally **[`run_openroad_baseline.sbatch`](run_openroad_baseline.sbatch)**:

> [!TIP]
> **Job 1 (`run_openroad_baseline.sbatch`) Can Be Skipped**:
> Job 2 (`run_openroad_ae_cand14_py.sbatch`) is self-contained: when run on its own, it automatically executes both the **Python single-step baseline** and the **Python evolved policy (`14_oneshot_r3_winner.py`)** across all 6 `mini6` designs. Job 1 runs the standalone **C++ `detailed_route` baseline**, and the two baselines produce consistent iteration counts, `0` DRVs, wirelength, vias, and `FlexDR::main()` behavior.

```bash
# Job 1 (Optional): Standalone C++ `detailed_route` baseline on `mini6`
sbatch /scratch/openroad_drt_schedule/tutorial/run_openroad_baseline.sbatch

# Job 2 (Self-Contained): Python baseline + Candidate #14 on `mini6`
sbatch /scratch/openroad_drt_schedule/tutorial/run_openroad_ae_cand14_py.sbatch

# Monitor Slurm queue and view the side-by-side comparison table once completed:
squeue
cat /scratch/$USER/openroad_ae_cand14_py_logs/run_*/cand14_py_comparison.txt
```

### Step 3: Run 1-Shot Schedule Evolution (`20` Programs)

Submit **[`run_openroad_ae_evolve.sbatch`](run_openroad_ae_evolve.sbatch)**. By default (`MAX_PROGRAMS=20`), it automatically configures `/tools/ae_poc/bin/ae` for the cluster's GCP project, creates and seeds a new AlphaEvolve experiment from [`../main.py`](../main.py) and [`problem_description.md`](problem_description.md), reuses the cached baseline from Step 2, launches **4 parallel `16`-thread `ae experiment run` workers** (`5` programs per worker = `20` programs total) on the `3`-design `mini_train` split, and exports the Rank `#1` program to `/scratch/$USER/openroad_ae_evolve_logs/evolved_winner.py`:

```bash
# Submit the 20-program evolution job:
sbatch /scratch/openroad_drt_schedule/tutorial/run_openroad_ae_evolve.sbatch

# Monitor live progress and view the top-5 leaderboard at any time:
cat /scratch/$USER/openroad_ae_evolve_logs/latest_dashboard.md
EXP_NICK=$(cat /scratch/$USER/openroad_ae_evolve_logs/latest_exp_nick.txt)
/tools/ae_poc/bin/ae results best "$EXP_NICK" --top 5
```

### Step 4: Validate the Best Evolved Candidate on `mini6`

When Step 3 finishes, [`run_openroad_ae_evolve.sbatch`](run_openroad_ae_evolve.sbatch) automatically exports the Rank `#1` program to `/scratch/$USER/openroad_ae_evolve_logs/evolved_winner.py` (or you can export any candidate manually with `/tools/ae_poc/bin/ae program show <PROG_NICK> --experiment "$EXP_NICK" --output-file /scratch/$USER/openroad_ae_evolve_logs/evolved_winner.py`).

Validate `evolved_winner.py` across all 6 `mini6` designs (`3` training + `3` held-out) by passing `PROGRAM_FILE` to [`run_openroad_ae_cand14_py.sbatch`](run_openroad_ae_cand14_py.sbatch) (reusing the cached baseline):

```bash
PROGRAM_FILE=/scratch/$USER/openroad_ae_evolve_logs/evolved_winner.py \
  sbatch /scratch/openroad_drt_schedule/tutorial/run_openroad_ae_cand14_py.sbatch

# View the validation summary table once completed:
cat $(ls -t /scratch/$USER/openroad_ae_cand14_py_logs/run_*/cand14_py_comparison.txt | head -n 1)
```

---

## 2. Tutorial 2: Full 20-Design Suite (`all`)

Tutorial 2 runs across all **20 `ISPD-18` (`ispd18_test1..10`) and `ISPD-19` (`ispd19_test1..10`)** detailed routing contest benchmarks.

> [!TIP]
> **Evaluate-Only Mode (Skip Evolution)**:
> You do **not** have to run the full evolution loop in Tutorial 2. You can run **Step 1** and **Step 2** to evaluate the stock OpenROAD baseline and the pre-evolved policy ([`candidates/14_oneshot_r3_winner.py`](candidates/14_oneshot_r3_winner.py)) across all 20 designs and skip **Step 3** (evolution).

### Step 1: Stage All 20 ISPD-18 & ISPD-19 Benchmarks

You can stage all 20 benchmarks either during `gcluster deploy` (by setting `--vars stage_all20="1"`) or on an already-deployed cluster by running `setup.sh --slurm --all20` on the login node:

```bash
# Option A: During initial cluster deployment from your workstation:
gcluster deploy openroad-ae-slurm.yaml \
  --vars project_id="${PROJECT_ID}" \
  --vars region="${REGION}" \
  --vars zone="${ZONE}" \
  --vars stage_all20="1" \
  -w --auto-approve

# Option B: On an existing Slurm login node (downloads the remaining 14 designs into /scratch/ispd_bench):
sudo /scratch/openroad_drt_schedule/setup.sh --slurm --all20
```

### Step 2: Evaluate the Baseline & Pre-Evolved Policy on All 20 Designs (`RUN_PRESET=all`)

Run [`run_openroad_ae_cand14_py.sbatch`](run_openroad_ae_cand14_py.sbatch) (and optionally [`run_openroad_baseline.sbatch`](run_openroad_baseline.sbatch)) with `RUN_PRESET=all`:

```bash
# Self-contained 20-design evaluation of Baseline + Candidate #14:
RUN_PRESET=all sbatch /scratch/openroad_drt_schedule/tutorial/run_openroad_ae_cand14_py.sbatch

# Optional: standalone 20-design C++ `detailed_route` baseline:
RUN_PRESET=all sbatch /scratch/openroad_drt_schedule/tutorial/run_openroad_baseline.sbatch
```

*(Note: On GCP Slurm clusters with OS Login, always pass environment variables as prefixes before `sbatch` — e.g., `RUN_PRESET=all sbatch ...` — rather than `sbatch --export=ALL,RUN_PRESET=all ...`, so Slurm captures the environment on the login node without triggering remote `get_user_env` lookups on compute nodes.)*

#### Inspecting Raw OpenROAD Logs (`DRT-0267`) and Summary Reports

Both runs produce native OpenROAD stdout logs containing `[INFO DRT-0195]` (iteration start), `[INFO DRT-0199]` (remaining DRC violations), `[INFO DRT-0267]` (`cpu time = HH:MM:SS, elapsed time = HH:MM:SS, memory = ..., peak = ...`), `Total wire length = ... um`, and `Total number of vias = ...`:

- **Log 1 — C++ Baseline (`run_openroad_baseline.sbatch`)**:
  `/scratch/$USER/openroad_baseline_logs/run_${RUN_ID}/${bm}/patched/${bm}/${bm}.log`
- **Log 2 — Python Baseline & Evolved Policy (`run_openroad_ae_cand14_py.sbatch`)**:
  - Python Baseline: `/scratch/$USER/openroad_ae_cand14_py_logs/run_${RUN_ID}/b1_data/candidate_logs/baseline__${bm}.openroad.log`
  - Evolved Policy: `/scratch/$USER/openroad_ae_cand14_py_logs/run_${RUN_ID}/b1_data/candidate_logs/${CODE_SHA}__${bm}.openroad.log`

```bash
# 1. Print the side-by-side Baseline vs. Evolved Policy CLI comparison table:
cat /scratch/$USER/openroad_ae_cand14_py_logs/run_${RUN_ID}/cand14_py_comparison.txt

# 2. Format the master CSV summary tables in the terminal:
column -s, -t < /scratch/$USER/openroad_ae_cand14_py_logs/run_${RUN_ID}/all_ae_cand14_py_summary.csv

# 3. Inspect DRT-0267 timing entries along with the OpenROAD phase/iteration header for a design:
grep -E "DRT-0166|DRT-0179|DRT-0182|DRT-0187|DRT-0195|DRT-0198|DRT-0199|DRT-0267" \
  /scratch/$USER/openroad_ae_cand14_py_logs/run_${RUN_ID}/b1_data/candidate_logs/*__ispd19_test9.openroad.log

# 4. Parse any raw OpenROAD log directly into structured JSON:
python3 /scratch/$USER/openroad_drt_schedule/evaluator.py \
  --parse-log /scratch/$USER/openroad_ae_cand14_py_logs/run_${RUN_ID}/b1_data/candidate_logs/*__ispd19_test9.openroad.log
```

**How `DRT-0267` Entries in the Raw OpenROAD Logs Map to Routing Phases**:

- **Lines 1–5 of `DRT-0267` (Pre-Routing Setup + Iteration 0 `it0`)**: Both baseline and candidate runs execute identical stock OpenROAD initialization (`DRT-0166` pin access, `DRT-0179` gr pin query, `DRT-0182` track assignment, `DRT-0187` data prep) and stock `it0` (`DRT-0195 Start 0th optimization iteration`, `it0_wall_s`).
- **From Line 6 of `DRT-0267` onward (`Start 1st .. Nth ... iteration`, `it >= 1`)**: Measures the Search-and-Repair loop (`sr_wall_s`), where `next_step(it, drvs, history)` selects the 9 `drt::step_dr` parameters at each iteration until `drvs == 0`.

### Step 3 (Optional): Run Full 150-Program Evolution & Validate on All 20 Designs

If you want to run a full 150-program evolution search (on `mini_train` or `stage1`) and validate the newly discovered policy across all 20 benchmarks:

```bash
# 1. Launch 150-program evolution (38 iterations per worker across 4 parallel 16T workers):
MAX_PROGRAMS=150 sbatch /scratch/openroad_drt_schedule/tutorial/run_openroad_ae_evolve.sbatch

# (Optional: to evolve on the 5-design `stage1` split instead of the 3-design `mini_train` split:)
# B1_DESIGNS=stage1 MAX_PROGRAMS=150 sbatch /scratch/openroad_drt_schedule/tutorial/run_openroad_ae_evolve.sbatch

# 2. Once evolution finishes, validate the exported winner across all 20 designs:
RUN_PRESET=all PROGRAM_FILE=/scratch/$USER/openroad_ae_evolve_logs/evolved_winner.py \
  sbatch /scratch/openroad_drt_schedule/tutorial/run_openroad_ae_cand14_py.sbatch
```

---

## 3. Evolution Setup: Problem Formulation, Solver Invariants & Scoring

### 3.1 Problem Overview & Seed Program ([`../main.py`](../main.py), [`../drt_config.py`](../drt_config.py))

In back-end physical design (`RTL-to-GDSII`), Detailed Routing (`DR`) takes a legalized placement (`.def`), technology rules (`.lef`), and 3D Global Routing (`GR`) guides (`.guide`), and assigns physical metal wire segments and vias onto legal routing tracks. OpenROAD's `TritonRoute` (`drt`) partitions the die into spatial GCell clip windows (`size x size` GCells shifted by `offset`), runs an initial whole-chip maze pass at `it = 0`, and iterates through a search-and-repair loop (`it = 1 .. 64`).

- **[`../drt_config.py`](../drt_config.py)** defines the 65-row stock `DEFAULT` schedule table (`FlexDR.cpp:1734-1798`), routing constants (`SHAPE`, `MARKER`, `DRC`, `ALL`, `NEARDRC`, `ITER0_ARGS`), and benchmark splits (`DESIGN_SETS`).
- **[`../main.py`](../main.py)** imports `DEFAULT` from [`../drt_config.py`](../drt_config.py), wraps `next_step(it, drvs, history)` inside `# EVOLVE-BLOCK-START` / `# EVOLVE-BLOCK-END`, and includes `evaluate_design_loop()` and `compute_routing_qor_score()` below `# EVOLVE-BLOCK-END` to give AlphaEvolve context on how `next_step` is called and scored.

At each iteration `it >= 1`, `next_step(it, drvs, history)` receives the current iteration index `it`, remaining `drvs`, and `history` (list of `(args_9tuple, drvs_after_step, step_wall_s)` from `it = 0 .. it - 1`) and returns `(size, offset, mazeEndIter, drcCost, markerCost, fixedShapeCost, markerDecay, ripupMode, followGuide)` or `None`:

- `size`: GCell clip window width/height (odd integer `3..25`).
- `offset`: Grid shift offset (`-size <= offset <= 0`, `-24 <= offset <= 0`).
- `mazeEndIter`: Maximum A\* maze inner iterations per clip worker (`1..64`).
- `drcCost` (`1..1024`), `markerCost` (`0..1024`), `fixedShapeCost` (`1..2048`): A\* penalty weights.
- `markerDecay`: Historical marker decay factor (`0.50..1.00`).
- `ripupMode`: `0 = DRC` (violating nets only), `1 = ALL` (all nets in clip; triggers whole-chip `State::OPTIMIZATION`), `2 = NEARDRC` (violating nets plus nearby legal blockers).
- `followGuide`: Whether A\* strongly honors global routing guides (`True` or `False`).

### 3.2 Benchmark Splits (`DESIGN_SETS`)

| Preset (`RUN_PRESET` / `B1_DESIGNS`) | # Designs | Benchmarks | Staged by Default (`setup.sh`)? | Role |
| :--- | :---: | :--- | :---: | :--- |
| **`mini_train` (Default Evolution)** | `3` | `ispd18_test1`, `ispd18_test2`, `ispd19_test5` | Yes | Fast high-DRV decay across two net scales (`ispd18_test1,2`) + 5-metal-layer stubborn low-DRV tail convergence (`ispd19_test5`, `30` baseline iters) |
| **`mini_holdout` (Mini Held-Out)** | `3` | `ispd19_test1`, `ispd18_test5`, `ispd19_test3` | Yes | Tests zero-shot generalization to unseen `ISPD-19` rules (`ispd19_test1,3`) and `2x` larger net/DRV scale (`ispd18_test5`) |
| **`mini6` (Default Slurm Eval)** | `6` | `mini_train` (`3`) + `mini_holdout` (`3`) | Yes | Combined 6-design dataset used in Tutorial 1 |
| **`stage1`** | `5` | `ispd18_test1,2,3,5`, `ispd19_test5` | Requires `--all20` | 5-design training split |
| **`all` / `all20`** | `20` | `ispd18_test1..10`, `ispd19_test1..10` | Requires `--all20` | Full 20-design `ISPD-18` and `ISPD-19` benchmark suite used in Tutorial 2 |

### 3.3 Four Solver Invariants for Zero-Shot Generalization ([`../experiment.yaml`](../experiment.yaml) & [`../evaluator.py`](../evaluator.py))

Evolving on small training designs alone has three failure modes when generalizing to held-out designs:

1. **Scale / CPU Overfitting**: Branching on absolute initial DRVs (`history[0][1]`) or absolute step wall-clock time (`h[2]`) overfits to small designs and specific CPU SKUs.
2. **Whole-Chip Blowups & Mid-DRV Stagnation**: Issuing `ripupMode = 1 (ALL)` when `drvs > 10` (or unbounded `mazeEndIter > 12` on `3x3 ALL`) triggers whole-chip `State::OPTIMIZATION` blowups in [`src/drt/src/dr/FlexDRFlow.cpp`](https://github.com/The-OpenROAD-Project/OpenROAD/blob/master/src/drt/src/dr/FlexDRFlow.cpp), while failing to escalate `size` (`9 -> 11`) or oscillating back to `5x5 DRC (0)` during mid-DRV (`10 < drvs <= 100`) plateaus stalls on held-out congested designs (`ispd18_test4`, `ispd18_test10`).
3. **Repeating 4-Step Loops**: A naive 4-step periodic loop (repeating every 4 stagnant steps, e.g., `(stagnant_steps - 4) % 4`) can trap the router in a limit cycle at low DRVs (`drvs <= 3`) and fail to converge.

To guarantee zero-shot generalization across held-out benchmarks, [`../experiment.yaml`](../experiment.yaml) and `_check_scale_invariance()` in [`../evaluator.py`](../evaluator.py) enforce **4 solver invariants** before launching OpenROAD:

1. **Scale & Runtime Invariance**: Drive all transitions from live `drvs`, relative DRV reduction rate `(prev_drvs - drvs) / max(1, prev_drvs)`, `stagnant_steps`, and `last_success_size` (never `history[0][1]` or `h[2]`).
2. **High-DRV Initial Repair Bound (`it == 1`)**: Use targeted `ripupMode = 0 (DRC)` with compact `5x5` clips (`mazeEndIter <= 3`, `drcCost <= 16`, `fixedShapeCost <= 16`).
3. **No Whole-Chip `ripupMode = 1 (ALL)` & Mid/High-DRV Escalation (`drvs > 10`)**: Keep `ripupMode` in `{0 (DRC), 2 (NEARDRC)}` with `mazeEndIter <= 12` (`drvs > 100`) or `<= 16` (`drvs > 15`). For both `drvs > 100` and `10 < drvs <= 100`, stay in `ripupMode = 2 (NEARDRC)` with `size >= 7` whenever progress slows (`improvement_rate < 0.15` for `drvs > 100` or `< 0.10` for `10 < drvs <= 100`), escalate to `9x9 NEARDRC` (`drcCost >= 128`) at `stagnant_steps == 2` and `11x11 NEARDRC` (`drcCost >= 256`) at `stagnant_steps >= 3`, and track `last_success_size` so clip windows step down gradually (`size >= 7`) after a `>= 9x9` breakthrough.
4. **Non-Repeating Low-DRV Endgame (`drvs <= 10`)**: Escalate through `5x5` and `7x7 NEARDRC (2)` (`stagnant_steps in (2, 3)`), sweep all 3 grid offsets (`0, -1, -2`) of localized `3x3 ALL (1)` pin-unboxing (`stagnant_steps in (4, 5, 6)`, `mazeEndIter <= 12`, `drcCost <= 64`), run a `7x7 DRC (0)` cleanup pass (`stagnant_steps == 7`), and fall back to `DEFAULT[min(64, 30 + (stagnant_steps - 8))]` (`stagnant_steps >= 8`) without repeating a 4-step loop.

### 3.4 Composite Routing QoR Score (`routing_qor_score`)

The primary optimization metric `routing_qor_score` (maximized by AlphaEvolve) combines **DRC violation (`DRV`) counts**, **total routed wirelength (`wl`)**, **total via count (`vias`)**, and **Search-and-Repair (`it >= 1`) wall-clock runtime (`wall_s`)** relative to the stock OpenROAD `DEFAULT` baseline:

- **Pre-Flight Solver-Invariance Gate**: Candidates violating any of the 4 solver invariants in `_check_scale_invariance()` receive `routing_qor_score = 0.0` without launching OpenROAD.
- **Unconverged DRC Penalty (`left_drvs > 0`)**: If any DRC violations remain on any evaluated benchmark (`left_drvs = sum(cand[d]["drvs"]) > 0`), evaluation fails fast and returns:
  `routing_qor_score = -1000.0 * (1 + skipped_designs) - left_drvs`
- **Converged Routing QoR & Speedup Score (`left_drvs == 0`)**: When `drvs == 0` across all evaluated benchmarks (and `wl >= 0.95 * base_wl`):
  `routing_qor_score = 100.0 * (0.5 * geomean_sr_speedup + 0.5 * agg_sr_speedup) - wl_penalty - via_penalty - slowdown_penalty`
  where `wl_penalty = 1000.0 * max(0.0, worst_wl_ratio - 1.005)`, `via_penalty = 500.0 * max(0.0, worst_via_ratio - 1.020)`, and `slowdown_penalty = 150.0 * sum(max(0.0, cand_sr_s / base_sr_s - 1.0))`.

### 3.5 Key Mechanisms Discovered ([`candidates/14_oneshot_r3_winner.py`](candidates/14_oneshot_r3_winner.py))

1. **Targeted `5x5 DRC` at `it = 1` Instead of Whole-Chip `7x7 ALL`**: Stock OpenROAD runs three consecutive whole-chip `ripupMode = 1 (ALL)` passes at `it = 0, 1, 2`. At `it = 1`, `14_oneshot_r3_winner` switches immediately to `ripupMode = 0 (DRC)` with `5x5` clips (`(5, -5, 3, 8, 8, 8, 0.95, 0, True)`), leaving already-clean nets untouched.
2. **History-Driven Clip Sizing (`last_success_size`)**: Tracks the most recent DRV-reducing clip size and sets `adaptive_size = 9 if last_success_size > 11 else max(5, last_success_size - 2)`, keeping clip boxes compact (`5x5`) during fast `DRC` progress while avoiding abrupt size drops (`>= 7x7`) after a large-box plateau escape.
3. **Velocity- and Stall-Triggered `NEARDRC` Escalation (`9x9` / `11x11`)**: When relative DRV reduction `(prev_drvs - drvs) / prev_drvs` slows below `15%` (`drvs > 100`) or `10%` (`10 < drvs <= 100`), stays in `ripupMode = 2 (NEARDRC)` with `size >= 7`. When `stagnant_steps >= 2` at `drvs > 10`, expands to `9x9 NEARDRC` (`cost = 256`) at `stagnant_steps == 2` and `11x11 NEARDRC` (`mazeEndIter = 12, cost = 512..1024`) at `stagnant_steps >= 3` without triggering whole-chip `ripupMode = 1 (ALL)`.
4. **Staged Endgame Pin-Unboxing (`drvs <= 10`)**: Escalates through `5x5 NEARDRC` (`stagnant_steps == 2`) -> `7x7 NEARDRC` (`stagnant_steps == 3`) -> localized `3x3 ALL` across all 3 grid phases `0, -1, -2` (`stagnant_steps == 4..6`) -> `7x7 DRC` cleanup (`stagnant_steps == 7`) -> fallback `DEFAULT[30 + (stagnant_steps - 8)]`.
