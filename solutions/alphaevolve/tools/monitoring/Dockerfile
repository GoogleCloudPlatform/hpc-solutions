# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0 (the "License");

ARG BASE_IMAGE=python:3.12-slim-bookworm
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /app

# Copy and install core requirements with checksum verification
COPY infrastructure/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /app/requirements.txt

# Copy application source
COPY . /app

# Create and switch to non-root user
RUN useradd -m -u 1000 monitoruser
RUN chown -R monitoruser:monitoruser /app /opt/venv
USER monitoruser

ENV PORT=8080
EXPOSE 8080

ENTRYPOINT ["python3", "tools/monitoring/ae_web_monitor.py"]
