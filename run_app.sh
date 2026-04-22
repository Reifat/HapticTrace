#!/bin/bash
# Copyright 2026 Nikolai Kolesnikov
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
PYTHON_BIN="$VENV_DIR/bin/python"
PIP_BIN="$VENV_DIR/bin/pip"
REQUIREMENTS_DIR="$ROOT_DIR/requirements"
BOOTSTRAP_PROFILE="${HAPTIC_BOOTSTRAP_PROFILE:-runtime}"
APP_ARGS=()
export XDG_CACHE_HOME="$VENV_DIR/.cache"
export MPLCONFIGDIR="$XDG_CACHE_HOME/matplotlib"

cd "$ROOT_DIR"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --bootstrap-profile)
      if [ "$#" -lt 2 ]; then
        echo "missing value for --bootstrap-profile"
        exit 1
      fi
      BOOTSTRAP_PROFILE="$2"
      shift 2
      ;;
    --bootstrap-dev)
      BOOTSTRAP_PROFILE="dev"
      shift
      ;;
    --bootstrap-runtime)
      BOOTSTRAP_PROFILE="runtime"
      shift
      ;;
    *)
      APP_ARGS+=("$1")
      shift
      ;;
  esac
done

case "$BOOTSTRAP_PROFILE" in
  runtime)
    LOCKFILE="$REQUIREMENTS_DIR/runtime.lock"
    STAMP_FILE="$VENV_DIR/.runtime_deps_installed"
    ;;
  dev)
    LOCKFILE="$REQUIREMENTS_DIR/dev.lock"
    STAMP_FILE="$VENV_DIR/.dev_deps_installed"
    ;;
  *)
    echo "unsupported bootstrap profile: $BOOTSTRAP_PROFILE"
    echo "expected one of: runtime, dev"
    exit 1
    ;;
esac

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found"
  exit 1
fi

if [ ! -x "$PYTHON_BIN" ] || [ ! -x "$PIP_BIN" ]; then
  python3 -m venv "$VENV_DIR"
fi

mkdir -p "$MPLCONFIGDIR"

"$PYTHON_BIN" -m pip install --upgrade pip >/dev/null

if [ ! -f "$LOCKFILE" ]; then
  echo "lockfile not found: $LOCKFILE"
  exit 1
fi

if [ ! -f "$STAMP_FILE" ] || [ "$LOCKFILE" -nt "$STAMP_FILE" ]; then
  "$PIP_BIN" install -r "$LOCKFILE"
  touch "$STAMP_FILE"
  if [ "$BOOTSTRAP_PROFILE" = "dev" ]; then
    touch "$VENV_DIR/.runtime_deps_installed"
  fi
fi

if [ "${#APP_ARGS[@]}" -eq 0 ]; then
  exec env PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" -m app
fi

exec env PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" -m app "${APP_ARGS[@]}"