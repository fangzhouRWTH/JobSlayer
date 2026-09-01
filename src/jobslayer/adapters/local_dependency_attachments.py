"""Resolve and re-inspect operator-owned local dependencies without mutating them."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess

from jobslayer.application.runbook import LocalDependencyAttachmentConfig
from jobslayer.task_manager.binding import TaskManagerDependencyAttachment


class LocalDependencyAttachmentError(RuntimeError):
    pass


def resolve_local_dependency_attachment(
    config: LocalDependencyAttachmentConfig,
    configured_path: str | os.PathLike[str] | None,
) -> TaskManagerDependencyAttachment:
    common = {
        "attachment_id": config.attachment_id,
        "kind": config.kind,
        "environment_variable": config.environment_variable,
        "expected_sha256": config.expected_sha256,
        "expected_revision": config.expected_revision,
        "repository_urls": config.repository_urls,
    }
    if configured_path is None:
        return TaskManagerDependencyAttachment(
            **common,
            issue=(
                "operator path is not configured; pass the attachment explicitly"
            ),
        )
    candidate = Path(configured_path).expanduser()
    try:
        if candidate.is_symlink():
            raise LocalDependencyAttachmentError("attachment root cannot be a symlink")
        root = candidate.resolve(strict=True)
        if config.kind == "file":
            if not root.is_file():
                raise LocalDependencyAttachmentError(
                    "configured attachment is not a regular file"
                )
            if config.expose_relative_path != ".":
                raise LocalDependencyAttachmentError(
                    "file attachments must expose the configured file itself"
                )
            exposed = root
            observed_sha256 = _file_sha256(root)
            observed_revision = None
            observed_repository_url = None
            working_tree_clean = None
        else:
            if not root.is_dir():
                raise LocalDependencyAttachmentError(
                    "configured attachment is not a directory"
                )
            exposed_candidate = root / config.expose_relative_path
            if exposed_candidate.is_symlink():
                raise LocalDependencyAttachmentError(
                    "exposed dependency path cannot be a symlink"
                )
            exposed = exposed_candidate.resolve(strict=True)
            if not exposed.is_relative_to(root):
                raise LocalDependencyAttachmentError(
                    "exposed dependency path escapes the attachment root"
                )
            if config.kind == "git_checkout":
                observed_revision = _git(root, "rev-parse", "HEAD").strip()
                observed_repository_url = _git(
                    root, "remote", "get-url", "origin"
                ).strip()
                working_tree_clean = not bool(
                    _git(
                        root,
                        "status",
                        "--porcelain=v1",
                        "--untracked-files=all",
                    ).strip()
                )
                observed_sha256 = _git_archive_sha256(root, observed_revision)
            else:
                observed_sha256 = _directory_sha256(root)
                observed_revision = None
                observed_repository_url = None
                working_tree_clean = None
        issue = _identity_issue(
            config,
            observed_sha256=observed_sha256,
            observed_revision=observed_revision,
            observed_repository_url=observed_repository_url,
            working_tree_clean=working_tree_clean,
        )
        return TaskManagerDependencyAttachment(
            **common,
            observed_sha256=observed_sha256,
            observed_revision=observed_revision,
            observed_repository_url=observed_repository_url,
            root_path=str(root),
            exposed_path=str(exposed),
            working_tree_clean=working_tree_clean,
            issue=issue,
        )
    except (OSError, subprocess.SubprocessError, LocalDependencyAttachmentError) as exc:
        return TaskManagerDependencyAttachment(
            **common,
            issue=f"local attachment inspection failed: {exc}",
        )


def reinspect_local_dependency_attachment(
    attachment: TaskManagerDependencyAttachment,
) -> TaskManagerDependencyAttachment:
    if attachment.root_path is None:
        return attachment
    root = Path(attachment.root_path)
    if attachment.exposed_path is None:
        relative = "."
    elif attachment.kind == "file":
        relative = "."
    else:
        try:
            relative = Path(attachment.exposed_path).relative_to(root).as_posix()
        except ValueError:
            relative = "../invalid"
    config = LocalDependencyAttachmentConfig(
        attachment_id=attachment.attachment_id,
        kind=attachment.kind,
        environment_variable=attachment.environment_variable,
        expected_sha256=attachment.expected_sha256,
        expected_revision=attachment.expected_revision,
        repository_urls=attachment.repository_urls,
        expose_relative_path=relative,
    )
    return resolve_local_dependency_attachment(config, root)


def directory_sha256(path: str | os.PathLike[str]) -> str:
    """Public deterministic directory digest used by deployment tooling/tests."""

    return _directory_sha256(Path(path).resolve(strict=True))


def _identity_issue(
    config: LocalDependencyAttachmentConfig,
    *,
    observed_sha256: str,
    observed_revision: str | None,
    observed_repository_url: str | None,
    working_tree_clean: bool | None,
) -> str | None:
    if observed_sha256 != config.expected_sha256:
        return "content SHA-256 does not match the source-controlled requirement"
    if config.kind == "git_checkout":
        assert config.expected_revision is not None
        if observed_revision is None or (
            observed_revision.lower() != config.expected_revision.lower()
        ):
            return "Git revision does not match the source-controlled requirement"
        if observed_repository_url not in config.repository_urls:
            return "Git origin is not one of the source-controlled repository URLs"
        if working_tree_clean is not True:
            return "Git checkout contains tracked or untracked changes"
    return None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1_048_576):
            digest.update(chunk)
    return digest.hexdigest()


def _directory_sha256(root: Path) -> str:
    digest = hashlib.sha256(b"jobslayer-dependency-directory-v1\0")
    entries = sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix())
    for path in entries:
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise LocalDependencyAttachmentError(
                f"dependency directory contains a symlink: {relative}"
            )
        if path.is_dir():
            continue
        if not path.is_file():
            raise LocalDependencyAttachmentError(
                f"dependency directory contains a special file: {relative}"
            )
        stat = path.stat()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(b"x" if stat.st_mode & 0o111 else b"-")
        digest.update(b"\0")
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(b"\0")
        with path.open("rb") as stream:
            while chunk := stream.read(1_048_576):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _git_archive_sha256(root: Path, revision: str) -> str:
    process = subprocess.Popen(
        ["git", "-C", str(root), "archive", "--format=tar", revision],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    digest = hashlib.sha256()
    try:
        while chunk := process.stdout.read(1_048_576):
            digest.update(chunk)
        stderr = process.stderr.read()
        returncode = process.wait()
    finally:
        process.stdout.close()
        process.stderr.close()
    if returncode != 0:
        raise LocalDependencyAttachmentError(
            "could not hash Git attachment: "
            + stderr.decode("utf-8", errors="replace").strip()
        )
    return digest.hexdigest()


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout


__all__ = [
    "directory_sha256",
    "LocalDependencyAttachmentError",
    "reinspect_local_dependency_attachment",
    "resolve_local_dependency_attachment",
]
