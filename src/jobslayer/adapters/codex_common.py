"""Shared, fail-closed process configuration for Codex CLI adapters."""

from __future__ import annotations

import os
from collections.abc import Sequence


class CodexCommandConfigurationError(ValueError):
    """Raised when a Codex executable command is structurally unsafe."""


def normalize_codex_command(
    command: str | os.PathLike[str] | Sequence[str],
) -> tuple[str, ...]:
    if isinstance(command, (str, os.PathLike)):
        normalized = (os.fspath(command),)
    else:
        normalized = tuple(str(argument) for argument in command)
    if not normalized or any(
        not argument or "\x00" in argument for argument in normalized
    ):
        raise CodexCommandConfigurationError(
            "Codex executable command must contain non-empty arguments"
        )
    return normalized


def codex_environment() -> dict[str, str]:
    """Return the minimal inherited environment used by Codex adapters.

    Authentication may use an explicitly selected ``CODEX_HOME``. Ambient API
    keys and unrelated process environment are intentionally not inherited.
    """

    environment = {"PATH": os.environ.get("PATH", os.defpath)}
    for name in (
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
        "CODEX_HOME",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    ):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    environment.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "PYTHONNOUSERSITE": "1",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        }
    )
    return environment


__all__ = [
    "CodexCommandConfigurationError",
    "codex_environment",
    "normalize_codex_command",
]
