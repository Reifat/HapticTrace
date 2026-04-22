[Русская версия](docs/ru/README.md)

# HapticTrace

`HapticTrace` is a desktop tool for recording and analyzing haptic-response events. It combines sensor data from `phyphox`, iPhone/iPad screen capture, and post-analysis tools. After recording, you can correlate the video with the measured signal, adjust the offset, and save the result for repeated analysis.

**Platform:** macOS only

## What it does

- captures sensor data via `phyphox`
- provides live preview and records the iPhone/iPad screen
- supports recording modes: `Sensors + Video`, `Sensors Only`, `Video Only`
- displays the signal and spectrogram
- plays back recorded video on a shared timeline
- supports automatic and manual offset adjustment
- saves and loads sessions as `.zip`
- compares multiple sessions

## Requirements

- macOS
- Python 3
- a device running `phyphox`, reachable over the network
- an iPhone or iPad available to macOS as a screen capture source
- internet access on the first launch from source

## Primary workflow

The primary mode is synchronized recording of sensor data and on-screen context followed by analysis.

A typical setup uses two mobile devices:

- **Device 1 (phone)** — sensor module with `phyphox`  
  Used to capture `accelerometer` and `gyroscope` data.
- **Device 2 (phone)** — target iPhone/iPad  
  Runs the application under test, plays haptics, and provides screen capture.
- **Mac** — host running `HapticTrace`  
  Connects to the sensor device over the network and to the iPhone/iPad over USB through the macOS screen capture stack.

### Preparation

1. Open `phyphox` on the sensor device.
2. Create or load an experiment with `accelerometer` and `gyroscope` enabled.
3. Enable remote access in `phyphox` and obtain the experiment URL.
4. Connect the iPhone/iPad to the Mac over USB as a screen capture source.
5. Rigidly fix both devices relative to each other (for example, in a stacked "sandwich" setup):
   - iPhone/iPad — screen facing up
   - sensor device — screen facing down

During recording, the devices must not shift relative to each other. Any parasitic motion directly affects measurement quality.

### Workflow

1. Launch `HapticTrace` on the Mac.
2. Connect the sensor device using the `phyphox` URL.
3. Connect the iPhone/iPad as the video source.
4. Start recording (`Play` button in the main window).
5. Trigger haptics in the application under test (on the iPhone/iPad).
6. Stop recording.
7. Analyze the signal and align it with on-screen activity on the shared timeline.

### Additional modes

In addition to the primary synchronized workflow, `HapticTrace` also supports standalone recording modes:

- **`Sensors Only`** — capture sensor data without video
- **`Video Only`** — record screen output without sensors

## Run

### From source

#### From terminal

```bash
./run_app.sh --url http://<device_local_ip>:8080
```

On first launch, the script creates `.venv`, updates `pip`, and installs runtime dependencies automatically.

#### From Finder

```bash
run_app.command
```

### Release

Open `HapticTrace.app` from the prepared release build.

# Documentation

## Contents

1. [Project overview](README.md) — start here
2. [Usage](docs/en/usage.md) — continue with usage and workflow
3. [Development](docs/en/development.md) — local setup, dependencies, tests, and release builds
4. [Troubleshooting](docs/en/troubleshooting.md) — common operational issues
4. [Third-Party Notices](THIRD_PARTY_NOTICES.md) — third-party package notices

## License

Apache-2.0
