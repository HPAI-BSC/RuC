#!/bin/bash
# Build the Harbor RuC uses: the pinned upstream release plus ruc.patch, in .venv/ here.
# Needs internet. Usage: third_party/harbor/setup_harbor.sh [python>=3.12]

set -euo pipefail

HARBOR_VERSION="0.20.1.dev202607310211"
SDIST_URL="https://files.pythonhosted.org/packages/source/h/harbor/harbor-${HARBOR_VERSION}.tar.gz"
SDIST_SHA256="2abeee59fdbb1f2bf396ad4112b86a884e635ded8fd2d133f84d32d873ff6556"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="${HERE}/harbor-src"
VENV_DIR="${HERE}/.venv"
PYTHON="${1:-}"

if [[ -z "$PYTHON" ]]; then
    for candidate in python3.13 python3.12 python3; do
        if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
            PYTHON="$candidate"
            break
        fi
    done
fi
if [[ -z "$PYTHON" ]] || ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
    echo "[ERROR] Harbor needs Python >= 3.12. Pass one as the first argument." >&2
    exit 1
fi

tarball="${HERE}/harbor-${HARBOR_VERSION}.tar.gz"
trap 'rm -f "$tarball"' EXIT

echo "[INFO] Downloading harbor ${HARBOR_VERSION}"
"$PYTHON" -c "import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])" "$SDIST_URL" "$tarball"
echo "${SDIST_SHA256}  ${tarball}" | sha256sum --check --quiet

rm -rf "$SRC_DIR"
mkdir -p "$SRC_DIR"
tar xzf "$tarball" -C "$SRC_DIR" --strip-components=1

echo "[INFO] Applying ruc.patch"
patch -p1 --directory "$SRC_DIR" --forward --quiet < "${HERE}/ruc.patch"

echo "[INFO] Creating ${VENV_DIR} with $("$PYTHON" --version)"
rm -rf "$VENV_DIR"
if command -v uv >/dev/null; then
    uv venv --quiet --python "$PYTHON" "$VENV_DIR"
    uv pip install --quiet --no-sources --python "$VENV_DIR/bin/python" --editable "$SRC_DIR"
else
    "$PYTHON" -m venv "$VENV_DIR"
    "$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip
    "$VENV_DIR/bin/python" -m pip install --quiet --editable "$SRC_DIR"
fi

"$VENV_DIR/bin/python" -m harbor.cli.main --version >/dev/null
echo "[INFO] Done: ${VENV_DIR}/bin/python"
