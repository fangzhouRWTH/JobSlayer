import hashlib
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from jobslayer.adapters.codex_cli import (
    AgentRunStillRunningError,
    CodexCliExecutor,
    CodexConfigurationError,
)
from jobslayer.adapters.git_workspace import GitWorktreeManager
from jobslayer.domain.models import (
    AgentInvocation,
    AgentRunSpec,
    AgentRunStatus,
    WorkspaceSpec,
)
from jobslayer.execution import ProcessSupervisor, native_process_supervisor
from jobslayer.workers import (
    NetworkPolicy,
    SandboxCapabilities,
    SandboxLaunchPlan,
    SandboxPolicy,
)


class _SandboxLauncher:
    def __init__(self):
        self.request = None
        self._capabilities = SandboxCapabilities(
            adapter="fixture-launcher",
            network_isolation=True,
            mount_isolation=True,
            cpu_limit=True,
            memory_limit=True,
            process_limit=True,
            process_tree_termination=True,
            wall_timeout=True,
        )

    def capabilities(self):
        return self._capabilities

    def prepare(self, request):
        self.request = request
        return SandboxLaunchPlan(
            request_id=request.request_id,
            run_id=request.run_id,
            adapter=self._capabilities.adapter,
            argv=request.argv,
            cwd=request.workspace,
            environment={"PATH": os.defpath},
            capabilities=self._capabilities,
        )


class RecordingProcessSupervisor:
    def __init__(self, delegate: ProcessSupervisor):
        self.delegate = delegate
        self.launch_count = 0
        self.termination_count = 0

    def popen_kwargs(self):
        self.launch_count += 1
        return self.delegate.popen_kwargs()

    def terminate(self, process, *, timeout_seconds: float = 0.5) -> None:
        self.termination_count += 1
        self.delegate.terminate(process, timeout_seconds=timeout_seconds)


FAKE_CODEX = """import json
import os
import sys
import time

prompt = sys.stdin.read()
print(json.dumps({"type": "thread.started", "thread_id": "fake-thread"}), flush=True)
print(json.dumps({"type": "turn.started"}), flush=True)
sys.stderr.write("ARGS=" + json.dumps(sys.argv[1:]) + "\\n")
sys.stderr.flush()
if "SLOW" in prompt:
    time.sleep(10)
if "BADJSON" in prompt:
    print("this is not json", flush=True)
else:
    print(json.dumps({
        "type": "item.started",
        "item": {
            "id": "item-command",
            "type": "command_execution",
            "command": "python -m unittest",
            "status": "in_progress"
        }
    }), flush=True)
    print(json.dumps({
        "type": "item.completed",
        "item": {
            "id": "item-command",
            "type": "command_execution",
            "command": "python -m unittest",
            "status": "completed"
        }
    }), flush=True)
    print(json.dumps({
        "type": "item.completed",
        "item": {
            "id": "item-message",
            "type": "agent_message",
            "text": "fake completed; api_key_seen=" + str("OPENAI_API_KEY" in os.environ)
        }
    }), flush=True)
    print(json.dumps({
        "type": "turn.completed",
        "usage": {
            "input_tokens": 12,
            "cached_input_tokens": 3,
            "output_tokens": 4
        }
    }), flush=True)
"""


class CodexCliExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.repository = root / "source"
        self.repository.mkdir()
        self._git("init", "-b", "main")
        self._git("config", "user.name", "JobSlayer Test")
        self._git("config", "user.email", "jobslayer@example.invalid")
        (self.repository / "README.md").write_text("fixture\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-m", "baseline")
        base_commit = self._git("rev-parse", "HEAD").strip()

        fake_codex = root / "fake_codex.py"
        fake_codex.write_text(FAKE_CODEX, encoding="utf-8")
        self.fake_codex = fake_codex

        self.manager = GitWorktreeManager(self.repository, root / "worktrees")
        self.manifest = self.manager.create(
            WorkspaceSpec(
                workspace_id="codex-workspace",
                task_id="task-codex",
                base_commit=base_commit,
            )
        )
        self.process_supervisor = RecordingProcessSupervisor(
            native_process_supervisor()
        )
        self.executor = CodexCliExecutor(
            self.manager,
            root / "artifacts",
            codex_binary=(sys.executable, str(fake_codex)),
            process_supervisor=self.process_supervisor,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _git(self, *arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout

    def invocation(self, run_id: str, prompt: str) -> AgentInvocation:
        return AgentInvocation(
            run_spec=AgentRunSpec(
                run_id=run_id,
                task_id=self.manifest.task_id,
                executor_type="codex_cli",
                model_profile="default",
                context_package_id="context-1",
                workspace_id=self.manifest.workspace_id,
                permission_profile="workspace_write",
                timeout_seconds=5,
                max_attempts=1,
                output_schema="none",
            ),
            prompt=prompt,
        )

    def collect_eventually(self, run_id: str, timeout: float = 3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                return self.executor.collect(run_id)
            except AgentRunStillRunningError:
                time.sleep(0.01)
        self.fail(f"run did not complete within {timeout} seconds")

    def test_normalizes_jsonl_and_preserves_raw_logs(self) -> None:
        previous_key = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "must-not-reach-codex"
        try:
            handle = self.executor.start(
                self.invocation("run-complete", "perform a fake task"),
                self.manifest,
            )
            result = self.collect_eventually(handle.run_id)
        finally:
            if previous_key is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = previous_key

        self.assertEqual(result.status, AgentRunStatus.COMPLETED)
        self.assertEqual(result.final_message, "fake completed; api_key_seen=False")
        self.assertEqual(result.usage["input_tokens"], 12)
        event_types = tuple(event.event_type for event in self.executor.events(handle.run_id))
        self.assertEqual(event_types[0], "run.started")
        self.assertIn("agent.thread.started", event_types)
        self.assertIn("command.started", event_types)
        self.assertIn("command.completed", event_types)
        self.assertIn("agent.message.completed", event_types)
        self.assertEqual(event_types[-1], "run.completed")
        sequences = tuple(event.sequence for event in self.executor.events(handle.run_id))
        self.assertEqual(sequences, tuple(range(1, len(sequences) + 1)))
        self.assertEqual(self.process_supervisor.launch_count, 1)

        raw_path = Path(result.raw_event_log_path)
        stderr_path = Path(result.stderr_log_path)
        self.assertTrue(raw_path.is_file())
        self.assertEqual(
            result.raw_event_log_sha256,
            hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        )
        self.assertIn("--json", stderr_path.read_text(encoding="utf-8"))
        self.assertIn("workspace-write", stderr_path.read_text(encoding="utf-8"))

    def test_supports_incremental_event_polling(self) -> None:
        handle = self.executor.start(
            self.invocation("run-events", "perform another fake task"),
            self.manifest,
        )
        self.collect_eventually(handle.run_id)
        all_events = self.executor.events(handle.run_id)
        remaining = self.executor.events(handle.run_id, after_sequence=2)

        self.assertEqual(remaining, all_events[2:])

    def test_cancels_a_running_process_group_and_keeps_terminal_evidence(self) -> None:
        handle = self.executor.start(
            self.invocation("run-cancel", "SLOW"),
            self.manifest,
        )
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if any(
                event.event_type == "agent.thread.started"
                for event in self.executor.events(handle.run_id)
            ):
                break
            time.sleep(0.01)
        cancellation = self.executor.cancel(handle.run_id)
        result = self.collect_eventually(handle.run_id)

        self.assertTrue(cancellation.cancellation_requested)
        self.assertEqual(result.status, AgentRunStatus.CANCELLED)
        self.assertIsNone(result.exit_code)
        self.assertEqual(
            self.executor.events(handle.run_id)[-1].event_type,
            "run.cancelled",
        )
        self.assertGreaterEqual(self.process_supervisor.termination_count, 1)

    def test_invalid_json_makes_the_run_fail_even_with_zero_exit(self) -> None:
        handle = self.executor.start(
            self.invocation("run-invalid-json", "BADJSON"),
            self.manifest,
        )
        result = self.collect_eventually(handle.run_id)

        self.assertEqual(result.status, AgentRunStatus.FAILED)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("non-JSON", result.error_summary or "")
        self.assertIn(
            "executor.output.invalid",
            tuple(event.event_type for event in self.executor.events(handle.run_id)),
        )

    def test_refuses_a_danger_full_access_mapping(self) -> None:
        with self.assertRaises(CodexConfigurationError):
            CodexCliExecutor(
                self.manager,
                Path(self.temporary_directory.name) / "unsafe-artifacts",
                permission_profiles={"unsafe": "danger-full-access"},
            )

    def test_rejects_an_empty_executable_command(self) -> None:
        with self.assertRaisesRegex(
            CodexConfigurationError,
            "must contain non-empty arguments",
        ):
            CodexCliExecutor(
                self.manager,
                Path(self.temporary_directory.name) / "empty-command-artifacts",
                codex_binary=(),
            )

    def test_rejects_an_unknown_permission_profile_before_launch(self) -> None:
        invocation = self.invocation("run-unknown-policy", "fake task")
        invocation = invocation.model_copy(
            update={
                "run_spec": invocation.run_spec.model_copy(
                    update={"permission_profile": "not-configured"}
                )
            }
        )

        with self.assertRaises(CodexConfigurationError):
            self.executor.start(invocation, self.manifest)

    def test_prepares_codex_through_the_enforcement_backed_sandbox_launcher(self) -> None:
        launcher = _SandboxLauncher()
        policy = SandboxPolicy(
            policy_id="codex-strict-v1",
            network=NetworkPolicy.DENY,
            cpu_seconds=5,
            memory_bytes=64 * 1024 * 1024,
            process_limit=16,
            timeout_seconds=5,
        )
        executor = CodexCliExecutor(
            self.manager,
            Path(self.temporary_directory.name) / "sandboxed-artifacts",
            codex_binary=(sys.executable, str(self.fake_codex)),
            sandbox_launcher=launcher,
            sandbox_policy=policy,
            credential_grant_id="grant-1",
        )

        command, cwd, environment = executor._launch_for(
            self.invocation("run-sandboxed", "fake task"), self.manifest
        )

        self.assertEqual(executor.credential_grant_id(), "grant-1")
        self.assertEqual(executor.sandbox_capabilities().adapter, "fixture-launcher")
        self.assertEqual(launcher.request.run_id, "run-sandboxed")
        cd_index = command.index("--cd") + 1
        self.assertEqual(command[cd_index], "/workspace")
        self.assertEqual(cwd, Path(self.manifest.path))
        self.assertEqual(environment, {"PATH": os.defpath})


if __name__ == "__main__":
    unittest.main()
