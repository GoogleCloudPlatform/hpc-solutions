#!/bin/bash
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

set -euo pipefail

OPENRADIOSS_VERSION="20230915"
OPENRADIOSS_URL="${OPENRADIOSS_URL:-https://www.phoronix-test-suite.com/benchmark-files/OpenRadioss_linux64_${OPENRADIOSS_VERSION}.tar.xz}"
OPENRADIOSS_SHA256="${OPENRADIOSS_SHA256:-b3f9313eceb5ef482182eced401a33848f9f6cad00c9b9e89c197a0fbdd36fcd}"
INSTALL_DIR="${OPENRADIOSS_PATH:-/opt/OpenRadioss}"

if [ ! -f "${INSTALL_DIR}/exec/starter_linux64_gf_sp" ]; then
  echo "OpenRADIOSS not found at ${INSTALL_DIR}; checking /tmp/openradioss_test..."
  if [ -f "/tmp/openradioss_test/OpenRadioss_linux64_${OPENRADIOSS_VERSION}/OpenRadioss/exec/starter_linux64_gf_sp" ]; then
    export OPENRADIOSS_PATH="/tmp/openradioss_test/OpenRadioss_linux64_${OPENRADIOSS_VERSION}/OpenRadioss"
    echo "Using existing OpenRADIOSS at ${OPENRADIOSS_PATH}"
  else
    echo "Downloading OpenRADIOSS ${OPENRADIOSS_VERSION} release binary..."
    mkdir -p /opt /tmp/openradioss_dl
    if command -v wget >/dev/null 2>&1; then
      wget -q -O /tmp/openradioss_dl/OpenRadioss_linux64.tar.xz "${OPENRADIOSS_URL}"
    else
      curl -fsSL -o /tmp/openradioss_dl/OpenRadioss_linux64.tar.xz "${OPENRADIOSS_URL}"
    fi
    echo "${OPENRADIOSS_SHA256}  /tmp/openradioss_dl/OpenRadioss_linux64.tar.xz" | sha256sum -c -
    tar -xJf /tmp/openradioss_dl/OpenRadioss_linux64.tar.xz -C /opt
    rm -rf /tmp/openradioss_dl
    ln -sf "/opt/OpenRadioss_linux64_${OPENRADIOSS_VERSION}/OpenRadioss" /opt/OpenRadioss
    export OPENRADIOSS_PATH="/opt/OpenRadioss"
  fi
fi

echo "Verified OpenRADIOSS installation at ${OPENRADIOSS_PATH:-${INSTALL_DIR}}"
