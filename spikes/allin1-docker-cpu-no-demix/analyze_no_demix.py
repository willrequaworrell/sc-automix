#!/usr/bin/env python3
"""Run upstream allin1 inference on CPU without Demucs by fabricating stem WAVs."""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import statistics
import sys
import tempfile
import time
from dataclasses import asdict, is_dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

ANALYSIS_VERSION = "allin1-torch-cpu-fast-v0"
ENGINE = "allin1-torch-cpu-no-demix"
STEMS = ("bass", "drums", "other", "vocals")


def package_version(name: str) -> str:
  try:
    return metadata.version(name)
  except metadata.PackageNotFoundError:
    return "not-installed"


def now() -> float:
  return time.perf_counter()


def elapsed(start: float) -> float:
  return round(time.perf_counter() - start, 6)


def load_audio_mono(input_path: Path) -> tuple[Any, int, float]:
  import numpy as np
  import soundfile as sf

  audio, sample_rate = sf.read(str(input_path), always_2d=True, dtype="float32")
  mono = audio.mean(axis=1).astype(np.float32, copy=False)
  duration = float(len(mono) / sample_rate) if sample_rate else 0.0
  return mono, int(sample_rate), duration


def write_fake_stems(input_path: Path, work_dir: Path) -> tuple[Path, float, int, float]:
  import soundfile as sf

  start = now()
  mono, sample_rate, duration = load_audio_mono(input_path)
  stem_dir = work_dir / input_path.stem
  stem_dir.mkdir(parents=True, exist_ok=True)
  for stem in STEMS:
    sf.write(str(stem_dir / f"{stem}.wav"), mono, sample_rate)
  return stem_dir, duration, sample_rate, elapsed(start)


def run_allin1(input_path: Path, stem_dir: Path, spec_dir: Path, cache_dir: Path, model_name: str) -> tuple[Any, dict[str, float]]:
  import torch
  from allin1.helpers import run_inference
  from allin1.models import load_pretrained_model
  from allin1.spectrogram import extract_spectrograms

  timings: dict[str, float] = {}

  start = now()
  spec_paths = extract_spectrograms([stem_dir], spec_dir, multiprocess=False)
  timings["spectrogram_sec"] = elapsed(start)

  start = now()
  model = load_pretrained_model(model_name=model_name, cache_dir=cache_dir, device="cpu")
  timings["model_load_sec"] = elapsed(start)

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


def tempo_stats(beats: list[float]) -> dict[str, float | None]:
  intervals = [
    b - a
    for a, b in zip(beats, beats[1:])
    if math.isfinite(a) and math.isfinite(b) and b > a
  ]
  if not intervals:
    return {"mean_bpm": None, "median_bpm": None, "long_span_bpm": None}
  bpms = [60.0 / interval for interval in intervals]
  long_span = (60.0 * (len(beats) - 1) / (beats[-1] - beats[0])) if len(beats) > 1 and beats[-1] > beats[0] else None
  return {
    "mean_bpm": round(float(statistics.mean(bpms)), 6),
    "median_bpm": round(float(statistics.median(bpms)), 6),
    "long_span_bpm": round(float(long_span), 6) if long_span is not None else None,
  }


def nearest_beat_index(beats: list[float], t: float) -> int | None:
  if not beats:
    return None
  return min(range(len(beats)), key=lambda i: abs(beats[i] - t))


def normalize_segments(segments: list[Any], bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
  normalized = []
  for idx, seg in enumerate(segments or []):
    data = asdict(seg) if is_dataclass(seg) else dict(seg)
    start = float(data.get("start", 0.0))
    start_bar_index = None
    for bar in bars:
      if bar["start_sec"] <= start:
        start_bar_index = bar["index"]
      else:
        break
    normalized.append({
      "index": idx,
      "start_sec": round(start, 6),
      "end_sec": round(float(data.get("end", start)), 6),
      "label": str(data.get("label", "")),
      "start_bar_index": start_bar_index,
    })
  return normalized


def normalize_result(result: Any, input_path: Path, duration: float, sample_rate: int, timings: dict[str, float], model_name: str) -> dict[str, Any]:
  beats_raw = [float(t) for t in (result.beats or [])]
  downbeats_raw = [float(t) for t in (result.downbeats or [])]
  beat_positions = [int(p) for p in (result.beat_positions or [])]
  downbeat_lookup = set()
  for downbeat in downbeats_raw:
    beat_index = nearest_beat_index(beats_raw, downbeat)
    if beat_index is not None:
      downbeat_lookup.add(beat_index)

  beats = []
  for idx, t in enumerate(beats_raw):
    label = beat_positions[idx] if idx < len(beat_positions) else (idx % 4) + 1
    beats.append({
      "index": idx,
      "time_sec": round(t, 6),
      "beat_label": label,
      "is_downbeat": idx in downbeat_lookup or label == 1,
    })

  downbeats = []
  for idx, t in enumerate(downbeats_raw):
    beat_index = nearest_beat_index(beats_raw, t)
    downbeats.append({
      "index": idx,
      "time_sec": round(t, 6),
      "beat_index": beat_index if beat_index is not None else 0,
    })

  bars = []
  for idx, downbeat in enumerate(downbeats):
    start_beat = downbeat["beat_index"]
    next_start_beat = downbeats[idx + 1]["beat_index"] if idx + 1 < len(downbeats) else min(start_beat + 4, len(beats_raw) - 1)
    end_sec = downbeats[idx + 1]["time_sec"] if idx + 1 < len(downbeats) else (beats_raw[next_start_beat] if beats_raw and next_start_beat >= 0 else duration)
    bars.append({
      "index": idx,
      "start_sec": downbeat["time_sec"],
      "end_sec": round(float(end_sec), 6),
      "start_beat_index": int(start_beat),
      "beat_count": max(1, int(next_start_beat - start_beat)) if beats_raw else 0,
    })

  stats = tempo_stats(beats_raw)
  tempo_bpm = stats["long_span_bpm"]
  warnings = [
    "No-demix mode duplicated the mono mix into bass/drums/other/vocals inputs; this bypasses Demucs and is not upstream default behavior."
  ]
  if len(beats_raw) < 2:
    warnings.append("Tempo could not be estimated from fewer than two beats.")
  dependency_versions = {
    "python": platform.python_version(),
    "platform": platform.platform(),
    "architecture": platform.machine(),
    "torch": package_version("torch"),
    "torchaudio": package_version("torchaudio"),
    "natten": package_version("natten"),
    "madmom": package_version("madmom"),
    "allin1": package_version("allin1"),
    "numpy": package_version("numpy"),
    "scipy": package_version("scipy"),
    "device": "cpu",
    "model": model_name,
  }
  engine_version = (
    f"allin1={dependency_versions['allin1']}; "
    f"torch={dependency_versions['torch']}; "
    f"torchaudio={dependency_versions['torchaudio']}; "
    f"natten={dependency_versions['natten']}; "
    f"madmom={dependency_versions['madmom']}; "
    f"numpy={dependency_versions['numpy']}; "
    f"scipy={dependency_versions['scipy']}; "
    f"device=cpu; model={model_name}"
  )

  normalized = {
    "track_id": input_path.stem,
    "analysis_version": ANALYSIS_VERSION,
    "engine": ENGINE,
    "engine_version": engine_version,
    "duration_analyzed_sec": round(duration, 6),
    "tempo_bpm": tempo_bpm,
    "meter": "4/4" if downbeats else None,
    "confidence": None,
    "beats": beats,
    "downbeats": downbeats,
    "bars": bars,
    "segments": normalize_segments(result.segments or [], bars),
    "warnings": warnings,
    "raw_summary": {
      "input_path": str(input_path),
      "dependency_versions": dependency_versions,
      "sample_rate": sample_rate,
      "model_integer_bpm": getattr(result, "bpm", None),
      "tempo_stats": stats,
      "beat_count": len(beats),
      "downbeat_count": len(downbeats),
      "bar_count": len(bars),
      "segment_count": len(result.segments or []),
      "timings_sec": timings,
      "no_demix_stem_strategy": "mono_mix_written_to_bass_drums_other_vocals",
    },
  }
  validate_contract_shape(normalized)
  return normalized


def validate_contract_shape(data: dict[str, Any]) -> None:
  required = ["track_id", "analysis_version", "engine", "duration_analyzed_sec", "beats", "downbeats", "bars", "warnings", "raw_summary"]
  missing = [key for key in required if key not in data]
  if missing:
    raise ValueError(f"Normalized output missing required fields: {missing}")
  for idx, beat in enumerate(data["beats"]):
    for key in ("index", "time_sec", "beat_label", "is_downbeat"):
      if key not in beat:
        raise ValueError(f"Beat {idx} missing {key}")
  for idx, downbeat in enumerate(data["downbeats"]):
    for key in ("index", "time_sec", "beat_index"):
      if key not in downbeat:
        raise ValueError(f"Downbeat {idx} missing {key}")
  for idx, bar in enumerate(data["bars"]):
    for key in ("index", "start_sec", "end_sec", "start_beat_index", "beat_count"):
      if key not in bar:
        raise ValueError(f"Bar {idx} missing {key}")


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(description="Analyze one WAV with upstream allin1 on Linux CPU without Demucs.")
  parser.add_argument("input", type=Path, help="Input WAV path, normally under /input.")
  parser.add_argument("--output-dir", type=Path, default=Path("/output"), help="Directory for normalized JSON.")
  parser.add_argument("--cache-dir", type=Path, default=Path("/cache/huggingface"), help="Model cache directory.")
  parser.add_argument("--work-dir", type=Path, default=None, help="Working directory for fake stems and spectrograms.")
  parser.add_argument("--model", default="harmonix-all", help="allin1 model name.")
  parser.add_argument("--track-id", default=None, help="Override track_id in output JSON.")
  parser.add_argument("--timings-path", type=Path, default=None, help="Optional JSONL timings sink.")
  parser.add_argument("--print-versions-only", action="store_true", help="Print dependency versions and exit.")
  return parser.parse_args()


def print_versions() -> None:
  import torch

  versions = {
    "os": platform.platform(),
    "architecture": platform.machine(),
    "python": f"{platform.python_version()} ({sys.executable})",
    "selected_device": "cpu",
    "torch_cuda_available": bool(torch.cuda.is_available()),
    "torch": package_version("torch"),
    "natten": package_version("natten"),
    "madmom": package_version("madmom"),
    "allin1": package_version("allin1"),
    "numpy": package_version("numpy"),
    "scipy": package_version("scipy"),
  }
  print(json.dumps(versions, indent=2, sort_keys=True))


def main() -> int:
  args = parse_args()
  os.environ.setdefault("XDG_CACHE_HOME", "/cache/xdg")
  os.environ.setdefault("TORCH_HOME", "/cache/torch")
  os.environ.setdefault("HF_HOME", "/cache/huggingface")
  os.environ.setdefault("MPLCONFIGDIR", "/cache/matplotlib")
  os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

  if args.print_versions_only:
    print_versions()
    return 0

  input_path = args.input
  if not input_path.is_file():
    raise FileNotFoundError(f"Input file not found: {input_path}")

  total_start = now()
  args.output_dir.mkdir(parents=True, exist_ok=True)
  args.cache_dir.mkdir(parents=True, exist_ok=True)
  timings: dict[str, float] = {}

  with tempfile.TemporaryDirectory(dir=str(args.work_dir) if args.work_dir else None) as temp_root:
    work_dir = Path(temp_root)
    stem_dir, duration, sample_rate, timings["fake_stems_sec"] = write_fake_stems(input_path, work_dir / "demix" / "htdemucs")
    result, run_timings = run_allin1(
      input_path=input_path,
      stem_dir=stem_dir,
      spec_dir=work_dir / "spec",
      cache_dir=args.cache_dir,
      model_name=args.model,
    )
    timings.update(run_timings)
    normalize_start = now()
    normalized = normalize_result(result, input_path, duration, sample_rate, timings, args.model)
    if args.track_id:
      normalized["track_id"] = args.track_id
    normalized["raw_summary"]["timings_sec"]["total_cli_sec"] = elapsed(total_start)
    out_path = args.output_dir / f"{input_path.stem}.json"
    out_path.write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n")
    normalized["raw_summary"]["timings_sec"]["normalization_write_sec"] = elapsed(normalize_start)
    out_path.write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n")

  if args.timings_path:
    args.timings_path.parent.mkdir(parents=True, exist_ok=True)
    with args.timings_path.open("a") as fh:
      fh.write(json.dumps({
        "track": input_path.name,
        "output": str(out_path),
        "timings_sec": normalized["raw_summary"]["timings_sec"],
      }, sort_keys=True) + "\n")

  print(json.dumps({
    "output": str(out_path),
    "tempo_bpm": normalized["tempo_bpm"],
    "beats": len(normalized["beats"]),
    "downbeats": len(normalized["downbeats"]),
    "segments": len(normalized.get("segments", [])),
    "timings_sec": normalized["raw_summary"]["timings_sec"],
  }, indent=2, sort_keys=True))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
