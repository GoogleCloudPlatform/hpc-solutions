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
# Prepares the AlphaFold 3 reference databases for the MSA pipeline: downloads
# upstream .zst archives, shards RNA FASTA databases with SeqKit, packs PDB
# mmCIF structures into a zstd-compressed SquashFS image, and uploads everything
# to Cloud Storage. Executed once per deployment by the 'db-prep-job' Batch job
# (submitted via ./submit-db-prep-job.sh in Step 1).

set -euo pipefail
exec 2>&1
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"

get_metadata() {
  local attr="$1"
  curl -s -f -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/attributes/${attr}" 2>/dev/null || true
}

DEST_GCS_BUCKET="${1:-${DEST_GCS_BUCKET:-$(get_metadata gcs_bucket_path)}}"
SOURCE_DATABASE_URL="${2:-${SOURCE_DATABASE_URL:-$(get_metadata source_database_url)}}"
SOURCE_DATABASE_URL="${SOURCE_DATABASE_URL:-https://storage.googleapis.com/alphafold-databases/v3.0}"

if [ -z "${DEST_GCS_BUCKET}" ]; then
  echo "ERROR: Destination GCS bucket path must be specified as \$1, via \$DEST_GCS_BUCKET, or via instance metadata 'gcs_bucket_path' (e.g. gs://my-bucket)." >&2
  exit 1
fi

echo "=== AlphaFold 3 Reference Database Preparation ==="
echo "Source Database URL: ${SOURCE_DATABASE_URL}"
echo "Destination GCS Bucket: ${DEST_GCS_BUCKET}"

if ! command -v mksquashfs >/dev/null 2>&1 || ! command -v zstd >/dev/null 2>&1 || ! command -v aria2c >/dev/null 2>&1; then
  echo "Installing squashfs-tools, zstd, and aria2..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq && apt-get install -y -qq squashfs-tools zstd aria2
fi

if ! command -v seqkit >/dev/null 2>&1; then
  echo "Installing seqkit v2.13.0..."
  SEQKIT_VERSION="v2.13.0"
  SEQKIT_SHA256="7d686de448464fada1b1988e2e07d693bec68768312da62846bc0e2b502bfc46"
  curl -fsSL "https://github.com/shenwei356/seqkit/releases/download/${SEQKIT_VERSION}/seqkit_linux_amd64.tar.gz" -o /tmp/seqkit.tar.gz
  echo "${SEQKIT_SHA256}  /tmp/seqkit.tar.gz" | sha256sum -c -
  tar -xzf /tmp/seqkit.tar.gz -C /usr/local/bin seqkit
  chmod +x /usr/local/bin/seqkit
  rm -f /tmp/seqkit.tar.gz
fi

WORK_DIR="${WORK_DIR:-/mnt/disks/hyperdisk}"
ZST_DIR="${WORK_DIR}/zst-databases"
SHARDED_DIR="${WORK_DIR}/sharded-databases"
MMCIF_SQUASHFS="${WORK_DIR}/pdb_mmcif.squashfs"
TMP_DIR="${WORK_DIR}/tmp"
mkdir -p "${ZST_DIR}" "${SHARDED_DIR}" "${TMP_DIR}"
trap 'rm -rf "${TMP_DIR}"' EXIT
ulimit -n 65535 2>/dev/null || true

download_archive() {
  local archive_name="$1"
  local dest_dir="$2"
  aria2c \
    --max-connection-per-server=16 \
    --split=16 \
    --min-split-size=16M \
    --file-allocation=none \
    --allow-overwrite=true \
    --auto-file-renaming=false \
    --max-tries=5 \
    --retry-wait=5 \
    --console-log-level=warn \
    --summary-interval=0 \
    --dir="${dest_dir}" \
    --out="${archive_name}" \
    "${SOURCE_DATABASE_URL}/${archive_name}"
}

retry_upload() {
  local processes="$1"
  shift
  for attempt in 1 2 3 4 5; do
    if CLOUDSDK_STORAGE_PROCESS_COUNT="${processes}" \
       CLOUDSDK_STORAGE_THREAD_COUNT=4 \
       CLOUDSDK_STORAGE_PARALLEL_COMPOSITE_UPLOAD_ENABLED=True \
       gcloud storage cp --quiet --no-user-output-enabled "$@"; then
      return 0
    fi
    echo "WARNING: Transient error uploading $* (attempt ${attempt}/5). Retrying in 10s..."
    sleep 10
  done
  echo "ERROR: Failed to upload $* after 5 attempts." >&2
  return 1
}

fetch_and_upload_zst() {
  local filename="$1"
  echo "=== Downloading ${filename} ==="
  download_archive "${filename}" "${ZST_DIR}"
  echo "=== Uploading ${filename} to GCS ==="
  retry_upload 8 "${ZST_DIR}/${filename}" "${DEST_GCS_BUCKET}/zst-databases/${filename}"
  echo "=== Finished ${filename} (uploaded to GCS) ==="
  rm -f "${ZST_DIR}/${filename}"
}

fetch_and_shard_db() {
  local filename="$1"
  local num_shards="$2"
  local threads="$3"
  local zst_file="${TMP_DIR}/${filename}.zst"

  echo "=== Downloading and sharding ${filename} into ${num_shards} shards ==="
  download_archive "${filename}.zst" "${TMP_DIR}"

  local split_out_dir="${TMP_DIR}/split_${filename}"
  mkdir -p "${split_out_dir}"

  zstd --decompress --stdout "${zst_file}" | \
    seqkit split2 --threads "${threads}" --by-part "${num_shards}" -O "${split_out_dir}"
  rm -f "${zst_file}"

  for part_file in "${split_out_dir}"/*.part_*; do
    [ -e "${part_file}" ] || continue
    local part_num
    part_num=$(echo "${part_file}" | sed -E 's/.*\.part_0*([0-9]+)\..*/\1/')
    local shard_idx=$((part_num - 1))
    local formatted_shard
    formatted_shard=$(printf "%05d-of-%05d" "${shard_idx}" "${num_shards}")
    mv "${part_file}" "${SHARDED_DIR}/${filename}-${formatted_shard}"
  done
  rm -rf "${split_out_dir}"

  echo "=== Finished sharding ${filename}. Uploading ${num_shards} shards to GCS... ==="
  retry_upload 16 "${SHARDED_DIR}/${filename}"-*-of-* "${DEST_GCS_BUCKET}/sharded-databases/"
  rm -f "${SHARDED_DIR}/${filename}"-*-of-*
  echo "=== Finished ${filename} (${num_shards} shards uploaded to GCS) ==="
}

fetch_and_upload_pdb_mmcif() {
  local archive_name="pdb_2022_09_28_mmcif_files.tar.zst"
  echo "=== Downloading and packaging ${archive_name} into SquashFS ==="
  download_archive "${archive_name}" "${TMP_DIR}"
  local extract_dir="${TMP_DIR}/mmcif_extract"
  mkdir -p "${extract_dir}"
  zstd --decompress --stdout "${TMP_DIR}/${archive_name}" | tar -x -C "${extract_dir}"
  rm -f "${TMP_DIR}/${archive_name}"

  local src_dir="${extract_dir}"
  if [ -d "${extract_dir}/mmcif_files" ]; then
    src_dir="${extract_dir}/mmcif_files"
  elif [ -d "${extract_dir}/pdb_mmcif" ]; then
    src_dir="${extract_dir}/pdb_mmcif"
  fi

  mksquashfs "${src_dir}" "${MMCIF_SQUASHFS}" \
    -comp zstd \
    -b 64K \
    -processors 96 \
    -noappend
  rm -rf "${extract_dir}"
  echo "=== Finished packaging ${MMCIF_SQUASHFS}. Uploading to GCS... ==="
  retry_upload 32 "${MMCIF_SQUASHFS}" "${DEST_GCS_BUCKET}/pdb_mmcif.squashfs"
  echo "=== Finished pdb_mmcif.squashfs (uploaded to GCS) ==="
}

echo "=== Downloading reference archives, building SquashFS, and streaming uploads to GCS in parallel ==="
pids=()
fetch_and_upload_pdb_mmcif &
pids+=($!)

fetch_and_upload_zst "pdb_seqres_2022_09_28.fasta.zst" &
pids+=($!)
fetch_and_upload_zst "mgy_clusters_2022_05.fa.zst" &
pids+=($!)
fetch_and_upload_zst "uniprot_all_2021_04.fa.zst" &
pids+=($!)
fetch_and_upload_zst "uniref90_2022_05.fa.zst" &
pids+=($!)
fetch_and_upload_zst "bfd-first_non_consensus_sequences.fasta.zst" &
pids+=($!)
fetch_and_shard_db "nt_rna_2023_02_23_clust_seq_id_90_cov_80_rep_seq.fasta" 384 16 &
pids+=($!)
fetch_and_shard_db "rnacentral_active_seq_id_90_cov_80_linclust.fasta" 64 16 &
pids+=($!)
fetch_and_shard_db "rfam_14_9_clust_seq_id_90_cov_80_rep_seq.fasta" 16 8 &
pids+=($!)

for pid in "${pids[@]}"; do
  wait "$pid"
done

echo "=== AlphaFold 3 database preparation and upload completed successfully ==="
