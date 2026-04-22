# Copyright 2026 Nikolai Kolesnikov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

_REQUIREMENTS_DIR = Path(__file__).resolve().parents[2] / "requirements"

RUNTIME_REQUIREMENTS_IN = _REQUIREMENTS_DIR / "runtime.in"
DEV_REQUIREMENTS_IN = _REQUIREMENTS_DIR / "dev.in"
RUNTIME_LOCKFILE = _REQUIREMENTS_DIR / "runtime.lock"
DEV_LOCKFILE = _REQUIREMENTS_DIR / "dev.lock"


def _read_declared_packages(path: Path) -> list[str]:
    packages: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-r "):
            continue
        packages.append(line)
    return packages


RUNTIME_PACKAGES = _read_declared_packages(RUNTIME_REQUIREMENTS_IN)
DEV_PACKAGES = [
    package
    for package in _read_declared_packages(DEV_REQUIREMENTS_IN)
    if package not in RUNTIME_PACKAGES
]