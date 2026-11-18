#!/bin/bash
# Download Node and OpenCode into vendor/, for ruc-sim-opencode.def. Needs internet.

set -euo pipefail

NODE_VERSION="22.20.0"                              # Must match %files in the .def
OPENCODE_VERSION="${OPENCODE_VERSION:-latest}"      # npm version of opencode-ai

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENDOR="${HERE}/vendor"
NODE_TAR="node-v${NODE_VERSION}-linux-x64.tar.xz"

if [[ "$OPENCODE_VERSION" == "latest" ]]; then
    echo "[WARNING] OPENCODE_VERSION is not pinned, installing the latest opencode-ai" >&2
fi

mkdir -p "$VENDOR"
cd "$VENDOR"

echo "[INFO] Downloading Node ${NODE_VERSION}"
curl -fsSLO "https://nodejs.org/dist/v${NODE_VERSION}/${NODE_TAR}"
curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt" | grep " ${NODE_TAR}\$" | sha256sum --check --quiet

work="$(mktemp -d -p "$VENDOR")"
trap 'rm -rf "$work"' EXIT

mkdir -p "$work/node" "$work/ocroot"
tar xJf "$NODE_TAR" -C "$work/node" --strip-components=1

echo "[INFO] Installing opencode-ai@${OPENCODE_VERSION}"
PATH="$work/node/bin:$PATH" npm install --global --no-fund --no-audit \
    --prefix "$work/ocroot" "opencode-ai@${OPENCODE_VERSION}"
PATH="$work/node/bin:$PATH" "$work/ocroot/bin/opencode" --version

tar czf ocroot.tgz -C "$work/ocroot" .

sha256sum "$NODE_TAR" ocroot.tgz > VENDOR.sha256
cat VENDOR.sha256
