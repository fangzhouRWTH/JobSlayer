from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import tempfile

from jobslayer.adapters.git_workspace import GitWorktreeManager
from jobslayer.domain.models import (
    SourceIntegrationResult,
    TaskSpec,
    WorkspaceManifest,
    WorkspacePatch,
)
from jobslayer.integration.manager import SourceIntegrationError


class LocalGitIntegrationError(SourceIntegrationError):
    """Raised when local Git facts no longer match the approved integration."""


class LocalGitIntegrator:
    """Commit one reviewed workspace and fast-forward its checked-out target.

    This adapter is deliberately local-only. It disables repository hooks and
    never fetches, pushes, rebases, force-updates, or creates merge commits.
    """

    def __init__(
        self,
        workspace_manager: GitWorktreeManager,
        *,
        command_timeout_seconds: int = 30,
    ):
        if command_timeout_seconds <= 0:
            raise ValueError("command_timeout_seconds must be positive")
        self.workspace_manager = workspace_manager
        self.repository = workspace_manager.repository
        self.command_timeout_seconds = command_timeout_seconds

    def integrate(
        self,
        *,
        task: TaskSpec,
        workspace: WorkspaceManifest,
        reviewed_patch: WorkspacePatch,
        target_ref: str,
        approved_by: str,
        commit_message: str,
    ) -> SourceIntegrationResult:
        self._validate_bindings(task, workspace, reviewed_patch)
        target_ref = self._validated_ref(target_ref)
        approved_by = self._single_line(approved_by, field="approved_by")
        commit_message = self._single_line(commit_message, field="commit_message")
        workspace_path = Path(workspace.path)

        target_branch = self._git(self.repository, "branch", "--show-current")
        if target_branch != target_ref:
            raise LocalGitIntegrationError(
                f"target checkout is on {target_branch or 'detached HEAD'}, not {target_ref}"
            )
        if self._status(self.repository):
            raise LocalGitIntegrationError("target checkout has uncommitted changes")

        base_commit = workspace.resolved_base_commit
        target_head = self._git(self.repository, "rev-parse", "HEAD")
        workspace_head = self._git(workspace_path, "rev-parse", "HEAD")
        expected_message = self._commit_message(
            commit_message=commit_message,
            task_id=task.task_id,
            patch_sha256=reviewed_patch.sha256,
            approved_by=approved_by,
        )
        expected_tree = self._expected_tree(
            reviewed_patch=reviewed_patch,
            base_commit=base_commit,
        )

        if workspace_head == base_commit:
            if target_head != base_commit:
                raise LocalGitIntegrationError(
                    "target branch moved away from the reviewed base commit"
                )
            current_patch = self.workspace_manager.collect_patch(workspace, task)
            if current_patch.model_dump(exclude={"created_at"}) != reviewed_patch.model_dump(
                exclude={"created_at"}
            ):
                raise LocalGitIntegrationError(
                    "workspace content no longer matches the reviewed patch"
                )
            if not current_patch.changed_paths:
                raise LocalGitIntegrationError("reviewed patch is empty")
            self._git(workspace_path, "add", "--all", "--", ".")
            self._git(
                workspace_path,
                "-c",
                "user.name=JobSlayer",
                "-c",
                "user.email=jobslayer@local.invalid",
                "commit",
                "--no-verify",
                "--no-gpg-sign",
                "-m",
                expected_message,
            )
            workspace_head = self._git(workspace_path, "rev-parse", "HEAD")
        else:
            self._validate_recoverable_commit(
                workspace_path=workspace_path,
                commit=workspace_head,
                base_commit=base_commit,
                expected_message=expected_message,
                expected_paths=reviewed_patch.changed_paths,
                expected_tree=expected_tree,
            )

        self._validate_created_commit(
            workspace_path=workspace_path,
            commit=workspace_head,
            base_commit=base_commit,
            expected_message=expected_message,
            expected_paths=reviewed_patch.changed_paths,
            expected_tree=expected_tree,
        )

        target_head = self._git(self.repository, "rev-parse", "HEAD")
        if target_head == base_commit:
            self._git(
                self.repository,
                "merge",
                "--ff-only",
                "--no-edit",
                workspace.branch_name,
            )
        elif target_head != workspace_head:
            raise LocalGitIntegrationError(
                "target branch moved to an unexpected commit during integration"
            )

        final_target = self._git(self.repository, "rev-parse", "HEAD")
        if final_target != workspace_head:
            raise LocalGitIntegrationError("target branch did not reach the source commit")
        if self._status(self.repository) or self._status(workspace_path):
            raise LocalGitIntegrationError(
                "integration finished with a dirty target or source workspace"
            )

        identity = hashlib.sha256(
            f"{task.task_id}\0{target_ref}\0{workspace_head}".encode("utf-8")
        ).hexdigest()[:32]
        return SourceIntegrationResult(
            integration_id=f"integration-{identity}",
            task_id=task.task_id,
            workspace_id=workspace.workspace_id,
            repository_root=str(self.repository),
            source_ref=workspace.branch_name,
            target_ref=target_ref,
            base_commit=base_commit,
            commit=workspace_head,
            target_previous_commit=base_commit,
            target_commit=final_target,
            source_patch_sha256=reviewed_patch.sha256,
            changed_paths=reviewed_patch.changed_paths,
            approved_by=approved_by,
        )

    @staticmethod
    def _validate_bindings(
        task: TaskSpec,
        workspace: WorkspaceManifest,
        patch: WorkspacePatch,
    ) -> None:
        if task.task_id != workspace.task_id or patch.task_id != task.task_id:
            raise LocalGitIntegrationError("task, workspace, and patch ids do not match")
        if patch.workspace_id != workspace.workspace_id:
            raise LocalGitIntegrationError("patch belongs to a different workspace")
        if patch.base_commit != workspace.resolved_base_commit:
            raise LocalGitIntegrationError("patch and workspace base commits do not match")

    def _validated_ref(self, value: str) -> str:
        value = self._single_line(value, field="target_ref")
        result = self._run(
            self.repository,
            "check-ref-format",
            "--branch",
            value,
            accepted_returncodes=(0, 1, 128),
        )
        if result.returncode != 0:
            raise LocalGitIntegrationError("target_ref is not a valid Git branch name")
        return value

    def _validate_recoverable_commit(
        self,
        *,
        workspace_path: Path,
        commit: str,
        base_commit: str,
        expected_message: str,
        expected_paths: tuple[str, ...],
        expected_tree: str,
    ) -> None:
        if self._status(workspace_path):
            raise LocalGitIntegrationError(
                "workspace has both a commit and uncommitted changes"
            )
        self._validate_created_commit(
            workspace_path=workspace_path,
            commit=commit,
            base_commit=base_commit,
            expected_message=expected_message,
            expected_paths=expected_paths,
            expected_tree=expected_tree,
        )

    def _validate_created_commit(
        self,
        *,
        workspace_path: Path,
        commit: str,
        base_commit: str,
        expected_message: str,
        expected_paths: tuple[str, ...],
        expected_tree: str,
    ) -> None:
        count = self._git(
            workspace_path, "rev-list", "--count", f"{base_commit}..{commit}"
        )
        parent = self._git(workspace_path, "rev-parse", f"{commit}^")
        actual_message = self._git(
            workspace_path, "show", "-s", "--format=%B", commit
        )
        actual_paths = self._changed_paths(workspace_path, base_commit, commit)
        actual_tree = self._git(workspace_path, "show", "-s", "--format=%T", commit)
        if count != "1" or parent != base_commit:
            raise LocalGitIntegrationError(
                "source branch is not one commit ahead of the reviewed base"
            )
        if actual_message != expected_message:
            raise LocalGitIntegrationError(
                "source commit does not carry the approved integration binding"
            )
        if actual_paths != expected_paths:
            raise LocalGitIntegrationError(
                "source commit paths differ from the reviewed patch"
            )
        if actual_tree != expected_tree:
            raise LocalGitIntegrationError(
                "source commit content differs from the reviewed patch"
            )

    def _expected_tree(
        self, *, reviewed_patch: WorkspacePatch, base_commit: str
    ) -> str:
        with tempfile.TemporaryDirectory(prefix="jobslayer-index-") as directory:
            index_path = Path(directory) / "index"
            environment = os.environ.copy()
            environment["GIT_INDEX_FILE"] = str(index_path)
            self._run_with_environment(
                self.repository,
                ("read-tree", base_commit),
                environment=environment,
            )
            self._run_with_environment(
                self.repository,
                ("apply", "--cached", "--whitespace=nowarn", "-"),
                environment=environment,
                input_text=reviewed_patch.patch_text,
            )
            return self._run_with_environment(
                self.repository,
                ("write-tree",),
                environment=environment,
            ).stdout.strip()

    def _changed_paths(
        self, repository: Path, base_commit: str, commit: str
    ) -> tuple[str, ...]:
        output = self._run_bytes(
            repository,
            "diff",
            "--name-only",
            "-z",
            "--no-renames",
            base_commit,
            commit,
            "--",
        )
        try:
            return tuple(
                sorted(raw.decode("utf-8") for raw in output.split(b"\0") if raw)
            )
        except UnicodeDecodeError as exc:
            raise LocalGitIntegrationError("Git returned a non-UTF-8 path") from exc

    @staticmethod
    def _commit_message(
        *, commit_message: str, task_id: str, patch_sha256: str, approved_by: str
    ) -> str:
        return (
            f"{commit_message}\n\n"
            f"JobSlayer-Task: {task_id}\n"
            f"JobSlayer-Patch-SHA256: {patch_sha256}\n"
            f"JobSlayer-Approved-By: {approved_by}"
        )

    @staticmethod
    def _single_line(value: str, *, field: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise LocalGitIntegrationError(f"{field} must not be blank")
        return normalized

    def _status(self, repository: Path) -> bytes:
        return self._run_bytes(
            repository,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        )

    def _git(self, repository: Path, *arguments: str) -> str:
        return self._run(repository, *arguments).stdout.strip()

    def _run_bytes(self, repository: Path, *arguments: str) -> bytes:
        command = self._command(repository, arguments)
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                timeout=self.command_timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LocalGitIntegrationError(
                f"Git command could not complete: {command}"
            ) from exc
        if result.returncode != 0:
            raise LocalGitIntegrationError(
                "Git command failed with "
                f"{result.returncode}: {result.stderr.decode('utf-8', errors='replace').strip()}"
            )
        return result.stdout

    def _run(
        self,
        repository: Path,
        *arguments: str,
        accepted_returncodes: tuple[int, ...] = (0,),
    ) -> subprocess.CompletedProcess[str]:
        command = self._command(repository, arguments)
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.command_timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LocalGitIntegrationError(
                f"Git command could not complete: {command}"
            ) from exc
        if result.returncode not in accepted_returncodes:
            raise LocalGitIntegrationError(
                f"Git command failed with {result.returncode}: {result.stderr.strip()}"
            )
        return result

    def _run_with_environment(
        self,
        repository: Path,
        arguments: tuple[str, ...],
        *,
        environment: dict[str, str],
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = self._command(repository, arguments)
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.command_timeout_seconds,
                env=environment,
                input=input_text,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LocalGitIntegrationError(
                f"Git command could not complete: {command}"
            ) from exc
        if result.returncode != 0:
            raise LocalGitIntegrationError(
                f"reviewed patch cannot produce a Git tree: {result.stderr.strip()}"
            )
        return result

    @staticmethod
    def _command(repository: Path, arguments: tuple[str, ...]) -> tuple[str, ...]:
        return (
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-C",
            str(repository),
            *arguments,
        )
