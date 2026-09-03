"""Read-only adapter for the pinned UI/UX Pro Max core search engine."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Any

from pydantic import Field, ValidationError, model_validator

from jobslayer.domain.models import DomainModel
from jobslayer.ui_advice import (
    UIAdviceMode,
    UIAdviceProviderIdentity,
    UIAdviceRecommendation,
    UIAdviceRecommendationKind,
    UIAdviceRequest,
    UIAdviceSourceField,
    UIAdvisorResponse,
)
from jobslayer.ui_design import IDENTIFIER_PATTERN, SHA256_PATTERN


class UIUXProMaxError(RuntimeError):
    """Base error for lock, snapshot, execution, and output failures."""


class UIUXProMaxLockError(UIUXProMaxError):
    pass


class UIUXProMaxExecutionError(UIUXProMaxError):
    pass


class UIUXProMaxOutputError(UIUXProMaxError):
    pass


class UIUXProMaxLock(DomainModel):
    schema_version: str = "1.0"
    integration_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    provider_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    provider_version: str = Field(min_length=1, max_length=64)
    source_repository: str = Field(min_length=1, max_length=512)
    source_ref: str = Field(min_length=1, max_length=128)
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    license: str = Field(min_length=1, max_length=64)
    snapshot_path: str = Field(min_length=1, max_length=512)
    snapshot_sha256: str = Field(pattern=SHA256_PATTERN)
    expected_file_count: int = Field(ge=1, le=10_000)
    expected_total_bytes: int = Field(ge=1, le=100 * 1024 * 1024)
    entrypoint: str = Field(min_length=1, max_length=256)
    validation_entrypoint: str = Field(min_length=1, max_length=256)
    supported_domains: tuple[str, ...] = Field(min_length=1, max_length=64)
    supported_stacks: tuple[str, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_lock(self) -> UIUXProMaxLock:
        for name, value in (
            ("snapshot_path", self.snapshot_path),
            ("entrypoint", self.entrypoint),
            ("validation_entrypoint", self.validation_entrypoint),
        ):
            path = PurePosixPath(value)
            if (
                path.is_absolute()
                or ".." in path.parts
                or value.startswith("./")
                or "\\" in value
            ):
                raise ValueError(f"{name} must be a normalized relative path")
        for name, values in (
            ("supported_domains", self.supported_domains),
            ("supported_stacks", self.supported_stacks),
        ):
            if len(values) != len(set(values)) or values != tuple(sorted(values)):
                raise ValueError(f"{name} must be unique and sorted")
            if any(not value or len(value) > 64 for value in values):
                raise ValueError(f"{name} contains an invalid identifier")
        return self


def _snapshot_digest(root: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    count = 0
    total = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise UIUXProMaxLockError("UI/UX Pro Max snapshot cannot contain symlinks")
        if path.is_dir():
            continue
        if not path.is_file():
            raise UIUXProMaxLockError("UI/UX Pro Max snapshot has a special file")
        relative = path.relative_to(root).as_posix()
        parts = PurePosixPath(relative).parts
        allowed = relative == "LICENSE" or (
            parts[0] == "data" and path.suffix in {".csv", ".json"}
        ) or (
            len(parts) == 2
            and parts[0] == "scripts"
            and parts[1]
            in {
                "core.py",
                "design_system.py",
                "reasoning_contract.py",
                "search.py",
                "validate_data.py",
            }
        )
        if not allowed:
            raise UIUXProMaxLockError(
                f"UI/UX Pro Max snapshot contains an unapproved path: {relative}"
            )
        content = path.read_bytes()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
        count += 1
        total += len(content)
    return digest.hexdigest(), count, total


def _minimal_environment() -> dict[str, str]:
    environment: dict[str, str] = {}
    for name in ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    environment.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    return environment


class UIUXProMaxAdvisor:
    """Invoke only the checked-in, hash-locked core JSON search surface."""

    def __init__(
        self,
        repository_root: str | Path,
        lock_path: str | Path,
        *,
        python_executable: str | Path | None = None,
        timeout_seconds: float = 20,
        max_output_bytes: int = 2 * 1024 * 1024,
    ):
        self.repository_root = Path(repository_root).resolve(strict=True)
        candidate = Path(lock_path)
        if not candidate.is_absolute():
            candidate = self.repository_root / candidate
        if candidate.is_symlink():
            raise UIUXProMaxLockError("UI/UX Pro Max lock file cannot be a symlink")
        try:
            self.lock_path = candidate.resolve(strict=True)
        except OSError as exc:
            raise UIUXProMaxLockError("UI/UX Pro Max lock file is unavailable") from exc
        if (
            not self.lock_path.is_file()
            or not self.lock_path.is_relative_to(self.repository_root)
        ):
            raise UIUXProMaxLockError("UI/UX Pro Max lock file is outside the repository")
        try:
            payload = json.loads(self.lock_path.read_text(encoding="utf-8"))
            self.lock = UIUXProMaxLock.model_validate(payload)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise UIUXProMaxLockError("UI/UX Pro Max lock file is invalid") from exc
        self.python_executable = Path(python_executable or sys.executable).resolve()
        if timeout_seconds <= 0 or timeout_seconds > 120:
            raise ValueError("UI/UX Pro Max timeout must be within 120 seconds")
        if max_output_bytes < 1_024 or max_output_bytes > 16 * 1024 * 1024:
            raise ValueError("UI/UX Pro Max output limit is outside the safe range")
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes

    @property
    def snapshot_root(self) -> Path:
        return self.repository_root / self.lock.snapshot_path

    def validate_snapshot(
        self, *, run_upstream_validation: bool = True
    ) -> UIAdviceProviderIdentity:
        root = self.snapshot_root
        try:
            resolved = root.resolve(strict=True)
        except OSError as exc:
            raise UIUXProMaxLockError("UI/UX Pro Max snapshot is unavailable") from exc
        if root.is_symlink() or not resolved.is_dir() or not resolved.is_relative_to(
            self.repository_root
        ):
            raise UIUXProMaxLockError("UI/UX Pro Max snapshot root is unsafe")
        actual_hash, actual_count, actual_bytes = _snapshot_digest(resolved)
        if actual_hash != self.lock.snapshot_sha256:
            raise UIUXProMaxLockError("UI/UX Pro Max snapshot hash mismatch")
        if actual_count != self.lock.expected_file_count:
            raise UIUXProMaxLockError("UI/UX Pro Max snapshot file count mismatch")
        if actual_bytes != self.lock.expected_total_bytes:
            raise UIUXProMaxLockError("UI/UX Pro Max snapshot byte count mismatch")
        for relative in (self.lock.entrypoint, self.lock.validation_entrypoint):
            target = (resolved / relative).resolve(strict=True)
            if not target.is_file() or not target.is_relative_to(resolved):
                raise UIUXProMaxLockError("UI/UX Pro Max entrypoint is unsafe")
        if run_upstream_validation:
            self._run((str(resolved / self.lock.validation_entrypoint),))
        return UIAdviceProviderIdentity(
            provider_id=self.lock.provider_id,
            provider_version=self.lock.provider_version,
            source_repository=self.lock.source_repository,
            source_ref=self.lock.source_ref,
            source_commit=self.lock.source_commit,
            snapshot_sha256=self.lock.snapshot_sha256,
            license=self.lock.license,
        )

    def advise(self, request: UIAdviceRequest) -> UIAdvisorResponse:
        provider = self.validate_snapshot(run_upstream_validation=False)
        if request.domain is not None and request.domain not in self.lock.supported_domains:
            raise UIUXProMaxExecutionError(
                f"unsupported UI/UX Pro Max domain: {request.domain}"
            )
        if request.stack is not None and request.stack not in self.lock.supported_stacks:
            raise UIUXProMaxExecutionError(
                f"unsupported UI/UX Pro Max stack: {request.stack}"
            )
        argv = [str(self.snapshot_root / self.lock.entrypoint)]
        if request.mode == UIAdviceMode.DESIGN_SYSTEM:
            argv.extend(("--design-system", "--json"))
            if request.project_name is not None:
                argv.append(f"--project-name={request.project_name}")
            for option, value in (
                ("--variance", request.variance),
                ("--motion", request.motion),
                ("--density", request.density),
            ):
                if value is not None:
                    argv.extend((option, str(value)))
        elif request.mode == UIAdviceMode.DOMAIN:
            assert request.domain is not None
            argv.extend(
                ("--domain", request.domain, "--max-results", str(request.max_results), "--json")
            )
        else:
            assert request.stack is not None
            argv.extend(
                ("--stack", request.stack, "--max-results", str(request.max_results), "--json")
            )
        # Terminate option parsing before the untrusted natural-language query so
        # provider flags such as --persist cannot be injected through that field.
        argv.extend(("--", request.query))
        raw_output = self._run(tuple(argv))
        try:
            payload = json.loads(raw_output)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise UIUXProMaxOutputError(
                "UI/UX Pro Max returned invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise UIUXProMaxOutputError("UI/UX Pro Max output must be a JSON object")
        recommendations = _normalize_recommendations(request, payload)
        return UIAdvisorResponse(
            provider=provider,
            recommendations=recommendations,
            raw_output=raw_output,
        )

    def _run(self, provider_argv: tuple[str, ...]) -> bytes:
        script = Path(provider_argv[0])
        isolated_runner = (
            "import runpy,sys;"
            "script_dir=sys.argv.pop(1);"
            "script=sys.argv.pop(1);"
            "sys.argv[0]=script;"
            "sys.path.insert(0,script_dir);"
            "runpy.run_path(script,run_name='__main__')"
        )
        command = (
            str(self.python_executable),
            "-I",
            "-B",
            "-c",
            isolated_runner,
            str(script.parent),
            str(script),
            *provider_argv[1:],
        )
        try:
            with TemporaryDirectory(prefix="jobslayer-ui-advice-") as directory:
                result = subprocess.run(
                    command,
                    cwd=directory,
                    env=_minimal_environment(),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=self.timeout_seconds,
                )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise UIUXProMaxExecutionError(
                "UI/UX Pro Max execution could not complete"
            ) from exc
        if len(result.stdout) > self.max_output_bytes or len(result.stderr) > 64 * 1024:
            raise UIUXProMaxOutputError("UI/UX Pro Max output exceeded its bound")
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise UIUXProMaxExecutionError(
                f"UI/UX Pro Max exited with {result.returncode}: {detail[:1_000]}"
            )
        return result.stdout


def _text(value: Any, *, limit: int = 12_000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        result = value
    else:
        result = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return result[:limit]


def _source_fields(value: dict[str, Any]) -> tuple[UIAdviceSourceField, ...]:
    return tuple(
        UIAdviceSourceField(name=str(name)[:160], value=_text(field_value))
        for name, field_value in sorted(value.items(), key=lambda item: str(item[0]))
    )


def _recommendation(
    *,
    kind: UIAdviceRecommendationKind,
    title: str,
    guidance: str,
    source_ref: str,
    source: dict[str, Any],
    do: str | None = None,
    avoid: str | None = None,
    severity: str | None = None,
) -> UIAdviceRecommendation:
    identity = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    recommendation_id = "uipro-" + hashlib.sha256(
        (kind.value + "\0" + source_ref + "\0" + identity).encode("utf-8")
    ).hexdigest()[:24]
    return UIAdviceRecommendation(
        recommendation_id=recommendation_id,
        kind=kind,
        title=(title.strip() or kind.value)[:400],
        guidance=(guidance.strip() or identity)[:12_000],
        do=do[:12_000] if do else None,
        avoid=avoid[:12_000] if avoid else None,
        severity=severity[:64] if severity else None,
        source_ref=(source_ref.strip() or title.strip() or kind.value)[:400],
        source_fields=_source_fields(source),
    )


def _design_system_recommendations(
    payload: dict[str, Any],
) -> tuple[UIAdviceRecommendation, ...]:
    value = payload.get("design_system")
    if not isinstance(value, dict):
        raise UIUXProMaxOutputError("design-system output is missing")
    result: list[UIAdviceRecommendation] = []
    category = _text(value.get("category"))
    pattern = value.get("pattern")
    if isinstance(pattern, dict) and pattern:
        title = _text(pattern.get("name") or category or "Product pattern")
        result.append(
            _recommendation(
                kind=UIAdviceRecommendationKind.PRODUCT_PATTERN,
                title=title,
                guidance="; ".join(
                    item
                    for item in (
                        _text(pattern.get("sections")),
                        _text(pattern.get("conversion")),
                    )
                    if item
                ),
                source_ref=title,
                source=pattern,
            )
        )
    for key, kind, fallback in (
        ("style", UIAdviceRecommendationKind.VISUAL_STYLE, "Visual style"),
        ("colors", UIAdviceRecommendationKind.COLOR_SYSTEM, "Color system"),
        ("typography", UIAdviceRecommendationKind.TYPOGRAPHY, "Typography"),
        ("spacing_scale", UIAdviceRecommendationKind.SPACING, "Spacing scale"),
    ):
        item = value.get(key)
        if not isinstance(item, dict) or not item:
            continue
        title = _text(
            item.get("name")
            or item.get("heading")
            or item.get("id")
            or fallback
        )
        guidance = "; ".join(
            part
            for part in (
                _text(item.get("best_for")),
                _text(item.get("effects")),
                _text(item.get("mood")),
            )
            if part
        ) or _text(item)
        result.append(
            _recommendation(
                kind=kind,
                title=title,
                guidance=guidance,
                source_ref=_text(item.get("id") or title),
                source=item,
            )
        )
    constraints = value.get("constraints")
    if isinstance(constraints, list):
        for constraint in constraints:
            text = _text(constraint)
            if text:
                result.append(
                    _recommendation(
                        kind=UIAdviceRecommendationKind.CONSTRAINT,
                        title=text,
                        guidance=text,
                        source_ref=text,
                        source={"constraint": text},
                    )
                )
    anti_patterns = _text(value.get("anti_patterns"))
    if anti_patterns:
        result.append(
            _recommendation(
                kind=UIAdviceRecommendationKind.ANTI_PATTERN,
                title="Anti-patterns",
                guidance="Avoid the provider-identified anti-patterns.",
                avoid=anti_patterns,
                source_ref="anti_patterns",
                source={"anti_patterns": anti_patterns},
            )
        )
    return tuple(result)


_DOMAIN_KINDS = {
    "product": UIAdviceRecommendationKind.PRODUCT_PATTERN,
    "landing": UIAdviceRecommendationKind.PRODUCT_PATTERN,
    "style": UIAdviceRecommendationKind.VISUAL_STYLE,
    "color": UIAdviceRecommendationKind.COLOR_SYSTEM,
    "typography": UIAdviceRecommendationKind.TYPOGRAPHY,
    "ux": UIAdviceRecommendationKind.UX_GUIDELINE,
    "react": UIAdviceRecommendationKind.STACK_GUIDELINE,
    "web": UIAdviceRecommendationKind.UX_GUIDELINE,
    "chart": UIAdviceRecommendationKind.CHART,
    "icons": UIAdviceRecommendationKind.ICON,
    "gsap": UIAdviceRecommendationKind.MOTION,
}


def _result_recommendations(
    request: UIAdviceRequest, payload: dict[str, Any]
) -> tuple[UIAdviceRecommendation, ...]:
    rows = payload.get("results")
    if not isinstance(rows, list):
        raise UIUXProMaxOutputError("search output has no results array")
    recommendations: list[UIAdviceRecommendation] = []
    for row in rows:
        if not isinstance(row, dict):
            raise UIUXProMaxOutputError("search output contains a non-object result")
        title = next(
            (
                _text(row.get(name))
                for name in (
                    "Style Category",
                    "Product Type",
                    "Issue",
                    "Guideline",
                    "Font Pairing Name",
                    "Best Chart Type",
                    "Pattern Name",
                    "Icon Name",
                    "Family",
                    "Category",
                )
                if _text(row.get(name))
            ),
            "UI/UX recommendation",
        )
        guidance = next(
            (
                _text(row.get(name))
                for name in (
                    "Description",
                    "Best For",
                    "When to Use",
                    "Usage",
                    "Implementation Checklist",
                )
                if _text(row.get(name))
            ),
            _text(row),
        )
        source_ref = next(
            (
                _text(row.get(name))
                for name in (
                    "Style ID",
                    "Pattern ID",
                    "Product Type",
                    "Issue",
                    "Guideline",
                    "Icon Name",
                    "Family",
                )
                if _text(row.get(name))
            ),
            title,
        )
        kind = (
            UIAdviceRecommendationKind.STACK_GUIDELINE
            if request.mode == UIAdviceMode.STACK
            else _DOMAIN_KINDS.get(request.domain or "", UIAdviceRecommendationKind.OTHER)
        )
        recommendations.append(
            _recommendation(
                kind=kind,
                title=title,
                guidance=guidance,
                do=_text(row.get("Do")) or None,
                avoid=(
                    _text(row.get("Don't"))
                    or _text(row.get("When NOT to Use"))
                    or None
                ),
                severity=_text(row.get("Severity")) or None,
                source_ref=source_ref,
                source=row,
            )
        )
    return tuple(recommendations)


def _normalize_recommendations(
    request: UIAdviceRequest, payload: dict[str, Any]
) -> tuple[UIAdviceRecommendation, ...]:
    if request.mode == UIAdviceMode.DESIGN_SYSTEM:
        return _design_system_recommendations(payload)
    return _result_recommendations(request, payload)


__all__ = [
    "UIUXProMaxAdvisor",
    "UIUXProMaxError",
    "UIUXProMaxExecutionError",
    "UIUXProMaxLock",
    "UIUXProMaxLockError",
    "UIUXProMaxOutputError",
]
