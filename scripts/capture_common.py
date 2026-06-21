"""Shared acquisition primitives for the ASVspoof 5 playback extension.

This module deliberately imports the hardware/audio stack lazily. Manifest
inspection, test-plan generation and CLI help therefore work before NumPy,
SciPy, SoundDevice and SoundFile are installed.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import sqlite3
import sys
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PLAN_DIRECTORY = REPOSITORY_ROOT / "protocols" / "capture-plan"
PLAN_INDEX_PATH = PLAN_DIRECTORY / "capture-plan.json"
CONDITIONS = ("HH", "HL", "LH", "LL")
PARTITIONS = ("train", "development", "evaluation")


class CaptureError(RuntimeError):
    """Base error for acquisition failures."""


class ConfigurationError(CaptureError):
    """Raised when an acquisition configuration is incomplete or ambiguous."""


class AlignmentError(CaptureError):
    """Raised when the captured waveform cannot be aligned safely."""


_AUDIO_STACK: tuple[Any, Any, Any, Any] | None = None


def require_audio_stack() -> tuple[Any, Any, Any, Any]:
    """Return numpy, scipy.signal, sounddevice and soundfile.

    Imports are delayed so that --help, --plan-only and --dry-run don't require
    native audio dependencies.
    """

    global _AUDIO_STACK
    if _AUDIO_STACK is not None:
        return _AUDIO_STACK
    try:
        import numpy as np
        from scipy import signal
        import sounddevice as sd
        import soundfile as sf
    except ImportError as exc:
        raise ConfigurationError(
            "Missing capture dependencies. Install them with "
            "`python -m pip install -r requirements-capture.txt`."
        ) from exc
    _AUDIO_STACK = (np, signal, sd, sf)
    return _AUDIO_STACK


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_plan_index() -> dict[str, Any]:
    with PLAN_INDEX_PATH.open("r", encoding="utf-8") as handle:
        index = json.load(handle)
    if index.get("scope", {}).get("source_track") != 1:
        raise CaptureError("The capture index is not ASVspoof 5 Track 1.")
    return index


def condition_shards(index: dict[str, Any], condition: str) -> list[dict[str, Any]]:
    if condition not in CONDITIONS:
        raise ConfigurationError(f"Unsupported condition: {condition}")
    by_partition = {
        shard["partition"]: shard
        for shard in index["shards"]
        if shard["playback_condition"] == condition
    }
    missing = [partition for partition in PARTITIONS if partition not in by_partition]
    if missing:
        raise CaptureError(
            f"Condition {condition} lacks shards for: {', '.join(missing)}"
        )
    return [by_partition[partition] for partition in PARTITIONS]


def shard_path(shard: dict[str, Any]) -> Path:
    return PLAN_DIRECTORY.joinpath(*str(shard["path"]).split("/"))


def verify_condition_shards(
    index: dict[str, Any], condition: str
) -> list[dict[str, Any]]:
    shards = condition_shards(index, condition)
    for shard in shards:
        path = shard_path(shard)
        if not path.is_file():
            raise CaptureError(f"Missing capture-plan shard: {path}")
        actual = sha256_file(path)
        if actual != shard["sha256"]:
            raise CaptureError(
                f"SHA-256 mismatch for {path}: expected {shard['sha256']}, got {actual}"
            )
    return shards


def read_shard_rows(
    shard: dict[str, Any], expected_columns: Sequence[str]
) -> Iterator[dict[str, str]]:
    path = shard_path(shard)
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames != list(expected_columns):
            raise CaptureError(f"Unexpected TSV header in {path}")
        count = 0
        for count, row in enumerate(reader, start=1):
            if row["playback_condition"] != shard["playback_condition"]:
                raise CaptureError(f"Condition mismatch in {path}, row {count + 1}")
            if row["source_partition"] != shard["partition"]:
                raise CaptureError(f"Partition mismatch in {path}, row {count + 1}")
            yield row
        if count != int(shard["row_count"]):
            raise CaptureError(
                f"Row-count mismatch in {path}: expected {shard['row_count']}, got {count}"
            )


def iter_condition_jobs(
    index: dict[str, Any], condition: str
) -> Iterator[dict[str, str]]:
    columns = index["file_format"]["columns"]
    for shard in condition_shards(index, condition):
        yield from read_shard_rows(shard, columns)


def condition_job_count(index: dict[str, Any], condition: str) -> int:
    return sum(int(shard["row_count"]) for shard in condition_shards(index, condition))


def default_acquisition_config(
    condition: str,
    *,
    input_device: str | None = None,
    output_device: str | None = None,
    input_host_api: str | None = None,
    output_host_api: str | None = None,
    recording_equipment: str | None = None,
    playback_equipment: str | None = None,
    audio_interface: str | None = None,
) -> dict[str, Any]:
    if condition not in CONDITIONS:
        raise ConfigurationError(f"Unsupported condition: {condition}")
    return {
        "schema_version": "1.0",
        "condition": condition,
        "devices": {
            "input": {
                "selector": input_device,
                "host_api": input_host_api,
                "physical_device": recording_equipment,
                "audio_interface": audio_interface,
                "channel": 1,
                "stream_channels": 1,
            },
            "output": {
                "selector": output_device,
                "host_api": output_host_api,
                "physical_device": playback_equipment,
                "audio_interface": audio_interface,
                "stream_channels": 2,
                "duplicate_mono_to_all_channels": True,
            },
        },
        "capture": {
            "sample_rate_hz": 48000,
            "output_sample_rate_hz": 16000,
            "output_subtype": "PCM_16",
            "pre_roll_ms": 500,
            "post_roll_ms": 750,
            "latency": "high",
        },
        "alignment": {
            "analysis_sample_rate_hz": 4000,
            "search_before_expected_ms": 100,
            "search_after_expected_ms": 600,
            "refine_window_ms": 30,
            "refine_reference_ms": 1000,
        },
        "validation": {
            "minimum_alignment_score": 0.10,
            "clip_threshold": 0.999,
            "maximum_clipped_fraction": 0.0001,
            "minimum_final_rms_dbfs": -60.0,
            "amplitude_delta_db_min": None,
            "amplitude_delta_db_max": None,
        },
        "execution": {
            "maximum_attempts_per_job": 2,
            "maximum_consecutive_failed_jobs": 3,
        },
        "fixed_setup": {
            "room": None,
            "speaker_microphone_distance": None,
            "speaker_microphone_orientation": None,
            "speaker_volume": None,
            "microphone_gain": None,
            "windows_audio_enhancements_disabled": None,
        },
    }


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_acquisition_config(
    path: Path | None,
    condition: str,
    *,
    overrides: dict[str, Any] | None = None,
    require_devices: bool = True,
) -> dict[str, Any]:
    config = default_acquisition_config(condition)
    if path is not None:
        with path.open("r", encoding="utf-8") as handle:
            config = _deep_merge(config, json.load(handle))
    if overrides:
        config = _deep_merge(config, overrides)
    validate_acquisition_config(config, condition, require_devices=require_devices)
    return config


def validate_acquisition_config(
    config: dict[str, Any], condition: str, *, require_devices: bool = True
) -> None:
    if config.get("condition") != condition:
        raise ConfigurationError(
            f"Configuration condition {config.get('condition')!r} does not match {condition}."
        )
    devices = config.get("devices", {})
    for direction in ("input", "output"):
        device = devices.get(direction, {})
        selector = device.get("selector")
        if require_devices and (
            selector in (None, "") or "REPLACE" in str(selector).upper()
        ):
            raise ConfigurationError(
                f"The {direction} device selector must be configured explicitly."
            )
        channel_count = int(device.get("stream_channels", 0))
        if channel_count < 1:
            raise ConfigurationError(
                f"devices.{direction}.stream_channels must be positive."
            )
    input_device = devices.get("input", {})
    if int(input_device.get("channel", 0)) not in range(
        1, int(input_device.get("stream_channels", 0)) + 1
    ):
        raise ConfigurationError("The selected input channel is outside the stream.")
    capture = config.get("capture", {})
    for key in ("sample_rate_hz", "output_sample_rate_hz"):
        if int(capture.get(key, 0)) <= 0:
            raise ConfigurationError(f"capture.{key} must be positive.")
    if (
        float(capture.get("pre_roll_ms", -1)) < 0
        or float(capture.get("post_roll_ms", -1)) < 0
    ):
        raise ConfigurationError("Pre-roll and post-roll cannot be negative.")
    if int(config.get("execution", {}).get("maximum_attempts_per_job", 0)) < 1:
        raise ConfigurationError("At least one attempt per job is required.")
    if int(config.get("execution", {}).get("maximum_consecutive_failed_jobs", 0)) < 1:
        raise ConfigurationError(
            "execution.maximum_consecutive_failed_jobs must be positive."
        )


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.partial")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(temporary, path)


def list_audio_devices() -> None:
    _, _, sd, _ = require_audio_stack()
    host_apis = sd.query_hostapis()
    devices = sd.query_devices()
    print("index\tdirection\thost_api\tname\tinputs\toutputs")
    for index, device in enumerate(devices):
        directions = []
        if int(device["max_input_channels"]) > 0:
            directions.append("input")
        if int(device["max_output_channels"]) > 0:
            directions.append("output")
        print(
            f"{index}\t{'+'.join(directions) or '-'}\t"
            f"{host_apis[device['hostapi']]['name']}\t{device['name']}\t"
            f"{device['max_input_channels']}\t{device['max_output_channels']}"
        )


def resolve_audio_device(
    selector: str | int,
    direction: str,
    *,
    host_api: str | None = None,
) -> dict[str, Any]:
    _, _, sd, _ = require_audio_stack()
    if direction not in ("input", "output"):
        raise ValueError(direction)
    devices = sd.query_devices()
    host_apis = sd.query_hostapis()
    channel_key = f"max_{direction}_channels"

    def describe(index: int) -> dict[str, Any]:
        device = devices[index]
        return {
            "index": index,
            "name": str(device["name"]),
            "host_api": str(host_apis[device["hostapi"]]["name"]),
            "max_input_channels": int(device["max_input_channels"]),
            "max_output_channels": int(device["max_output_channels"]),
            "default_samplerate": float(device["default_samplerate"]),
        }

    selector_text = str(selector).strip()
    if selector_text.isdecimal():
        index = int(selector_text)
        if index >= len(devices) or int(devices[index][channel_key]) < 1:
            raise ConfigurationError(
                f"Device index {index} cannot be used for {direction}."
            )
        result = describe(index)
        if host_api and host_api.casefold() not in result["host_api"].casefold():
            raise ConfigurationError(
                f"Device {index} uses {result['host_api']}, not {host_api}."
            )
        return result

    candidates: list[dict[str, Any]] = []
    for index, device in enumerate(devices):
        if int(device[channel_key]) < 1:
            continue
        candidate = describe(index)
        if host_api and host_api.casefold() not in candidate["host_api"].casefold():
            continue
        if selector_text.casefold() in candidate["name"].casefold():
            candidates.append(candidate)
    exact = [
        candidate
        for candidate in candidates
        if candidate["name"].casefold() == selector_text.casefold()
    ]
    if len(exact) == 1:
        return exact[0]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ConfigurationError(
            f"No {direction} device matches {selector_text!r}"
            + (f" on {host_api!r}." if host_api else ".")
        )
    choices = ", ".join(
        f"[{candidate['index']}] {candidate['name']} ({candidate['host_api']})"
        for candidate in candidates
    )
    raise ConfigurationError(
        f"Ambiguous {direction} selector {selector_text!r}: {choices}"
    )


def resolve_and_check_devices(config: dict[str, Any]) -> dict[str, Any]:
    _, _, sd, _ = require_audio_stack()
    input_config = config["devices"]["input"]
    output_config = config["devices"]["output"]
    input_device = resolve_audio_device(
        input_config["selector"], "input", host_api=input_config.get("host_api")
    )
    output_device = resolve_audio_device(
        output_config["selector"], "output", host_api=output_config.get("host_api")
    )
    if input_device["host_api"] != output_device["host_api"]:
        raise ConfigurationError(
            "Input and output must use the same PortAudio host API for the "
            "persistent full-duplex stream."
        )
    sample_rate = int(config["capture"]["sample_rate_hz"])
    input_channels = int(input_config["stream_channels"])
    output_channels = int(output_config["stream_channels"])
    try:
        sd.check_input_settings(
            device=input_device["index"],
            channels=input_channels,
            samplerate=sample_rate,
            dtype="float32",
        )
        sd.check_output_settings(
            device=output_device["index"],
            channels=output_channels,
            samplerate=sample_rate,
            dtype="float32",
        )
    except Exception as exc:
        raise ConfigurationError(
            f"Audio device settings are unsupported: {exc}"
        ) from exc
    return {"input": input_device, "output": output_device}


def print_preflight(
    condition: str, config: dict[str, Any], resolved: dict[str, Any]
) -> None:
    print(f"Condition: {condition}")
    print(
        "Playback: "
        f"[{resolved['output']['index']}] {resolved['output']['name']} "
        f"({resolved['output']['host_api']}) -> "
        f"{config['devices']['output'].get('physical_device') or 'not documented'}"
    )
    print(
        "Recording: "
        f"[{resolved['input']['index']}] {resolved['input']['name']} "
        f"({resolved['input']['host_api']}) <- "
        f"{config['devices']['input'].get('physical_device') or 'not documented'}"
    )
    print(
        f"Capture: {config['capture']['sample_rate_hz']} Hz; "
        f"final: {config['capture']['output_sample_rate_hz']} Hz "
        f"{config['capture']['output_subtype']} mono"
    )
    setup = config.get("fixed_setup", {})
    print(
        "Fixed setup: "
        f"distance={setup.get('speaker_microphone_distance')!r}, "
        f"speaker volume={setup.get('speaker_volume')!r}, "
        f"microphone gain={setup.get('microphone_gain')!r}"
    )


def require_operator_confirmation(condition: str) -> None:
    answer = input(
        f"Type {condition} to confirm the physical chain and fixed setup: "
    ).strip()
    if answer != condition:
        raise ConfigurationError("Operator did not confirm the acquisition condition.")


def _resample(audio: Any, source_rate: int, target_rate: int) -> Any:
    np, signal, _, _ = require_audio_stack()
    audio = np.asarray(audio, dtype=np.float32)
    if source_rate == target_rate:
        return audio.copy()
    divisor = math.gcd(source_rate, target_rate)
    result = signal.resample_poly(audio, target_rate // divisor, source_rate // divisor)
    return np.asarray(result, dtype=np.float32)


def _normalized_valid_correlation(reference: Any, search: Any) -> tuple[Any, Any]:
    np, signal, _, _ = require_audio_stack()
    reference = np.asarray(reference, dtype=np.float64)
    search = np.asarray(search, dtype=np.float64)
    if reference.size < 2 or search.size < reference.size:
        raise AlignmentError("The correlation window is shorter than the reference.")
    reference = reference - float(reference.mean())
    search = search - float(search.mean())
    reference_energy = float(np.dot(reference, reference))
    if reference_energy <= 1e-12:
        raise AlignmentError("The reference signal has insufficient energy.")
    correlation = signal.correlate(search, reference, mode="valid", method="fft")
    cumulative = np.concatenate(
        (np.zeros(1, dtype=np.float64), np.cumsum(search * search))
    )
    window_energy = cumulative[reference.size :] - cumulative[: -reference.size]
    denominator = np.sqrt(np.maximum(window_energy * reference_energy, 1e-24))
    scores = correlation / denominator
    return scores, np.abs(scores)


@dataclass(frozen=True)
class AlignmentResult:
    offset_samples: int
    score: float
    coarse_score: float
    polarity: int


def align_capture(
    source_at_capture_rate: Any,
    raw_capture: Any,
    capture_rate: int,
    config: dict[str, Any],
) -> AlignmentResult:
    np, _, _, _ = require_audio_stack()
    alignment = config["alignment"]
    capture = config["capture"]
    analysis_rate = min(int(alignment["analysis_sample_rate_hz"]), capture_rate)
    reference_coarse = _resample(source_at_capture_rate, capture_rate, analysis_rate)
    capture_coarse = _resample(raw_capture, capture_rate, analysis_rate)
    expected = round(float(capture["pre_roll_ms"]) * analysis_rate / 1000)
    before = round(float(alignment["search_before_expected_ms"]) * analysis_rate / 1000)
    after = round(float(alignment["search_after_expected_ms"]) * analysis_rate / 1000)
    maximum_offset = capture_coarse.size - reference_coarse.size
    low = max(0, expected - before)
    high = min(maximum_offset, expected + after)
    if high < low:
        raise AlignmentError("The coarse alignment search window is empty.")
    search = capture_coarse[low : high + reference_coarse.size]
    signed_scores, absolute_scores = _normalized_valid_correlation(
        reference_coarse, search
    )
    best = int(np.argmax(absolute_scores))
    coarse_offset = low + best
    coarse_score = float(absolute_scores[best])

    reference = np.asarray(source_at_capture_rate, dtype=np.float32)
    raw = np.asarray(raw_capture, dtype=np.float32)
    desired_chunk = round(float(alignment["refine_reference_ms"]) * capture_rate / 1000)
    chunk_size = min(reference.size, max(256, desired_chunk))
    if chunk_size == reference.size:
        chunk_start = 0
    else:
        squared = np.asarray(reference, dtype=np.float64) ** 2
        cumulative = np.concatenate((np.zeros(1, dtype=np.float64), np.cumsum(squared)))
        energies = cumulative[chunk_size:] - cumulative[:-chunk_size]
        chunk_start = int(np.argmax(energies))
    chunk = reference[chunk_start : chunk_start + chunk_size]
    predicted_offset = round(coarse_offset * capture_rate / analysis_rate)
    refine_radius = round(float(alignment["refine_window_ms"]) * capture_rate / 1000)
    predicted_chunk = predicted_offset + chunk_start
    low_chunk = max(0, predicted_chunk - refine_radius)
    high_chunk = min(raw.size - chunk_size, predicted_chunk + refine_radius)
    if high_chunk < low_chunk:
        raise AlignmentError("The fine alignment search window is empty.")
    fine_search = raw[low_chunk : high_chunk + chunk_size]
    fine_signed, fine_absolute = _normalized_valid_correlation(chunk, fine_search)
    fine_best = int(np.argmax(fine_absolute))
    offset = low_chunk + fine_best - chunk_start
    if offset < 0 or offset + reference.size > raw.size:
        raise AlignmentError(
            f"Aligned segment [{offset}, {offset + reference.size}) is outside raw capture."
        )
    signed_score = float(fine_signed[fine_best])
    return AlignmentResult(
        offset_samples=offset,
        score=abs(signed_score),
        coarse_score=coarse_score,
        polarity=1 if signed_score >= 0 else -1,
    )


def _dbfs(value: float) -> float:
    if value <= 1e-12:
        return -240.0
    return 20.0 * math.log10(value)


@dataclass
class CaptureOutcome:
    job_id: str
    status: str
    output_path: str | None
    raw_path: str | None
    source_sample_rate_hz: int
    capture_sample_rate_hz: int
    output_sample_rate_hz: int
    raw_frames: int
    final_frames: int
    expected_final_frames: int
    alignment_offset_samples: int
    alignment_score: float
    coarse_alignment_score: float
    alignment_polarity: int
    amplitude_delta_db: float
    source_active_rms_dbfs: float
    capture_active_rms_dbfs: float
    final_peak_dbfs: float
    clipped_fraction: float
    callback_status: str
    validation_errors: list[str]
    output_sha256: str | None


class AudioCaptureSession:
    """One persistent full-duplex PortAudio stream for a condition session."""

    def __init__(
        self, config: dict[str, Any], resolved_devices: dict[str, Any]
    ) -> None:
        self.config = config
        self.resolved_devices = resolved_devices
        self.stream: Any = None
        self._active: dict[str, Any] | None = None

    def __enter__(self) -> "AudioCaptureSession":
        _, _, sd, _ = require_audio_stack()
        input_channels = int(self.config["devices"]["input"]["stream_channels"])
        output_channels = int(self.config["devices"]["output"]["stream_channels"])
        try:
            self.stream = sd.Stream(
                samplerate=int(self.config["capture"]["sample_rate_hz"]),
                device=(
                    self.resolved_devices["input"]["index"],
                    self.resolved_devices["output"]["index"],
                ),
                channels=(input_channels, output_channels),
                dtype=("float32", "float32"),
                latency=self.config["capture"].get("latency", "high"),
                callback=self._callback,
                prime_output_buffers_using_stream_callback=False,
            )
            self.stream.start()
        except Exception as exc:
            if self.stream is not None:
                self.stream.close()
                self.stream = None
            raise ConfigurationError(
                f"Could not open the persistent full-duplex audio stream: {exc}"
            ) from exc
        return self

    def __exit__(self, *_: Any) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None

    def _callback(
        self, indata: Any, outdata: Any, frames: int, _time: Any, status: Any
    ) -> None:
        outdata.fill(0)
        state = self._active
        if state is None:
            return
        try:
            if status:
                state["callback_status"].append(str(status))
            cursor = int(state["cursor"])
            remaining = int(state["playback"].shape[0]) - cursor
            count = min(frames, remaining)
            if count > 0:
                outdata[:count] = state["playback"][cursor : cursor + count]
                state["recording"][cursor : cursor + count] = indata[:count]
                state["cursor"] = cursor + count
            if int(state["cursor"]) >= int(state["playback"].shape[0]):
                state["done"].set()
        except BaseException as exc:
            state["error"] = exc
            state["done"].set()

    def capture(self, playback: Any) -> tuple[Any, str]:
        np, _, _, _ = require_audio_stack()
        if self.stream is None:
            raise CaptureError("The audio session is not open.")
        if self._active is not None:
            raise CaptureError("The audio session already has an active job.")
        input_channels = int(self.config["devices"]["input"]["stream_channels"])
        state: dict[str, Any] = {
            "playback": np.asarray(playback, dtype=np.float32),
            "recording": np.zeros((len(playback), input_channels), dtype=np.float32),
            "cursor": 0,
            "callback_status": [],
            "error": None,
            "done": threading.Event(),
        }
        self._active = state
        duration = len(playback) / int(self.config["capture"]["sample_rate_hz"])
        completed = state["done"].wait(timeout=duration + 15.0)
        self._active = None
        if not completed:
            raise CaptureError(
                f"Audio callback timed out after {duration + 15.0:.1f} seconds."
            )
        if state["error"] is not None:
            raise CaptureError(f"Audio callback failed: {state['error']}")
        status_text = "; ".join(dict.fromkeys(state["callback_status"]))
        return state["recording"], status_text


def _write_raw_capture(path: Path, raw: Any, sample_rate: int) -> None:
    _, _, _, sf = require_audio_stack()
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), raw, sample_rate, format="WAV", subtype="FLOAT")


def _write_final_flac(path: Path, audio: Any, sample_rate: int, subtype: str) -> None:
    _, _, _, sf = require_audio_stack()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.stem}.partial{path.suffix}")
    sf.write(str(temporary), audio, sample_rate, format="FLAC", subtype=subtype)
    os.replace(temporary, path)


def _fit_length(audio: Any, expected_frames: int) -> Any:
    np, _, _, _ = require_audio_stack()
    audio = np.asarray(audio, dtype=np.float32)
    if audio.size > expected_frames:
        return audio[:expected_frames]
    if audio.size < expected_frames:
        return np.pad(audio, (0, expected_frames - audio.size))
    return audio


def capture_job(
    job: dict[str, str],
    config: dict[str, Any],
    output_path: Path,
    *,
    audio_session: AudioCaptureSession,
    raw_path: Path,
    keep_raw_on_success: bool,
    publish_invalid_final: bool,
) -> CaptureOutcome:
    np, _, _, sf = require_audio_stack()
    source_path = REPOSITORY_ROOT.joinpath(*job["source_audio_path"].split("/"))
    source, source_rate = sf.read(str(source_path), dtype="float32", always_2d=True)
    if source.shape[1] != 1:
        raise CaptureError(f"Source is not mono: {source_path}")
    source = np.asarray(source[:, 0], dtype=np.float32)
    capture_rate = int(config["capture"]["sample_rate_hz"])
    output_rate = int(config["capture"]["output_sample_rate_hz"])
    source_playback = _resample(source, int(source_rate), capture_rate)
    pre_frames = round(float(config["capture"]["pre_roll_ms"]) * capture_rate / 1000)
    post_frames = round(float(config["capture"]["post_roll_ms"]) * capture_rate / 1000)
    output_channels = int(config["devices"]["output"]["stream_channels"])
    playback = np.zeros(
        (pre_frames + source_playback.size + post_frames, output_channels),
        dtype=np.float32,
    )
    playback[pre_frames : pre_frames + source_playback.size, :] = source_playback[
        :, None
    ]
    selected_input = int(config["devices"]["input"]["channel"]) - 1
    raw_multichannel = None
    raw = None
    try:
        raw_multichannel, callback_status = audio_session.capture(playback)
        raw = np.asarray(raw_multichannel[:, selected_input], dtype=np.float32)
        alignment = align_capture(source_playback, raw, capture_rate, config)
        captured_segment = raw[
            alignment.offset_samples : alignment.offset_samples + source_playback.size
        ]
        final = _resample(captured_segment, capture_rate, output_rate)
        expected_final_frames = round(source.size * output_rate / int(source_rate))
        final = _fit_length(final, expected_final_frames)
        source_for_metrics = _fit_length(
            _resample(source, int(source_rate), output_rate), expected_final_frames
        )
        reference_peak = float(np.max(np.abs(source_for_metrics), initial=0.0))
        active_threshold = max(10 ** (-50 / 20), reference_peak * 10 ** (-35 / 20))
        active = np.abs(source_for_metrics) >= active_threshold
        if int(active.sum()) < max(64, expected_final_frames // 100):
            active = np.ones(expected_final_frames, dtype=bool)
        source_rms = float(np.sqrt(np.mean(source_for_metrics[active] ** 2)))
        capture_rms = float(np.sqrt(np.mean(final[active] ** 2)))
        amplitude_delta = _dbfs(capture_rms) - _dbfs(source_rms)
        peak = float(np.max(np.abs(final), initial=0.0))
        clip_threshold = float(config["validation"]["clip_threshold"])
        clipped_fraction = float(np.mean(np.abs(final) >= clip_threshold))
        errors: list[str] = []
        if callback_status:
            errors.append(f"audio callback status: {callback_status}")
        if alignment.score < float(config["validation"]["minimum_alignment_score"]):
            errors.append(
                f"alignment score {alignment.score:.4f} below minimum "
                f"{config['validation']['minimum_alignment_score']}"
            )
        if clipped_fraction > float(config["validation"]["maximum_clipped_fraction"]):
            errors.append(
                f"clipped fraction {clipped_fraction:.8f} exceeds "
                f"{config['validation']['maximum_clipped_fraction']}"
            )
        if _dbfs(capture_rms) < float(config["validation"]["minimum_final_rms_dbfs"]):
            errors.append(
                f"capture RMS {_dbfs(capture_rms):.2f} dBFS below minimum "
                f"{config['validation']['minimum_final_rms_dbfs']} dBFS"
            )
        minimum_delta = config["validation"].get("amplitude_delta_db_min")
        maximum_delta = config["validation"].get("amplitude_delta_db_max")
        if minimum_delta is not None and amplitude_delta < float(minimum_delta):
            errors.append(
                f"amplitude delta {amplitude_delta:.2f} dB below {minimum_delta} dB"
            )
        if maximum_delta is not None and amplitude_delta > float(maximum_delta):
            errors.append(
                f"amplitude delta {amplitude_delta:.2f} dB above {maximum_delta} dB"
            )
        if keep_raw_on_success or errors:
            _write_raw_capture(raw_path, raw, capture_rate)
        should_publish = not errors or publish_invalid_final
        output_sha256 = None
        if should_publish:
            _write_final_flac(
                output_path,
                final,
                output_rate,
                str(config["capture"]["output_subtype"]),
            )
            output_sha256 = sha256_file(output_path)
        return CaptureOutcome(
            job_id=job["job_id"],
            status="completed" if not errors else "validation_failed",
            output_path=str(output_path.relative_to(REPOSITORY_ROOT)).replace("\\", "/")
            if should_publish
            else None,
            raw_path=str(raw_path.relative_to(REPOSITORY_ROOT)).replace("\\", "/")
            if raw_path.exists()
            else None,
            source_sample_rate_hz=int(source_rate),
            capture_sample_rate_hz=capture_rate,
            output_sample_rate_hz=output_rate,
            raw_frames=int(raw.size),
            final_frames=int(final.size),
            expected_final_frames=expected_final_frames,
            alignment_offset_samples=alignment.offset_samples,
            alignment_score=alignment.score,
            coarse_alignment_score=alignment.coarse_score,
            alignment_polarity=alignment.polarity,
            amplitude_delta_db=amplitude_delta,
            source_active_rms_dbfs=_dbfs(source_rms),
            capture_active_rms_dbfs=_dbfs(capture_rms),
            final_peak_dbfs=_dbfs(peak),
            clipped_fraction=clipped_fraction,
            callback_status=callback_status,
            validation_errors=errors,
            output_sha256=output_sha256,
        )
    except BaseException:
        if raw is not None and not raw_path.exists():
            _write_raw_capture(raw_path, raw, capture_rate)
        raise


class CaptureLedger:
    """Crash-resilient progress ledger kept separately from the capture plan."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                condition TEXT NOT NULL,
                mode TEXT NOT NULL,
                config_sha256 TEXT NOT NULL,
                configuration_json TEXT NOT NULL,
                resolved_devices_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                condition TEXT NOT NULL,
                partition_name TEXT NOT NULL,
                source_path TEXT NOT NULL,
                output_path TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                started_at TEXT,
                completed_at TEXT,
                session_id TEXT,
                alignment_offset_samples INTEGER,
                alignment_score REAL,
                amplitude_delta_db REAL,
                raw_frames INTEGER,
                final_frames INTEGER,
                callback_status TEXT,
                output_sha256 TEXT,
                validation_errors_json TEXT,
                last_error TEXT
            );
            CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs(status);
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "CaptureLedger":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def add_session(
        self,
        session_id: str,
        condition: str,
        mode: str,
        config: dict[str, Any],
        resolved_devices: dict[str, Any],
    ) -> None:
        config_digest = sha256_json(config)
        existing_digests = {
            str(row["config_sha256"])
            for row in self.connection.execute(
                "SELECT DISTINCT config_sha256 FROM sessions "
                "WHERE condition = ? AND mode = ?",
                (condition, mode),
            )
        }
        if existing_digests and existing_digests != {config_digest}:
            raise ConfigurationError(
                f"Condition {condition} already has {mode} sessions with a "
                "different acquisition configuration. Refusing to mix setups."
            )
        self.connection.execute(
            """
            INSERT INTO sessions(
                session_id, started_at, condition, mode, config_sha256,
                configuration_json, resolved_devices_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                utc_now(),
                condition,
                mode,
                config_digest,
                json.dumps(config, ensure_ascii=False, sort_keys=True),
                json.dumps(resolved_devices, ensure_ascii=False, sort_keys=True),
            ),
        )
        self.connection.commit()

    def get(self, job_id: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()

    def start_attempt(
        self,
        job: dict[str, str],
        output_path: Path,
        session_id: str,
    ) -> int:
        existing = self.get(job["job_id"])
        attempts = int(existing["attempts"]) + 1 if existing else 1
        self.connection.execute(
            """
            INSERT INTO jobs(
                job_id, condition, partition_name, source_path, output_path,
                status, attempts, started_at, session_id
            ) VALUES (?, ?, ?, ?, ?, 'recording', ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                status = 'recording', attempts = excluded.attempts,
                started_at = excluded.started_at, session_id = excluded.session_id,
                last_error = NULL
            """,
            (
                job["job_id"],
                job["playback_condition"],
                job["source_partition"],
                job["source_audio_path"],
                str(output_path.relative_to(REPOSITORY_ROOT)).replace("\\", "/"),
                attempts,
                utc_now(),
                session_id,
            ),
        )
        self.connection.commit()
        return attempts

    def complete(self, outcome: CaptureOutcome, session_id: str) -> None:
        self.connection.execute(
            """
            UPDATE jobs SET
                status = 'completed', completed_at = ?, session_id = ?,
                alignment_offset_samples = ?, alignment_score = ?,
                amplitude_delta_db = ?, raw_frames = ?, final_frames = ?,
                callback_status = ?, output_sha256 = ?,
                validation_errors_json = ?, last_error = NULL
            WHERE job_id = ?
            """,
            (
                utc_now(),
                session_id,
                outcome.alignment_offset_samples,
                outcome.alignment_score,
                outcome.amplitude_delta_db,
                outcome.raw_frames,
                outcome.final_frames,
                outcome.callback_status,
                outcome.output_sha256,
                json.dumps(outcome.validation_errors, ensure_ascii=False),
                outcome.job_id,
            ),
        )
        self.connection.commit()

    def fail(
        self,
        job_id: str,
        session_id: str,
        error: str,
        outcome: CaptureOutcome | None = None,
    ) -> None:
        self.connection.execute(
            """
            UPDATE jobs SET
                status = 'failed', completed_at = ?, session_id = ?,
                alignment_offset_samples = ?, alignment_score = ?,
                amplitude_delta_db = ?, raw_frames = ?, final_frames = ?,
                callback_status = ?, output_sha256 = ?,
                validation_errors_json = ?, last_error = ?
            WHERE job_id = ?
            """,
            (
                utc_now(),
                session_id,
                outcome.alignment_offset_samples if outcome else None,
                outcome.alignment_score if outcome else None,
                outcome.amplitude_delta_db if outcome else None,
                outcome.raw_frames if outcome else None,
                outcome.final_frames if outcome else None,
                outcome.callback_status if outcome else None,
                outcome.output_sha256 if outcome else None,
                json.dumps(outcome.validation_errors, ensure_ascii=False)
                if outcome
                else None,
                error,
                job_id,
            ),
        )
        self.connection.commit()

    def status_counts(self) -> dict[str, int]:
        return {
            row["status"]: int(row["count"])
            for row in self.connection.execute(
                "SELECT status, COUNT(*) AS count FROM jobs GROUP BY status"
            )
        }


def session_id(condition: str, mode: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{condition}-{mode}-{timestamp}-{os.getpid()}"


def write_failure_diagnostic(
    directory: Path,
    job: dict[str, str],
    error: str,
    *,
    outcome: CaptureOutcome | None = None,
    attempt: int | None = None,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{job['job_id']}.json"
    write_json(
        path,
        {
            "job_id": job["job_id"],
            "source_audio_path": job["source_audio_path"],
            "intended_output_audio_path": job["output_audio_path"],
            "attempt": attempt,
            "error": error,
            "outcome": asdict(outcome) if outcome else None,
            "recorded_at": utc_now(),
        },
    )
    return path


def print_progress(current: int, total: int, job: dict[str, str]) -> None:
    percent = current * 100 / total if total else 100.0
    print(
        f"[{current}/{total} {percent:6.2f}%] "
        f"{job['source_partition']} {job['job_id']}",
        flush=True,
    )


def ensure_python_version() -> None:
    if sys.version_info < (3, 11):
        raise ConfigurationError("Python 3.11 or newer is required.")
