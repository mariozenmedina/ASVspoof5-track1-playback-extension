"""Execute every capture-plan job for one HH/HL/LH/LL condition."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from capture_common import (
    CONDITIONS,
    REPOSITORY_ROOT,
    AudioCaptureSession,
    CaptureLedger,
    ConfigurationError,
    capture_job,
    condition_job_count,
    ensure_python_version,
    iter_condition_jobs,
    list_audio_devices,
    load_acquisition_config,
    load_plan_index,
    print_preflight,
    require_operator_confirmation,
    resolve_and_check_devices,
    session_id,
    sha256_json,
    verify_condition_shards,
    write_failure_diagnostic,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Record every train/development/evaluation job assigned to one "
            "playback condition. Completed jobs resume from a SQLite ledger."
        )
    )
    parser.add_argument("--condition", choices=CONDITIONS)
    parser.add_argument("--config", type=Path)
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List input/output devices and exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Verify plan/config and print counts without opening devices.",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Resolve devices and confirm the chain without recording jobs.",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry jobs that already exhausted the configured attempt limit.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the typed physical-chain confirmation.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=100,
        help="Print progress every N visited manifest jobs.",
    )
    return parser.parse_args()


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
    if args.config is None:
        raise ConfigurationError("The definitive recorder requires --config.")
    if args.progress_every < 1:
        raise ConfigurationError("--progress-every must be positive.")

    condition = args.condition
    index = load_plan_index()
    shards = verify_condition_shards(index, condition)
    total = condition_job_count(index, condition)
    config = load_acquisition_config(
        args.config,
        condition,
        require_devices=not args.dry_run,
    )
    print(
        json.dumps(
            {
                "condition": condition,
                "jobs": total,
                "partitions": {
                    shard["partition"]: shard["row_count"] for shard in shards
                },
                "output_root": "playback_flac",
                "config_sha256": sha256_json(config),
            },
            indent=2,
        )
    )
    if args.dry_run:
        return 0

    resolved = resolve_and_check_devices(config)
    print_preflight(condition, config, resolved)
    if not args.yes:
        require_operator_confirmation(condition)
    if args.preflight_only:
        return 0

    ledger_directory = REPOSITORY_ROOT / "capture-ledgers" / condition
    failure_directory = REPOSITORY_ROOT / "capture-failures" / condition
    current_session = session_id(condition, "definitive")
    write_json(
        ledger_directory / "sessions" / f"{current_session}.json",
        {
            "session_id": current_session,
            "condition": condition,
            "mode": "definitive",
            "config_sha256": sha256_json(config),
            "configuration": config,
            "resolved_devices": resolved,
            "capture_plan_manifest_version": index["manifest_version"],
        },
    )
    maximum_attempts = int(config["execution"]["maximum_attempts_per_job"])
    maximum_consecutive_failures = int(
        config["execution"]["maximum_consecutive_failed_jobs"]
    )
    failures = 0
    consecutive_failures = 0
    completed_now = 0
    skipped = 0
    with (
        CaptureLedger(ledger_directory / "capture-ledger.sqlite3") as ledger,
        AudioCaptureSession(config, resolved) as audio_session,
    ):
        ledger.add_session(current_session, condition, "definitive", config, resolved)
        for visited, job in enumerate(iter_condition_jobs(index, condition), start=1):
            if visited == 1 or visited % args.progress_every == 0 or visited == total:
                print(
                    f"[{visited}/{total}] completed_now={completed_now} "
                    f"skipped={skipped} failures={failures} {job['job_id']}",
                    flush=True,
                )
            output_path = REPOSITORY_ROOT.joinpath(*job["output_audio_path"].split("/"))
            existing = ledger.get(job["job_id"])
            if existing and existing["status"] == "completed" and output_path.is_file():
                skipped += 1
                continue
            existing_attempts = int(existing["attempts"]) if existing else 0
            if (
                existing
                and existing["status"] == "failed"
                and existing_attempts >= maximum_attempts
                and not args.retry_failed
            ):
                failures += 1
                continue

            succeeded = False
            attempts_this_run = 0
            allowed_attempts = (
                maximum_attempts
                if args.retry_failed
                else max(0, maximum_attempts - existing_attempts)
            )
            while attempts_this_run < allowed_attempts:
                attempts_this_run += 1
                attempt = ledger.start_attempt(job, output_path, current_session)
                raw_path = (
                    failure_directory
                    / "raw"
                    / job["source_partition"]
                    / job["content_channel_category"]
                    / f"{job['job_id']}.attempt-{attempt}.wav"
                )
                try:
                    outcome = capture_job(
                        job,
                        config,
                        output_path,
                        audio_session=audio_session,
                        raw_path=raw_path,
                        keep_raw_on_success=False,
                        publish_invalid_final=False,
                    )
                    if not outcome.validation_errors:
                        ledger.complete(outcome, current_session)
                        completed_now += 1
                        consecutive_failures = 0
                        succeeded = True
                        break
                    error = "; ".join(outcome.validation_errors)
                    ledger.fail(job["job_id"], current_session, error, outcome=outcome)
                    write_failure_diagnostic(
                        failure_directory / "diagnostics",
                        job,
                        error,
                        outcome=outcome,
                        attempt=attempt,
                    )
                except KeyboardInterrupt:
                    ledger.fail(job["job_id"], current_session, "interrupted")
                    print("Capture interrupted; progress is preserved in SQLite.")
                    return 130
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    ledger.fail(job["job_id"], current_session, error)
                    write_failure_diagnostic(
                        failure_directory / "diagnostics",
                        job,
                        error,
                        attempt=attempt,
                    )
            if not succeeded:
                failures += 1
                consecutive_failures += 1
                if consecutive_failures >= maximum_consecutive_failures:
                    print(
                        f"Stopping after {consecutive_failures} consecutive "
                        "failed jobs. The ledger preserves progress.",
                        file=sys.stderr,
                    )
                    return 3
        counts = ledger.status_counts()
    print(f"Condition {condition} finished: {counts}")
    return 2 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2)
