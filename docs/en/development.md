[Русская версия](./../ru/development.md)

# Development

This document describes the development environment, dependency layout, test workflow, and release build process for `HapticTrace`.

## Repository layout

The application lives in the `app/` package.

The repository also includes:

- runtime and development dependency manifests
- bootstrap scripts for local execution
- release build scripts
- compliance artifacts and lockfiles

## Development environment

`HapticTrace` is developed for **macOS only**.

Required tools:

- macOS
- Python 3
- network access for initial bootstrap
- access to an iPhone/iPad capture source if you work on the video path
- a reachable `phyphox` device if you work on the sensor path

## Dependency model

Python dependencies are split by profile.

### Runtime profile

Runtime dependencies used by the application itself:

- `requirements/runtime.in`
- `requirements/runtime.lock`

`runtime.lock` is the reproducible lockfile used by the standard bootstrap flow.

### Development profile

Additional dependencies for tests and developer tooling:

- `requirements/dev.in`
- `requirements/dev.lock`

The development profile extends the runtime profile.

## Bootstrap and local run

The standard local entry point is:

```bash
./run_app.sh --url http://<device_local_ip>:8080
```

On first launch, the script:

1. creates the root-level `.venv`
2. updates `pip`
3. installs dependencies from the selected lockfile

By default, `run_app.sh` uses the **runtime** profile.

To bootstrap the **development** profile:

```bash
./run_app.sh --bootstrap-profile dev --help
```

This is the recommended way to prepare the environment for tests and development tooling.

## Finder launch

The Finder entry point is:

```bash
run_app.command
```

It uses the same bootstrap flow as the shell script.

## tkinter note

`tkinter` is usually available in the system Python on macOS.  
If it is missing, use a Python build that includes Tk support.

## Running tests

First prepare the development environment:

```bash
./run_app.sh --bootstrap-profile dev --help
```

Then run tests:

```bash
.venv/bin/python -m pytest app/tests
```

## Dependency and compliance artifacts

The repository keeps lockfiles and compliance inputs required for repeatable builds and release packaging.

Main artifacts:

- `requirements/runtime.lock` — exact runtime lockfile
- `requirements/dev.lock` — exact development and test lockfile
- `scripts/requirements-release.lock` — exact lockfile for Python release tooling
- `THIRD_PARTY_NOTICES.md` — human-readable third-party notices

## Release build

The macOS release build entry point is:

```bash
./scripts/build_release_macos.sh
```

The release script uses an isolated build environment and performs the following high-level steps:

1. recreates the `build/` directory
2. installs runtime dependencies from `requirements/runtime.lock`
3. installs Python release tooling from `scripts/requirements-release.lock`
4. downloads the pinned binary release of `syft`
5. builds the macOS application bundle
6. generates compliance artifacts
7. packages the release output

## Release outputs

The release build produces:

- `build/release/HapticTrace.app` — macOS app bundle
- `build/release/compliance/sbom.runtime.cdx.json` — SBOM for the final app bundle
- `build/release/requirements/` — runtime manifests and lockfiles used for the release
- `build/release/scripts/` — release script and pinned tooling inputs
- `build/HapticTrace-macos-<arch>-release.zip` — packaged release archive for the host architecture

## Codesign

Portable release bundles require a Developer ID signing identity.

To use a specific signing identity, set:

```bash
HAPTIC_CODESIGN_IDENTITY=<your_identity>
```

before running the release script.

For local-only smoke builds, set `HAPTIC_ALLOW_ADHOC=1` to permit an ad-hoc signature.

## SBOM and release tooling

The release pipeline generates a runtime SBOM in CycloneDX JSON format.

Tooling assumptions:

- runtime Python packages come from the pinned runtime lockfile
- release-tooling packages come from the pinned release-tooling lockfile
- `syft` is downloaded as a pinned binary release

This keeps the release path reproducible and auditable.

## Typical development workflow

1. bootstrap the development environment
2. run the application locally
3. validate sensor and/or video path depending on the change
4. run tests
5. build a release bundle when release validation is required

## Notes

- the root-level `.venv` is part of the standard local workflow
- runtime and development dependencies are intentionally separated
- release builds use their own controlled tooling path
- development and release workflows should use the committed lockfiles
