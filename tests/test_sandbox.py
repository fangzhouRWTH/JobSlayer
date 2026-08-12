from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from jobslayer.adapters.linux_sandbox import LinuxNamespaceSandbox
from jobslayer.workers import (
    NetworkPolicy,
    SandboxPolicy,
    SandboxRequest,
    SandboxUnavailableError,
)


class SandboxContractTests(unittest.TestCase):
    def policy(self) -> SandboxPolicy:
        return SandboxPolicy(
            policy_id="strict-local-v1",
            network=NetworkPolicy.DENY,
            read_only_root=True,
            cpu_seconds=5,
            memory_bytes=256 * 1024 * 1024,
            process_limit=16,
            timeout_seconds=5,
        )

    def test_missing_adapter_fails_closed_with_explicit_capability_gaps(self) -> None:
        sandbox = LinuxNamespaceSandbox(
            bubblewrap_binary="definitely-missing-jobslayer-bwrap",
            prlimit_binary="definitely-missing-jobslayer-prlimit",
        )
        capabilities = sandbox.capabilities()
        self.assertIn("network_isolation", capabilities.unmet(self.policy()))

        with TemporaryDirectory() as directory:
            request = SandboxRequest(
                request_id="sandbox-missing",
                run_id="run-missing",
                workspace=Path(directory),
                argv=("python", "-c", "print('must not run')"),
                policy=self.policy(),
            )
            with self.assertRaisesRegex(SandboxUnavailableError, "cannot enforce"):
                sandbox.execute(request)


@unittest.skipUnless(
    os.environ.get("JOBSLAYER_TEST_BWRAP"),
    "JOBSLAYER_TEST_BWRAP is not configured",
)
class LinuxSandboxIntegrationTests(unittest.TestCase):
    @staticmethod
    def sandbox() -> LinuxNamespaceSandbox:
        return LinuxNamespaceSandbox(
            bubblewrap_binary=os.environ["JOBSLAYER_TEST_BWRAP"]
        )

    @staticmethod
    def policy(**updates) -> SandboxPolicy:
        values = {
            "policy_id": "strict-linux-v1",
            "network": NetworkPolicy.DENY,
            "read_only_root": True,
            "cpu_seconds": 5,
            "memory_bytes": 256 * 1024 * 1024,
            "process_limit": 16,
            "timeout_seconds": 5,
        }
        values.update(updates)
        return SandboxPolicy(**values)

    def test_denies_network_and_root_writes_while_allowing_workspace_write(self) -> None:
        sandbox = self.sandbox()
        with TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            probe = (
                "import json, pathlib, socket; "
                "result={}; "
                "p=pathlib.Path('allowed.txt'); p.write_text('ok'); "
                "result['workspace_write']=p.read_text()=='ok'; "
                "\ntry:\n pathlib.Path('/etc/jobslayer-denied').write_text('bad'); "
                "result['root_write_denied']=False\nexcept OSError:\n "
                "result['root_write_denied']=True\n"
                "try:\n socket.create_connection(('1.1.1.1', 53), timeout=0.2); "
                "result['network_denied']=False\nexcept OSError:\n "
                "result['network_denied']=True\n"
                "print(json.dumps(result, sort_keys=True))"
            )
            request = SandboxRequest(
                request_id="sandbox-real",
                run_id="run-real",
                workspace=workspace,
                argv=("/usr/bin/python3", "-c", probe),
                policy=self.policy(),
            )
            result = sandbox.execute(request)

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertFalse(result.timed_out)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "network_denied": True,
                "root_write_denied": True,
                "workspace_write": True,
            },
        )
        self.assertEqual(result.capabilities.unmet(request.policy), ())

    def test_does_not_mount_a_host_file_outside_the_workspace(self) -> None:
        sandbox = self.sandbox()
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            workspace = root / "workspace"
            workspace.mkdir()
            secret = root / "operator-secret.txt"
            secret.write_text("must-not-be-visible", encoding="utf-8")
            probe = (
                "import json, pathlib; "
                f"p=pathlib.Path({str(secret)!r}); "
                "print(json.dumps({'host_secret_visible': p.exists()}))"
            )
            result = sandbox.execute(
                SandboxRequest(
                    request_id="sandbox-host-read",
                    run_id="run-host-read",
                    workspace=workspace,
                    argv=("/usr/bin/python3", "-c", probe),
                    policy=self.policy(),
                )
            )

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"host_secret_visible": False})

    def test_enforces_cpu_and_memory_limits(self) -> None:
        sandbox = self.sandbox()
        with TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            memory = sandbox.execute(
                SandboxRequest(
                    request_id="sandbox-memory",
                    run_id="run-memory",
                    workspace=workspace,
                    argv=(
                        "/usr/bin/python3",
                        "-c",
                        "value=bytearray(512*1024*1024); print(len(value))",
                    ),
                    policy=self.policy(memory_bytes=128 * 1024 * 1024),
                )
            )
            cpu = sandbox.execute(
                SandboxRequest(
                    request_id="sandbox-cpu",
                    run_id="run-cpu",
                    workspace=workspace,
                    argv=("/usr/bin/python3", "-c", "while True: pass"),
                    policy=self.policy(cpu_seconds=1),
                )
            )

        self.assertNotEqual(memory.exit_code, 0)
        self.assertFalse(memory.timed_out)
        self.assertNotEqual(cpu.exit_code, 0)
        self.assertFalse(cpu.timed_out)

    def test_enforces_process_limit_and_wall_timeout_process_tree(self) -> None:
        sandbox = self.sandbox()
        with TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            process_probe = (
                "import json, subprocess; children=[]; denied=False; "
                "\ntry:\n"
                "  [children.append(subprocess.Popen(['/bin/sleep','0.2'])) for _ in range(32)]\n"
                "except OSError:\n  denied=True\n"
                "[child.terminate() for child in children]; "
                "[child.wait() for child in children]; "
                "print(json.dumps({'process_denied': denied}))"
            )
            process_result = sandbox.execute(
                SandboxRequest(
                    request_id="sandbox-process",
                    run_id="run-process",
                    workspace=workspace,
                    argv=("/usr/bin/python3", "-c", process_probe),
                    policy=self.policy(process_limit=4),
                )
            )
            marker = workspace / "orphan-marker.txt"
            child_script = (
                "import pathlib, time; time.sleep(1); "
                "pathlib.Path('orphan-marker.txt').write_text('orphan')"
            )
            parent_script = (
                "import subprocess, time; "
                f"subprocess.Popen(['/usr/bin/python3','-c',{child_script!r}]); "
                "time.sleep(5)"
            )
            timeout_result = sandbox.execute(
                SandboxRequest(
                    request_id="sandbox-timeout",
                    run_id="run-timeout",
                    workspace=workspace,
                    argv=("/usr/bin/python3", "-c", parent_script),
                    policy=self.policy(timeout_seconds=0.2),
                )
            )
            time.sleep(1.1)

        self.assertEqual(json.loads(process_result.stdout), {"process_denied": True})
        self.assertTrue(timeout_result.timed_out)
        self.assertIsNone(timeout_result.exit_code)
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
