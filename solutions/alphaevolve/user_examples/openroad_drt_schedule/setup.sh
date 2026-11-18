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
# Automated setup script for OpenROAD, AlphaEvolve runtime, and the 6-design
# ISPD-18 / ISPD-19 Mini Dataset (`mini6`: 3 Training + 3 Held-Out):
#   - Training (`mini_train`, 3 designs):
#       ispd18_test1, ispd18_test2, ispd19_test5
#   - Held-Out (`mini_holdout`, 3 designs):
#       ispd19_test1, ispd18_test5, ispd19_test3
#
# Invoked automatically during Cluster Toolkit (`gcluster`) deployment:
#   1. Cloud Batch deployment (`gcluster deploy alpha-evolve-experiment.yaml`):
#        ./setup.sh --container [--all20]
#   2. Slurm cluster deployment (`gcluster deploy tutorial/openroad-ae-slurm.yaml`)
#      or interactive Slurm login-node provisioning:
#        ./setup.sh --slurm [--all20]
#   3. Benchmark-only staging:
#        ISPD_BENCH_DIR=/path/to/ispd_bench ./setup.sh --benchmarks-only [--all20]

set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 6-design Mini Dataset (`mini6` = 3 Training + 3 Held-Out):
readonly -a MINI6_DESIGNS=(
  "ispd18_test1"
  "ispd18_test2"
  "ispd19_test5"
  "ispd19_test1"
  "ispd18_test5"
  "ispd19_test3"
)

readonly -a ALL20_DESIGNS=(
  "ispd18_test1" "ispd18_test2" "ispd18_test3" "ispd18_test4" "ispd18_test5"
  "ispd18_test6" "ispd18_test7" "ispd18_test8" "ispd18_test9" "ispd18_test10"
  "ispd19_test1" "ispd19_test2" "ispd19_test3" "ispd19_test4" "ispd19_test5"
  "ispd19_test6" "ispd19_test7" "ispd19_test8" "ispd19_test9" "ispd19_test10"
)

stage_ispd_benchmarks() {
  local bench_dir="${1}"
  local stage_all20="${2:-0}"
  local bm ext
  mkdir -p "${bench_dir}"

  local designs=("${MINI6_DESIGNS[@]}")
  if [[ "${stage_all20}" == "1" ]]; then
    designs=("${ALL20_DESIGNS[@]}")
  fi

  echo "=== Staging ${#designs[@]} ISPD benchmarks into ${bench_dir} ==="
  for bm in "${designs[@]}"; do
    local url=""
    if [[ "${bm}" == ispd19_* ]]; then
      url="https://www.ispd.cc/contests/19/benchmarks/${bm}.tgz"
    else
      url="https://www.ispd.cc/contests/18/${bm}.tgz"
    fi

    if [[ ! -f "${bench_dir}/${bm}/${bm}.input.def" ]] && [[ ! -f "${bench_dir}/${bm}/${bm}/${bm}.input.def" ]]; then
      echo "  -> Downloading ${bm} from ${url} ..."
      mkdir -p "${bench_dir}/${bm}"
      local tgz_tmp="${bench_dir}/${bm}/${bm}.tgz"
      curl -fsSL --retry 3 --retry-delay 2 -o "${tgz_tmp}" "${url}"
      if [[ -n "${ISPD_SHA256_FILE:-}" ]] && [[ -f "${ISPD_SHA256_FILE}" ]]; then
        local expected_sha
        expected_sha="$(awk -v f="${bm}.tgz" '$2 == f || $2 == "*"f {print $1; exit}' "${ISPD_SHA256_FILE}")"
        if [[ -n "${expected_sha}" ]]; then
          echo "${expected_sha}  ${tgz_tmp}" | sha256sum -c -
        fi
      fi
      tar --no-same-owner -xzf "${tgz_tmp}" -C "${bench_dir}/${bm}"
      rm -f "${tgz_tmp}"
    else
      echo "  -> Already staged: ${bm}"
    fi

    # Flatten nested ${bm}/${bm}/* directory if tarball contained a top-level ${bm}/ folder
    if [[ -d "${bench_dir}/${bm}/${bm}" ]]; then
      mv -f "${bench_dir}/${bm}/${bm}"/* "${bench_dir}/${bm}/" 2>/dev/null || true
      rm -rf "${bench_dir}/${bm}/${bm}"
    fi
    rm -f "${bench_dir}/${bm}"/.*.swp 2>/dev/null || true
    if [[ ! -f "${bench_dir}/${bm}/${bm}.input.guide" ]] && [[ -f "${bench_dir}/${bm}/${bm}.guide" ]]; then
      ln -sf "${bm}.guide" "${bench_dir}/${bm}/${bm}.input.guide"
    fi
  done

  # Fix `ispd19_test5.input.def` section ordering (`GROUPS` must follow `END COMPONENTS` per DEF 5.8)
  PYTHONDONTWRITEBYTECODE=1 python3 - "${bench_dir}" <<'PY'
import glob
import os
import re
import sys

bench_dir = sys.argv[1]
pattern = os.path.join(bench_dir, "**", "*.input.def")
for def_path in sorted(glob.glob(pattern, recursive=True)):
  with open(def_path, "r", encoding="utf-8") as fh:
    text = fh.read()
  m = re.search(r"(GROUPS\s+\d+\s*;.*?END GROUPS\s*\n)", text, flags=re.DOTALL)
  if m and m.start() < text.find("END COMPONENTS"):
    groups_block = m.group(1)
    text_fixed = (text[:m.start()] + text[m.end():]).replace(
        "END COMPONENTS\n", "END COMPONENTS\n\n" + groups_block
    )
    with open(def_path, "w", encoding="utf-8") as out:
      out.write(text_fixed)
    print(f"  -> Fixed DEF 5.8 GROUPS ordering in {def_path}")
PY

  # Verify all target designs have .input.lef, .input.def, and .input.guide
  for bm in "${designs[@]}"; do
    for ext in "input.lef" "input.def" "input.guide"; do
      if [[ ! -f "${bench_dir}/${bm}/${bm}.${ext}" ]]; then
        echo "FATAL: Missing ${bench_dir}/${bm}/${bm}.${ext} after staging" >&2
        exit 1
      fi
    done
  done
  echo "=== Successfully staged and verified ${#designs[@]} ISPD benchmarks in ${bench_dir} ==="
}

verify_evaluator_smoke() {
  echo "=== Running evaluator.py pre-flight solver-invariance verification ==="
  PYTHONDONTWRITEBYTECODE=1 python3 "${SCRIPT_DIR}/evaluator.py" \
    --check-only \
    --program-file "${SCRIPT_DIR}/main.py"
  if [[ -f "${SCRIPT_DIR}/tutorial/candidates/14_oneshot_r3_winner.py" ]]; then
    PYTHONDONTWRITEBYTECODE=1 python3 "${SCRIPT_DIR}/evaluator.py" \
      --check-only \
      --program-file "${SCRIPT_DIR}/tutorial/candidates/14_oneshot_r3_winner.py"
  fi
}

setup_container_mode() {
  local stage_all20="${1:-0}"
  local or_bin="${OPENROAD_BIN:-/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad}"
  local bench_dir="${ISPD_BENCH_DIR:-/app/ispd_bench}"

  echo "=== [Container Mode] Verifying OpenROAD & AlphaEvolve Runtime ==="
  if [[ ! -x "${or_bin}" ]]; then
    echo "FATAL: OpenROAD binary not found at ${or_bin}" >&2
    exit 1
  fi
  ln -sfn "${or_bin}" /usr/local/bin/openroad || true
  echo -n "OpenROAD version: "
  "${or_bin}" -version

  echo -n "Python version:   "
  python3 --version

  stage_ispd_benchmarks "${bench_dir}" "${stage_all20}"
  verify_evaluator_smoke
}

setup_slurm_mode() {
  local stage_all20="${1:-0}"
  local sif_image="${ORFS_DOCKER_IMAGE:-docker://openroad/orfs:26Q3-586-gfbc8fa48d}"
  local sif_path="${OPENROAD_SIF_PATH:-/tools/containers/openroad-2130.sif}"
  local wrapper_path="${OPENROAD_BIN:-/tools/bin/openroad_wrapper.sh}"
  local bench_dir="${ISPD_BENCH_DIR:-/scratch/ispd_bench}"

  echo "=== [Slurm Mode] Setting up OpenROAD container, AlphaEvolve env, and ISPD datasets ==="

  # 1. Install Apptainer and runtime packages if running as root and missing
  if (( EUID == 0 )); then
    if ! command -v apptainer >/dev/null 2>&1; then
      if command -v dnf >/dev/null 2>&1; then
        dnf install -y epel-release
        dnf --enablerepo=crb install -y apptainer python3 python3-pip curl tar jq time diffutils || true
        mkdir -p /tools/rpms
        if ! compgen -G "/tools/rpms/*.rpm" >/dev/null 2>&1; then
          dnf download --resolve --alldeps --destdir=/tools/rpms apptainer squashfs-tools fuse-overlayfs numactl time diffutils || true
        fi
      elif command -v apt-get >/dev/null 2>&1; then
        apt-get update && apt-get install -y apptainer python3 python3-venv curl tar jq time
      fi
    fi
  fi

  # 2. Pull OpenROAD Apptainer image (`openroad-2130.sif`) and create wrapper script
  if ! mkdir -p "$(dirname "${sif_path}")" 2>/dev/null || [[ ! -w "$(dirname "${sif_path}")" ]]; then
    sif_path="/scratch/${USER:-root}/containers/openroad-2130.sif"
    wrapper_path="/scratch/${USER:-root}/bin/openroad_wrapper.sh"
    mkdir -p "$(dirname "${sif_path}")"
  fi
  mkdir -p "$(dirname "${wrapper_path}")"

  export APPTAINER_TMPDIR="${APPTAINER_TMPDIR:-/var/tmp/apptainer_tmp}"
  export APPTAINER_CACHEDIR="${APPTAINER_CACHEDIR:-/var/tmp/apptainer_cache}"
  mkdir -p "${APPTAINER_TMPDIR}" "${APPTAINER_CACHEDIR}"

  if [[ ! -f "${sif_path}" ]]; then
    echo "=== Pulling ${sif_image} -> ${sif_path} ==="
    apptainer pull "${sif_path}.tmp" "${sif_image}"
    mv -f "${sif_path}.tmp" "${sif_path}"
    chmod 0755 "${sif_path}" || true
  else
    echo "=== OpenROAD container already present at ${sif_path} ==="
  fi

  cat <<EOF > "${wrapper_path}"
#!/bin/bash
export OMP_WAIT_POLICY=PASSIVE
OR_BIN="\${OR_BIN:-/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad}"
exec apptainer exec --bind /scratch:/scratch --bind /tmp:/tmp \\
  "${sif_path}" "\${OR_BIN}" "\$@"
EOF
  chmod +x "${wrapper_path}"

  if [[ -n "${USER:-}" ]] && [[ "${USER}" != "root" ]] && [[ -d "/scratch/${USER}" || -w "/scratch" ]]; then
    mkdir -p "/scratch/${USER}/bin"
    if [[ "${wrapper_path}" != "/scratch/${USER}/bin/openroad_wrapper.sh" ]]; then
      ln -sfn "${wrapper_path}" "/scratch/${USER}/bin/openroad_wrapper.sh"
    fi
  fi

  echo -n "Verified OpenROAD wrapper (${wrapper_path}): "
  "${wrapper_path}" -version

  # 3. Stage the 6-design Mini Dataset (`mini6`) onto shared `/scratch`
  stage_ispd_benchmarks "${bench_dir}" "${stage_all20}"
  chmod -R a+rX "${bench_dir}" 2>/dev/null || true
  if [[ -n "${USER:-}" ]] && [[ "${USER}" != "root" ]] && [[ "${bench_dir}" == "/scratch/ispd_bench" ]]; then
    mkdir -p "/scratch/${USER}"
    if [[ ! -e "/scratch/${USER}/ispd_bench" ]]; then
      ln -sfn "${bench_dir}" "/scratch/${USER}/ispd_bench"
    fi
  fi

  # 4. Set up AlphaEvolve Python / CLI environment if bundle or wheel is present
  local ae_root="${AE_INSTALL_DIR:-/tools/ae_poc}"
  if ! mkdir -p "${ae_root}" 2>/dev/null || [[ ! -w "${ae_root}" ]]; then
    ae_root="/scratch/${USER:-root}/ae_poc"
    mkdir -p "${ae_root}"
  fi
  local bundle_tar=""
  if [[ -n "${AE_CLI_BUNDLE_URI:-}" ]]; then
    echo "=== Staging ae_cli_bundle from ${AE_CLI_BUNDLE_URI} into ${ae_root} ==="
    if [[ "${AE_CLI_BUNDLE_URI}" == gs://* ]]; then
      gcloud storage cp "${AE_CLI_BUNDLE_URI}" "/var/tmp/ae_cli_bundle.tar.gz"
      bundle_tar="/var/tmp/ae_cli_bundle.tar.gz"
    elif [[ -f "${AE_CLI_BUNDLE_URI}" ]]; then
      bundle_tar="${AE_CLI_BUNDLE_URI}"
    fi
  elif [[ -f "/var/tmp/openroad_ae_staged/ae_cli_bundle.tar.gz" ]]; then
    bundle_tar="/var/tmp/openroad_ae_staged/ae_cli_bundle.tar.gz"
  elif [[ -f "/user_provided_software/ae_cli_bundle.tar.gz" ]]; then
    bundle_tar="/user_provided_software/ae_cli_bundle.tar.gz"
  fi
  if [[ -n "${bundle_tar}" ]] && [[ -f "${bundle_tar}" ]]; then
    echo "=== Unpacking ${bundle_tar} into ${ae_root} ==="
    tar --no-same-owner -xzf "${bundle_tar}" -C "${ae_root}"
    if [[ ! -x "${ae_root}/bin/ae" ]]; then
      local cand_bin cand_dir
      for cand_bin in "${ae_root}"/*/bin; do
        if [[ -x "${cand_bin}/ae" ]]; then
          cand_dir="$(dirname "${cand_bin}")"
          cp -a "${cand_dir}/." "${ae_root}/"
          rm -rf "${cand_dir}"
          break
        fi
      done
    fi
    chmod -R a+rX "${ae_root}" 2>/dev/null || true
    [[ -f "${ae_root}/bin/ae" ]] && chmod +x "${ae_root}/bin/ae"
  fi

  verify_evaluator_smoke
  chmod -R a+rX "${SCRIPT_DIR}" 2>/dev/null || true
  local default_ae_engine="${AE_ENGINE:-alpha-evolve-infra-experiment-engine}"
  if (( EUID == 0 )) && [[ -d /etc/profile.d ]]; then
    cat <<EOF > /etc/profile.d/openroad_ae_env.sh
export AE_ENGINE="\${AE_ENGINE:-${default_ae_engine}}"
if [[ -d /tools/ae_poc/bin ]] && [[ ":\${PATH}:" != *":/tools/ae_poc/bin:"* ]]; then
  export PATH="/tools/ae_poc/bin:\${PATH}"
fi
if [[ -d /scratch ]] && [[ -w /scratch ]] && [[ -n "\${USER:-}" ]]; then
  mkdir -p "/scratch/\${USER}/openroad_baseline_logs" \\
           "/scratch/\${USER}/openroad_ae_cand14_py_logs" \\
           "/scratch/\${USER}/openroad_ae_evolve_logs" 2>/dev/null || true
fi
if [[ -n "\${HOME:-}" ]] && [[ -w "\${HOME}" ]] && [[ ! -f "\${HOME}/.config/ae/profiles/default.toml" ]] && [[ -x /tools/ae_poc/bin/ae ]]; then
  _ae_proj=\$(curl -sf -m 2 -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/project/numeric-project-id 2>/dev/null || true)
  if [[ -n "\${_ae_proj}" ]]; then
    mkdir -p "\${HOME}/.config/ae/profiles" 2>/dev/null || true
    cat <<AEEOF > "\${HOME}/.config/ae/profiles/default.toml"
[defaults]
project = "\${_ae_proj}"
location = "global"
engine = "\${AE_ENGINE}"

[[models]]
name = "gemini-3.5-flash"
AEEOF
  fi
  unset _ae_proj
fi
EOF
    chmod 0644 /etc/profile.d/openroad_ae_env.sh
  fi
  echo "=== [Slurm Mode] Setup Complete ==="
  echo "  OPENROAD_BIN   = ${wrapper_path}"
  echo "  ISPD_BENCH_DIR = ${bench_dir}"
}

main() {
  local mode="auto"
  local stage_all20="${STAGE_ALL20:-0}"

  while [[ $# -gt 0 ]]; do
    case "${1}" in
      --container)
        mode="container"
        shift
        ;;
      --slurm)
        mode="slurm"
        shift
        ;;
      --benchmarks-only)
        mode="benchmarks"
        shift
        ;;
      --all20)
        stage_all20="1"
        shift
        ;;
      *)
        echo "Unknown argument: ${1}" >&2
        echo "Usage: ${0} [--container | --slurm | --benchmarks-only] [--all20]" >&2
        exit 1
        ;;
    esac
  done

  if [[ "${mode}" == "auto" ]]; then
    if [[ -f "/.dockerenv" ]] || [[ -d "/OpenROAD-flow-scripts/tools/install/OpenROAD" ]]; then
      mode="container"
    else
      mode="slurm"
    fi
  fi

  case "${mode}" in
    container)
      setup_container_mode "${stage_all20}"
      ;;
    slurm)
      setup_slurm_mode "${stage_all20}"
      ;;
    benchmarks)
      stage_ispd_benchmarks "${ISPD_BENCH_DIR:-${SCRIPT_DIR}/ispd_bench}" "${stage_all20}"
      verify_evaluator_smoke
      ;;
  esac
}

main "$@"
