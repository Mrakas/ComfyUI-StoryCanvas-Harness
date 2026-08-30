#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
HARNESS_ROOT="${SCRIPT_DIR:h}"
COMFY_ROOT="${HARNESS_ROOT:h}/ComfyUI"

if [[ ! -x "${COMFY_ROOT}/.venv/bin/python" ]]; then
  print -u2 "ComfyUI environment is missing: ${COMFY_ROOT}/.venv"
  exit 1
fi

export STORYCANVAS_PROVIDER_MODE="${STORYCANVAS_PROVIDER_MODE:-codex}"
export STORYCANVAS_CODEX_ENABLED="${STORYCANVAS_CODEX_ENABLED:-true}"
export STORYCANVAS_CODEX_MODEL="${STORYCANVAS_CODEX_MODEL:-gpt-5.6-sol}"
export STORYCANVAS_CODEX_REASONING_EFFORT="${STORYCANVAS_CODEX_REASONING_EFFORT:-medium}"
export STORYCANVAS_CODEX_BIN="${STORYCANVAS_CODEX_BIN:-/opt/homebrew/bin/codex}"
export STORYCANVAS_RUNS_DIR="${STORYCANVAS_RUNS_DIR:-${HARNESS_ROOT}/output/comfyui_runs}"

cd "${COMFY_ROOT}"
exec .venv/bin/python main.py --listen 127.0.0.1 --port 8188 --disable-auto-launch
