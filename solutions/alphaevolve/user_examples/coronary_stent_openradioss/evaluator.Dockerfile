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

USER root

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

ARG OPENRADIOSS_VERSION=20230915
ARG OPENRADIOSS_SHA256="b3f9313eceb5ef482182eced401a33848f9f6cad00c9b9e89c197a0fbdd36fcd"
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wget \
    xz-utils \
    ca-certificates \
    libgomp1 \
    libquadmath0 \
    libgfortran5 \
    libuuid1 \
    libcrypt1 && \
    mkdir -p /opt && \
    wget -q -O /tmp/OpenRadioss_linux64.tar.xz \
      "https://www.phoronix-test-suite.com/benchmark-files/OpenRadioss_linux64_${OPENRADIOSS_VERSION}.tar.xz" && \
    echo "${OPENRADIOSS_SHA256}  /tmp/OpenRadioss_linux64.tar.xz" | sha256sum -c - && \
    tar -xJf /tmp/OpenRadioss_linux64.tar.xz -C /opt && \
    rm -f /tmp/OpenRadioss_linux64.tar.xz && \
    ln -sf "/opt/OpenRadioss_linux64_${OPENRADIOSS_VERSION}/OpenRadioss" /opt/OpenRadioss && \
    chmod -R a+rX /opt/OpenRadioss "/opt/OpenRadioss_linux64_${OPENRADIOSS_VERSION}" && \
    rm -rf /var/lib/apt/lists/*

ARG CLOUD_BUCKET_NAME
ARG PROJECT_ID
ARG MOUNT_PATH="/mnt/disks/share"

ENV _CLOUD_BUCKET_NAME=${CLOUD_BUCKET_NAME}
ENV _PROJECT_ID=${PROJECT_ID}
ENV _JOB_ID=""
ENV _CANDIDATE_PROGRAM_ID=""
ENV _MOUNT_PATH=${MOUNT_PATH}
ENV _PROGRAMS_DIR=""
ENV _CLIENT_EVALUATOR_SCRIPT=""
ENV _CLIENT_EVALUATOR_METHOD=""
ENV _CANDIDATE_DIR=""

ENV OPENRADIOSS_PATH="/opt/OpenRadioss"
ENV RAD_CFG_PATH="/opt/OpenRadioss/hm_cfg_files"
ENV LD_LIBRARY_PATH="/opt/OpenRadioss/extlib/hm_reader/linux64:/opt/OpenRadioss/extlib/h3d/lib/linux64"
ENV OMP_STACKSIZE="400m"

WORKDIR /app

# Copy experiment files
COPY user_examples/coronary_stent_openradioss/ /app/experiment/

# Execute setup.sh at build time if present
RUN if [ -f /app/experiment/setup.sh ]; then \
      chmod +x /app/experiment/setup.sh && \
      /app/experiment/setup.sh; \
    fi

WORKDIR /app/src/alpha_evolve

RUN useradd -m -u 1000 evaluser && \
    chown -R evaluser:evaluser /app/experiment
USER evaluser
