# Runlog

## 2026-07-01

### Implementation

- Added a Linux `amd64` CPU Docker spike based on `python:3.11-slim-bookworm`.
- Added a no-demix analyzer that writes the original mono mix into `bass.wav`, `drums.wav`, `other.wav`, and `vocals.wav`, then resumes upstream `allin1` at spectrogram extraction.
- Added benchmark script with separate cold and warm container runs using mounted `/input`, `/output`, and persistent `/cache`.

### Local MLX Baseline For Comparison

- Track: `Dombresky - Dombresky - Meli-Melo.wav`
- MLX no-demix BPM: `125` model integer
- MLX no-demix raw long-span BPM: approximately `123.985`
- MLX no-demix beat count: `510`
- MLX no-demix downbeat count: `128`
- MLX no-demix segment count: `15`
- MLX no-demix runtime: approximately `16.6s` on local Apple Silicon

### Docker Results

Initial image build succeeded.

- Base image: `python:3.11-slim-bookworm`
- Platform: `linux/amd64`
- Torch CPU wheel: `torch==2.2.2+cpu`
- NATTEN: `natten==0.14.6`, source-built successfully
- NATTEN build time inside Docker: approximately `366s`

First cold run failed during import before analysis:

```text
OSError: libcudart.so.13: cannot open shared object file: No such file or directory
```

Cause: installing `allin1==1.1.0` pulled `torchaudio==2.11.0`, and importing upstream `allin1` imports `demucs`, which imports `torchaudio`. That wheel expected CUDA runtime libraries even though this spike uses CPU only and skips Demucs at execution time.

Allowed dependency retry:

- Re-pin `torchaudio==2.2.2` from the PyTorch CPU wheel index alongside `torch==2.2.2`.

Retry result: successful.

### Benchmark Result

Command:

```bash
./spikes/allin1-docker-cpu-no-demix/benchmark.sh
```

Mounted input:

```text
/Users/willworrell/Downloads/Dombresky - Dombresky - Meli-Melo.wav -> /input/Dombresky - Dombresky - Meli-Melo.wav
```

Output:

```text
spikes/allin1-docker-cpu-no-demix/out/warm/Dombresky - Dombresky - Meli-Melo.json
```

Validation:

- JSON validates against `packages/analysis-contract/src/analysis.schema.json`.
- Demucs was not invoked. The script generated fake `bass.wav`, `drums.wav`, `other.wav`, and `vocals.wav` from the mono mix and resumed at upstream spectrogram extraction.
- Device: CPU only.

Cold run:

- Total Docker run: `84.757s`
- Total analyzer CLI: `77.8807s`
- Fake stems: `0.816305s`
- Spectrogram: `3.597575s`
- Model load/download: `7.171308s`
- Inference and postprocess: `63.689936s`
- Normalize/write: `0.069127s`

Warm run from `benchmark.sh`:

- Total Docker run: `70.019s`
- Total analyzer CLI: `68.511378s`
- Fake stems: `0.770616s`
- Spectrogram: `3.872424s`
- Cached model load: `1.167358s`
- Inference and postprocess: `60.177445s`
- Normalize/write: `0.057589s`

Corrected schema-shape warm rerun after changing `engine_version` from object to string:

- Total analyzer CLI: `70.314534s`
- Fake stems: `0.67861s`
- Spectrogram: `3.528699s`
- Cached model load: `1.358053s`
- Inference and postprocess: `62.445351s`
- Normalize/write: `0.072819s`

Normalized result:

- `tempo_bpm`: `123.98506`
- model integer BPM: `125`
- beats: `510`
- downbeats: `128`
- bars: `128`
- segments: `15`
- first beat: `0.06s`
- last beat: `246.38s`

Comparison to MLX no-demix artifact:

- Model BPM diff: `0`
- Beat count diff: `0`
- Downbeat count diff: `0`
- Segment count diff: `0`
- First beat diff: `0.0s`
- Last beat diff: `0.0s`

Conclusion:

Linux CPU no-demix is viable for this track under the `120s` warm target on Docker Desktop with `--cpus=4`. The main cost is neural inference, not spectrogram extraction or normalization. Build-time NATTEN compilation is slow enough that production should publish/cache the image instead of compiling during deploy.

### New Rights-Cleared Reference Track

User-provided local source:

```text
/Users/willworrell/Splice/sounds/packs/Progressive House Worldwide/Function_Loops_-_Progressive_House_Worldwide/FL_PHW_Kit03__128BPM___Key_G#_/FL_PHW_Kit_03__Full_Demo_G#_128BPM.wav
```

Reference BPM from filename/pack metadata: `128`.

File size: approximately `22M`.

Command:

```bash
INPUT_DIR='/Users/willworrell/Splice/sounds/packs/Progressive House Worldwide/Function_Loops_-_Progressive_House_Worldwide/FL_PHW_Kit03__128BPM___Key_G#_' \
TRACK_NAME='FL_PHW_Kit_03__Full_Demo_G#_128BPM.wav' \
./spikes/allin1-docker-cpu-no-demix/benchmark.sh
```

Output:

```text
spikes/allin1-docker-cpu-no-demix/out/warm/FL_PHW_Kit_03__Full_Demo_G#_128BPM.json
```

Validation:

- JSON validates against `packages/analysis-contract/src/analysis.schema.json`.
- Device: CPU only.
- Demucs was not invoked.

Warm result:

- `tempo_bpm`: `128.017069`
- model integer BPM: `128`
- beats: `161`
- downbeats: `41`
- bars: `41`
- segments: `7`
- duration: `86.254875s`
- first beat: `0.01s`
- last beat: `75.0s`
- total analyzer CLI: `26.284194s`
- total Docker run: `27.714s`
- fake stems: `0.380006s`
- spectrogram: `1.2068s`
- cached model load: `1.194883s`
- inference and postprocess: `20.712879s`
- normalize/write: `0.060622s`

CI note:

This is a good reference track for BPM and schema regression because it is rights-cleared and lands within `0.02 BPM` of the listed tempo. At `22M`, do not commit it to normal git history. Prefer Git LFS or a manual/private CI artifact download path for GitHub Actions.

### Real-Length MP3 Trial

User-provided local source:

```text
/Users/willworrell/Downloads/In-Search-Of-Sunset-126bpm-1000-Handz.mp3
```

Reference BPM from filename: `126`.

Command:

```bash
INPUT_DIR='/Users/willworrell/Downloads' \
TRACK_NAME='In-Search-Of-Sunset-126bpm-1000-Handz.mp3' \
./spikes/allin1-docker-cpu-no-demix/benchmark.sh
```

Result:

- MP3 input works in the container as-is.
- JSON validates against `packages/analysis-contract/src/analysis.schema.json`.
- Device: CPU only.
- Demucs was not invoked.

Warm result:

- `tempo_bpm`: `129.993854`
- model integer BPM: `130`
- beats: `424`
- downbeats: `106`
- segments: `13`
- duration: `198.112653s`
- first beat: `0.07s`
- last beat: `195.31s`
- total analyzer CLI: `58.887509s`
- total Docker run: `60.513s`
- fake stems: `0.722948s`
- spectrogram: `2.67429s`
- cached model load: `1.122711s`
- inference and postprocess: `51.45886s`
- normalize/write: `0.055234s`

Observation:

This is a better real-length performance sample than the short Splice demo. The filename says `126 BPM`, but the user clarified the file is actually poorly labeled and should be treated as `130 BPM`. The model result is consistent with `130 BPM`, so this is now the primary GitHub Actions benchmark fixture.

### GitHub Actions Benchmark Setup

Added committed fixture:

```text
spikes/allin1-docker-cpu-no-demix/fixtures/In-Search-Of-Sunset-126bpm-1000-Handz.mp3
```

Added manual workflow:

```text
.github/workflows/allin1-cpu-no-demix-benchmark.yml
```

Workflow behavior:

- Runs only via `workflow_dispatch`.
- Builds the Linux `amd64` Docker image with GitHub Actions cache.
- Runs cold and warm no-demix analysis on the committed long MP3 fixture.
- Validates warm JSON against `packages/analysis-contract/src/analysis.schema.json`.
- Checks `tempo_bpm` within `130 +/- 1`.
- Checks warm `total_cli_sec <= 180`.
- Uploads warm/cold JSON and timing logs as artifacts.

Updated local default benchmark:

```bash
./spikes/allin1-docker-cpu-no-demix/benchmark.sh
```

now defaults to:

```text
spikes/allin1-docker-cpu-no-demix/fixtures/In-Search-Of-Sunset-126bpm-1000-Handz.mp3
```

Verification run after making it the default:

- `tempo_bpm`: `129.993854`
- model integer BPM: `130`
- beats: `424`
- downbeats: `106`
- bars: `106`
- segments: `13`
- total analyzer CLI: `58.120453s`
- total Docker run: `59.523s`
- fake stems: `0.797999s`
- spectrogram: `2.497365s`
- cached model load: `1.093913s`
- inference and postprocess: `51.063934s`
- normalize/write: `0.051s`
