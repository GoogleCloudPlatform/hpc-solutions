ARG BASE_IMAGE=python:3.12-slim-bookworm
FROM ${BASE_IMAGE}

# Prevent interactive prompts
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Install OpenMPI & OpenSSH services for multi-node MPI execution
RUN apt-get update && apt-get install -y \
    openmpi-bin \
    libopenmpi-dev \
    openssh-server \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Create evaluser and configure passwordless SSH for non-root execution
RUN useradd -m -u 1000 evaluser \
    && mkdir -p /var/run/sshd /home/evaluser/.ssh /root/.ssh \
    && ssh-keygen -t rsa -f /home/evaluser/.ssh/id_rsa -N "" \
    && cp /home/evaluser/.ssh/id_rsa.pub /home/evaluser/.ssh/authorized_keys \
    && cp /home/evaluser/.ssh/id_rsa.pub /root/.ssh/authorized_keys \
    && echo "Host *\n\tStrictHostKeyChecking no\n\tUserKnownHostsFile /dev/null" > /home/evaluser/.ssh/config \
    && chmod 700 /home/evaluser/.ssh \
    && chmod 600 /home/evaluser/.ssh/id_rsa /home/evaluser/.ssh/authorized_keys /home/evaluser/.ssh/config \
    && chown -R evaluser:evaluser /home/evaluser/.ssh

# Copy the experiment code
COPY user_examples/nbody_molecular_dynamics/ /app/experiment/
RUN chmod +x /app/experiment/run-ssh.sh
RUN chown -R evaluser:evaluser /app/experiment
USER evaluser
