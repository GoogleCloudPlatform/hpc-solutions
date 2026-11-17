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

# Stage 1: AlphaEvolve base image built by Cloud Build during `gcluster deploy`
ARG BASE_IMAGE=python:3.12-slim-bookworm
FROM ${BASE_IMAGE} AS ae_base

# Stage 2: Official OpenROAD runtime image (includes pre-built `openroad` 26Q3 binary)
FROM docker.io/openroad/orfs:26Q3-586-gfbc8fa48d

ARG CLOUD_BUCKET_NAME
ARG PROJECT_ID
ARG MOUNT_PATH="/mnt/disks/share"
ARG STAGE_ALL20="0"

# Copy relocatable uv Python 3.12 runtime and AlphaEvolve harness from ae_base
COPY --from=ae_base /bin/uv /bin/uvx /bin/
COPY --from=ae_base /opt/uv /opt/uv
COPY --from=ae_base /opt/venv /opt/venv
COPY --from=ae_base /app/src /app/src

# Environment variables required by the AlphaEvolve Batch Harness & OpenROAD Evaluator
ENV UV_PYTHON_INSTALL_DIR="/opt/uv/python"
ENV VIRTUAL_ENV="/opt/venv"
ENV PATH="/opt/venv/bin:/OpenROAD-flow-scripts/tools/install/OpenROAD/bin:${PATH}"
ENV PYTHONPATH="/app/src:/app/experiment"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV OMP_WAIT_POLICY=PASSIVE
ENV OPENROAD_BIN="/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad"
ENV ISPD_BENCH_DIR="/app/ispd_bench"
ENV B1_DESIGNS="mini_train"
ENV STAGE_ALL20="${STAGE_ALL20}"

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

# Copy the experiment code and run automated setup (verifies OpenROAD & AlphaEvolve
# runtime and downloads + patches the 6-design ISPD Mini Dataset into /app/ispd_bench)
COPY user_examples/openroad_drt_schedule/ /app/experiment/
RUN chmod +x /app/experiment/setup.sh /app/experiment/tutorial/*.sbatch && \
    /app/experiment/setup.sh --container

WORKDIR /app/src/alpha_evolve

RUN useradd -m -u 1000 evaluser 2>/dev/null || true && \
    mkdir -p /tmp/b1_ae_data_1000 && \
    chown -R 1000:1000 /app/experiment /app/ispd_bench /tmp/b1_ae_data_1000
USER 1000

ENTRYPOINT ["python", "-m", "alpha_evolve.runner"]
