#!/bin/bash
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
#SBATCH --job-name=openroad_ae_evolve
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --exclusive
#SBATCH --time=06:00:00
#SBATCH --output=/scratch/%u/openroad_ae_evolve_logs/slurm_%j.out
#SBATCH --error=/scratch/%u/openroad_ae_evolve_logs/slurm_%j.err

# Launches 4 parallel 16-thread `ae experiment run` workers on 1 x c3-standard-176
# (pinned to physical core sets 0-15, 16-31, 44-59, 60-75) to evolve `next_step()`
# on the 3-design `mini_train` split (`ispd18_test1`, `ispd18_test2`, `ispd19_test5`;
# set `B1_DESIGNS=stage1` to use the 5-design split).
#
# Usage:
#   mkdir -p /scratch/$USER/openroad_ae_evolve_logs
#   sbatch /scratch/openroad_drt_schedule/tutorial/run_openroad_ae_evolve.sbatch [EXP_NICK]

set -euo pipefail

export OMP_WAIT_POLICY=PASSIVE
export PYTHONDONTWRITEBYTECODE=1

USER_SCRATCH="/scratch/${USER}"
export EXAMPLE_DIR="${EXAMPLE_DIR:-${USER_SCRATCH}/openroad_drt_schedule}"
if [[ ! -f "${EXAMPLE_DIR}/evaluator.py" && -f "/scratch/openroad_drt_schedule/evaluator.py" ]]; then
  export EXAMPLE_DIR="/scratch/openroad_drt_schedule"
elif [[ ! -f "${EXAMPLE_DIR}/evaluator.py" && -f "$(pwd)/evaluator.py" ]]; then
  export EXAMPLE_DIR="$(pwd)"
fi
export PYTHONPATH="${EXAMPLE_DIR}:${PYTHONPATH:-}"

export AE="${AE:-${USER_SCRATCH}/ae_poc/bin/ae}"
if [[ ! -x "${AE}" && -x "/tools/ae_poc/bin/ae" ]]; then
  export AE="/tools/ae_poc/bin/ae"
elif [[ ! -x "${AE}" ]] && command -v ae >/dev/null 2>&1; then
  export AE="$(command -v ae)"
fi
if [[ ! -x "${AE}" ]]; then
  echo "ERROR: AlphaEvolve CLI ('ae') not found at ${AE} or /tools/ae_poc/bin/ae." >&2
  echo "Pass --vars ae_cli_bundle_uri=gs://YOUR_BUCKET/ae_cli_bundle.tar.gz during 'gcluster deploy' or set AE=/path/to/ae." >&2
  exit 1
fi
export AE_ENGINE="${AE_ENGINE:-alpha-evolve-infra-experiment-engine}"
export OPENROAD_BIN="${OPENROAD_BIN:-${USER_SCRATCH}/bin/openroad_wrapper.sh}"
if [[ ! -x "${OPENROAD_BIN}" && -x "/tools/bin/openroad_wrapper.sh" ]]; then
  export OPENROAD_BIN="/tools/bin/openroad_wrapper.sh"
fi
if [[ ! -x "${OPENROAD_BIN}" ]]; then
  SIF_PATH="${OPENROAD_SIF_PATH:-/tools/containers/openroad-2130.sif}"
  if [[ ! -f "${SIF_PATH}" && -f "${USER_SCRATCH}/containers/openroad-2130.sif" ]]; then
    SIF_PATH="${USER_SCRATCH}/containers/openroad-2130.sif"
  fi
  mkdir -p "$(dirname "${OPENROAD_BIN}")"
  cat << WEOF > "${OPENROAD_BIN}"
#!/bin/bash
export OMP_WAIT_POLICY=PASSIVE
OR_BIN="\${OR_BIN:-/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad}"
exec apptainer exec --bind /scratch:/scratch --bind /tmp:/tmp \\
  "${SIF_PATH}" "\${OR_BIN}" "\$@"
WEOF
  chmod +x "${OPENROAD_BIN}"
fi
export ISPD_BENCH_DIR="${ISPD_BENCH_DIR:-${USER_SCRATCH}/ispd_bench}"
if [[ ! -d "${ISPD_BENCH_DIR}" && -d "/scratch/ispd_bench" ]]; then
  export ISPD_BENCH_DIR="/scratch/ispd_bench"
fi
export B1_DESIGNS="${B1_DESIGNS:-mini_train}"
export B1_THREADS="${B1_THREADS:-16}"
export B1_WALL_LIMIT_FACTOR="${B1_WALL_LIMIT_FACTOR:-0.85}"
export B1_FAIL_FAST="${B1_FAIL_FAST:-1}"
export MAX_PROGRAMS="${MAX_PROGRAMS:-20}"
export WORKER_ITERS="${WORKER_ITERS:-$(( (MAX_PROGRAMS + 3) / 4 ))}"
export EVAL_TIMEOUT="${EVAL_TIMEOUT:-1500}"

LOG_ROOT="${USER_SCRATCH}/openroad_ae_evolve_logs"
export B1_DATA_DIR="${B1_DATA_DIR:-${LOG_ROOT}/b1_data}"
mkdir -p "${LOG_ROOT}" "${B1_DATA_DIR}/cache" "${B1_DATA_DIR}/candidate_logs"

# Auto-configure `ae` CLI for this GCP project if default.toml is not yet present
if [[ ! -f "${HOME}/.config/ae/profiles/default.toml" ]]; then
  GCP_PROJECT_NUM="${GCP_PROJECT_NUM:-$(curl -sf -m 2 -H 'Metadata-Flavor: Google' http://metadata.google.internal/computeMetadata/v1/project/numeric-project-id || true)}"
  if [[ -n "${GCP_PROJECT_NUM}" ]]; then
    "${AE}" config \
      --project="${GCP_PROJECT_NUM}" \
      --engine="${AE_ENGINE}" \
      --location=global \
      --models=gemini-3.5-flash
  fi
fi

EXP_NICK="${1:-${EXP_NICK:-}}"
if [[ -z "${EXP_NICK}" ]]; then
  echo "No EXP_NICK provided; auto-creating and seeding a new AlphaEvolve experiment..."
  PROBLEM_FILE="${EXAMPLE_DIR}/tutorial/problem_description.md"
  if [[ ! -f "${PROBLEM_FILE}" ]]; then
    PROBLEM_FILE="${EXAMPLE_DIR}/problem_description.md"
  fi
  EXP_JSON=$("${AE}" --json experiment create \
    --max-programs "${MAX_PROGRAMS}" \
    --concurrency 4 \
    --problem-file "${PROBLEM_FILE}" \
    --title "OpenROAD DRT 1-Shot Schedule Evolution (Job ${SLURM_JOB_ID:-local})")
  EXP_NICK=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["nickname"])' "${EXP_JSON}")
  SEED_DIR="${LOG_ROOT}/seed_prog_${EXP_NICK}"
  mkdir -p "${SEED_DIR}"
  cp "${EXAMPLE_DIR}/main.py" "${SEED_DIR}/initial_program.py"
  "${AE}" experiment start "${EXP_NICK}" \
    --program-dir "${SEED_DIR}" \
    --score 100.0
  echo "Auto-created and seeded experiment: ${EXP_NICK}"
fi

RUN_DIR="${LOG_ROOT}/${EXP_NICK}_job${SLURM_JOB_ID}"
mkdir -p "${RUN_DIR}"
echo "${EXP_NICK}" > "${LOG_ROOT}/latest_exp_nick.txt"
ln -sf "${LOG_ROOT}/${EXP_NICK}-dashboard.md" "${LOG_ROOT}/latest_dashboard.md"

echo "================================================================================"
echo "OpenROAD DRT AlphaEvolve 1-Shot Evolution Job"
echo "Host: $(hostname) | Job ID: ${SLURM_JOB_ID} | Start: $(date)"
echo "Experiment Nickname : ${EXP_NICK}"
echo "Evolving Split      : ${B1_DESIGNS} (B1_THREADS=${B1_THREADS})"
echo "Max Programs        : ${MAX_PROGRAMS} (${WORKER_ITERS} iterations per worker across 4 workers)"
echo "Example Dir         : ${EXAMPLE_DIR}"
echo "Data / Cache Dir    : ${B1_DATA_DIR}"
echo "Dashboard           : ${LOG_ROOT}/${EXP_NICK}-dashboard.md (symlink: ${LOG_ROOT}/latest_dashboard.md)"
echo "================================================================================"

CORE_SETS=("0-15" "16-31" "44-59" "60-75")

# 1. Prefer exact Python single-step baseline cache from Job 2 (`run_openroad_ae_cand14_py.sbatch`) if available
LATEST_CAND_CACHE=$(ls -td /scratch/${USER}/openroad_ae_cand14_py_logs/run_*/b1_data/cache 2>/dev/null | head -n 1 || true)
if [[ -n "${LATEST_CAND_CACHE}" && -d "${LATEST_CAND_CACHE}" ]]; then
  python3 - "${LATEST_CAND_CACHE}" "${OPENROAD_BIN}" "${ISPD_BENCH_DIR}" "${B1_DATA_DIR}" "${B1_THREADS}" << 'REKEY_EOF'
import glob, hashlib, os, shlex, shutil, sys
prev_cache, or_cmd_raw, bench_dir, data_dir, threads_s = sys.argv[1:6]
or_cmd = " ".join(shlex.split(or_cmd_raw.strip()))
threads = int(threads_s)
cache_dir = os.path.join(data_dir, "cache")
os.makedirs(cache_dir, exist_ok=True)
harness_ver = "b1-live-openroad-v1"
copied = 0
for src in glob.glob(os.path.join(prev_cache, f"ref-b1-*-t{threads}-*.json")):
    base = os.path.basename(src)
    bm = base.split("ref-b1-")[1].split(f"-t{threads}-")[0]
    key_src = f"{harness_ver}|{or_cmd}|{bench_dir}|{bm}|{threads}"
    h = hashlib.sha256(key_src.encode()).hexdigest()[:12]
    dst = os.path.join(cache_dir, f"ref-b1-{bm}-t{threads}-{h}.json")
    if not os.path.isfile(dst):
        shutil.copyfile(src, dst)
        copied += 1
if copied:
    print(f"Pre-seeded {copied} Python single-step baseline reference cache entries from {prev_cache}")
REKEY_EOF
fi

# 2. Fall back to pre-seeding from Job 1 (`run_openroad_baseline.sbatch`) CSV if any designs are still uncached
if [[ -z "${BASELINE_CSV:-}" ]]; then
  BASELINE_CSV=$(ls -t /scratch/${USER}/openroad_baseline_logs/run_*/all_baseline_summary.csv 2>/dev/null | head -n 1 || true)
fi
if [[ -n "${BASELINE_CSV:-}" && -f "${BASELINE_CSV}" ]]; then
  python3 - "${BASELINE_CSV}" "${OPENROAD_BIN}" "${ISPD_BENCH_DIR}" "${B1_DATA_DIR}" "${B1_THREADS}" << 'CEOF'
import csv, hashlib, json, os, shlex, sys
csv_path, or_cmd_raw, bench_dir, data_dir, threads_s = sys.argv[1:6]
or_cmd = " ".join(shlex.split(or_cmd_raw.strip()))
threads = int(threads_s)
cache_dir = os.path.join(data_dir, "cache")
os.makedirs(cache_dir, exist_ok=True)
harness_ver = "b1-live-openroad-v1"

def _flt(val, default=0.0):
    try:
        return float(val) if val not in (None, "") else default
    except (ValueError, TypeError):
        return default

def _int(val, default=0):
    try:
        return int(float(val)) if val not in (None, "") else default
    except (ValueError, TypeError):
        return default

seeded = 0
with open(csv_path, encoding="utf-8") as fh:
    for row in csv.DictReader(fh):
        bm = (row.get("design") or "").strip()
        if not bm:
            continue
        key_src = f"{harness_ver}|{or_cmd}|{bench_dir}|{bm}|{threads}"
        h = hashlib.sha256(key_src.encode()).hexdigest()[:12]
        cache_file = os.path.join(cache_dir, f"ref-b1-{bm}-t{threads}-{h}.json")
        if os.path.isfile(cache_file):
            continue
        if not row.get("wl_um") or _int(row.get("final_drvs"), -1) < 0:
            continue
        drvs = _int(row.get("final_drvs"), 0)
        iters = _int(row.get("iters"), 0)
        sr_s = _flt(row.get("sr_s"), 0.0)
        it0_s = _flt(row.get("it0_s"), 0.0)
        tot_s = _flt(row.get("drt_total_s"), it0_s + sr_s)
        wl = _flt(row.get("wl_um"), 0.0)
        vias = _int(row.get("vias"), 0)
        peak_mb = _flt(row.get("peak_mb"), 0.0) or None
        traj = row.get("traj", "") or ""
        it0_drvs = 0
        if "it0:" in traj:
            try:
                it0_drvs = int(traj.split("it0:")[1].split("(")[0])
            except (ValueError, IndexError):
                pass
        ref_obj = {
            "it0_drvs": it0_drvs,
            "drvs": drvs,
            "init_wall_s": round(max(0.0, _flt(row.get("wall_s"), tot_s) - tot_s), 3),
            "it0_wall_s": round(it0_s, 3),
            "it0_cpu_s": round(it0_s * threads, 3),
            "sr_wall_s": round(sr_s, 3),
            "sr_cpu_s": round(sr_s * threads, 3),
            "step_end_wall_s": 0.0,
            "total_wall_s": round(tot_s, 3),
            "total_cpu_s": round(tot_s * threads, 3),
            "wall_s": round(sr_s, 3),
            "cpu_s": round(sr_s * threads, 3),
            "wl": wl,
            "vias": vias,
            "peak_mem_mb": peak_mb,
            "iters": iters,
            "timed_out": False,
            "steps": [],
            "traj": traj,
        }
        with open(cache_file, "w", encoding="utf-8") as cf:
            json.dump(ref_obj, cf, indent=2)
        seeded += 1
print(f"Pre-seeded {seeded} baseline reference cache entries from {csv_path}")
CEOF
fi

# Pre-warm any remaining stage1 baseline references across the 4 physical core slots
# so the 4 evolution workers do not race to compute the initial baseline cache.
echo "[$(date +%H:%M:%S)] Pre-warming ${B1_DESIGNS} baseline references across 4 pinned 16T slots..."
WARM_PIDS=()
for slot in 0 1 2 3; do
  CORES="${CORE_SETS[${slot}]}"
  taskset -c "${CORES}" python3 - "${slot}" <<'PYEOF' &
import os, shlex, sys
slot = int(sys.argv[1])
sys.path.insert(0, os.environ["EXAMPLE_DIR"])
import evaluator

openroad_cmd = shlex.split(os.environ["OPENROAD_BIN"])
bench_dir = os.environ["ISPD_BENCH_DIR"]
data_dir = os.environ["B1_DATA_DIR"]
threads = int(os.environ.get("B1_THREADS", "16"))
dset = os.environ.get("B1_DESIGNS", "stage1")
designs = list(evaluator.DESIGN_SETS.get(dset, tuple(x.strip() for x in dset.split(",") if x.strip())))

for idx, bm in enumerate(designs):
    if idx % 4 == slot:
        res = evaluator._get_or_compute_ref(openroad_cmd, bench_dir, data_dir, bm, threads)
        print(f"  [slot {slot} ref ready] {bm}: drvs={res['drvs']}, sr_wall={res['wall_s']}s, total_wall={res.get('total_wall_s')}s", flush=True)
PYEOF
    WARM_PIDS+=($!)
done
for pid in "${WARM_PIDS[@]}"; do
    wait "${pid}"
done

# Launch 4 parallel 16-thread `ae experiment run` workers pinned to dedicated physical cores
PIDS=()
for slot in 0 1 2 3; do
  CORES="${CORE_SETS[${slot}]}"
  WORKER_LOG="${RUN_DIR}/worker_${slot}.log"
  echo "[$(date +%H:%M:%S)] Starting worker ${slot} on cores ${CORES} -> ${WORKER_LOG}"
  if (( slot == 0 )); then
    taskset -c "${CORES}" "${AE}" experiment run "${EXP_NICK}" \
      --evaluator "${EXAMPLE_DIR}/evaluator.py" \
      --backend local \
      --max-iterations "${WORKER_ITERS}" \
      --timeout "${EVAL_TIMEOUT}" \
      --dashboard "${LOG_ROOT}/${EXP_NICK}-dashboard.md" \
      > "${WORKER_LOG}" 2>&1 &
  else
    taskset -c "${CORES}" "${AE}" experiment run "${EXP_NICK}" \
      --evaluator "${EXAMPLE_DIR}/evaluator.py" \
      --backend local \
      --max-iterations "${WORKER_ITERS}" \
      --timeout "${EVAL_TIMEOUT}" \
      > "${WORKER_LOG}" 2>&1 &
  fi
  PIDS+=($!)
done

for pid in "${PIDS[@]}"; do
  wait "${pid}" || true
done

echo "================================================================================"
echo "Evolution Job Completed: $(date)"
echo "Top 5 Discovered Programs (${EXP_NICK}):"
"${AE}" results best "${EXP_NICK}" --top 5 || true
TOP_PROG=$("${AE}" --json results best "${EXP_NICK}" --top 1 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); p=(d if isinstance(d,list) else d.get("programs",[]))[0]; print(p["nickname"])' 2>/dev/null || true)
if [[ -n "${TOP_PROG}" ]]; then
  "${AE}" program show "${TOP_PROG}" --experiment "${EXP_NICK}" \
    --output-file "${LOG_ROOT}/evolved_winner.py" || true
  echo "Exported Rank #1 program (${TOP_PROG}) -> ${LOG_ROOT}/evolved_winner.py"
  echo "Validate on 6-design mini6 dataset with:"
  echo "  PROGRAM_FILE=${LOG_ROOT}/evolved_winner.py sbatch ${EXAMPLE_DIR}/tutorial/run_openroad_ae_cand14_py.sbatch"
fi
echo "================================================================================"
