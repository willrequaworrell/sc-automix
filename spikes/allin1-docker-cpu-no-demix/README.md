# Linux CPU No-Demix All-In-One Spike

This spike tests whether upstream Torch `allin1` can run cheaply on Linux CPU without Demucs. It does not integrate with the app runtime.

The no-demix path fabricates the directory shape that upstream `allin1` expects after Demucs:

```text
<work>/demix/htdemucs/<track>/
  bass.wav
  drums.wav
  other.wav
  vocals.wav
```

Each stem is the same mono mix from the original WAV. The script then calls upstream internals in the same order as `allin1.analyze()` after Demucs:

1. `allin1.spectrogram.extract_spectrograms(...)`
2. `allin1.models.load_pretrained_model(...)`
3. `allin1.helpers.run_inference(...)`
4. local normalization to the `@sc-automix/analysis-contract` shape

## Build And Run

From the repo root:

```bash
./spikes/allin1-docker-cpu-no-demix/benchmark.sh
```

Defaults:

- input directory: `spikes/allin1-docker-cpu-no-demix/fixtures` when the fixture exists, otherwise `/Users/willworrell/Downloads`
- track: `In-Search-Of-Sunset-126bpm-1000-Handz.mp3`
- output directory: `spikes/allin1-docker-cpu-no-demix/out`
- cache directory: `spikes/allin1-docker-cpu-no-demix/cache`
- image: `sc-allin1-cpu-no-demix:local`

Override with env vars:

```bash
INPUT_DIR=/path/to/audio \
TRACK_NAME='track.wav' \
DOCKER_CPUS=4 \
./spikes/allin1-docker-cpu-no-demix/benchmark.sh
```

## Success Criteria

- container is Linux `amd64`
- no GPU is visible to the process
- Demucs is not invoked
- warm no-demix analysis completes under 120 seconds for the test track
- output JSON is schema-compatible with `@sc-automix/analysis-contract`
- benchmark fixture BPM is within 1 BPM of `130`
- warm analyzer runtime stays under the configured benchmark threshold

## Output

The normalized JSON is written to:

```text
spikes/allin1-docker-cpu-no-demix/out/warm/In-Search-Of-Sunset-126bpm-1000-Handz.json
```

The analysis version is `allin1-torch-cpu-fast-v0`; the engine is `allin1-torch-cpu-no-demix`.

## Dependency Policy

The initial attempt intentionally uses:

- `python:3.11-slim-bookworm`
- `torch==2.2.2` CPU
- `natten==0.14.6`, source-built
- `allin1==1.1.0`
- latest `madmom` from `git+https://github.com/CPJKU/madmom`
- `numpy==1.26.4`
- `scipy==1.13.1`

If this pin set fails, allow exactly one dependency retry based on the observed error. Do not switch to GPU or a pure-PyTorch fallback inside this spike.

## GitHub Actions

The manual workflow `.github/workflows/allin1-cpu-no-demix-benchmark.yml` builds the Docker image, runs cold and warm analysis on the committed long MP3 fixture, validates the normalized JSON against the analysis contract, checks BPM near `130`, checks warm runtime, and uploads the JSON/timing files as artifacts.

## Cloud Run Jobs Proof

`cloud_run_job.py` is a Cloud Run wrapper around the local analyzer. It can run one track from env vars or a batch of tracks from a manifest. In batch mode it downloads each track, loads the all-in-one model once, analyzes tracks sequentially, uploads one normalized JSON per track, writes optional timings, and exits.

Required environment variables:

```text
INPUT_URI=gs://sc-automix-analysis-dev-sc-automix/input/In-Search-Of-Sunset-126bpm-1000-Handz.mp3
OUTPUT_URI=gs://sc-automix-analysis-dev-sc-automix/output/In-Search-Of-Sunset-126bpm-1000-Handz.json
```

Optional:

```text
TIMINGS_OUTPUT_URI=gs://sc-automix-analysis-dev-sc-automix/output/In-Search-Of-Sunset-126bpm-1000-Handz.timings.jsonl
ALLIN1_MODEL=harmonix-all
```

Batch mode uses:

```text
MANIFEST_URI=gs://sc-automix-analysis-dev-sc-automix/manifests/playlist-analysis-test.json
SUMMARY_OUTPUT_URI=gs://sc-automix-analysis-dev-sc-automix/output/playlist-analysis-test.summary.json
```

Manifest shape:

```json
{
  "playlist_analysis_id": "playlist-analysis-test",
  "tracks": [
    {
      "track_id": "in-search-of-sunset",
      "input_uri": "gs://sc-automix-analysis-dev-sc-automix/input/In-Search-Of-Sunset-126bpm-1000-Handz.mp3",
      "output_uri": "gs://sc-automix-analysis-dev-sc-automix/output/In-Search-Of-Sunset-126bpm-1000-Handz.json",
      "timings_output_uri": "gs://sc-automix-analysis-dev-sc-automix/output/In-Search-Of-Sunset-126bpm-1000-Handz.timings.jsonl"
    }
  ]
}
```

Sequential mode processes every track in the manifest. Later parallel mode can use Cloud Run Jobs `--tasks` and `--parallelism`; the wrapper already shards by `CLOUD_RUN_TASK_INDEX` and `CLOUD_RUN_TASK_COUNT`.

After changing the wrapper or Dockerfile, rebuild and push the `linux/amd64` image to Google Artifact Registry:

```bash
export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"

docker build --platform linux/amd64 \
  -t us-east1-docker.pkg.dev/sc-automix/sc-automix/allin1-cpu-no-demix:latest \
  spikes/allin1-docker-cpu-no-demix

docker push us-east1-docker.pkg.dev/sc-automix/sc-automix/allin1-cpu-no-demix:latest
```

Create the first Cloud Run Job:

```bash
gcloud run jobs create allin1-cpu-no-demix \
  --project sc-automix \
  --region us-east1 \
  --image us-east1-docker.pkg.dev/sc-automix/sc-automix/allin1-cpu-no-demix:latest \
  --command python \
  --args /app/cloud_run_job.py \
  --cpu 4 \
  --memory 8Gi \
  --task-timeout 10m \
  --max-retries 0 \
  --tasks 1 \
  --parallelism 1 \
  --service-account sc-automix-analysis-runner@sc-automix.iam.gserviceaccount.com \
  --set-env-vars INPUT_URI=gs://sc-automix-analysis-dev-sc-automix/input/In-Search-Of-Sunset-126bpm-1000-Handz.mp3,OUTPUT_URI=gs://sc-automix-analysis-dev-sc-automix/output/In-Search-Of-Sunset-126bpm-1000-Handz.json,TIMINGS_OUTPUT_URI=gs://sc-automix-analysis-dev-sc-automix/output/In-Search-Of-Sunset-126bpm-1000-Handz.timings.jsonl
```

Run it:

```bash
gcloud run jobs execute allin1-cpu-no-demix \
  --project sc-automix \
  --region us-east1 \
  --wait
```

To switch the existing job to batch-manifest mode:

```bash
gcloud run jobs update allin1-cpu-no-demix \
  --project sc-automix \
  --region us-east1 \
  --set-env-vars MANIFEST_URI=gs://sc-automix-analysis-dev-sc-automix/manifests/playlist-analysis-test.json,SUMMARY_OUTPUT_URI=gs://sc-automix-analysis-dev-sc-automix/output/playlist-analysis-test.summary.json \
  --remove-env-vars INPUT_URI,OUTPUT_URI,TIMINGS_OUTPUT_URI
```

Upload a manifest:

```bash
cat >/tmp/playlist-analysis-test.json <<'JSON'
{
  "playlist_analysis_id": "playlist-analysis-test",
  "tracks": [
    {
      "track_id": "in-search-of-sunset",
      "input_uri": "gs://sc-automix-analysis-dev-sc-automix/input/In-Search-Of-Sunset-126bpm-1000-Handz.mp3",
      "output_uri": "gs://sc-automix-analysis-dev-sc-automix/output/In-Search-Of-Sunset-126bpm-1000-Handz.json",
      "timings_output_uri": "gs://sc-automix-analysis-dev-sc-automix/output/In-Search-Of-Sunset-126bpm-1000-Handz.timings.jsonl"
    }
  ]
}
JSON

gcloud storage cp /tmp/playlist-analysis-test.json \
  gs://sc-automix-analysis-dev-sc-automix/manifests/playlist-analysis-test.json
```
