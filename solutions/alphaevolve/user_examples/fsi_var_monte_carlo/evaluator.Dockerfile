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
ENV PYTHONUNBUFFERED=1
ENV OMP_NUM_THREADS=1

# Install OpenMPI and OpenSSH services for multi-node MPI execution
RUN apt-get update && apt-get install -y --no-install-recommends \
    openmpi-bin \
    libopenmpi-dev \
    openssh-server \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Create evaluser for non-root execution (Cloud Batch permissiveSsh provisions /etc/ssh/id_cloud_batch; run-ssh.sh provides a local fallback)
RUN useradd -m -u 1000 evaluser \
    && mkdir -p /var/run/sshd /home/evaluser/.ssh \
    && echo "Host *\n\tStrictHostKeyChecking no\n\tUserKnownHostsFile /dev/null" > /home/evaluser/.ssh/config \
    && chmod 700 /home/evaluser/.ssh \
    && chmod 600 /home/evaluser/.ssh/config \
    && chown -R evaluser:evaluser /home/evaluser/.ssh

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

WORKDIR /app/src/alpha_evolve

# Copy the experiment code
COPY user_examples/fsi_var_monte_carlo/ /app/experiment/
RUN chmod +x /app/experiment/run-ssh.sh && \
    chown -R evaluser:evaluser /app/experiment
USER evaluser
