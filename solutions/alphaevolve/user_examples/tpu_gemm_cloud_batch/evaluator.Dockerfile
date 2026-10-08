ARG BASE_IMAGE=python:3.12-slim-bookworm
FROM ${BASE_IMAGE}

ARG CLOUD_BUCKET_NAME
ARG PROJECT_ID
ARG MOUNT_PATH="/mnt/disks/share"

# Environment variables
ENV _CLOUD_BUCKET_NAME=${CLOUD_BUCKET_NAME}
ENV _PROJECT_ID=${PROJECT_ID}
ENV _JOB_ID=""
ENV _CANDIDATE_PROGRAM_ID=""
ENV _MOUNT_PATH=${MOUNT_PATH}
ENV _PROGRAMS_DIR=""
ENV _CLIENT_EVALUATOR_SCRIPT=""
ENV _CLIENT_EVALUATOR_METHOD=""
ENV _CANDIDATE_DIR=""
ENV TPU_ACCELERATION_ENABLED=true

# Set the working directory
WORKDIR /app

# Copy and install custom experiment dependencies (JAX, libtpu) using uv
COPY user_examples/tpu_gemm_cloud_batch/requirements.txt /app/experiment/requirements.txt
RUN uv pip install --require-hashes -r /app/experiment/requirements.txt

# Copy the experiment code
COPY user_examples/tpu_gemm_cloud_batch/ /app/experiment/

WORKDIR /app/src/alpha_evolve
