from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from pydantic import ValidationError

from jobslayer.domain.models import ArtifactManifest


class ArtifactRegistryError(RuntimeError):
    """Base error for local artifact registration and integrity failures."""


class ArtifactIntegrityError(ArtifactRegistryError):
    pass


class LocalArtifactRegistry:
    """Content-addressed, immutable local artifact storage for Phase 0."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve(strict=False)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.root.is_dir():
            raise ArtifactRegistryError("artifact root is not a directory")

    def register_bytes(
        self,
        *,
        task_id: str,
        artifact_type: str,
        producer: str,
        content: bytes,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactManifest:
        if not isinstance(content, bytes):
            raise TypeError("artifact content must be bytes")
        digest = hashlib.sha256(content).hexdigest()
        destination = self._path_for(digest)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if destination.exists():
            if self._digest_file(destination) != digest:
                raise ArtifactIntegrityError(
                    "existing content-addressed artifact does not match its path"
                )
        else:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".artifact-", dir=destination.parent
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.chmod(0o400)
                try:
                    os.link(temporary, destination)
                except FileExistsError:
                    if self._digest_file(destination) != digest:
                        raise ArtifactIntegrityError(
                            "concurrently registered artifact has unexpected content"
                        )
            finally:
                temporary.unlink(missing_ok=True)

        manifest = ArtifactManifest(
            artifact_id=f"artifact-{uuid4().hex}",
            task_id=task_id,
            run_id=run_id,
            artifact_type=artifact_type,
            uri=destination.as_uri(),
            sha256=digest,
            size_bytes=len(content),
            producer=producer,
            metadata=dict(metadata or {}),
        )
        self._store_manifest(manifest)
        return manifest

    def register_file(
        self,
        path: str | Path,
        *,
        task_id: str,
        artifact_type: str,
        producer: str,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactManifest:
        source = Path(path).resolve(strict=True)
        if not source.is_file():
            raise ArtifactRegistryError("artifact source is not a regular file")
        return self.register_bytes(
            task_id=task_id,
            artifact_type=artifact_type,
            producer=producer,
            content=source.read_bytes(),
            run_id=run_id,
            metadata=metadata,
        )

    def read(self, manifest: ArtifactManifest) -> bytes:
        path = self._validated_manifest_path(manifest)
        content = path.read_bytes()
        if len(content) != manifest.size_bytes:
            raise ArtifactIntegrityError("artifact size does not match its manifest")
        if hashlib.sha256(content).hexdigest() != manifest.sha256:
            raise ArtifactIntegrityError("artifact hash does not match its manifest")
        return content

    def get(self, artifact_id: str) -> ArtifactManifest:
        if not artifact_id or "/" in artifact_id or "\\" in artifact_id:
            raise ArtifactRegistryError("invalid artifact id")
        path = self._manifest_path_for(artifact_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            manifest = ArtifactManifest.model_validate(payload)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise ArtifactRegistryError("artifact manifest is unavailable or invalid") from exc
        if manifest.artifact_id != artifact_id:
            raise ArtifactIntegrityError("artifact manifest id does not match its path")
        if not self.verify(manifest):
            raise ArtifactIntegrityError("artifact content does not match its manifest")
        return manifest

    def verify(self, manifest: ArtifactManifest) -> bool:
        try:
            self.read(manifest)
        except (ArtifactRegistryError, OSError):
            return False
        return True

    def _path_for(self, digest: str) -> Path:
        return self.root / "objects" / digest[:2] / digest

    def _manifest_path_for(self, artifact_id: str) -> Path:
        return self.root / "manifests" / f"{artifact_id}.json"

    def _store_manifest(self, manifest: ArtifactManifest) -> None:
        payload = json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        destination = self._manifest_path_for(manifest.artifact_id)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".manifest-", dir=destination.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(0o400)
            try:
                os.link(temporary, destination)
            except FileExistsError as exc:
                raise ArtifactRegistryError("artifact manifest id already exists") from exc
        finally:
            temporary.unlink(missing_ok=True)

    def _validated_manifest_path(self, manifest: ArtifactManifest) -> Path:
        parsed = urlsplit(manifest.uri)
        if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
            raise ArtifactIntegrityError("artifact URI is not a local file URI")
        candidate = Path(unquote(parsed.path))
        expected = self._path_for(manifest.sha256)
        if candidate != expected:
            raise ArtifactIntegrityError("artifact URI does not match its content hash")
        try:
            actual = candidate.resolve(strict=True)
        except OSError as exc:
            raise ArtifactIntegrityError("artifact URI is not a readable local file") from exc
        if actual != candidate or not actual.is_relative_to(self.root):
            raise ArtifactIntegrityError("artifact path uses a symlink or escapes its root")
        if not actual.is_file():
            raise ArtifactIntegrityError("artifact path is not a regular file")
        return actual

    @staticmethod
    def _digest_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(65_536), b""):
                digest.update(chunk)
        return digest.hexdigest()
