"""Build immutable, size-bounded context manifests from admitted files."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import mimetypes
from pathlib import Path
from uuid import uuid4

from jobslayer.artifacts.registry import ArtifactRegistry
from jobslayer.governance import (
    ContextComponent,
    ContextPackage,
    ContextPackageError,
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class ContextPackageBuilder:
    def __init__(self, admitted_root: str | Path, artifacts: ArtifactRegistry):
        self.admitted_root = Path(admitted_root).resolve(strict=True)
        if not self.admitted_root.is_dir() or self.admitted_root.is_symlink():
            raise ContextPackageError("context root must be a real directory")
        self.artifacts = artifacts

    def build(
        self,
        *,
        task_id: str,
        run_id: str,
        sources: dict[str, str | Path],
        maximum_size_bytes: int,
        package_id: str | None = None,
        now: datetime | None = None,
    ) -> ContextPackage:
        if not sources:
            raise ContextPackageError("context package needs at least one source")
        if maximum_size_bytes < 1:
            raise ContextPackageError("context size limit must be positive")
        prepared: list[tuple[str, Path, bytes, str]] = []
        total = 0
        for logical_name, requested in sorted(sources.items()):
            source = Path(requested)
            if not source.is_absolute():
                source = self.admitted_root / source
            if source.is_symlink():
                raise ContextPackageError("context source must not be a symlink")
            try:
                resolved = source.resolve(strict=True)
                relative = resolved.relative_to(self.admitted_root)
                content = resolved.read_bytes()
            except (OSError, ValueError) as exc:
                raise ContextPackageError(
                    "context source escapes the admitted root or is unreadable"
                ) from exc
            if not resolved.is_file():
                raise ContextPackageError("context source must be a regular file")
            total += len(content)
            if total > maximum_size_bytes:
                raise ContextPackageError("context package exceeds its byte budget")
            media_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
            prepared.append((logical_name, relative.as_posix(), content, media_type))

        components: list[ContextComponent] = []
        for logical_name, relative, content, media_type in prepared:
            manifest = self.artifacts.register_bytes(
                task_id=task_id,
                run_id=run_id,
                artifact_type="context-component",
                producer="context-package-builder",
                content=content,
                metadata={
                    "logical_name": logical_name,
                    "source_path": relative,
                    "media_type": media_type,
                },
            )
            components.append(
                ContextComponent(
                    logical_name=logical_name,
                    source_path=relative,
                    artifact_id=manifest.artifact_id,
                    sha256=manifest.sha256,
                    size_bytes=manifest.size_bytes,
                    media_type=media_type,
                )
            )
        package_payload = [item.model_dump(mode="json") for item in components]
        return ContextPackage(
            package_id=package_id or f"context-{uuid4().hex}",
            task_id=task_id,
            run_id=run_id,
            components=tuple(components),
            total_size_bytes=total,
            package_sha256=hashlib.sha256(_canonical(package_payload)).hexdigest(),
            created_at=now or datetime.now(UTC),
        )

    def verify(self, package: ContextPackage) -> bool:
        try:
            manifests = tuple(
                self.artifacts.get(component.artifact_id)
                for component in package.components
            )
        except (OSError, RuntimeError):
            return False
        for component, manifest in zip(package.components, manifests, strict=True):
            if (
                manifest.task_id != package.task_id
                or manifest.run_id != package.run_id
                or manifest.artifact_type != "context-component"
                or manifest.sha256 != component.sha256
                or manifest.size_bytes != component.size_bytes
            ):
                return False
        payload = [item.model_dump(mode="json") for item in package.components]
        return (
            sum(item.size_bytes for item in package.components)
            == package.total_size_bytes
            and hashlib.sha256(_canonical(payload)).hexdigest()
            == package.package_sha256
        )


__all__ = ["ContextPackageBuilder"]
