#!/bin/bash
# Copyright 2026 Nikolai Kolesnikov
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="$ROOT_DIR/build"
BUILD_VENV="$BUILD_DIR/.release-build-venv"
PY2APP_DIST="$BUILD_DIR/py2app-dist"
PY2APP_WORK="$BUILD_DIR/py2app-work"
PY2APP_PROJECT="$BUILD_DIR/py2app-project"
TOOLS_DIR="$BUILD_DIR/tools"
SYFT_DIR="$TOOLS_DIR/syft"
RELEASE_DIR="$BUILD_DIR/release"
RELEASE_REQUIREMENTS_DIR="$RELEASE_DIR/requirements"
RELEASE_SCRIPTS_DIR="$RELEASE_DIR/scripts"
RELEASE_COMPLIANCE_DIR="$RELEASE_DIR/compliance"
APP_NAME="HapticTrace"
BUNDLE_ID="com.nikolaikolesnikov.haptictrace"
APP_BUNDLE="$PY2APP_DIST/$APP_NAME.app"
FINAL_APP="$RELEASE_DIR/$APP_NAME.app"
INFO_PLIST="$FINAL_APP/Contents/Info.plist"
SBOM_PATH="$RELEASE_COMPLIANCE_DIR/sbom.runtime.cdx.json"
BUILD_INFO_PATH="$RELEASE_DIR/BUILD_INFO.txt"
CODESIGN_IDENTITY="${HAPTIC_CODESIGN_IDENTITY:--}"
ALLOW_ADHOC_CODESIGN="${HAPTIC_ALLOW_ADHOC:-0}"
SYFT_VERSION="${HAPTIC_SYFT_VERSION:-1.42.2}"
APP_ENTRY="$ROOT_DIR/scripts/HapticTrace.py"
RELEASE_TOOLS_IN="$ROOT_DIR/scripts/requirements-release.in"
RELEASE_TOOLS_LOCK="$ROOT_DIR/scripts/requirements-release.lock"

case "$(uname -m)" in
  arm64|aarch64)
    HOST_ARCH="arm64"
    DEFAULT_MAX_MACHO_MINOS="12.3"
    SYFT_ARCH="arm64"
    ;;
  x86_64)
    HOST_ARCH="x86_64"
    DEFAULT_MAX_MACHO_MINOS="10.14"
    SYFT_ARCH="amd64"
    ;;
  *)
    echo "unsupported macOS architecture for release build: $(uname -m)" >&2
    exit 1
    ;;
esac

RELEASE_ARCH="${HAPTIC_RELEASE_ARCH:-$HOST_ARCH}"
MAX_MACHO_MINOS="${HAPTIC_MAX_MACHO_MINOS:-$DEFAULT_MAX_MACHO_MINOS}"
ARCHIVE_PATH="$BUILD_DIR/${APP_NAME}-macos-${RELEASE_ARCH}-release.zip"

log() {
  printf '\n[%s] %s\n' "build-release" "$1"
}

require_file() {
  if [ ! -f "$1" ]; then
    echo "required file is missing: $1" >&2
    exit 1
  fi
}

if [ "$(uname -s)" != "Darwin" ]; then
  echo "this release builder currently supports macOS only" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found" >&2
  exit 1
fi

if ! command -v codesign >/dev/null 2>&1; then
  echo "codesign not found" >&2
  exit 1
fi

if ! command -v ditto >/dev/null 2>&1; then
  echo "ditto not found" >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl not found" >&2
  exit 1
fi

if ! command -v tar >/dev/null 2>&1; then
  echo "tar not found" >&2
  exit 1
fi

if ! command -v lipo >/dev/null 2>&1; then
  echo "lipo not found" >&2
  exit 1
fi

if ! command -v otool >/dev/null 2>&1; then
  echo "otool not found" >&2
  exit 1
fi

if [ "$RELEASE_ARCH" != "$HOST_ARCH" ]; then
  echo "cross-architecture release builds are not supported by this script" >&2
  echo "requested: $RELEASE_ARCH, host: $HOST_ARCH" >&2
  exit 1
fi

if [ "$CODESIGN_IDENTITY" = "-" ] && [ "$ALLOW_ADHOC_CODESIGN" != "1" ]; then
  echo "release builds require a Developer ID signing identity" >&2
  echo "set HAPTIC_CODESIGN_IDENTITY, or set HAPTIC_ALLOW_ADHOC=1 for local-only test builds" >&2
  exit 1
fi

require_file "$ROOT_DIR/requirements/runtime.lock"
require_file "$ROOT_DIR/requirements/runtime.in"
require_file "$APP_ENTRY"
require_file "$RELEASE_TOOLS_IN"
require_file "$RELEASE_TOOLS_LOCK"
require_file "$ROOT_DIR/LICENSE"
require_file "$ROOT_DIR/NOTICE"
require_file "$ROOT_DIR/THIRD_PARTY_NOTICES.md"
require_file "$ROOT_DIR/README.md"
require_file "$ROOT_DIR/app/__main__.py"

log "Resetting build directory"
python3 - "$BUILD_DIR" <<'PY'
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

build_dir = Path(sys.argv[1])

for _ in range(5):
    if build_dir.exists():
        shutil.rmtree(build_dir, ignore_errors=True)
    if not build_dir.exists():
        break
    time.sleep(0.2)
else:
    raise SystemExit(f"failed to remove build directory: {build_dir}")
PY
mkdir -p \
  "$RELEASE_REQUIREMENTS_DIR" \
  "$RELEASE_SCRIPTS_DIR" \
  "$RELEASE_COMPLIANCE_DIR" \
  "$TOOLS_DIR" \
  "$SYFT_DIR" \
  "$PY2APP_PROJECT"

log "Cleaning Python bytecode from source tree"
python3 - "$ROOT_DIR/app" "$ROOT_DIR/scripts" <<'PY'
from __future__ import annotations

import shutil
import sys
from pathlib import Path

for root_arg in sys.argv[1:]:
    root = Path(root_arg)
    if not root.exists():
        continue
    for cache_dir in root.rglob("__pycache__"):
        shutil.rmtree(cache_dir, ignore_errors=True)
    for bytecode_path in root.rglob("*.pyc"):
        bytecode_path.unlink(missing_ok=True)
PY

SYFT_TARBALL="syft_${SYFT_VERSION}_darwin_${SYFT_ARCH}.tar.gz"
SYFT_TARBALL_URL="https://github.com/anchore/syft/releases/download/v${SYFT_VERSION}/${SYFT_TARBALL}"
SYFT_CHECKSUMS_URL="https://github.com/anchore/syft/releases/download/v${SYFT_VERSION}/syft_${SYFT_VERSION}_checksums.txt"
SYFT_TARBALL_PATH="$SYFT_DIR/$SYFT_TARBALL"
SYFT_CHECKSUMS_PATH="$SYFT_DIR/syft_checksums.txt"
SYFT_BIN="$SYFT_DIR/syft"

log "Creating release build environment"
python3 -m venv "$BUILD_VENV"
"$BUILD_VENV/bin/python" -m pip install --upgrade pip >/dev/null
"$BUILD_VENV/bin/pip" install -r "$ROOT_DIR/requirements/runtime.lock"
"$BUILD_VENV/bin/pip" install -r "$RELEASE_TOOLS_LOCK"
PYTHON_VERSION_MM="$("$BUILD_VENV/bin/python" - <<'PY'
import sys

print(f"{sys.version_info.major}.{sys.version_info.minor}")
PY
)"
BUILD_SITE_PACKAGES="$("$BUILD_VENV/bin/python" - <<'PY'
import sysconfig

print(sysconfig.get_path("purelib"))
PY
)"

log "Preparing py2app project"
python3 - "$PY2APP_PROJECT/setup.py" "$BUNDLE_ID" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

setup_path = Path(sys.argv[1])
bundle_id = sys.argv[2]

setup_path.write_text(
    f"""from __future__ import annotations

import sys
from pathlib import Path

from setuptools import setup

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

OPTIONS = {{
    "argv_emulation": False,
    "packages": [
        "app",
        "requests",
        "urllib3",
        "charset_normalizer",
        "idna",
        "certifi",
        "numpy",
        "scipy",
        "matplotlib",
        "PIL",
    ],
    "includes": [
        "tkinter",
        "matplotlib.backends.backend_tkagg",
        "PIL._tkinter_finder",
        "AVFoundation",
        "CoreMedia",
        "CoreMediaIO",
        "Quartz",
        "objc",
        "Foundation",
    ],
    "excludes": ["pytest", "_pytest", "app.tests"],
    "plist": {{
        "CFBundleDisplayName": "HapticTrace",
        "CFBundleName": "HapticTrace",
        "CFBundleIdentifier": {bundle_id!r},
        "CFBundleShortVersionString": "0.0.0",
        "NSCameraUsageDescription": "HapticTrace accesses video capture devices to record iPhone and iPad screens.",
        "NSHighResolutionCapable": True,
    }},
}}

setup(
    app=[str(ROOT_DIR / "scripts" / "HapticTrace.py")],
    options={{"py2app": OPTIONS}},
)
""",
    encoding="utf-8",
)
PY

log "Building the macOS app bundle with py2app"
env PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}" \
  PYTHONDONTWRITEBYTECODE=1 \
  MACOSX_DEPLOYMENT_TARGET="$MAX_MACHO_MINOS" \
  "$BUILD_VENV/bin/python" "$PY2APP_PROJECT/setup.py" py2app \
  --dist-dir "$PY2APP_DIST" \
  --bdist-base "$PY2APP_WORK"

if [ ! -d "$APP_BUNDLE" ]; then
  echo "expected app bundle was not produced: $APP_BUNDLE" >&2
  exit 1
fi

log "Staging release contents"
ditto "$APP_BUNDLE" "$FINAL_APP"
cp "$ROOT_DIR/LICENSE" "$RELEASE_DIR/"
cp "$ROOT_DIR/NOTICE" "$RELEASE_DIR/"
cp "$ROOT_DIR/THIRD_PARTY_NOTICES.md" "$RELEASE_DIR/"
cp "$ROOT_DIR/README.md" "$RELEASE_DIR/README.md"
cp "$ROOT_DIR/requirements/runtime.in" "$RELEASE_REQUIREMENTS_DIR/"
cp "$ROOT_DIR/requirements/runtime.lock" "$RELEASE_REQUIREMENTS_DIR/"
cp "$ROOT_DIR/scripts/build_release_macos.sh" "$RELEASE_SCRIPTS_DIR/"
cp "$ROOT_DIR/scripts/HapticTrace.py" "$RELEASE_SCRIPTS_DIR/"
cp "$RELEASE_TOOLS_IN" "$RELEASE_SCRIPTS_DIR/"
cp "$RELEASE_TOOLS_LOCK" "$RELEASE_SCRIPTS_DIR/"

log "Copying compiled helper modules needed by bundled dependencies"
python3 - "$BUILD_SITE_PACKAGES" "$FINAL_APP/Contents/Resources/lib/python${PYTHON_VERSION_MM}" <<'PY'
from __future__ import annotations

import shutil
import sys
from pathlib import Path

source_root = Path(sys.argv[1])
bundle_lib_root = Path(sys.argv[2])

for source_path in source_root.rglob("*__mypyc*.so"):
    destination_path = bundle_lib_root / source_path.relative_to(source_root)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination_path)
PY

log "Copying Tcl/Tk runtime resources"
"$BUILD_VENV/bin/python" - "$FINAL_APP/Contents/lib" <<'PY'
from __future__ import annotations

import shutil
import sys
import tkinter
from pathlib import Path

destination_root = Path(sys.argv[1])
source_root = Path(sys.base_prefix) / "lib"
required_libraries = {
    f"tcl{tkinter.TclVersion}": "init.tcl",
    f"tk{tkinter.TkVersion}": "tk.tcl",
    "tcl8": "8.6",
}

destination_root.mkdir(parents=True, exist_ok=True)
for library_name, marker_name in required_libraries.items():
    source_path = source_root / library_name
    marker_path = source_path / marker_name
    if not marker_path.exists():
        raise SystemExit(f"missing Tcl/Tk runtime resource: {marker_path}")
    destination_path = destination_root / library_name
    shutil.rmtree(destination_path, ignore_errors=True)
    shutil.copytree(source_path, destination_path, symlinks=True)
PY

log "Removing development-only modules and bundled bytecode caches"
python3 - "$FINAL_APP/Contents/Resources" "$FINAL_APP/Contents/Resources/lib/python${PYTHON_VERSION_MM}/app" <<'PY'
from __future__ import annotations

import shutil
import sys
from pathlib import Path

resources_root = Path(sys.argv[1])
app_root = Path(sys.argv[2])

shutil.rmtree(app_root / "tests", ignore_errors=True)
for test_dir in sorted(
    (
        path
        for path in resources_root.rglob("*")
        if path.is_dir() and path.name in {"test", "tests"}
    ),
    key=lambda path: len(path.parts),
    reverse=True,
):
    shutil.rmtree(test_dir, ignore_errors=True)
for cache_dir in resources_root.rglob("__pycache__"):
    shutil.rmtree(cache_dir, ignore_errors=True)
for bytecode_path in app_root.rglob("*.pyc"):
    bytecode_path.unlink(missing_ok=True)
PY

log "Thinning bundled Mach-O files to $RELEASE_ARCH"
python3 - "$FINAL_APP" "$RELEASE_ARCH" <<'PY'
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

bundle = Path(sys.argv[1])
target_arch = sys.argv[2]

for path in bundle.rglob("*"):
    if path.is_symlink() or not path.is_file():
        continue
    result = subprocess.run(
        ["lipo", "-info", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        continue
    info = result.stdout.strip()
    if "Non-fat file:" in info:
        if f"architecture: {target_arch}" not in info:
            raise SystemExit(f"{path} does not contain target architecture {target_arch}: {info}")
        continue
    if target_arch not in info:
        raise SystemExit(f"{path} does not contain target architecture {target_arch}: {info}")
    with tempfile.NamedTemporaryFile(dir=str(path.parent), delete=False) as temp_file:
        temp_path = Path(temp_file.name)
    try:
        subprocess.check_call(["lipo", str(path), "-thin", target_arch, "-output", str(temp_path)])
        mode = path.stat().st_mode
        os.chmod(temp_path, mode)
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)
PY

log "Adding bundle metadata required for camera-style capture prompts"
python3 - "$INFO_PLIST" <<'PY'
from __future__ import annotations

import plistlib
import sys
from pathlib import Path

path = Path(sys.argv[1])
with path.open("rb") as fh:
    data = plistlib.load(fh)

data["CFBundleDisplayName"] = "HapticTrace"
data["CFBundleIdentifier"] = "com.nikolaikolesnikov.haptictrace"
data["NSCameraUsageDescription"] = (
    "HapticTrace accesses video capture devices to record iPhone and iPad screens."
)
data["NSHighResolutionCapable"] = True
python_info = data.get("PythonInfoDict")
if isinstance(python_info, dict):
    python_info.pop("PythonExecutable", None)

with path.open("wb") as fh:
    plistlib.dump(data, fh, sort_keys=False)
PY

log "Codesigning the app bundle"
codesign --force --deep --sign "$CODESIGN_IDENTITY" --timestamp=none "$FINAL_APP"
codesign --verify --deep --strict "$FINAL_APP"
if [ "$CODESIGN_IDENTITY" != "-" ]; then
  spctl --assess --type execute --verbose=4 "$FINAL_APP"
fi

log "Downloading pinned syft release"
curl -L --fail --silent --show-error "$SYFT_TARBALL_URL" -o "$SYFT_TARBALL_PATH"
curl -L --fail --silent --show-error "$SYFT_CHECKSUMS_URL" -o "$SYFT_CHECKSUMS_PATH"
python3 - "$SYFT_TARBALL_PATH" "$SYFT_CHECKSUMS_PATH" "$SYFT_TARBALL" <<'PY'
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

tarball_path = Path(sys.argv[1])
checksums_path = Path(sys.argv[2])
target_name = sys.argv[3]

expected = None
for line in checksums_path.read_text(encoding="utf-8").splitlines():
    parts = line.split()
    if len(parts) >= 2 and parts[-1] == target_name:
        expected = parts[0]
        break

if expected is None:
    raise SystemExit(f"checksum entry not found for {target_name}")

actual = hashlib.sha256(tarball_path.read_bytes()).hexdigest()
if actual != expected:
    raise SystemExit(
        "syft download checksum mismatch: "
        f"expected {expected}, got {actual}"
    )
PY
tar -xzf "$SYFT_TARBALL_PATH" -C "$SYFT_DIR" syft
chmod +x "$SYFT_BIN"

log "Generating release SBOM from the final app bundle"
(
  cd "$RELEASE_DIR"
  "$SYFT_BIN" "dir:$APP_NAME.app" --source-name "$APP_NAME.app" -o "cyclonedx-json=$SBOM_PATH"
)

log "Normalizing release SBOM paths"
python3 - "$SBOM_PATH" "$RELEASE_DIR" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sbom_path = Path(sys.argv[1])
release_dir = Path(sys.argv[2]).resolve()

def normalize(value: Any) -> Any:
    if isinstance(value, str):
        path = Path(value)
        if path.is_absolute():
            try:
                return path.resolve().relative_to(release_dir).as_posix()
            except ValueError:
                return value
        return value
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()}
    return value

data = json.loads(sbom_path.read_text(encoding="utf-8"))
sbom_path.write_text(
    json.dumps(normalize(data), ensure_ascii=False, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
PY

log "Writing build metadata"
python3 - "$ROOT_DIR" "$BUILD_INFO_PATH" "$CODESIGN_IDENTITY" "$SYFT_VERSION" "$RELEASE_ARCH" "$MAX_MACHO_MINOS" <<'PY'
from __future__ import annotations

import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(sys.argv[1])
output_path = Path(sys.argv[2])
codesign_identity = sys.argv[3]
syft_version = sys.argv[4]
release_arch = sys.argv[5]
max_macho_minos = sys.argv[6]

try:
    git_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
    ).strip()
except Exception:
    git_commit = "unknown"

output_path.write_text(
    "\n".join(
        [
            "HapticTrace release build",
            f"Built at (UTC): {datetime.now(timezone.utc).isoformat()}",
            f"Git commit: {git_commit}",
            f"Platform: {platform.platform()}",
            f"Python: {platform.python_version()}",
            f"Release architecture: {release_arch}",
            f"Maximum Mach-O minimum macOS: {max_macho_minos}",
            f"Codesign identity: {codesign_identity}",
            f"Syft version: {syft_version}",
            "",
            "Portable release builds require a Developer ID identity via",
            "HAPTIC_CODESIGN_IDENTITY. Ad-hoc signatures are allowed only when",
            "HAPTIC_ALLOW_ADHOC=1 is set for local smoke builds.",
        ]
    )
    + "\n",
    encoding="utf-8",
)
PY

log "Validating release bundle portability"
python3 - "$RELEASE_DIR" "$ROOT_DIR" "$MAX_MACHO_MINOS" "$RELEASE_ARCH" <<'PY'
from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

release_dir = Path(sys.argv[1])
root_dir = Path(sys.argv[2])
max_minos = tuple(int(part) for part in sys.argv[3].split("."))
release_arch = sys.argv[4]

def parse_version(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))

old_source_root = b"/" + b"Users" + b"/" + b"work" + b"/" + b"haptic_detector"
forbidden_patterns = [
    str(root_dir).encode(),
    old_source_root,
]
bad_files: list[str] = []
for path in release_dir.rglob("*"):
    if path.is_symlink() or not path.is_file():
        continue
    try:
        data = path.read_bytes()
    except OSError:
        continue
    if any(pattern in data for pattern in forbidden_patterns):
        bad_files.append(str(path.relative_to(release_dir)))

if bad_files:
    raise SystemExit("release contains build-machine paths:\n" + "\n".join(bad_files[:50]))

info_plist = release_dir / "HapticTrace.app" / "Contents" / "Info.plist"
with info_plist.open("rb") as fh:
    info_data = plistlib.load(fh)
python_info = info_data.get("PythonInfoDict", {})
if isinstance(python_info, dict) and "PythonExecutable" in python_info:
    raise SystemExit("Info.plist still contains PythonInfoDict.PythonExecutable")

arch_errors: list[str] = []
minos_errors: list[str] = []
for path in (release_dir / "HapticTrace.app").rglob("*"):
    if path.is_symlink() or not path.is_file():
        continue
    lipo_result = subprocess.run(
        ["lipo", "-info", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if lipo_result.returncode != 0:
        continue
    lipo_info = lipo_result.stdout.strip()
    if f"architecture: {release_arch}" not in lipo_info:
        arch_errors.append(f"{path.relative_to(release_dir)}: {lipo_info}")
        continue
    if "Architectures in the fat file" in lipo_info:
        arch_errors.append(f"{path.relative_to(release_dir)} is still universal: {lipo_info}")

    otool_result = subprocess.run(
        ["otool", "-l", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if otool_result.returncode != 0:
        continue
    lines = otool_result.stdout.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        value = None
        if stripped == "cmd LC_BUILD_VERSION":
            for candidate in lines[index + 1:index + 8]:
                candidate = candidate.strip()
                if candidate.startswith("minos "):
                    value = candidate.split()[1]
                    break
        elif stripped == "cmd LC_VERSION_MIN_MACOSX":
            for candidate in lines[index + 1:index + 5]:
                candidate = candidate.strip()
                if candidate.startswith("version "):
                    value = candidate.split()[1]
                    break
        if value and parse_version(value) > max_minos:
            minos_errors.append(f"{path.relative_to(release_dir)}: min macOS {value}")

if arch_errors:
    raise SystemExit("release contains unexpected architectures:\n" + "\n".join(arch_errors[:50]))
if minos_errors:
    raise SystemExit("release contains Mach-O files above target macOS:\n" + "\n".join(minos_errors[:50]))
PY

log "Smoke-testing the built app entrypoint"
PYTHONDONTWRITEBYTECODE=1 "$FINAL_APP/Contents/MacOS/$APP_NAME" --help >/dev/null
find "$FINAL_APP/Contents/Resources" -name __pycache__ -type d -prune -exec rm -rf {} +
codesign --verify --deep --strict "$FINAL_APP"

log "Creating the release archive"
ditto -c -k --sequesterRsrc --keepParent "$RELEASE_DIR" "$ARCHIVE_PATH"

log "Release build completed"
printf '%s\n' \
  "App bundle: $FINAL_APP" \
  "SBOM: $SBOM_PATH" \
  "Archive: $ARCHIVE_PATH"
