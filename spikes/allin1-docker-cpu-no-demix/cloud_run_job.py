#!/usr/bin/env python3
"""Cloud Run Jobs wrapper for gs:// input/output analysis."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from google.cloud import storage


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


def upload_blob(client: storage.Client, src: Path, uri: str) -> float:
  start = now()
  bucket_name, blob_name = parse_gs_uri(uri)
  client.bucket(bucket_name).blob(blob_name).upload_from_filename(str(src), content_type="application/json")
  return elapsed(start)


def require_env(name: str) -> str:
  value = os.environ.get(name)
  if not value:
    raise ValueError(f"Missing required environment variable: {name}")
  return value


def main() -> int:
  input_uri = require_env("INPUT_URI")
  output_uri = require_env("OUTPUT_URI")
  model = os.environ.get("ALLIN1_MODEL", "harmonix-all")
  cache_dir = Path(os.environ.get("ALLIN1_CACHE_DIR", "/tmp/allin1-cache"))
  work_root = Path(os.environ.get("ALLIN1_WORK_DIR", "/tmp/allin1-work"))

  client = storage.Client()
  timings: dict[str, float] = {}
  total_start = now()

  with tempfile.TemporaryDirectory(dir=str(work_root.parent)) as temp_dir:
    temp_root = Path(temp_dir)
    input_name = Path(parse_gs_uri(input_uri)[1]).name
    local_input = temp_root / "input" / input_name
    local_output_dir = temp_root / "output"
    local_work_dir = temp_root / "work"
    local_work_dir.mkdir(parents=True, exist_ok=True)

    timings["download_sec"] = download_blob(client, input_uri, local_input)

    cmd = [
      sys.executable,
      "/app/analyze_no_demix.py",
      str(local_input),
      "--output-dir",
      str(local_output_dir),
      "--cache-dir",
      str(cache_dir),
      "--work-dir",
      str(local_work_dir),
      "--model",
      model,
      "--timings-path",
      str(local_output_dir / "timings.jsonl"),
    ]
    subprocess.run(cmd, check=True)

    local_json = local_output_dir / f"{local_input.stem}.json"
    if not local_json.is_file():
      raise FileNotFoundError(f"Analyzer did not create expected output: {local_json}")

    timings["upload_sec"] = upload_blob(client, local_json, output_uri)

    timing_jsonl = local_output_dir / "timings.jsonl"
    timing_output_uri = os.environ.get("TIMINGS_OUTPUT_URI")
    if timing_output_uri and timing_jsonl.is_file():
      timings["timings_upload_sec"] = upload_blob(client, timing_jsonl, timing_output_uri)

    cloud_summary = {
      "input_uri": input_uri,
      "output_uri": output_uri,
      "timings_output_uri": timing_output_uri,
      "local_input_size_bytes": local_input.stat().st_size,
      "analysis_output_size_bytes": local_json.stat().st_size,
      "cloud_wrapper_timings_sec": timings,
      "total_cloud_wrapper_sec": elapsed(total_start),
    }
    print(json.dumps(cloud_summary, indent=2, sort_keys=True), flush=True)

  shutil.rmtree(work_root, ignore_errors=True)
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
