#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
HARNESS_ROOT="${SCRIPT_DIR:h}"
COMFY_ROOT="${HARNESS_ROOT:h}/ComfyUI"
COMFY_TAG="v0.33.0"

if ! command -v uv >/dev/null 2>&1; then
  print -u2 "uv is required: https://docs.astral.sh/uv/"
  exit 1
fi

if [[ ! -d "${COMFY_ROOT}/.git" ]]; then
  git clone --depth 1 --branch "${COMFY_TAG}" \
    https://github.com/comfyanonymous/ComfyUI.git "${COMFY_ROOT}"
else
  CURRENT_TAG="$(git -C "${COMFY_ROOT}" describe --tags --exact-match 2>/dev/null || true)"
  if [[ "${CURRENT_TAG}" != "${COMFY_TAG}" ]]; then
    print -u2 "Existing ComfyUI is not pinned to ${COMFY_TAG}: ${COMFY_ROOT}"
    exit 1
  fi
fi

uv venv --python 3.12 "${COMFY_ROOT}/.venv"
uv pip install --python "${COMFY_ROOT}/.venv/bin/python" \
  -r "${COMFY_ROOT}/requirements.txt"
uv pip install --python "${COMFY_ROOT}/.venv/bin/python" \
  -e "${HARNESS_ROOT}[codex]"

NODE_LINK="${COMFY_ROOT}/custom_nodes/ComfyUI-StoryCanvas-Harness"
mkdir -p "${COMFY_ROOT}/custom_nodes"
if [[ -e "${NODE_LINK}" && ! -L "${NODE_LINK}" ]]; then
  print -u2 "Custom-node path exists and is not a symlink: ${NODE_LINK}"
  exit 1
fi
ln -sfn "${HARNESS_ROOT}" "${NODE_LINK}"

print "Installed ComfyUI ${COMFY_TAG} with Python $("${COMFY_ROOT}/.venv/bin/python" -V 2>&1)"
print "Start with: ${HARNESS_ROOT}/scripts/start_local_comfyui.sh"
