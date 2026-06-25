"""Create and execute a small disposable capture pilot for one condition."""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import secrets
import shutil
import stat
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from capture_common import (
    CONDITIONS,
    PARTITIONS,
    REPOSITORY_ROOT,
    AudioCaptureSession,
    CaptureLedger,
    ConfigurationError,
    capture_job,
    condition_shards,
    ensure_python_version,
    list_audio_devices,
    load_acquisition_config,
    load_plan_index,
    print_preflight,
    read_shard_rows,
    require_operator_confirmation,
    resolve_and_check_devices,
    session_id,
    sha256_json,
    verify_condition_shards,
    write_failure_diagnostic,
    write_json,
)


TEST_ROOT = REPOSITORY_ROOT / "capture-tests"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Select a small stratified pilot for HH/HL/LH/LL and optionally "
            "record it outside the definitive playback_flac tree."
        )
    )
    parser.add_argument("--condition", choices=CONDITIONS)
    parser.add_argument("--count", type=int, default=12)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--save-config", type=Path)
    parser.add_argument("--input-device")
    parser.add_argument("--output-device")
    parser.add_argument("--input-host-api")
    parser.add_argument("--output-host-api")
    parser.add_argument("--recording-equipment")
    parser.add_argument("--playback-equipment")
    parser.add_argument("--audio-interface")
    parser.add_argument("--input-channel", type=int)
    parser.add_argument("--input-stream-channels", type=int)
    parser.add_argument("--output-channel", type=int)
    parser.add_argument("--output-stream-channels", type=int)
    parser.add_argument("--capture-rate", type=int)
    parser.add_argument("--output-rate", type=int)
    parser.add_argument("--stream-warmup-ms", type=float)
    parser.add_argument("--distance")
    parser.add_argument("--orientation")
    parser.add_argument("--speaker-volume")
    parser.add_argument("--microphone-gain")
    parser.add_argument(
        "--wasapi-exclusive",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Open Windows WASAPI endpoints in exclusive mode when possible "
            "(default comes from the acquisition config)."
        ),
    )
    parser.add_argument(
        "--never-drop-input",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Request PortAudio's never-drop-input stream flag. Leave disabled "
            "when a device reports PaErrorCode -9995 Invalid flag."
        ),
    )
    parser.add_argument(
        "--windows-audio-enhancements-disabled",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Record whether Windows input enhancements/noise suppression are disabled.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Safely remove the whole capture-tests/<condition> pilot folder.",
    )
    parser.add_argument(
        "--clean-all",
        action="store_true",
        help="Safely remove the whole local capture-tests folder before the pilot.",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Generate the pilot selection without opening audio devices.",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List input/output devices and exit.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the typed physical-chain confirmation.",
    )
    return parser.parse_args()


def _retry_remove_readonly(func: Any, path: str, _exc_info: Any) -> None:
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    func(path)


def _remove_tree(target: Path) -> None:
    if not target.exists():
        return
    shutil.rmtree(target, onerror=_retry_remove_readonly)
    if target.exists():
        leftovers = list(target.rglob("*"))[:8]
        preview = ", ".join(
            str(path.relative_to(target)).replace("\\", "/") for path in leftovers
        )
        raise ConfigurationError(
            f"Cleanup left files under {target}"
            + (f": {preview}" if preview else ".")
        )


def safe_clean_condition(condition: str) -> None:
    root = TEST_ROOT.resolve()
    target = (TEST_ROOT / condition).resolve()
    if target == root or root not in target.parents or target.name not in CONDITIONS:
        raise ConfigurationError(f"Refusing unsafe test cleanup: {target}")
    _remove_tree(target)


def safe_clean_all() -> None:
    repository_root = REPOSITORY_ROOT.resolve()
    root = TEST_ROOT.resolve()
    if root == repository_root or root.parent != repository_root or root.name != "capture-tests":
        raise ConfigurationError(f"Refusing unsafe full test cleanup: {root}")
    _remove_tree(root)


def build_overrides(args: argparse.Namespace) -> dict[str, Any]:
    input_values = {
        "selector": args.input_device,
        "host_api": args.input_host_api,
        "physical_device": args.recording_equipment,
        "audio_interface": args.audio_interface,
        "channel": args.input_channel,
        "stream_channels": args.input_stream_channels,
    }
    output_values = {
        "selector": args.output_device,
        "host_api": args.output_host_api,
        "physical_device": args.playback_equipment,
        "audio_interface": args.audio_interface,
        "channel": args.output_channel,
        "stream_channels": args.output_stream_channels,
    }
    capture_values = {
        "sample_rate_hz": args.capture_rate,
        "output_sample_rate_hz": args.output_rate,
        "stream_warmup_ms": args.stream_warmup_ms,
    }
    if args.wasapi_exclusive is not None:
        capture_values["wasapi_exclusive_mode"] = args.wasapi_exclusive
    if args.never_drop_input is not None:
        capture_values["never_drop_input"] = args.never_drop_input
    setup_values = {
        "speaker_microphone_distance": args.distance,
        "speaker_microphone_orientation": args.orientation,
        "speaker_volume": args.speaker_volume,
        "microphone_gain": args.microphone_gain,
    }
    if args.windows_audio_enhancements_disabled is not None:
        setup_values["windows_audio_enhancements_disabled"] = (
            args.windows_audio_enhancements_disabled
        )

    def compact(values: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in values.items() if value is not None}

    return {
        "devices": {
            "input": compact(input_values),
            "output": compact(output_values),
        },
        "capture": compact(capture_values),
        "fixed_setup": compact(setup_values),
    }


def allocate_quotas(count: int, seed: int) -> dict[tuple[str, str], int]:
    strata = [
        (partition, category) for partition in PARTITIONS for category in ("PH", "PS")
    ]
    rng = random.Random(seed ^ 0xA5_5A_2026)
    rng.shuffle(strata)
    base, remainder = divmod(count, len(strata))
    return {
        stratum: base + (1 if index < remainder else 0)
        for index, stratum in enumerate(strata)
    }


def select_pilot_jobs(
    index: dict[str, Any], condition: str, count: int, seed: int
) -> list[dict[str, str]]:
    if count < 6:
        raise ConfigurationError(
            "Pilot count must be at least 6 to cover PH/PS in all partitions."
        )
    quotas = allocate_quotas(count, seed)
    reservoirs: dict[tuple[str, str], list[dict[str, str]]] = {
        stratum: [] for stratum in quotas
    }
    observed = {stratum: 0 for stratum in quotas}
    rng = random.Random(seed)
    columns = index["file_format"]["columns"]
    for shard in condition_shards(index, condition):
        for row in read_shard_rows(shard, columns):
            stratum = (
                row["source_partition"],
                row["content_channel_category"],
            )
            quota = quotas[stratum]
            if quota == 0:
                continue
            observed[stratum] += 1
            bucket = reservoirs[stratum]
            if len(bucket) < quota:
                bucket.append(row)
            else:
                replacement = rng.randrange(observed[stratum])
                if replacement < quota:
                    bucket[replacement] = row
    selected: list[dict[str, str]] = []
    for stratum, quota in quotas.items():
        if len(reservoirs[stratum]) != quota:
            raise ConfigurationError(
                f"Could not fill pilot stratum {stratum}: "
                f"expected {quota}, got {len(reservoirs[stratum])}"
            )
        selected.extend(reservoirs[stratum])
    rng.shuffle(selected)
    return selected


def write_test_plan(
    path: Path,
    selected: list[dict[str, str]],
    columns: list[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["test_order", *columns],
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        for order, row in enumerate(selected, start=1):
            writer.writerow({"test_order": order, **row})


def append_attempt_result(
    path: Path,
    job: dict[str, str],
    attempt: int,
    *,
    outcome: Any = None,
    error: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "job_id": job["job_id"],
        "attempt": attempt,
        "error": error,
        "outcome": asdict(outcome) if outcome else None,
    }
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def main() -> int:
    ensure_python_version()
    args = parse_args()
    if args.list_devices:
        list_audio_devices()
        return 0
    if not args.condition:
        raise ConfigurationError(
            "--condition is required unless --list-devices is used."
        )
    if args.clean and args.clean_all:
        raise ConfigurationError("Use only one of --clean or --clean-all.")
    condition = args.condition
    test_directory = TEST_ROOT / condition
    if args.clean_all:
        safe_clean_all()
    elif args.clean:
        safe_clean_condition(condition)
    if test_directory.exists() and any(test_directory.iterdir()):
        raise ConfigurationError(
            f"{test_directory} already contains a pilot. Use --clean for a new one."
        )
    test_directory.mkdir(parents=True, exist_ok=True)

    index = load_plan_index()
    verify_condition_shards(index, condition)
    seed = args.seed if args.seed is not None else secrets.randbits(63)
    selected = select_pilot_jobs(index, condition, args.count, seed)
    write_test_plan(
        test_directory / "test-plan.tsv",
        selected,
        list(index["file_format"]["columns"]),
    )
    selection_summary = {
        "condition": condition,
        "seed": seed,
        "requested_jobs": args.count,
        "selected_jobs": len(selected),
        "strata": {
            f"{partition}/{category}": sum(
                1
                for row in selected
                if row["source_partition"] == partition
                and row["content_channel_category"] == category
            )
            for partition in PARTITIONS
            for category in ("PH", "PS")
        },
    }
    write_json(test_directory / "selection.json", selection_summary)
    print(json.dumps(selection_summary, indent=2, ensure_ascii=False))
    if args.plan_only:
        print(f"Pilot plan written to {test_directory / 'test-plan.tsv'}")
        return 0

    config = load_acquisition_config(
        args.config,
        condition,
        overrides=build_overrides(args),
        require_devices=True,
    )
    write_json(test_directory / "acquisition-config.json", config)
    if args.save_config:
        write_json(args.save_config.resolve(), config)
    resolved = resolve_and_check_devices(config)
    print_preflight(condition, config, resolved)
    if not args.yes:
        require_operator_confirmation(condition)

    current_session = session_id(condition, "pilot")
    session_snapshot = {
        "session_id": current_session,
        "condition": condition,
        "mode": "pilot",
        "config_sha256": sha256_json(config),
        "configuration": config,
        "resolved_devices": resolved,
        "selection": selection_summary,
    }
    write_json(test_directory / "session.json", session_snapshot)
    maximum_attempts = int(config["execution"]["maximum_attempts_per_job"])
    results_path = test_directory / "attempt-results.jsonl"
    failures = 0
    with (
        CaptureLedger(test_directory / "pilot-ledger.sqlite3") as ledger,
        AudioCaptureSession(config, resolved) as audio_session,
    ):
        ledger.add_session(current_session, condition, "pilot", config, resolved)
        for order, job in enumerate(selected, start=1):
            print(f"[{order}/{len(selected)}] {job['job_id']}", flush=True)
            completed = False
            for attempt in range(1, maximum_attempts + 1):
                output_path = (
                    test_directory
                    / "recordings"
                    / job["source_partition"]
                    / job["content_channel_category"]
                    / job["output_file_name"]
                )
                raw_path = (
                    test_directory
                    / "raw"
                    / job["source_partition"]
                    / job["content_channel_category"]
                    / f"{job['job_id']}.attempt-{attempt}.wav"
                )
                ledger.start_attempt(job, output_path, current_session)
                try:
                    outcome = capture_job(
                        job,
                        config,
                        output_path,
                        audio_session=audio_session,
                        raw_path=raw_path,
                        keep_raw_on_success=True,
                        publish_invalid_final=True,
                    )
                    append_attempt_result(results_path, job, attempt, outcome=outcome)
                    if not outcome.validation_errors:
                        ledger.complete(outcome, current_session)
                        completed = True
                        break
                    error = "; ".join(outcome.validation_errors)
                    ledger.fail(job["job_id"], current_session, error, outcome=outcome)
                    write_failure_diagnostic(
                        test_directory / "diagnostics",
                        job,
                        error,
                        outcome=outcome,
                        attempt=attempt,
                    )
                except KeyboardInterrupt:
                    ledger.fail(job["job_id"], current_session, "interrupted")
                    raise
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    append_attempt_result(results_path, job, attempt, error=error)
                    ledger.fail(job["job_id"], current_session, error)
                    write_failure_diagnostic(
                        test_directory / "diagnostics",
                        job,
                        error,
                        attempt=attempt,
                    )
            if not completed:
                failures += 1
        counts = ledger.status_counts()
    write_json(
        test_directory / "summary.json",
        {"condition": condition, "status_counts": counts, "failures": failures},
    )
    print(f"Pilot completed: {counts}")
    return 2 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2)
