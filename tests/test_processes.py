import os
import subprocess
import sys
import unittest

from jobslayer.execution import ProcessSupervisor
from jobslayer.execution.processes import (
    PosixProcessSupervisor,
    WindowsProcessSupervisor,
    native_process_supervisor,
    process_group_launch_kwargs,
    terminate_process_group,
)


class ProcessGroupTests(unittest.TestCase):
    def test_native_implementation_satisfies_the_common_protocol(self) -> None:
        supervisor = native_process_supervisor()

        self.assertIsInstance(supervisor, ProcessSupervisor)
        if os.name == "nt":
            self.assertIsInstance(supervisor, WindowsProcessSupervisor)
        else:
            self.assertIsInstance(supervisor, PosixProcessSupervisor)

    def test_launch_options_match_the_current_platform(self) -> None:
        options = process_group_launch_kwargs()

        if os.name == "nt":
            self.assertEqual(
                options,
                {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP},
            )
        else:
            self.assertEqual(options, {"start_new_session": True})

    def test_terminates_an_owned_sleeping_process(self) -> None:
        process = subprocess.Popen(
            (sys.executable, "-c", "import time; time.sleep(30)"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **process_group_launch_kwargs(),
        )
        try:
            terminate_process_group(process)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)

        self.assertIsNotNone(process.returncode)


if __name__ == "__main__":
    unittest.main()
