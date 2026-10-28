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
# Configures the host environment for Hyperdisk storage and mounts,
# and dynamically scales Hyperdisk Balanced performance on boot.

set -euo pipefail
exec 2>&1

mkdir -p /mnt/disks/hyperdisk
chmod -R 777 /mnt/disks/hyperdisk
modprobe loop 2>/dev/null || true
modprobe squashfs 2>/dev/null || true

if ! command -v zstd >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq && apt-get install -y -qq zstd
fi

# The batch-debian-12-official image pre-installs docker-credential-gcr in /usr/local/bin,
# but does NOT include docker-credential-gcloud. Standard 'gcloud auth configure-docker'
# registers 'gcloud' as the helper, which fails when Docker calls docker-credential-gcloud.
# Using docker-credential-gcr leverages VM service account ADC for on-demand tokens
# (avoiding 1-hr token expiry from 'docker login'). Symlinking to /usr/bin ensures
# availability in Batch subshells where PATH may be minimal or if docker-credential-gcloud is invoked.
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"
if [ -x /usr/local/bin/docker-credential-gcr ]; then
  REGISTRY_REGION="${1:-europe-west4}"
  /usr/local/bin/docker-credential-gcr configure-docker --registries="${REGISTRY_REGION}-docker.pkg.dev"
  ln -sf /usr/local/bin/docker-credential-gcr /usr/bin/docker-credential-gcr
  ln -sf /usr/local/bin/docker-credential-gcr /usr/bin/docker-credential-gcloud
fi

# Dynamic Hyperdisk Balanced performance boost:
# Boost provisioned IOPS and throughput on boot (50,000 IOPS, 2,400 MiB/s).
# Fails immediately if the disk cannot be updated.
echo "=== Scaling Hyperdisk Balanced performance (50k IOPS, 2,400 MiB/s) ==="
INSTANCE_NAME="$(curl -s -f -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/name")"
ZONE_PATH="$(curl -s -f -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/zone")"
ZONE="${ZONE_PATH##*/}"
PROJECT_ID="$(curl -s -f -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/project/project-id")"
TOKEN="$(curl -s -f -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token" | grep -o '"access_token": *"[^"]*"' | cut -d'"' -f4)"

if [ -z "${TOKEN}" ]; then
  echo "ERROR: Failed to retrieve OAuth access token from instance metadata." >&2
  exit 1
fi

# Fetch instance details via Compute Engine API to resolve the true underlying disk resource name.
# (The metadata server returns device-name which is 'persistent-disk-0', whereas the GCE disk resource
# name attached by Batch/Instance Template is typically the VM name or a generated disk name).
echo "Resolving boot disk resource name for instance '${INSTANCE_NAME}' in zone '${ZONE}'..."
INSTANCE_JSON="$(curl -s -f -H "Authorization: Bearer ${TOKEN}" "https://compute.googleapis.com/compute/v1/projects/${PROJECT_ID}/zones/${ZONE}/instances/${INSTANCE_NAME}")"

# Find the disk source URL where boot = true
if command -v python3 >/dev/null 2>&1; then
  DISK_SOURCE="$(python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
    for d in data.get("disks", []):
        if d.get("boot"):
            print(d.get("source", ""))
            break
except Exception:
    pass
' <<< "${INSTANCE_JSON}")"
fi

if [ -z "${DISK_SOURCE}" ]; then
  DISK_SOURCE="$(echo "${INSTANCE_JSON}" | grep -B 10 -A 10 '"boot": true' | grep '"source":' | head -n 1 | cut -d'"' -f4 || true)"
fi

if [ -n "${DISK_SOURCE}" ]; then
  DISK_NAME="${DISK_SOURCE##*/}"
else
  # Fallback to instance name if parsing failed
  DISK_NAME="${INSTANCE_NAME}"
fi

echo "Updating disk '${DISK_NAME}' in zone '${ZONE}' (project: ${PROJECT_ID}) to 50,000 IOPS and 2,400 MiB/s throughput..."

# Use the Compute Engine REST API PATCH method rather than 'gcloud compute disks update'.
# 'gcloud' synchronously blocks and polls the disk update operation for ~5 minutes until completion,
# which blocks google-startup-scripts.service and delays cloud-batch-agent.service from starting.
# In contrast, the REST API returns the pending operation in <200 ms, allowing the VM to finish
# boot immediately and begin Batch tasks while GCE reconfigures Hyperdisk QoS online in the background.
#
# TODO(jacobwarwick): REMOVE - Remove this dynamic boost script once upgrading to Cluster
# Toolkit v1.108.0+, which supports configuring instance templates with disk_provisioned_iops
# and disk_provisioned_throughput directly on the disk definition.
RESPONSE="$(curl -s -w "\n%{http_code}" -X PATCH \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"name\": \"${DISK_NAME}\", \"provisionedIops\": \"50000\", \"provisionedThroughput\": \"2400\"}" \
  "https://compute.googleapis.com/compute/v1/projects/${PROJECT_ID}/zones/${ZONE}/disks/${DISK_NAME}?paths=provisionedIops&paths=provisionedThroughput")"
HTTP_STATUS="$(echo "${RESPONSE}" | tail -n 1)"
BODY="$(echo "${RESPONSE}" | sed '$d')"
if [ "${HTTP_STATUS}" -lt 200 ] || [ "${HTTP_STATUS}" -ge 300 ]; then
  echo "ERROR: REST API disk update failed with HTTP ${HTTP_STATUS}: ${BODY}" >&2
  exit 1
fi
echo "Disk update request submitted successfully: ${BODY}"

echo "=== Successfully updated Hyperdisk Balanced performance ==="
