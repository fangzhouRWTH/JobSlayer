from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path, PurePosixPath

from jobslayer.domain.models import (
    TaskSpec,
    WorkspaceInspection,
    WorkspaceManifest,
    WorkspacePatch,
    WorkspaceSpec,
)
from jobslayer.workspace.manager import WorkspaceOperationError


class WorkspaceError(WorkspaceOperationError):
    """Base error for workspace lifecycle failures."""


class GitCommandError(WorkspaceError):
    pass


class InvalidRepositoryError(WorkspaceError):
    pass


class WorkspaceExistsError(WorkspaceError):
    pass


class WorkspaceNotFoundError(WorkspaceError):
    pass


class WorkspaceDirtyError(WorkspaceError):
    pass


class PathPolicyViolation(WorkspaceError):
    def __init__(self, paths: tuple[str, ...]):
        self.paths = paths
        super().__init__(f"changed paths violate the task policy: {', '.join(paths)}")


class GitWorktreeManager:
    """Local Git implementation of task-scoped workspaces.

    Git hooks are disabled for management commands. This class does not execute
    repository build/test commands and is not a process or network sandbox.
    """

    def __init__(
        self,
        repository: str | Path,
        workspace_root: str | Path,
        *,
        command_timeout_seconds: int = 30,
    ):
        try:
            self.repository = Path(repository).resolve(strict=True)
        except FileNotFoundError as exc:
            raise InvalidRepositoryError(f"repository does not exist: {repository}") from exc

        self.workspace_root = Path(workspace_root).resolve(strict=False)
        self.command_timeout_seconds = command_timeout_seconds
        if command_timeout_seconds <= 0:
            raise ValueError("command_timeout_seconds must be positive")

        top_level = Path(
            self._run_git(
                "-C",
                str(self.repository),
                "rev-parse",
                "--show-toplevel",
            ).strip()
        ).resolve()
        if top_level != self.repository:
            raise InvalidRepositoryError(
                "repository must be the root of a non-bare Git working tree"
            )
        if self.workspace_root == self.repository or self.workspace_root.is_relative_to(
            self.repository
        ):
            raise InvalidRepositoryError(
                "workspace root must not be inside the source repository"
            )
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    def create(self, spec: WorkspaceSpec) -> WorkspaceManifest:
        target = self._target_for(spec.workspace_id)
        branch_name = f"jobslayer/{spec.workspace_id}"
        if target.exists():
            raise WorkspaceExistsError(f"workspace path already exists: {target}")
        if self._branch_exists(branch_name):
            raise WorkspaceExistsError(f"workspace branch already exists: {branch_name}")

        resolved_base = self._resolve_commit(spec.base_commit)
        self._run_git(
            "-C",
            str(self.repository),
            "worktree",
            "add",
            "-b",
            branch_name,
            str(target),
            resolved_base,
        )
        actual_head = self._run_git(
            "-C", str(target), "rev-parse", "HEAD"
        ).strip()
        if actual_head != resolved_base:
            raise WorkspaceError(
                f"created worktree at unexpected commit: {actual_head}"
            )

        return WorkspaceManifest(
            workspace_id=spec.workspace_id,
            task_id=spec.task_id,
            repository_root=str(self.repository),
            path=str(target),
            requested_base_commit=spec.base_commit,
            resolved_base_commit=resolved_base,
            branch_name=branch_name,
        )

    def inspect(self, manifest: WorkspaceManifest) -> WorkspaceInspection:
        target = self._validate_manifest(manifest)
        head_commit = self._run_git(
            "-C", str(target), "rev-parse", "HEAD"
        ).strip()
        branch_name = self._run_git(
            "-C", str(target), "branch", "--show-current"
        ).strip()
        if branch_name != manifest.branch_name:
            raise WorkspaceError(
                "registered worktree branch does not match the workspace manifest"
            )
        changed_paths = self._changed_paths(target, manifest.resolved_base_commit)
        status = self._run_git_bytes(
            "-C",
            str(target),
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        )
        return WorkspaceInspection(
            workspace_id=manifest.workspace_id,
            task_id=manifest.task_id,
            head_commit=head_commit,
            branch_name=branch_name,
            changed_paths=changed_paths,
            working_tree_clean=not status,
        )

    def collect_patch(
        self, manifest: WorkspaceManifest, task: TaskSpec
    ) -> WorkspacePatch:
        target = self._validate_manifest(manifest)
        if task.task_id != manifest.task_id:
            raise WorkspaceError("task and workspace identifiers do not match")
        if self._resolve_commit(task.base_commit) != manifest.resolved_base_commit:
            raise WorkspaceError("task and workspace base commits do not match")

        inspection = self.inspect(manifest)
        violations = tuple(
            path
            for path in inspection.changed_paths
            if not self._path_is_allowed(
                path,
                allowed_paths=task.allowed_paths,
                forbidden_paths=task.forbidden_paths,
            )
        )
        if violations:
            raise PathPolicyViolation(violations)

        patch_parts = [
            self._run_git(
                "-C",
                str(target),
                "diff",
                "--binary",
                "--full-index",
                "--no-renames",
                manifest.resolved_base_commit,
                "--",
            )
        ]
        for path in self._untracked_paths(target):
            result = self._run_git_process(
                "-C",
                str(target),
                "diff",
                "--no-index",
                "--binary",
                "--full-index",
                "--",
                os.devnull,
                path,
                accepted_returncodes=(0, 1),
            )
            patch_parts.append(result.stdout)
        patch_text = "".join(patch_parts)
        patch_hash = hashlib.sha256(patch_text.encode("utf-8")).hexdigest()
        return WorkspacePatch(
            workspace_id=manifest.workspace_id,
            task_id=manifest.task_id,
            base_commit=manifest.resolved_base_commit,
            changed_paths=inspection.changed_paths,
            patch_text=patch_text,
            sha256=patch_hash,
        )

    def remove(self, manifest: WorkspaceManifest) -> None:
        target = self._validate_manifest(manifest)
        inspection = self.inspect(manifest)
        if not inspection.working_tree_clean:
            raise WorkspaceDirtyError(
                "refusing to remove a workspace with uncommitted or untracked changes"
            )
        self._run_git(
            "-C",
            str(self.repository),
            "worktree",
            "remove",
            "--",
            str(target),
        )
        if target.exists():
            raise WorkspaceError(f"Git reported removal but path still exists: {target}")

    def _target_for(self, workspace_id: str) -> Path:
        target = (self.workspace_root / workspace_id).resolve(strict=False)
        if target.parent != self.workspace_root:
            raise WorkspaceError("workspace target escapes the configured root")
        return target

    def _validate_manifest(self, manifest: WorkspaceManifest) -> Path:
        if Path(manifest.repository_root).resolve() != self.repository:
            raise WorkspaceError("workspace belongs to a different repository")
        target = self._target_for(manifest.workspace_id)
        if Path(manifest.path).resolve() != target:
            raise WorkspaceError("manifest path does not match its workspace id")
        if not target.exists():
            raise WorkspaceNotFoundError(f"workspace path does not exist: {target}")
        if target not in self._registered_worktrees():
            raise WorkspaceNotFoundError("workspace is not registered with Git")
        return target

    def _registered_worktrees(self) -> set[Path]:
        output = self._run_git(
            "-C", str(self.repository), "worktree", "list", "--porcelain"
        )
        return {
            Path(line.removeprefix("worktree ")).resolve()
            for line in output.splitlines()
            if line.startswith("worktree ")
        }

    def _branch_exists(self, branch_name: str) -> bool:
        result = self._run_git_process(
            "-C",
            str(self.repository),
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{branch_name}",
            accepted_returncodes=(0, 1),
        )
        return result.returncode == 0

    def _resolve_commit(self, revision: str) -> str:
        try:
            return self._run_git(
                "-C",
                str(self.repository),
                "rev-parse",
                "--verify",
                f"{revision}^{{commit}}",
            ).strip()
        except GitCommandError as exc:
            raise WorkspaceError(f"base commit cannot be resolved: {revision}") from exc

    def _changed_paths(self, target: Path, base_commit: str) -> tuple[str, ...]:
        tracked = self._run_git_bytes(
            "-C",
            str(target),
            "diff",
            "--name-only",
            "-z",
            "--no-renames",
            base_commit,
            "--",
        )
        paths = set(self._decode_nul_paths(tracked))
        paths.update(self._untracked_paths(target))
        return tuple(sorted(paths))

    def _untracked_paths(self, target: Path) -> tuple[str, ...]:
        output = self._run_git_bytes(
            "-C",
            str(target),
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        )
        return tuple(sorted(self._decode_nul_paths(output)))

    @staticmethod
    def _decode_nul_paths(output: bytes) -> tuple[str, ...]:
        try:
            return tuple(
                raw.decode("utf-8") for raw in output.split(b"\0") if raw
            )
        except UnicodeDecodeError as exc:
            raise WorkspaceError("Git returned a non-UTF-8 path") from exc

    @staticmethod
    def _path_is_allowed(
        path: str,
        *,
        allowed_paths: tuple[str, ...],
        forbidden_paths: tuple[str, ...],
    ) -> bool:
        if any(GitWorktreeManager._is_at_or_below(path, root) for root in forbidden_paths):
            return False
        return any(
            GitWorktreeManager._is_at_or_below(path, root) for root in allowed_paths
        )

    @staticmethod
    def _is_at_or_below(path: str, root: str) -> bool:
        if root in {".", "./"}:
            return True
        candidate = PurePosixPath(path)
        boundary = PurePosixPath(root.rstrip("/"))
        return candidate == boundary or boundary in candidate.parents

    def _run_git(self, *arguments: str) -> str:
        return self._run_git_process(*arguments).stdout

    def _run_git_bytes(self, *arguments: str) -> bytes:
        command = self._git_command(arguments)
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                timeout=self.command_timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitCommandError(f"Git command could not complete: {command}") from exc
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            raise GitCommandError(
                f"Git command failed with {result.returncode}: {stderr}"
            )
        return result.stdout

    def _run_git_process(
        self,
        *arguments: str,
        accepted_returncodes: tuple[int, ...] = (0,),
    ) -> subprocess.CompletedProcess[str]:
        command = self._git_command(arguments)
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
            raise GitCommandError(f"Git command could not complete: {command}") from exc
        if result.returncode not in accepted_returncodes:
            raise GitCommandError(
                f"Git command failed with {result.returncode}: {result.stderr.strip()}"
            )
        return result

    @staticmethod
    def _git_command(arguments: tuple[str, ...]) -> list[str]:
        return ["git", "-c", f"core.hooksPath={os.devnull}", *arguments]
