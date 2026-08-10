# TinyCoder Sandbox Image
# Mirrors the qwen-code sandbox image design.
# Provides an isolated Linux environment with Python and common dev tools.

FROM python:3.11-slim

LABEL org.tinycoder.sandbox=true

# Install common development tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    curl \
    git \
    grep \
    ripgrep \
    sed \
    findutils \
    build-essential \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user matching typical host setup
RUN useradd -m -s /bin/bash node && \
    mkdir -p /home/node/.tinycoder && \
    chown -R node:node /home/node

# Install tinycoder into the image
COPY . /tmp/tinycoder
RUN pip install --no-cache-dir /tmp/tinycoder && \
    rm -rf /tmp/tinycoder

# Default working directory — workspace will be mounted here at runtime
WORKDIR /workspace

USER node

ENTRYPOINT ["tinycoder"]
