#!/bin/bash
# container_configs/EXAMPLE.sh
#
# Setup script for a project that requires system-level packages before pip.
# Listed under [setup] in the corresponding .txt config.
# Piped to bash inside the Odoo container — runs as root.
#
# Uncomment and adapt the blocks you need.

set -e

# Install C build tools (needed for packages that compile native extensions,
# e.g. pycairo, lxml without a wheel, Pillow without a wheel):
# apt-get update -qq
# apt-get install -y --no-install-recommends \
#     gcc \
#     libcairo2-dev \
#     pkg-config \
#     python3-dev
