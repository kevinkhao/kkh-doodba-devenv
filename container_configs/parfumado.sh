#!/bin/bash
# Install system-level build dependencies needed before pip install.
# pycairo requires gcc, libcairo2 headers, and pkg-config to build from source.
set -e
apt-get update -qq
apt-get install -y --no-install-recommends \
    gcc \
    libcairo2-dev \
    pkg-config \
    python3-dev
