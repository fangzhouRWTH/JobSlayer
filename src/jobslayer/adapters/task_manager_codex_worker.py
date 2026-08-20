"""Trusted detached worker used by the durable TaskManager Codex adapter."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
from typing import Any

from jobslayer.adapters.codex_common import codex_environment


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    content = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _claim(directory: Path) -> bool:
    lock_path = directory / "worker-claim.lock"
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "wb") as stream:
        stream.flush()
        os.fsync(stream.fileno())
    _atomic_json(
        directory / "worker-claim.json",
        {
            "schema_version": "1.0",
            "worker_pid": os.getpid(),
            "claimed_at": datetime.now(UTC).isoformat(),
        },
    )
    return True


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except OSError:
            pass


def _inspect_events(path: Path) -> tuple[bool, str | None, str | None, dict[str, int]]:
    protocol_failed = False
    error_summary = None
    final_message = None
    usage: dict[str, int] = {}
    if not path.exists():
        return True, "Codex produced no JSONL event log", None, usage
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            protocol_failed = True
            error_summary = "Codex emitted non-JSON output in --json mode"
            continue
        event_type = event.get("type")
        if event_type in {"error", "turn.failed"}:
            protocol_failed = True
            error_summary = str(event.get("message") or event.get("error") or "Codex failed")
        if event_type == "turn.completed" and isinstance(event.get("usage"), dict):
            usage = {
                str(key): value
                for key, value in event["usage"].items()
                if isinstance(value, int) and not isinstance(value, bool)
            }
        item = event.get("item")
        if (
            event_type == "item.completed"
            and isinstance(item, dict)
            and item.get("type") == "agent_message"
            and isinstance(item.get("text"), str)
        ):
            final_message = item["text"]
    return protocol_failed, error_summary, final_message, usage


def run(state_directory: Path) -> int:
    directory = state_directory.resolve(strict=True)
    if not _claim(directory):
        return 0
    started_at = datetime.now(UTC)
    terminal_path = directory / "terminal.json"
    process: subprocess.Popen[bytes] | None = None
    try:
        launch = json.loads((directory / "launch.json").read_text(encoding="utf-8"))
        argv = launch["argv"]
        cwd = Path(launch["cwd"]).resolve(strict=True)
        timeout_seconds = int(launch["timeout_seconds"])
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(item, str) or not item or "\x00" in item for item in argv)
            or timeout_seconds <= 0
        ):
            raise ValueError("invalid durable launch envelope")
        with (
            (directory / "prompt.txt").open("rb") as prompt,
            (directory / "codex-events.jsonl").open("wb") as events,
            (directory / "codex-stderr.log").open("wb") as stderr,
        ):
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=codex_environment(),
                stdin=prompt,
                stdout=events,
                stderr=stderr,
                start_new_session=os.name != "nt",
            )
            try:
                exit_code = process.wait(timeout=timeout_seconds)
                timed_out = False
            except subprocess.TimeoutExpired:
                timed_out = True
                _terminate(process)
                exit_code = process.returncode
        protocol_failed, protocol_error, final_message, usage = _inspect_events(
            directory / "codex-events.jsonl"
        )
        succeeded = exit_code == 0 and not timed_out and not protocol_failed
        error_summary = None
        if timed_out:
            error_summary = f"Codex exceeded the {timeout_seconds}-second task-node limit"
        elif protocol_error:
            error_summary = protocol_error
        elif exit_code != 0:
            error_summary = f"Codex exited with status {exit_code}"
        _atomic_json(
            terminal_path,
            {
                "schema_version": "1.0",
                "status": "succeeded" if succeeded else "failed",
                "exit_code": exit_code,
                "timed_out": timed_out,
                "protocol_failed": protocol_failed,
                "error_summary": error_summary,
                "final_message": final_message,
                "usage": usage,
                "started_at": started_at.isoformat(),
                "finished_at": datetime.now(UTC).isoformat(),
            },
        )
        return 0
    except Exception as exc:
        if process is not None:
            _terminate(process)
        _atomic_json(
            terminal_path,
            {
                "schema_version": "1.0",
                "status": "failed",
                "exit_code": None,
                "timed_out": False,
                "protocol_failed": False,
                "error_summary": f"durable Codex worker failed: {type(exc).__name__}: {exc}",
                "final_message": None,
                "usage": {},
                "started_at": started_at.isoformat(),
                "finished_at": datetime.now(UTC).isoformat(),
            },
        )
        return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True)
    arguments = parser.parse_args()
    return run(Path(arguments.state_dir))


if __name__ == "__main__":
    raise SystemExit(main())
