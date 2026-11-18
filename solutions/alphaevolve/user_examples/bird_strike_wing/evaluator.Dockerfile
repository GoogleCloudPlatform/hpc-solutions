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

WORKDIR /app

# Copy experiment files
COPY user_examples/bird_strike_wing/ /app/experiment/

# Execute setup.sh at build time to install OpenRADIOSS and Linux runtime libraries
RUN if [ -f /app/experiment/setup.sh ]; then \
      chmod +x /app/experiment/setup.sh && \
      /app/experiment/setup.sh; \
    fi

ENV OPENRADIOSS_PATH="/opt/OpenRadioss"
ENV RAD_CFG_PATH="/opt/OpenRadioss/hm_cfg_files"
ENV LD_LIBRARY_PATH="/opt/OpenRadioss/extlib/hm_reader/linux64:/opt/OpenRadioss/extlib/h3d/lib/linux64"
ENV OMP_STACKSIZE="400m"

WORKDIR /app/src/alpha_evolve

RUN useradd -m -u 1000 evaluser && \
    chown -R evaluser:evaluser /app/experiment
USER evaluser
