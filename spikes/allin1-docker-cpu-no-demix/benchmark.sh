#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="${IMAGE_NAME:-sc-allin1-cpu-no-demix:local}"
DEFAULT_TRACK_NAME="In-Search-Of-Sunset-126bpm-1000-Handz.mp3"
DEFAULT_INPUT_DIR="$SCRIPT_DIR/fixtures"
if [[ ! -f "$DEFAULT_INPUT_DIR/$DEFAULT_TRACK_NAME" ]]; then
  DEFAULT_INPUT_DIR="/Users/willworrell/Downloads"
fi
INPUT_DIR="${INPUT_DIR:-$DEFAULT_INPUT_DIR}"
TRACK_NAME="${TRACK_NAME:-$DEFAULT_TRACK_NAME}"
OUTPUT_DIR="${OUTPUT_DIR:-$SCRIPT_DIR/out}"
CACHE_DIR="${CACHE_DIR:-$SCRIPT_DIR/cache}"
HOST_PYTHON="${HOST_PYTHON:-python3}"
TRACK_STEM="${TRACK_NAME%.*}"

mkdir -p "$OUTPUT_DIR/cold" "$OUTPUT_DIR/warm" "$CACHE_DIR"

if [[ ! -f "$INPUT_DIR/$TRACK_NAME" ]]; then
  echo "Input track not found: $INPUT_DIR/$TRACK_NAME" >&2
  exit 1
fi

time_sec() {
  "$HOST_PYTHON" - "$@" <<'PY'
import time
print(f"{time.perf_counter():.9f}")
PY
}

elapsed() {
  "$HOST_PYTHON" - "$1" "$2" <<'PY'
import sys
start = float(sys.argv[1])
end = float(sys.argv[2])
print(f"{end - start:.3f}s")
PY
}

echo "==> Building $IMAGE_NAME for linux/amd64"
build_start="$(time_sec)"
docker build --platform linux/amd64 -t "$IMAGE_NAME" "$SCRIPT_DIR"
build_end="$(time_sec)"
echo "build: $(elapsed "$build_start" "$build_end")"

run_container() {
  local label="$1"
  local out_subdir="$2"
  local start end
  echo "==> $label run"
  start="$(time_sec)"
  docker run --rm --platform linux/amd64 \
    --cpus="${DOCKER_CPUS:-4}" \
    -e CUDA_VISIBLE_DEVICES="" \
    -e XDG_CACHE_HOME=/cache/xdg \
    -e TORCH_HOME=/cache/torch \
    -e HF_HOME=/cache/huggingface \
    -e MPLCONFIGDIR=/cache/matplotlib \
    -v "$INPUT_DIR:/input:ro" \
    -v "$OUTPUT_DIR/$out_subdir:/output" \
    -v "$CACHE_DIR:/cache" \
    "$IMAGE_NAME" \
    "/input/$TRACK_NAME" \
    --output-dir /output \
    --cache-dir /cache/huggingface \
    --timings-path "/output/timings.jsonl"
  end="$(time_sec)"
  echo "$label total docker run: $(elapsed "$start" "$end")"
}

run_container "cold/model-download" "cold"
run_container "warm/no-demix" "warm"

echo "==> Warm output"
"$HOST_PYTHON" - "$OUTPUT_DIR/warm/$TRACK_STEM.json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text())
print(json.dumps({
  "path": str(path),
  "analysis_version": data["analysis_version"],
  "tempo_bpm": data.get("tempo_bpm"),
  "beats": len(data["beats"]),
  "downbeats": len(data["downbeats"]),
  "bars": len(data["bars"]),
  "segments": len(data.get("segments", [])),
  "timings_sec": data["raw_summary"]["timings_sec"],
}, indent=2, sort_keys=True))
PY
