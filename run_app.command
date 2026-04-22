#!/bin/bash
# Copyright 2026 Nikolai Kolesnikov
# SPDX-License-Identifier: Apache-2.0
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$ROOT_DIR/run_app.sh" "$@"