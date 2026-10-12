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

# Base Python image
ARG BASE_PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ${BASE_PYTHON_IMAGE}

# Copy high-performance uv package manager
COPY --from=ghcr.io/astral-sh/uv@sha256:606e70c71c852d03f611b1e56a195d08648507018a7057fab82c4974c4eae105 /uv /uvx /bin/

# Install essential system build tools and utilities for user experiments
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        git \
        wget \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Create virtual environment with Python 3.12 (globally accessible)
ENV UV_PYTHON_INSTALL_DIR=/opt/uv/python
ENV VIRTUAL_ENV=/opt/venv
RUN uv python install 3.12 && uv venv --python 3.12 $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Set working directory
WORKDIR /app

# Copy and install core framework requirements
COPY infrastructure/requirements.txt .
RUN uv pip install --require-hashes -r requirements.txt

# Copy core framework library
COPY google_framework/alpha_evolve ./src/alpha_evolve
ENV PYTHONPATH="/app/src:${PYTHONPATH}"

# Ensure global read and execute permissions on Python runtime and framework
RUN chmod -R a+rx /opt/uv /opt/venv /app/src

# Default universal platform runner entrypoint
ENTRYPOINT ["python", "-m", "alpha_evolve.runner"]
