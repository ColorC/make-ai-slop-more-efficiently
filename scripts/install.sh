#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

find_python() {
  if command -v python3 >/dev/null 2>&1; then
    printf '%s\n' "python3"
    return 0
  fi

  if command -v python >/dev/null 2>&1; then
    printf '%s\n' "python"
    return 0
  fi

  return 1
}

PYTHON="$(find_python || true)"
if [ -z "${PYTHON}" ]; then
  printf 'Error: Python 3.10+ was not found on PATH.\n' >&2
  printf 'Install Python first, then re-run: bash scripts/install.sh\n' >&2
  exit 1
fi

if ! "${PYTHON}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
  printf 'Error: %s is older than Python 3.10. Install Python 3.10+ and retry.\n' "$("${PYTHON}" --version 2>&1)" >&2
  exit 1
fi

if ! "${PYTHON}" -m pip --version >/dev/null 2>&1; then
  printf 'Error: pip is not available for %s.\n' "${PYTHON}" >&2
  printf 'Install/enable pip, then re-run: bash scripts/install.sh\n' >&2
  exit 1
fi

cd "${REPO_ROOT}"

if command -v pipx >/dev/null 2>&1; then
  printf 'Installing omnicompany with pipx...\n'
  if pipx install .; then
    printf 'Install complete. Try: omni --help\n'
    exit 0
  fi

  printf 'pipx install failed; falling back to pip install .\n' >&2
else
  printf 'pipx was not found; falling back to pip install .\n' >&2
  printf 'Tip: install pipx for isolated CLI installs: python -m pip install --user pipx\n' >&2
fi

printf 'Installing omnicompany with pip...\n'
"${PYTHON}" -m pip install .
printf 'Install complete. Try: omni --help\n'
