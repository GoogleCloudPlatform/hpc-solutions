#!/bin/bash
#
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
# Stages the prepared reference databases from Cloud Storage onto each worker
# VM's local Hyperdisk Balanced boot volume: downloads and decompresses protein FASTA
# databases, syncs pre-sharded RNA databases, and downloads and loopback-mounts
# pdb_mmcif.squashfs (skipping transfers via sentinel files on subsequent tasks).
# Executed as the first runnable of 'msa-array-job' (submitted via
# ./submit-msa-array-job.sh in Step 3).

set -euo pipefail
exec 2>&1
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"

get_metadata() {
  local attr="$1"
  curl -s -f -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/attributes/${attr}" 2>/dev/null || true
}

GCS_BUCKET_PATH="${1:-${GCS_BUCKET_PATH:-$(get_metadata gcs_bucket_path)}}"
PARALLELISM="${2:-${PARALLELISM:-$(get_metadata msa_parallelism)}}"
PARALLELISM="${PARALLELISM:-1}"
WORK_DIR="${WORK_DIR:-/mnt/disks/hyperdisk}"

if [ -z "${GCS_BUCKET_PATH}" ]; then
  echo "ERROR: GCS bucket path must be specified as \$1, via \$GCS_BUCKET_PATH, or via instance metadata 'gcs_bucket_path' (e.g. gs://my-bucket)." >&2
  exit 1
fi

if ! command -v zstd >/dev/null 2>&1; then
  echo "Installing zstd..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq && apt-get install -y -qq zstd
fi

LOCAL_DB_DIR="${WORK_DIR}/databases"
LOCAL_MMCIF_DIR="${WORK_DIR}/pdb_mmcif"
LOCAL_MMCIF_SQFS="${WORK_DIR}/pdb_mmcif.squashfs"
SENTINEL_FILE="${WORK_DIR}/.download_complete"
MMCIF_SENTINEL="${WORK_DIR}/.pdb_mmcif_complete"

if [ -f "$SENTINEL_FILE" ] && [ -f "$MMCIF_SENTINEL" ]; then
  if ! mountpoint -q "$LOCAL_MMCIF_DIR"; then
    mount -o loop,ro "$LOCAL_MMCIF_SQFS" "$LOCAL_MMCIF_DIR"
  fi
  echo "=== Sentinel files $SENTINEL_FILE and $MMCIF_SENTINEL found. Databases already present, skipping download. ==="
  exit 0
fi

# Stagger startup downloads to prevent GCS egress contention: 10 VMs per wave, 30s between waves
TASK_INDEX="${BATCH_TASK_INDEX:-0}"
let "WAVE = (TASK_INDEX % PARALLELISM) / 10" || true
let "SLEEP_SEC = WAVE * 30" || true
if [ "$SLEEP_SEC" -gt 0 ]; then
  echo "Staggering GCS download (Wave $WAVE for task $TASK_INDEX): sleeping ${SLEEP_SEC}s..."
  sleep "$SLEEP_SEC"
fi

echo "=== Syncing databases and pdb_mmcif.squashfs to ${WORK_DIR} in parallel ==="
mkdir -p "$LOCAL_DB_DIR" "$LOCAL_MMCIF_DIR"

sync_sharded_databases() {
  echo "=== Syncing sharded RNA databases from GCS to ${LOCAL_DB_DIR} ==="
  for attempt in 1 2 3 4 5; do
    if CLOUDSDK_STORAGE_PROCESS_COUNT=16 \
       CLOUDSDK_STORAGE_THREAD_COUNT=4 \
       CLOUDSDK_STORAGE_PARALLEL_COMPOSITE_UPLOAD_ENABLED=True \
       gcloud storage cp --quiet --no-user-output-enabled --verbosity=error -r "${GCS_BUCKET_PATH}/sharded-databases/*" "$LOCAL_DB_DIR/" > /tmp/gcs_cp_sharded_databases.log 2>&1; then
      rm -f /tmp/gcs_cp_sharded_databases.log
      echo "=== Sharded RNA databases successfully synced ==="
      return 0
    fi
    echo "WARNING: Transient error copying sharded databases (attempt $attempt/5). Retrying in 10s..."
    cat /tmp/gcs_cp_sharded_databases.log || true
    rm -f /tmp/gcs_cp_sharded_databases.log
    sleep 10
  done
  echo "ERROR: Failed to copy sharded databases after 5 attempts." >&2
  return 1
}

sync_databases() {
  echo "=== Downloading compressed FASTA databases (.zst) from GCS ==="
  local zst_tmp="${WORK_DIR}/zst_staging"
  mkdir -p "$zst_tmp"
  for attempt in 1 2 3 4 5; do
    if CLOUDSDK_STORAGE_PROCESS_COUNT=16 \
       CLOUDSDK_STORAGE_THREAD_COUNT=4 \
       CLOUDSDK_STORAGE_PARALLEL_COMPOSITE_UPLOAD_ENABLED=True \
       gcloud storage cp --quiet --no-user-output-enabled --verbosity=error -r "${GCS_BUCKET_PATH}/zst-databases/*" "$zst_tmp/" > /tmp/gcs_cp_databases.log 2>&1; then
      rm -f /tmp/gcs_cp_databases.log
      break
    fi
    echo "WARNING: Transient error copying compressed databases (attempt $attempt/5). Retrying in 10s..."
    cat /tmp/gcs_cp_databases.log || true
    rm -f /tmp/gcs_cp_databases.log
    sleep 10
    if [ "$attempt" -eq 5 ]; then
      echo "ERROR: Failed to copy compressed databases after 5 attempts." >&2
      return 1
    fi
  done

  echo "=== Decompressing reference databases in parallel to ${LOCAL_DB_DIR} ==="
  local dec_pids=()
  for zst_file in "$zst_tmp"/*.zst; do
    [ -e "$zst_file" ] || continue
    local base_name
    base_name="$(basename "$zst_file" .zst)"
    (
      zstd --decompress --rm -f "$zst_file" -o "${LOCAL_DB_DIR}/${base_name}"
    ) &
    dec_pids+=($!)
  done

  for dpid in "${dec_pids[@]}"; do
    wait "$dpid"
  done
  rm -rf "$zst_tmp"
  echo "=== All reference databases decompressed successfully ==="
}

sync_mmcif() {
  for attempt in 1 2 3 4 5; do
    if mountpoint -q "$LOCAL_MMCIF_DIR"; then
      umount "$LOCAL_MMCIF_DIR" || true
    fi
    if gcloud storage cp --quiet --no-user-output-enabled --verbosity=error "${GCS_BUCKET_PATH}/pdb_mmcif.squashfs" "$LOCAL_MMCIF_SQFS" > /tmp/gcs_cp_mmcif.log 2>&1; then
      rm -f /tmp/gcs_cp_mmcif.log
      mount -o loop,ro "$LOCAL_MMCIF_SQFS" "$LOCAL_MMCIF_DIR"
      return 0
    fi
    echo "WARNING: Transient error downloading/mounting pdb_mmcif.squashfs (attempt $attempt/5). Retrying in 10s..."
    cat /tmp/gcs_cp_mmcif.log || true
    rm -f /tmp/gcs_cp_mmcif.log
    sleep 10
  done
  echo "ERROR: Failed to download and mount pdb_mmcif.squashfs after 5 attempts."
  return 1
}

sync_databases &
pid_databases=$!
sync_sharded_databases &
pid_sharded=$!
sync_mmcif &
pid_mmcif=$!

wait "$pid_databases"
wait "$pid_sharded"
wait "$pid_mmcif"
touch "$MMCIF_SENTINEL" "$SENTINEL_FILE"
echo "=== Databases and pdb_mmcif.squashfs successfully synced. Created sentinel $SENTINEL_FILE ==="
