"""Task-independent Codex app-server adapter for the local Quick Agent UI."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
import subprocess
import threading
import time
from typing import Any, Sequence
from uuid import uuid4

from jobslayer.adapters.codex_common import (
    CodexCommandConfigurationError,
    codex_environment,
    normalize_codex_command,
)
from jobslayer.execution.processes import (
    ProcessGroupTerminationError,
    ProcessSupervisor,
    native_process_supervisor,
)
from jobslayer.quick_agent import (
    QuickAgentBusyError,
    QuickAgentCapacitySnapshot,
    QuickAgentEvent,
    QuickAgentMode,
    QuickAgentModelCatalogSnapshot,
    QuickAgentModelOption,
    QuickAgentRateLimitBucket,
    QuickAgentRateLimitWindow,
    QuickAgentReasoningEffortOption,
    QuickAgentSessionSnapshot,
    QuickAgentServiceTierOption,
    QuickAgentState,
    QuickAgentUnavailableError,
)


@dataclass
class _PendingResponse:
    event: threading.Event = field(default_factory=threading.Event)
    response: dict[str, Any] | None = None


class CodexQuickAgent:
    """Embed the documented Codex app-server core protocol behind a small surface.

    This adapter deliberately excludes app-server's unsandboxed process APIs and
    never auto-approves a server-initiated permission request. Each turn is either
    repository-scoped read-only discussion or repository-scoped workspace-write
    execution, selected explicitly by the caller.
    """

    adapter_id = "codex-app-server-quick-agent-v1"
    _MAXIMUM_PROMPT_CHARACTERS = 16_000
    _MAXIMUM_EVENT_CHARACTERS = 24_000
    _MAXIMUM_EVENTS = 1_000
    _CAPACITY_CACHE_SECONDS = 30
    _MODEL_CACHE_SECONDS = 300
    _REQUEST_TIMEOUT_SECONDS = 15.0

    def __init__(
        self,
        repository_root: str | Path,
        state_root: str | Path,
        *,
        codex_binary: str | os.PathLike[str] | Sequence[str] = "codex",
        model: str = "gpt-5.6-sol",
        reasoning_effort: str = "xhigh",
        maximum_turn_seconds: int = 1_800,
        process_supervisor: ProcessSupervisor | None = None,
    ):
        self.repository_root = Path(repository_root).resolve(strict=True)
        if not self.repository_root.is_dir():
            raise QuickAgentUnavailableError("Quick Agent workspace is not a directory")
        unresolved_state_root = Path(state_root)
        if unresolved_state_root.exists() and unresolved_state_root.is_symlink():
            raise QuickAgentUnavailableError("Quick Agent state root must not be a symlink")
        self.state_root = unresolved_state_root.resolve(strict=False)
        self.state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not model.strip() or len(model) > 120:
            raise ValueError("Quick Agent model must be a bounded non-blank string")
        if not reasoning_effort.strip() or len(reasoning_effort) > 40:
            raise ValueError("unsupported Quick Agent reasoning effort")
        if not 30 <= maximum_turn_seconds <= 7_200:
            raise ValueError("Quick Agent turn timeout must be between 30 and 7200 seconds")
        try:
            self.codex_command = normalize_codex_command(codex_binary)
        except CodexCommandConfigurationError as exc:
            raise QuickAgentUnavailableError(str(exc)) from exc
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.service_tier: str | None = None
        self.maximum_turn_seconds = maximum_turn_seconds
        self.process_supervisor = process_supervisor or native_process_supervisor()

        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._turn_lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._ready = False
        self._closed = False
        self._next_request_id = 1
        self._pending: dict[int, _PendingResponse] = {}
        self._loaded_thread_id: str | None = None
        self._raw_log_path: Path | None = None
        self._stderr_log_path: Path | None = None

        self._conversation_id = f"quick-{uuid4().hex}"
        self._thread_id: str | None = None
        self._active_turn_id: str | None = None
        self._state = QuickAgentState.IDLE
        self._events: list[dict[str, Any]] = []
        self._stream_events: dict[tuple[str, str], int] = {}
        self._next_sequence = 1
        self._usage: dict[str, int] = {}
        self._last_error: str | None = None
        self._current_mode: QuickAgentMode | None = None
        self._turn_done: threading.Event | None = None
        self._user_cancel_requested = False
        self._timeout_requested = False
        self._updated_at = datetime.now(UTC)
        self._capacity_cache: QuickAgentCapacitySnapshot | None = None
        self._capacity_cached_at = 0.0
        self._model_cache: QuickAgentModelCatalogSnapshot | None = None
        self._model_cached_at = 0.0

    def capacity(self, *, force_refresh: bool = False) -> QuickAgentCapacitySnapshot:
        with self._lock:
            cached = self._capacity_cache
            fresh = time.monotonic() - self._capacity_cached_at < self._CAPACITY_CACHE_SECONDS
            if cached is not None and fresh and not force_refresh:
                return cached
        observed_at = datetime.now(UTC)
        try:
            response = self._request("account/rateLimits/read", {})
            snapshot = self._normalize_capacity(response, observed_at=observed_at)
        except (
            QuickAgentUnavailableError,
            ValueError,
            TypeError,
            OverflowError,
            OSError,
        ) as exc:
            snapshot = QuickAgentCapacitySnapshot(
                available=False,
                source="codex_app_server.account/rateLimits/read",
                observed_at=observed_at,
                refresh_after_seconds=self._CAPACITY_CACHE_SECONDS,
                error=str(exc),
            )
        with self._lock:
            self._capacity_cache = snapshot
            self._capacity_cached_at = time.monotonic()
        return snapshot

    def snapshot(self) -> QuickAgentSessionSnapshot:
        with self._lock:
            events = tuple(QuickAgentEvent.model_validate(item) for item in self._events)
            return QuickAgentSessionSnapshot(
                adapter_id=self.adapter_id,
                conversation_id=self._conversation_id,
                thread_id=self._thread_id,
                active_turn_id=self._active_turn_id,
                state=self._state,
                model=self.model,
                reasoning_effort=self.reasoning_effort,
                service_tier=self.service_tier,
                workspace_root=str(self.repository_root),
                maximum_turn_seconds=self.maximum_turn_seconds,
                events=events,
                usage=dict(self._usage),
                last_error=self._last_error,
                updated_at=self._updated_at,
            )

    def models(self, *, force_refresh: bool = False) -> QuickAgentModelCatalogSnapshot:
        with self._lock:
            cached = self._model_cache
            fresh = time.monotonic() - self._model_cached_at < self._MODEL_CACHE_SECONDS
            if cached is not None and fresh and not force_refresh:
                return cached
        observed_at = datetime.now(UTC)
        try:
            response = self._request(
                "model/list",
                {"limit": 100, "includeHidden": False},
            )
            snapshot = self._normalize_models(response, observed_at=observed_at)
        except (
            QuickAgentUnavailableError,
            ValueError,
            TypeError,
            OverflowError,
            OSError,
        ) as exc:
            snapshot = QuickAgentModelCatalogSnapshot(
                available=False,
                source="codex_app_server.model/list",
                observed_at=observed_at,
                refresh_after_seconds=self._MODEL_CACHE_SECONDS,
                error=str(exc),
            )
        with self._lock:
            self._model_cache = snapshot
            self._model_cached_at = time.monotonic()
        return snapshot

    def start_turn(
        self,
        content: str,
        *,
        mode: QuickAgentMode,
        model: str | None = None,
        reasoning_effort: str | None = None,
        service_tier: str | None = None,
    ) -> QuickAgentSessionSnapshot:
        prompt = content.strip()
        if not prompt or len(prompt) > self._MAXIMUM_PROMPT_CHARACTERS or "\x00" in prompt:
            raise ValueError(
                "Quick Agent content must be 1-16000 characters without NUL bytes"
            )
        with self._turn_lock:
            with self._lock:
                if self._state is QuickAgentState.RUNNING:
                    raise QuickAgentBusyError("Quick Agent already has an active turn")
            self._ensure_process()
            selected_model = model or self.model
            selected_effort = reasoning_effort or self.reasoning_effort
            self._validate_configuration(
                model=selected_model,
                reasoning_effort=selected_effort,
                service_tier=service_tier,
            )
            with self._lock:
                self.model = selected_model
                self.reasoning_effort = selected_effort
                self.service_tier = service_tier
            thread_id = self._ensure_thread(mode)
            done = threading.Event()
            with self._lock:
                self._state = QuickAgentState.RUNNING
                self._active_turn_id = None
                self._current_mode = mode
                self._last_error = None
                self._user_cancel_requested = False
                self._timeout_requested = False
                self._turn_done = done
                self._stream_events.clear()
                self._append_event_locked(
                    event_type="user.message",
                    role="user",
                    content=prompt,
                    mode=mode,
                    status="submitted",
                )
            try:
                response = self._request(
                    "turn/start",
                    {
                        "threadId": thread_id,
                        "input": [{"type": "text", "text": prompt}],
                        "cwd": str(self.repository_root),
                        "approvalPolicy": "never",
                        "sandboxPolicy": self._sandbox_policy(mode),
                        "model": self.model,
                        "effort": self.reasoning_effort,
                        "serviceTier": self.service_tier,
                        "summary": "concise",
                    },
                )
                turn = response.get("turn")
                turn_id = turn.get("id") if isinstance(turn, dict) else None
                if not isinstance(turn_id, str) or not turn_id:
                    raise QuickAgentUnavailableError(
                        "Codex app-server returned no turn identifier"
                    )
                with self._lock:
                    if self._state is QuickAgentState.RUNNING:
                        self._active_turn_id = turn_id
                        self._touch_locked()
            except Exception as exc:
                error = str(exc)
                with self._lock:
                    self._state = QuickAgentState.FAILED
                    self._last_error = error
                    self._active_turn_id = None
                    self._append_event_locked(
                        event_type="turn.failed",
                        role="system",
                        content=error,
                        mode=mode,
                        status="failed",
                    )
                    done.set()
                if isinstance(exc, (QuickAgentUnavailableError, ValueError)):
                    raise
                raise QuickAgentUnavailableError(error) from exc
            watchdog = threading.Thread(
                target=self._watch_turn,
                args=(thread_id, turn_id, done),
                name=f"quick-agent-{turn_id}-timeout",
                daemon=True,
            )
            watchdog.start()
            return self.snapshot()

    def cancel(self) -> QuickAgentSessionSnapshot:
        with self._lock:
            if self._state is not QuickAgentState.RUNNING:
                return self.snapshot()
            thread_id = self._thread_id
            turn_id = self._active_turn_id
            if not thread_id or not turn_id:
                raise QuickAgentBusyError("Quick Agent turn is still starting")
            self._user_cancel_requested = True
            mode = self._current_mode
            self._append_event_locked(
                event_type="turn.cancel.requested",
                role="system",
                content="已请求中断当前 Codex turn。",
                mode=mode,
                status="interrupting",
            )
        self._request(
            "turn/interrupt",
            {"threadId": thread_id, "turnId": turn_id},
        )
        return self.snapshot()

    def new_session(self) -> QuickAgentSessionSnapshot:
        with self._turn_lock:
            with self._lock:
                if self._state is QuickAgentState.RUNNING:
                    raise QuickAgentBusyError(
                        "cannot start a new Quick Agent session during an active turn"
                    )
                self._conversation_id = f"quick-{uuid4().hex}"
                self._thread_id = None
                self._loaded_thread_id = None
                self._active_turn_id = None
                self._state = QuickAgentState.IDLE
                self._events.clear()
                self._stream_events.clear()
                self._next_sequence = 1
                self._usage.clear()
                self._last_error = None
                self._current_mode = None
                self._turn_done = None
                self._user_cancel_requested = False
                self._timeout_requested = False
                self._touch_locked()
        return self.snapshot()

    def close(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                if self._closed:
                    return
                self._closed = True
                process = self._process
                reader = self._reader_thread
                stderr_reader = self._stderr_thread
                self._ready = False
            if process is not None:
                if process.stdin is not None and not process.stdin.closed:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass
                if process.poll() is None:
                    try:
                        self.process_supervisor.terminate(process, timeout_seconds=2.0)
                    except ProcessGroupTerminationError:
                        pass
                for thread in (reader, stderr_reader):
                    if thread is not None and thread is not threading.current_thread():
                        thread.join(timeout=2.0)
                for stream in (process.stdout, process.stderr):
                    if stream is not None and not stream.closed:
                        stream.close()

    def _ensure_process(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                if self._closed:
                    raise QuickAgentUnavailableError("Quick Agent adapter is closed")
                if (
                    self._process is not None
                    and self._process.poll() is None
                    and self._ready
                ):
                    return
            run_directory = self.state_root / (
                datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + f"-{uuid4().hex}"
            )
            run_directory.mkdir(mode=0o700)
            raw_path = run_directory / "app-server-events.jsonl"
            stderr_path = run_directory / "app-server-stderr.log"
            raw_path.touch(mode=0o600)
            stderr_path.touch(mode=0o600)
            command = [*self.codex_command, "app-server", "--stdio"]
            try:
                process = subprocess.Popen(
                    command,
                    cwd=self.repository_root,
                    env=codex_environment(),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    **self.process_supervisor.popen_kwargs(),
                )
            except OSError as exc:
                raise QuickAgentUnavailableError(
                    "could not launch the local Codex app-server"
                ) from exc
            with self._lock:
                self._process = process
                self._ready = False
                self._loaded_thread_id = None
                self._raw_log_path = raw_path
                self._stderr_log_path = stderr_path
            reader = threading.Thread(
                target=self._consume_stdout,
                args=(process, raw_path),
                name="quick-agent-app-server-stdout",
                daemon=True,
            )
            stderr_reader = threading.Thread(
                target=self._consume_stderr,
                args=(process, stderr_path),
                name="quick-agent-app-server-stderr",
                daemon=True,
            )
            self._reader_thread = reader
            self._stderr_thread = stderr_reader
            reader.start()
            stderr_reader.start()
            try:
                self._request_current(
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "jobslayer_quick_agent",
                            "title": "JobSlayer Quick Agent",
                            "version": "0.1.0",
                        }
                    },
                )
                self._send_message({"method": "initialized", "params": {}})
            except Exception:
                try:
                    self.process_supervisor.terminate(process, timeout_seconds=1.0)
                except ProcessGroupTerminationError:
                    pass
                raise
            with self._lock:
                self._ready = True

    def _ensure_thread(self, mode: QuickAgentMode) -> str:
        with self._lock:
            thread_id = self._thread_id
            loaded = self._loaded_thread_id
        if thread_id is not None and loaded != thread_id:
            response = self._request(
                "thread/resume",
                {
                    "threadId": thread_id,
                    "model": self.model,
                    "cwd": str(self.repository_root),
                    "approvalPolicy": "never",
                    "sandbox": "read-only" if mode is QuickAgentMode.DISCUSS else "workspace-write",
                    "serviceTier": self.service_tier,
                },
            )
            thread = response.get("thread")
            resumed_id = thread.get("id") if isinstance(thread, dict) else None
            if resumed_id != thread_id:
                raise QuickAgentUnavailableError("Codex resumed a different thread")
            with self._lock:
                self._loaded_thread_id = thread_id
            return thread_id
        if thread_id is not None:
            return thread_id
        response = self._request(
            "thread/start",
            {
                "model": self.model,
                "cwd": str(self.repository_root),
                "approvalPolicy": "never",
                "sandbox": "read-only" if mode is QuickAgentMode.DISCUSS else "workspace-write",
                "serviceTier": self.service_tier,
                "serviceName": "jobslayer_quick_agent",
            },
        )
        thread = response.get("thread")
        created_id = thread.get("id") if isinstance(thread, dict) else None
        if not isinstance(created_id, str) or not created_id:
            raise QuickAgentUnavailableError("Codex app-server returned no thread identifier")
        with self._lock:
            self._thread_id = created_id
            self._loaded_thread_id = created_id
            self._touch_locked()
        return created_id

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._ensure_process()
        return self._request_current(method, params)

    def _request_current(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            request_id = self._next_request_id
            self._next_request_id += 1
            pending = _PendingResponse()
            self._pending[request_id] = pending
        try:
            self._send_message({"method": method, "id": request_id, "params": params})
        except Exception:
            with self._lock:
                self._pending.pop(request_id, None)
            raise
        if not pending.event.wait(self._REQUEST_TIMEOUT_SECONDS):
            with self._lock:
                self._pending.pop(request_id, None)
            raise QuickAgentUnavailableError(
                f"Codex app-server did not answer {method} within the local timeout"
            )
        response = pending.response or {}
        error = response.get("error")
        if error is not None:
            if isinstance(error, dict):
                message = str(error.get("message") or error)
            else:
                message = str(error)
            raise QuickAgentUnavailableError(f"Codex {method} failed: {message}")
        result = response.get("result")
        if not isinstance(result, dict):
            raise QuickAgentUnavailableError(
                f"Codex {method} returned an invalid result"
            )
        return result

    def _send_message(self, payload: dict[str, Any]) -> None:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._write_lock:
            with self._lock:
                process = self._process
            if process is None or process.poll() is not None or process.stdin is None:
                raise QuickAgentUnavailableError("Codex app-server is not running")
            try:
                process.stdin.write(encoded + "\n")
                process.stdin.flush()
            except OSError as exc:
                raise QuickAgentUnavailableError(
                    "could not send a request to Codex app-server"
                ) from exc

    def _consume_stdout(self, process: subprocess.Popen[str], path: Path) -> None:
        assert process.stdout is not None
        try:
            with path.open("a", encoding="utf-8") as raw_log:
                for line in process.stdout:
                    raw_log.write(line)
                    raw_log.flush()
                    try:
                        message = json.loads(line)
                    except json.JSONDecodeError:
                        self._record_protocol_error(
                            "Codex app-server emitted non-JSON protocol output"
                        )
                        continue
                    if not isinstance(message, dict):
                        self._record_protocol_error(
                            "Codex app-server emitted a non-object protocol message"
                        )
                        continue
                    if "method" not in message and "id" in message:
                        self._resolve_response(message)
                    elif "method" in message and "id" in message:
                        self._reject_server_request(message)
                    else:
                        self._handle_notification(message)
        finally:
            process.stdout.close()
            self._process_ended(process)

    def _consume_stderr(self, process: subprocess.Popen[str], path: Path) -> None:
        assert process.stderr is not None
        with path.open("a", encoding="utf-8") as stderr_log:
            for chunk in iter(lambda: process.stderr.read(65_536), ""):
                stderr_log.write(chunk)
                stderr_log.flush()
        process.stderr.close()

    def _resolve_response(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        if not isinstance(request_id, int) or isinstance(request_id, bool):
            return
        with self._lock:
            pending = self._pending.pop(request_id, None)
        if pending is not None:
            pending.response = message
            pending.event.set()

    def _reject_server_request(self, message: dict[str, Any]) -> None:
        method = str(message.get("method", "unknown"))
        request_id = message.get("id")
        with self._lock:
            self._append_event_locked(
                event_type="permission.declined",
                role="system",
                content=f"未自动批准 Codex 请求：{method}",
                mode=self._current_mode,
                status="declined",
            )
        try:
            self._send_message(
                {
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": "JobSlayer Quick Agent does not auto-approve server requests",
                    },
                }
            )
        except QuickAgentUnavailableError:
            return

    def _handle_notification(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        params = message.get("params")
        if not isinstance(method, str) or not isinstance(params, dict):
            return
        if method == "account/rateLimits/updated":
            with self._lock:
                self._capacity_cache = None
                self._capacity_cached_at = 0.0
            return
        if method == "turn/started":
            turn = params.get("turn")
            turn_id = turn.get("id") if isinstance(turn, dict) else None
            if isinstance(turn_id, str) and turn_id:
                with self._lock:
                    if self._state is QuickAgentState.RUNNING:
                        self._active_turn_id = turn_id
                        self._touch_locked()
            return
        if method == "turn/completed":
            self._complete_turn(params)
            return
        if method == "error":
            error = params.get("error")
            if isinstance(error, dict):
                error = error.get("message") or error
            self._record_turn_error(str(error or "Codex turn failed"))
            return
        if method in {"warning", "configWarning"}:
            content = params.get("message") or params.get("summary")
            if isinstance(content, str) and content:
                with self._lock:
                    self._append_event_locked(
                        event_type="codex.warning",
                        role="system",
                        content=content,
                        mode=self._current_mode,
                        status="warning",
                    )
            return
        if method == "thread/tokenUsage/updated":
            self._update_usage(params)
            return
        if method == "item/agentMessage/delta":
            self._append_stream_delta("agent", params, role="agent")
            return
        if method == "item/commandExecution/outputDelta":
            self._append_stream_delta("command", params, role="tool")
            return
        if method == "item/reasoning/summaryTextDelta":
            self._append_stream_delta("progress", params, role="system")
            return
        if method in {"item/started", "item/completed"}:
            item = params.get("item")
            if isinstance(item, dict):
                self._handle_item(item, completed=method.endswith("completed"))

    def _handle_item(self, item: dict[str, Any], *, completed: bool) -> None:
        item_type = str(item.get("type", "unknown"))
        item_id = str(item.get("id", "unknown"))
        status = str(item.get("status") or ("completed" if completed else "running"))
        if item_type == "agentMessage" and completed:
            text = item.get("text")
            if isinstance(text, str):
                self._set_stream_content("agent", item_id, text, role="agent", status=status)
            return
        if item_type == "commandExecution":
            command = item.get("command")
            command_text = command if isinstance(command, str) else "command"
            output = item.get("aggregatedOutput")
            content = f"$ {command_text}"
            if completed and isinstance(output, str) and output:
                content += "\n" + output
            self._set_stream_content(
                "command", item_id, content, role="tool", status=status
            )
            return
        if item_type == "fileChange":
            changes = item.get("changes")
            paths = []
            if isinstance(changes, list):
                paths = [
                    str(change.get("path"))
                    for change in changes
                    if isinstance(change, dict) and change.get("path")
                ]
            content = ("文件变更：" + ", ".join(paths)) if paths else "文件变更"
            self._set_stream_content(
                "file", item_id, content, role="tool", status=status
            )
            return
        if item_type in {"webSearch", "mcpToolCall", "dynamicToolCall", "collabToolCall"}:
            label = item.get("query") or item.get("tool") or item_type
            self._set_stream_content(
                "tool", item_id, str(label), role="tool", status=status
            )
            return
        if item_type == "contextCompaction" and completed:
            with self._lock:
                self._append_event_locked(
                    event_type="context.compacted",
                    role="system",
                    content="Codex 已压缩本会话上下文。",
                    mode=self._current_mode,
                    status="completed",
                )

    def _append_stream_delta(
        self,
        kind: str,
        params: dict[str, Any],
        *,
        role: str,
    ) -> None:
        item_id = params.get("itemId")
        delta = params.get("delta")
        if not isinstance(item_id, str) or not isinstance(delta, str) or not delta:
            return
        with self._lock:
            index = self._stream_events.get((kind, item_id))
            if index is None:
                self._append_event_locked(
                    event_type=f"{kind}.stream",
                    role=role,
                    content=delta,
                    mode=self._current_mode,
                    status="streaming",
                )
                self._stream_events[(kind, item_id)] = len(self._events) - 1
            else:
                event = self._events[index]
                event["content"] = self._bounded_text(event["content"] + delta)
                event["status"] = "streaming"
                self._touch_locked()

    def _set_stream_content(
        self,
        kind: str,
        item_id: str,
        content: str,
        *,
        role: str,
        status: str,
    ) -> None:
        with self._lock:
            index = self._stream_events.get((kind, item_id))
            if index is None:
                running = status in {"running", "inProgress", "in_progress"}
                self._append_event_locked(
                    event_type=f"{kind}.started" if running else f"{kind}.completed",
                    role=role,
                    content=content,
                    mode=self._current_mode,
                    status=status,
                )
                self._stream_events[(kind, item_id)] = len(self._events) - 1
            else:
                event = self._events[index]
                event["content"] = self._bounded_text(content)
                event["status"] = status
                running = status in {"running", "inProgress", "in_progress"}
                event["event_type"] = (
                    f"{kind}.started" if running else f"{kind}.completed"
                )
                self._touch_locked()

    def _complete_turn(self, params: dict[str, Any]) -> None:
        turn = params.get("turn")
        if not isinstance(turn, dict):
            self._record_turn_error("Codex emitted an invalid turn completion")
            return
        status = str(turn.get("status", "failed"))
        error = turn.get("error")
        if isinstance(error, dict):
            error = error.get("message") or error
        with self._lock:
            if self._timeout_requested:
                state = QuickAgentState.TIMED_OUT
                content = f"Codex turn 超过 {self.maximum_turn_seconds} 秒，已中断。"
            elif self._user_cancel_requested or status == "interrupted":
                state = QuickAgentState.CANCELLED
                content = "Codex turn 已中断。"
            elif status == "completed":
                state = QuickAgentState.COMPLETED
                content = "Codex turn 已完成。"
            else:
                state = QuickAgentState.FAILED
                content = str(error or "Codex turn failed")
                self._last_error = content
            self._state = state
            self._active_turn_id = None
            self._append_event_locked(
                event_type="turn.completed" if state is QuickAgentState.COMPLETED else f"turn.{state.value}",
                role="system",
                content=content,
                mode=self._current_mode,
                status=state.value,
            )
            if self._turn_done is not None:
                self._turn_done.set()

    def _record_turn_error(self, message: str) -> None:
        with self._lock:
            self._last_error = self._bounded_text(message, maximum=2_000)
            self._append_event_locked(
                event_type="turn.error",
                role="system",
                content=self._last_error,
                mode=self._current_mode,
                status="error",
            )

    def _record_protocol_error(self, message: str) -> None:
        with self._lock:
            self._last_error = message
            if self._state is QuickAgentState.RUNNING:
                self._state = QuickAgentState.FAILED
                self._active_turn_id = None
                self._append_event_locked(
                    event_type="protocol.error",
                    role="system",
                    content=message,
                    mode=self._current_mode,
                    status="failed",
                )
                if self._turn_done is not None:
                    self._turn_done.set()

    def _update_usage(self, params: dict[str, Any]) -> None:
        token_usage = params.get("tokenUsage")
        total = token_usage.get("total") if isinstance(token_usage, dict) else None
        if not isinstance(total, dict):
            return
        mapping = {
            "inputTokens": "input_tokens",
            "cachedInputTokens": "cached_input_tokens",
            "outputTokens": "output_tokens",
            "reasoningOutputTokens": "reasoning_output_tokens",
            "totalTokens": "total_tokens",
        }
        usage = {
            target: value
            for source, target in mapping.items()
            if isinstance((value := total.get(source)), int) and not isinstance(value, bool)
        }
        with self._lock:
            self._usage = usage
            self._touch_locked()

    def _watch_turn(
        self,
        thread_id: str,
        turn_id: str,
        done: threading.Event,
    ) -> None:
        if done.wait(self.maximum_turn_seconds):
            return
        with self._lock:
            if self._active_turn_id != turn_id or self._state is not QuickAgentState.RUNNING:
                return
            self._timeout_requested = True
            self._append_event_locked(
                event_type="turn.timeout.requested",
                role="system",
                content=f"达到本地 {self.maximum_turn_seconds} 秒上限，正在中断。",
                mode=self._current_mode,
                status="interrupting",
            )
        try:
            self._request(
                "turn/interrupt",
                {"threadId": thread_id, "turnId": turn_id},
            )
        except QuickAgentUnavailableError as exc:
            self._record_turn_error(str(exc))

    def _process_ended(self, process: subprocess.Popen[str]) -> None:
        with self._lock:
            if self._process is not process:
                return
            self._ready = False
            self._loaded_thread_id = None
            pending = tuple(self._pending.values())
            self._pending.clear()
            if not self._closed and self._state is QuickAgentState.RUNNING:
                self._state = QuickAgentState.FAILED
                self._active_turn_id = None
                self._last_error = "Codex app-server exited during the active turn"
                self._append_event_locked(
                    event_type="provider.exited",
                    role="system",
                    content=self._last_error,
                    mode=self._current_mode,
                    status="failed",
                )
                if self._turn_done is not None:
                    self._turn_done.set()
        for waiter in pending:
            waiter.response = {
                "error": {"message": "Codex app-server exited before responding"}
            }
            waiter.event.set()

    def _append_event_locked(
        self,
        *,
        event_type: str,
        role: str,
        content: str,
        mode: QuickAgentMode | None,
        status: str | None,
    ) -> None:
        if len(self._events) >= self._MAXIMUM_EVENTS:
            del self._events[0]
            self._stream_events = {
                key: index - 1
                for key, index in self._stream_events.items()
                if index > 0
            }
        self._events.append(
            {
                "schema_version": "1.0",
                "sequence": self._next_sequence,
                "event_type": event_type,
                "role": role,
                "content": self._bounded_text(content),
                "created_at": datetime.now(UTC),
                "mode": mode,
                "status": status,
            }
        )
        self._next_sequence += 1
        self._touch_locked()

    def _touch_locked(self) -> None:
        self._updated_at = datetime.now(UTC)

    def _sandbox_policy(self, mode: QuickAgentMode) -> dict[str, Any]:
        if mode is QuickAgentMode.DISCUSS:
            return {"type": "readOnly", "networkAccess": False}
        return {
            "type": "workspaceWrite",
            "writableRoots": [str(self.repository_root)],
            "networkAccess": False,
        }

    def _validate_configuration(
        self,
        *,
        model: str,
        reasoning_effort: str,
        service_tier: str | None,
    ) -> None:
        catalog = self.models()
        if not catalog.available:
            raise QuickAgentUnavailableError(
                catalog.error or "Codex model catalog is unavailable"
            )
        selected = next(
            (item for item in catalog.models if item.model_id == model),
            None,
        )
        if selected is None:
            raise ValueError(f"Quick Agent model is not currently available: {model}")
        supported_efforts = {item.effort for item in selected.reasoning_efforts}
        if reasoning_effort not in supported_efforts:
            raise ValueError(
                f"{model} does not advertise reasoning effort {reasoning_effort}"
            )
        supported_tiers = {item.tier_id for item in selected.service_tiers}
        if service_tier is not None and service_tier not in supported_tiers:
            raise ValueError(
                f"{model} does not advertise service tier {service_tier}"
            )

    @classmethod
    def _bounded_text(cls, value: str, *, maximum: int | None = None) -> str:
        limit = maximum or cls._MAXIMUM_EVENT_CHARACTERS
        if len(value) <= limit:
            return value
        return value[: limit - 24] + "\n…[本地显示已截断]"

    @classmethod
    def _normalize_capacity(
        cls,
        result: dict[str, Any],
        *,
        observed_at: datetime,
    ) -> QuickAgentCapacitySnapshot:
        candidates: dict[str, dict[str, Any]] = {}
        by_id = result.get("rateLimitsByLimitId")
        if isinstance(by_id, dict):
            for key, value in by_id.items():
                if isinstance(key, str) and isinstance(value, dict):
                    candidates[key] = value
        legacy = result.get("rateLimits")
        if isinstance(legacy, dict):
            legacy_id = legacy.get("limitId")
            fallback_id = (
                legacy_id if isinstance(legacy_id, str) and legacy_id else "codex"
            )
            candidates.setdefault(fallback_id, legacy)
        buckets: list[QuickAgentRateLimitBucket] = []
        for fallback_id, raw in candidates.items():
            limit_id = raw.get("limitId") or fallback_id
            if not isinstance(limit_id, str) or not limit_id:
                continue
            primary = cls._normalize_window(raw.get("primary"))
            secondary = cls._normalize_window(raw.get("secondary"))
            if primary is None and secondary is None:
                continue
            name = raw.get("limitName")
            plan = raw.get("planType")
            reached = raw.get("rateLimitReachedType")
            buckets.append(
                QuickAgentRateLimitBucket(
                    limit_id=limit_id,
                    limit_name=name if isinstance(name, str) and name else None,
                    plan_type=plan if isinstance(plan, str) and plan else None,
                    primary=primary,
                    secondary=secondary,
                    rate_limit_reached_type=(
                        reached if isinstance(reached, str) and reached else None
                    ),
                )
            )
        buckets.sort(key=lambda item: (item.limit_id != "codex", item.limit_name or item.limit_id))
        if not buckets:
            raise ValueError("Codex returned no rate-limit windows")
        reset_credits = result.get("rateLimitResetCredits")
        count = reset_credits.get("availableCount") if isinstance(reset_credits, dict) else None
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            count = None
        return QuickAgentCapacitySnapshot(
            available=True,
            source="codex_app_server.account/rateLimits/read",
            observed_at=observed_at,
            refresh_after_seconds=cls._CAPACITY_CACHE_SECONDS,
            buckets=tuple(buckets),
            reset_credit_count=count,
        )

    @classmethod
    def _normalize_models(
        cls,
        result: dict[str, Any],
        *,
        observed_at: datetime,
    ) -> QuickAgentModelCatalogSnapshot:
        raw_models = result.get("data")
        if not isinstance(raw_models, list):
            raise ValueError("Codex returned an invalid model catalog")
        models: list[QuickAgentModelOption] = []
        for raw in raw_models:
            if not isinstance(raw, dict) or raw.get("hidden") is True:
                continue
            model_id = raw.get("model") or raw.get("id")
            if not isinstance(model_id, str) or not model_id or len(model_id) > 120:
                continue
            raw_efforts = raw.get("supportedReasoningEfforts")
            if not isinstance(raw_efforts, list):
                continue
            efforts = []
            for option in raw_efforts:
                if not isinstance(option, dict):
                    continue
                effort = option.get("reasoningEffort")
                description = option.get("description")
                if isinstance(effort, str) and effort and len(effort) <= 40:
                    efforts.append(
                        QuickAgentReasoningEffortOption(
                            effort=effort,
                            description=(
                                description
                                if isinstance(description, str)
                                else ""
                            ),
                        )
                    )
            default_effort = raw.get("defaultReasoningEffort")
            if not isinstance(default_effort, str) or not default_effort:
                continue
            tiers: list[QuickAgentServiceTierOption] = []
            raw_tiers = raw.get("serviceTiers")
            if isinstance(raw_tiers, list):
                for tier in raw_tiers:
                    if not isinstance(tier, dict):
                        continue
                    tier_id = tier.get("id")
                    if not isinstance(tier_id, str) or not tier_id:
                        continue
                    name = tier.get("name")
                    description = tier.get("description")
                    tiers.append(
                        QuickAgentServiceTierOption(
                            tier_id=tier_id,
                            name=name if isinstance(name, str) and name else tier_id,
                            description=(
                                description
                                if isinstance(description, str)
                                else ""
                            ),
                        )
                    )
            modalities = raw.get("inputModalities")
            normalized_modalities = tuple(
                value
                for value in modalities
                if isinstance(value, str) and value and len(value) <= 40
            ) if isinstance(modalities, list) else ("text", "image")
            upgrade = raw.get("upgrade")
            upgrade_info = raw.get("upgradeInfo")
            retirement = (
                upgrade_info.get("retirementAt")
                if isinstance(upgrade_info, dict)
                else None
            )
            retirement_at = None
            if isinstance(retirement, int) and not isinstance(retirement, bool):
                retirement_at = datetime.fromtimestamp(retirement, tz=UTC)
            display_name = raw.get("displayName")
            description = raw.get("description")
            multi_agent = raw.get("multiAgentVersion")
            default_tier = raw.get("defaultServiceTier")
            models.append(
                QuickAgentModelOption(
                    model_id=model_id,
                    display_name=(
                        display_name
                        if isinstance(display_name, str) and display_name
                        else model_id
                    ),
                    description=(description if isinstance(description, str) else ""),
                    default_reasoning_effort=default_effort,
                    reasoning_efforts=tuple(efforts),
                    input_modalities=normalized_modalities,
                    supports_personality=raw.get("supportsPersonality") is True,
                    multi_agent_version=(
                        multi_agent
                        if isinstance(multi_agent, str) and multi_agent
                        else None
                    ),
                    service_tiers=tuple(tiers),
                    default_service_tier=(
                        default_tier
                        if isinstance(default_tier, str) and default_tier
                        else None
                    ),
                    is_default=raw.get("isDefault") is True,
                    upgrade_model=(
                        upgrade if isinstance(upgrade, str) and upgrade else None
                    ),
                    retirement_at=retirement_at,
                )
            )
        if not models:
            raise ValueError("Codex returned no selectable models")
        return QuickAgentModelCatalogSnapshot(
            available=True,
            source="codex_app_server.model/list",
            observed_at=observed_at,
            refresh_after_seconds=cls._MODEL_CACHE_SECONDS,
            models=tuple(models),
        )

    @staticmethod
    def _normalize_window(value: Any) -> QuickAgentRateLimitWindow | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("Codex returned an invalid rate-limit window")
        used = value.get("usedPercent")
        if not isinstance(used, int) or isinstance(used, bool) or not 0 <= used <= 100:
            raise ValueError("Codex returned an invalid used percentage")
        duration = value.get("windowDurationMins")
        if duration is not None and (
            not isinstance(duration, int) or isinstance(duration, bool) or duration < 1
        ):
            raise ValueError("Codex returned an invalid window duration")
        reset_value = value.get("resetsAt")
        resets_at = None
        if reset_value is not None:
            if (
                not isinstance(reset_value, int)
                or isinstance(reset_value, bool)
                or reset_value < 0
            ):
                raise ValueError("Codex returned an invalid reset timestamp")
            resets_at = datetime.fromtimestamp(reset_value, tz=UTC)
        return QuickAgentRateLimitWindow(
            used_percent=used,
            remaining_percent=100 - used,
            window_duration_minutes=duration,
            resets_at=resets_at,
        )


__all__ = ["CodexQuickAgent"]
