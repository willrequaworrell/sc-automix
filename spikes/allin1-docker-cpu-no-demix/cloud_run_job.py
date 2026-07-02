#!/usr/bin/env python3
"""Cloud Run Jobs wrapper for gs:// single-track and batch analysis."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from google.cloud import storage

from analyze_no_demix import (
  normalize_result,
  write_fake_stems,
)


def now() -> float:
  return time.perf_counter()


def elapsed(start: float) -> float:
  return round(time.perf_counter() - start, 6)


def parse_gs_uri(uri: str) -> tuple[str, str]:
  parsed = urlparse(uri)
  if parsed.scheme != "gs" or not parsed.netloc or not parsed.path.lstrip("/"):
    raise ValueError(f"Expected gs://bucket/path URI, got: {uri}")
  return parsed.netloc, parsed.path.lstrip("/")


def download_blob(client: storage.Client, uri: str, dst: Path) -> float:
  start = now()
  bucket_name, blob_name = parse_gs_uri(uri)
  dst.parent.mkdir(parents=True, exist_ok=True)
  client.bucket(bucket_name).blob(blob_name).download_to_filename(str(dst))
  return elapsed(start)


def upload_blob(client: storage.Client, src: Path, uri: str, content_type: str = "application/json") -> float:
  start = now()
  bucket_name, blob_name = parse_gs_uri(uri)
  client.bucket(bucket_name).blob(blob_name).upload_from_filename(str(src), content_type=content_type)
  return elapsed(start)


def require_env(name: str) -> str:
  value = os.environ.get(name)
  if not value:
    raise ValueError(f"Missing required environment variable: {name}")
  return value


def load_manifest(client: storage.Client, manifest_uri: str, dst: Path) -> dict[str, Any]:
  download_blob(client, manifest_uri, dst)
  manifest = json.loads(dst.read_text())
  if not isinstance(manifest.get("tracks"), list) or not manifest["tracks"]:
    raise ValueError("Manifest must contain a non-empty tracks array.")
  return manifest


def single_track_manifest() -> dict[str, Any]:
  input_uri = require_env("INPUT_URI")
  output_uri = require_env("OUTPUT_URI")
  track_id = os.environ.get("TRACK_ID") or Path(parse_gs_uri(input_uri)[1]).stem
  track: dict[str, Any] = {
    "track_id": track_id,
    "input_uri": input_uri,
    "output_uri": output_uri,
  }
  timings_output_uri = os.environ.get("TIMINGS_OUTPUT_URI")
  if timings_output_uri:
    track["timings_output_uri"] = timings_output_uri
  return {
    "playlist_analysis_id": os.environ.get("PLAYLIST_ANALYSIS_ID"),
    "tracks": [track],
  }


def selected_tracks(manifest: dict[str, Any]) -> list[dict[str, Any]]:
  tracks = manifest["tracks"]
  task_index = int(os.environ.get("CLOUD_RUN_TASK_INDEX", "0"))
  task_count = int(os.environ.get("CLOUD_RUN_TASK_COUNT", "1"))
  if task_count <= 1:
    return tracks
  return [track for idx, track in enumerate(tracks) if idx % task_count == task_index]


def load_model_once(model_name: str, cache_dir: Path) -> tuple[Any, float]:
  import torch
  from allin1.models import load_pretrained_model

  start = now()
  model = load_pretrained_model(model_name=model_name, cache_dir=cache_dir, device="cpu")
  model.eval()
  # Keep torch imported and initialized before the timed per-track inference calls.
  torch.set_grad_enabled(False)
  return model, elapsed(start)


def run_inference_with_loaded_model(input_path: Path, stem_dir: Path, spec_dir: Path, model: Any) -> tuple[Any, dict[str, float]]:
  import torch
  from allin1.helpers import run_inference
  from allin1.spectrogram import extract_spectrograms

  timings: dict[str, float] = {}

  start = now()
  spec_paths = extract_spectrograms([stem_dir], spec_dir, multiprocess=False)
  timings["spectrogram_sec"] = elapsed(start)

  start = now()
  with torch.no_grad():
    result = run_inference(
      path=input_path,
      spec_path=spec_paths[0],
      model=model,
      device="cpu",
      include_activations=False,
      include_embeddings=False,
    )
  timings["inference_and_postprocess_sec"] = elapsed(start)
  return result, timings


def analyze_track(
  client: storage.Client,
  track: dict[str, Any],
  temp_root: Path,
  cache_dir: Path,
  model_name: str,
  model: Any,
  shared_model_load_sec: float,
) -> dict[str, Any]:
  track_id = track.get("track_id")
  input_uri = track["input_uri"]
  output_uri = track["output_uri"]
  timings_output_uri = track.get("timings_output_uri")

  track_root = temp_root / "tracks" / (track_id or Path(parse_gs_uri(input_uri)[1]).stem)
  local_input = track_root / "input" / Path(parse_gs_uri(input_uri)[1]).name
  local_output_dir = track_root / "output"
  local_work_dir = track_root / "work"
  local_work_dir.mkdir(parents=True, exist_ok=True)
  local_output_dir.mkdir(parents=True, exist_ok=True)

  timings: dict[str, float] = {}
  total_start = now()
  timings["download_sec"] = download_blob(client, input_uri, local_input)

  stem_dir, duration, sample_rate, timings["fake_stems_sec"] = write_fake_stems(
    local_input,
    local_work_dir / "demix" / "htdemucs",
  )
  result, run_timings = run_inference_with_loaded_model(
    input_path=local_input,
    stem_dir=stem_dir,
    spec_dir=local_work_dir / "spec",
    model=model,
  )
  timings.update(run_timings)
  timings["shared_model_load_sec"] = shared_model_load_sec

  normalize_start = now()
  normalized = normalize_result(result, local_input, duration, sample_rate, timings, model_name)
  if track_id:
    normalized["track_id"] = track_id
  normalized["raw_summary"]["input_uri"] = input_uri
  normalized["raw_summary"]["output_uri"] = output_uri
  normalized["raw_summary"]["cloud_batch_track"] = True
  normalized["raw_summary"]["timings_sec"]["normalization_write_sec"] = elapsed(normalize_start)
  normalized["raw_summary"]["timings_sec"]["total_track_sec"] = elapsed(total_start)

  local_json = local_output_dir / f"{local_input.stem}.json"
  local_json.write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n")
  timings["upload_sec"] = upload_blob(client, local_json, output_uri)

  timing_record = {
    "track_id": normalized["track_id"],
    "track": local_input.name,
    "input_uri": input_uri,
    "output_uri": output_uri,
    "timings_sec": normalized["raw_summary"]["timings_sec"] | {"upload_sec": timings["upload_sec"]},
  }
  local_timings = local_output_dir / "timings.jsonl"
  local_timings.write_text(json.dumps(timing_record, sort_keys=True) + "\n")
  if timings_output_uri:
    timings["timings_upload_sec"] = upload_blob(client, local_timings, timings_output_uri, "application/x-ndjson")

  return {
    "track_id": normalized["track_id"],
    "input_uri": input_uri,
    "output_uri": output_uri,
    "timings_output_uri": timings_output_uri,
    "tempo_bpm": normalized.get("tempo_bpm"),
    "model_integer_bpm": normalized.get("raw_summary", {}).get("model_integer_bpm"),
    "beats": len(normalized.get("beats", [])),
    "downbeats": len(normalized.get("downbeats", [])),
    "bars": len(normalized.get("bars", [])),
    "segments": len(normalized.get("segments", [])),
    "duration_analyzed_sec": normalized.get("duration_analyzed_sec"),
    "local_input_size_bytes": local_input.stat().st_size,
    "analysis_output_size_bytes": local_json.stat().st_size,
    "cloud_wrapper_timings_sec": timings,
    "total_track_sec": normalized["raw_summary"]["timings_sec"]["total_track_sec"],
  }


def main() -> int:
  model_name = os.environ.get("ALLIN1_MODEL", "harmonix-all")
  cache_dir = Path(os.environ.get("ALLIN1_CACHE_DIR", "/tmp/allin1-cache"))
  work_root = Path(os.environ.get("ALLIN1_WORK_DIR", "/tmp/allin1-work"))
  manifest_uri = os.environ.get("MANIFEST_URI")
  summary_output_uri = os.environ.get("SUMMARY_OUTPUT_URI")

  client = storage.Client()
  total_start = now()

  with tempfile.TemporaryDirectory(dir=str(work_root.parent)) as temp_dir:
    temp_root = Path(temp_dir)
    if manifest_uri:
      manifest = load_manifest(client, manifest_uri, temp_root / "manifest.json")
    else:
      manifest = single_track_manifest()

    tracks = selected_tracks(manifest)
    if not tracks:
      raise ValueError("No tracks selected for this task.")

    model, model_load_sec = load_model_once(model_name, cache_dir)
    results = [
      analyze_track(client, track, temp_root, cache_dir, model_name, model, model_load_sec)
      for track in tracks
    ]

    summary = {
      "playlist_analysis_id": manifest.get("playlist_analysis_id"),
      "manifest_uri": manifest_uri,
      "summary_output_uri": summary_output_uri,
      "task_index": int(os.environ.get("CLOUD_RUN_TASK_INDEX", "0")),
      "task_count": int(os.environ.get("CLOUD_RUN_TASK_COUNT", "1")),
      "selected_track_count": len(tracks),
      "processed_track_count": len(results),
      "model": model_name,
      "shared_model_load_sec": model_load_sec,
      "total_cloud_wrapper_sec": elapsed(total_start),
      "results": results,
    }
    print(json.dumps(summary, indent=2, sort_keys=True), flush=True)

    if summary_output_uri:
      local_summary = temp_root / "summary.json"
      local_summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
      upload_blob(client, local_summary, summary_output_uri)

  shutil.rmtree(work_root, ignore_errors=True)
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
