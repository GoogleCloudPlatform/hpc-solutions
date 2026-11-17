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

ARG BASE_IMAGE=python:3.12-slim-bookworm
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive

ARG OPENFOAM_GPG_SHA256=""

# Install OpenFOAM 14 (openfoam.org) on Ubuntu 24.04 (noble) base image
RUN . /etc/os-release && \
    if [ "${VERSION_CODENAME:-}" != "noble" ]; then \
      echo "ERROR: OpenFOAM 14 requires Ubuntu 24.04 (noble). Pass --vars base_evaluator_image=ubuntu:24.04 to gcluster deploy." >&2; \
      exit 1; \
    fi && \
    apt-get update && \
    apt-get install -y --no-install-recommends gnupg ca-certificates wget && \
    install -d -m 0755 /usr/share/keyrings && \
    wget -q -O /usr/share/keyrings/openfoam.asc https://dl.openfoam.org/gpg.key && \
    if [ -n "${OPENFOAM_GPG_SHA256}" ]; then \
      echo "${OPENFOAM_GPG_SHA256}  /usr/share/keyrings/openfoam.asc" | sha256sum -c -; \
    fi && \
    chmod a+r /usr/share/keyrings/openfoam.asc && \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/openfoam.asc] http://dl.openfoam.org/ubuntu noble main dev" > /etc/apt/sources.list.d/openfoam.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends openfoam14 && \
    chmod -R a+rx /opt/openfoam14 && \
    rm -rf /var/lib/apt/lists/*

# Configure OpenFOAM 14 runtime environment
ENV WM_PROJECT=OpenFOAM
ENV WM_PROJECT_VERSION=14
ENV WM_PROJECT_DIR=/opt/openfoam14
ENV WM_THIRD_PARTY_DIR=/opt/openfoam14/thirdparty
ENV FOAM_ETC=/opt/openfoam14/etc
ENV FOAM_SIGFPE=false
ENV PATH="${VIRTUAL_ENV}/bin:/opt/openfoam14/platforms/linux64GccDPInt32Opt/bin:/opt/openfoam14/bin:${PATH}"
ENV LD_LIBRARY_PATH="/opt/openfoam14/platforms/linux64GccDPInt32Opt/lib/openmpi-system:/opt/openfoam14/thirdparty/platforms/linux64GccDPInt32/lib/openmpi-system:/opt/openfoam14/platforms/linux64GccDPInt32Opt/lib:/opt/openfoam14/thirdparty/platforms/linux64GccDPInt32/lib:/opt/openfoam14/platforms/linux64GccDPInt32Opt/lib/dummy"

ARG CLOUD_BUCKET_NAME
ARG PROJECT_ID
ARG MOUNT_PATH="/mnt/disks/share"

# Environment variables required by the AlphaEvolve Batch Harness
ENV _CLOUD_BUCKET_NAME=${CLOUD_BUCKET_NAME}
ENV _PROJECT_ID=${PROJECT_ID}
ENV _JOB_ID=""
ENV _CANDIDATE_PROGRAM_ID=""
ENV _MOUNT_PATH=${MOUNT_PATH}
ENV _PROGRAMS_DIR=""
ENV _CLIENT_EVALUATOR_SCRIPT=""
ENV _CLIENT_EVALUATOR_METHOD=""
ENV _CANDIDATE_DIR=""

WORKDIR /app

# Copy the experiment code
COPY user_examples/rocket_nozzle_openfoam/ /app/experiment/

WORKDIR /app/src/alpha_evolve

RUN (userdel -r ubuntu 2>/dev/null || true) && \
    useradd -m -u 1000 evaluser && \
    chown -R evaluser:evaluser /app/experiment
USER evaluser
