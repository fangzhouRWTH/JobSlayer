#!/usr/bin/env python3
"""Cross-platform, project-local JobSlayer development bootstrap.

Python 3.11+ is the sole bootstrap prerequisite. Project Python dependencies are
installed into .venv. A compatible system Node is reused when available;
otherwise a pinned, checksum-verified Node distribution is installed into a
user-local tool cache without changing the user's PATH.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = "1.0"
STATE_FILENAME = ".bootstrap-state.json"
SUPPORTED_TOOLS = {"jobslayer", "node", "npm", "python"}
MAX_NODE_ARCHIVE_BYTES = 250 * 1024 * 1024


class BootstrapError(RuntimeError):
    """A deterministic bootstrap configuration or installation failure."""


@dataclass(frozen=True)
class NodeRuntime:
    node: Path
    npm: Path
    version: str
    npm_version: str
    source: str

    @property
    def path_entry(self) -> Path:
        return self.node.parent


def parse_version(value: str) -> tuple[int, int, int]:
    normalized = value.strip().lower().removeprefix("v").split("-", 1)[0]
    parts = normalized.split(".")
    if len(parts) < 2 or any(not part.isdigit() for part in parts[:3]):
        raise BootstrapError(f"cannot parse semantic version: {value!r}")
    numbers = [int(part) for part in parts[:3]]
    while len(numbers) < 3:
        numbers.append(0)
    return tuple(numbers)  # type: ignore[return-value]


def version_at_least(actual: str, minimum: str) -> bool:
    return parse_version(actual) >= parse_version(minimum)


def platform_key(system: str | None = None, machine: str | None = None) -> str:
    normalized_system = (system or platform.system()).strip().lower()
    normalized_machine = (machine or platform.machine()).strip().lower()
    systems = {"windows": "windows", "linux": "linux", "darwin": "darwin"}
    machines = {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "x64": "x86_64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    if normalized_system not in systems or normalized_machine not in machines:
        raise BootstrapError(
            f"unsupported bootstrap platform: {normalized_system}/{normalized_machine}"
        )
    return f"{systems[normalized_system]}-{machines[normalized_machine]}"


def default_tool_cache(environment: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    configured = env.get("JOBSLAYER_TOOL_CACHE")
    if configured:
        return Path(configured).expanduser().absolute()
    if os.name == "nt":
        base = env.get("LOCALAPPDATA")
        if base:
            return Path(base) / "JobSlayer" / "toolchains"
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Caches" / "JobSlayer" / "toolchains"
    return Path(env.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "jobslayer" / "toolchains"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_digest(paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.as_posix()):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _safe_destination(root: Path, member_name: str) -> Path:
    destination = (root / member_name).resolve()
    resolved_root = root.resolve()
    if destination != resolved_root and resolved_root not in destination.parents:
        raise BootstrapError(f"archive member escapes extraction root: {member_name}")
    return destination


def safe_extract_archive(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                _safe_destination(destination, member.filename)
                unix_mode = member.external_attr >> 16
                if unix_mode and (unix_mode & 0o170000) == 0o120000:
                    raise BootstrapError(
                        f"zip symlink is not allowed in bootstrap archive: {member.filename}"
                    )
            bundle.extractall(destination)
        return
    if archive.name.endswith((".tar.gz", ".tar.xz")):
        with tarfile.open(archive) as bundle:
            for member in bundle.getmembers():
                member_destination = _safe_destination(destination, member.name)
                if member.ischr() or member.isblk() or member.isfifo():
                    raise BootstrapError(
                        f"special archive member is not allowed: {member.name}"
                    )
                if member.issym():
                    if Path(member.linkname).is_absolute():
                        raise BootstrapError(
                            f"absolute symlink is not allowed: {member.name}"
                        )
                    _safe_destination(
                        destination,
                        os.fspath(Path(member.name).parent / member.linkname),
                    )
                elif member.islnk():
                    if Path(member.linkname).is_absolute():
                        raise BootstrapError(
                            f"absolute hard link is not allowed: {member.name}"
                        )
                    _safe_destination(destination, member.linkname)
            bundle.extractall(destination)
        return
    raise BootstrapError(f"unsupported bootstrap archive: {archive.name}")


def _run(
    argv: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path,
    environment: Mapping[str, str] | None = None,
    capture: bool = False,
    timeout: float = 600,
) -> subprocess.CompletedProcess[str]:
    command = [os.fspath(item) for item in argv]
    result = subprocess.run(
        command,
        cwd=cwd,
        env=None if environment is None else dict(environment),
        check=False,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=timeout,
    )
    if result.returncode != 0:
        detail = ""
        if capture:
            detail = (result.stderr or result.stdout).strip()
        suffix = f": {detail}" if detail else ""
        raise BootstrapError(
            f"command failed with exit {result.returncode}: {' '.join(command)}{suffix}"
        )
    return result


def _probe_version(
    executable: Path,
    *,
    cwd: Path,
    environment: Mapping[str, str] | None = None,
) -> str | None:
    try:
        result = _run(
            (executable, "--version"),
            cwd=cwd,
            environment=environment,
            capture=True,
            timeout=20,
        )
    except (BootstrapError, OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _state_matches(path: Path, expected: Mapping[str, Any]) -> bool:
    current = _read_json(path)
    return current is not None and all(current.get(key) == value for key, value in expected.items())


def _python_state_matches(path: Path, expected: Mapping[str, Any]) -> bool:
    """Accept an initialized Python environment with a superset of requested extras."""

    current = _read_json(path)
    if current is None:
        return False
    for key, value in expected.items():
        if key == "extras":
            installed = current.get("extras")
            if not isinstance(installed, list) or not set(value).issubset(installed):
                return False
        elif current.get(key) != value:
            return False
    return True


class BootstrapManager:
    def __init__(
        self,
        repository_root: Path,
        *,
        tool_cache: Path | None = None,
        offline: bool = False,
        force: bool = False,
        extras: Sequence[str] = (),
        verbose: bool = True,
    ) -> None:
        self.root = repository_root.resolve()
        self.tool_cache = (tool_cache or default_tool_cache()).resolve()
        self.offline = offline
        self.force = force
        self.extras = tuple(sorted(set(extras)))
        self.verbose = verbose
        self.config_path = self.root / "bootstrap" / "toolchains.json"
        self.ui_root = self.root / "ui-framework"
        self.venv_root = self.root / ".venv"
        self.config = self._load_config()

    def _say(self, message: str) -> None:
        if self.verbose:
            print(message, flush=True)

    def _load_config(self) -> dict[str, Any]:
        required = (self.root / "pyproject.toml", self.config_path)
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise BootstrapError(
                "not a JobSlayer source checkout; missing: " + ", ".join(missing)
            )
        config = _read_json(self.config_path)
        if config is None or config.get("schema_version") != SCHEMA_VERSION:
            raise BootstrapError("bootstrap/toolchains.json has an unsupported schema")
        return config

    @property
    def venv_python(self) -> Path:
        if os.name == "nt":
            return self.venv_root / "Scripts" / "python.exe"
        return self.venv_root / "bin" / "python"

    def _minimum_python(self) -> str:
        return str(self.config["python"]["minimum_version"])

    def _minimum_node(self) -> str:
        return str(self.config["node"]["minimum_version"])

    def _node_install_root(self) -> Path:
        version = str(self.config["node"]["version"])
        return self.tool_cache / "node" / f"v{version}" / platform_key()

    @staticmethod
    def _node_paths(install_root: Path) -> tuple[Path, Path]:
        if os.name == "nt":
            return install_root / "node.exe", install_root / "npm.cmd"
        return install_root / "bin" / "node", install_root / "bin" / "npm"

    def _runtime_from_node(self, node: Path, *, source: str) -> NodeRuntime | None:
        node_version = _probe_version(node, cwd=self.root)
        if node_version is None or not version_at_least(node_version, self._minimum_node()):
            return None
        sibling_npm = node.parent / ("npm.cmd" if os.name == "nt" else "npm")
        npm = sibling_npm if sibling_npm.is_file() else Path(shutil.which("npm") or "")
        if not npm.is_file():
            return None
        probe_environment = os.environ.copy()
        probe_environment["PATH"] = os.pathsep.join(
            (str(node.parent), probe_environment.get("PATH", ""))
        )
        npm_version = _probe_version(
            npm,
            cwd=self.root,
            environment=probe_environment,
        )
        if npm_version is None:
            return None
        return NodeRuntime(
            node=node.resolve(),
            npm=npm.resolve(),
            version=node_version.removeprefix("v"),
            npm_version=npm_version.removeprefix("v"),
            source=source,
        )

    def find_node(self) -> NodeRuntime | None:
        configured = os.environ.get("JOBSLAYER_NODE")
        if configured:
            configured_path = Path(configured).expanduser().absolute()
            if not configured_path.is_file():
                raise BootstrapError(
                    f"configured JOBSLAYER_NODE does not exist: {configured_path}"
                )
            runtime = self._runtime_from_node(configured_path, source="configured")
            if runtime is None:
                raise BootstrapError(
                    "configured JOBSLAYER_NODE does not satisfy the Node/npm contract"
                )
            return runtime

        system_node = shutil.which("node")
        if system_node:
            runtime = self._runtime_from_node(Path(system_node), source="system")
            if runtime is not None:
                return runtime

        install_root = self._node_install_root()
        node, _npm = self._node_paths(install_root)
        return self._runtime_from_node(node, source="jobslayer-cache")

    def _download_node_archive(self, destination: Path, url: str, expected_hash: str) -> None:
        if destination.is_file() and sha256_file(destination) == expected_hash:
            self._say(f"[reuse] verified Node archive {destination.name}")
            return
        if self.offline:
            raise BootstrapError(
                f"offline mode requires a verified cached Node archive: {destination}"
            )
        if destination.exists():
            destination.unlink()
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        self._say(f"[download] {url}")
        request = urllib.request.Request(url, headers={"User-Agent": "JobSlayer-bootstrap/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
                content_length = response.headers.get("Content-Length")
                if content_length is not None and int(content_length) > MAX_NODE_ARCHIVE_BYTES:
                    raise BootstrapError("Node archive exceeds the bootstrap size limit")
                written = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_NODE_ARCHIVE_BYTES:
                        raise BootstrapError("Node archive exceeds the bootstrap size limit")
                    output.write(chunk)
        except (BootstrapError, OSError, urllib.error.URLError, ValueError) as exc:
            partial.unlink(missing_ok=True)
            raise BootstrapError(f"Node download failed: {exc}") from exc
        actual_hash = sha256_file(partial)
        if actual_hash != expected_hash:
            partial.unlink(missing_ok=True)
            raise BootstrapError(
                f"Node archive checksum mismatch: expected {expected_hash}, got {actual_hash}"
            )
        os.replace(partial, destination)

    def ensure_node(self) -> NodeRuntime:
        existing = self.find_node()
        if existing is not None:
            self._say(
                f"[ready] Node {existing.version}, npm {existing.npm_version} ({existing.source})"
            )
            return existing

        key = platform_key()
        node_config = self.config["node"]
        distribution = node_config["distributions"].get(key)
        if not isinstance(distribution, dict):
            raise BootstrapError(f"no pinned Node distribution for {key}")
        archive_name = str(distribution["archive"])
        expected_hash = str(distribution["sha256"])
        archive = self.tool_cache / "downloads" / archive_name
        url = f"{str(node_config['base_url']).rstrip('/')}/{archive_name}"
        self._download_node_archive(archive, url, expected_hash)

        install_root = self._node_install_root()
        if install_root.exists():
            if not self.force:
                raise BootstrapError(
                    f"invalid cached Node installation exists at {install_root}; rerun with --force"
                )
            if self.tool_cache not in install_root.parents:
                raise BootstrapError("refusing to replace Node outside the dedicated tool cache")
            shutil.rmtree(install_root)

        install_root.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="jobslayer-node-", dir=install_root.parent
        ) as temporary:
            extraction_root = Path(temporary)
            safe_extract_archive(archive, extraction_root)
            extracted = extraction_root / str(distribution["root_dir"])
            if not extracted.is_dir():
                raise BootstrapError(
                    f"Node archive did not contain expected root: {distribution['root_dir']}"
                )
            os.replace(extracted, install_root)

        node, _npm = self._node_paths(install_root)
        runtime = self._runtime_from_node(node, source="jobslayer-cache")
        if runtime is None:
            raise BootstrapError("installed Node distribution failed its version/npm probe")
        self._say(
            f"[ready] Node {runtime.version}, npm {runtime.npm_version} ({runtime.source})"
        )
        return runtime

    def ensure_python(self) -> Path:
        current = ".".join(str(item) for item in sys.version_info[:3])
        if not version_at_least(current, self._minimum_python()):
            raise BootstrapError(
                f"Python {self._minimum_python()}+ is required; bootstrap is running on {current}"
            )
        if self.venv_root.exists() and not self.venv_python.is_file():
            raise BootstrapError(
                f"{self.venv_root} exists but has no interpreter for {platform_key()}; "
                "use a separate checkout per host platform or remove the incomplete .venv"
            )
        if not self.venv_python.is_file():
            self._say(f"[create] Python virtual environment {self.venv_root}")
            _run((sys.executable, "-m", "venv", self.venv_root), cwd=self.root)
        venv_version = _probe_version(self.venv_python, cwd=self.root)
        if venv_version is None or not version_at_least(venv_version.split()[-1], self._minimum_python()):
            raise BootstrapError(
                f"repository virtual environment is invalid or too old: {self.venv_python}"
            )

        expected_state = {
            "schema_version": SCHEMA_VERSION,
            "component": "python",
            "platform": platform_key(),
            "manifest_sha256": manifest_digest((self.root / "pyproject.toml",)),
            "extras": list(self.extras),
        }
        state_path = self.venv_root / STATE_FILENAME
        existing_state = _read_json(state_path)
        if (
            existing_state is not None
            and existing_state.get("platform") is not None
            and existing_state.get("platform") != platform_key()
        ):
            raise BootstrapError(
                "repository .venv was initialized for another host platform; "
                "use a separate checkout per platform"
            )
        dependency_probe_ok = False
        if not self.force and _python_state_matches(state_path, expected_state):
            try:
                _run(
                    (self.venv_python, "-m", "pip", "check"),
                    cwd=self.root,
                    capture=True,
                    timeout=60,
                )
                _run(
                    (self.venv_python, "-c", "import jobslayer, pydantic"),
                    cwd=self.root,
                    capture=True,
                    timeout=30,
                )
                dependency_probe_ok = True
            except BootstrapError:
                dependency_probe_ok = False
        if dependency_probe_ok:
            self._say("[ready] Python editable environment (manifest unchanged)")
            return self.venv_python

        extras = f"[{','.join(self.extras)}]" if self.extras else ""
        install_target = f".{extras}"
        command: list[str | os.PathLike[str]] = [
            self.venv_python,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
        ]
        if self.offline:
            command.extend(("--no-index", "--no-build-isolation"))
        command.extend(("-e", install_target))
        self._say(f"[install] Python project dependencies {install_target}")
        _run(command, cwd=self.root)
        _run((self.venv_python, "-m", "pip", "check"), cwd=self.root)
        _write_json_atomic(state_path, expected_state)
        return self.venv_python

    def _node_environment(self, runtime: NodeRuntime) -> dict[str, str]:
        environment = os.environ.copy()
        current_path = environment.get("PATH", "")
        environment["PATH"] = os.pathsep.join(
            (str(runtime.path_entry), current_path)
        )
        return environment

    def ensure_ui(self, runtime: NodeRuntime) -> None:
        package_json = self.ui_root / "package.json"
        lockfile = self.ui_root / "package-lock.json"
        if not package_json.is_file() or not lockfile.is_file():
            raise BootstrapError("ui-framework package.json/package-lock.json is missing")
        node_modules = self.ui_root / "node_modules"
        state_path = node_modules / STATE_FILENAME
        expected_state = {
            "schema_version": SCHEMA_VERSION,
            "component": "ui-framework",
            "platform": platform_key(),
            "manifest_sha256": manifest_digest((package_json, lockfile)),
            "node_version": runtime.version,
        }
        environment = self._node_environment(runtime)
        existing_state = _read_json(state_path)
        if (
            existing_state is not None
            and existing_state.get("platform") is not None
            and existing_state.get("platform") != platform_key()
        ):
            raise BootstrapError(
                "ui-framework/node_modules was initialized for another host platform; "
                "use a separate checkout per platform"
            )
        dependency_probe_ok = False
        dependencies_complete = False
        if not self.force and node_modules.is_dir():
            try:
                _run(
                    (runtime.npm, "ls", "--all"),
                    cwd=self.ui_root,
                    environment=environment,
                    capture=True,
                    timeout=90,
                )
                dependencies_complete = True
            except BootstrapError:
                dependencies_complete = False
        if dependencies_complete and _state_matches(state_path, expected_state):
            dependency_probe_ok = True
        if dependency_probe_ok:
            self._say("[ready] UI dependencies (lockfile unchanged)")
            return
        if dependencies_complete:
            _write_json_atomic(state_path, expected_state)
            self._say("[ready] UI dependencies adopted after full npm verification")
            return

        command: list[str | os.PathLike[str]] = [
            runtime.npm,
            "ci",
            "--no-audit",
            "--no-fund",
        ]
        if self.offline:
            command.append("--offline")
        self._say("[install] UI dependencies from package-lock.json")
        try:
            _run(command, cwd=self.ui_root, environment=environment)
        except BootstrapError as exc:
            if os.name == "nt":
                raise BootstrapError(
                    f"npm ci failed: {exc}. If npm reported EPERM/unlink, stop UI/Vite/Node "
                    "processes using this checkout and rerun init; the failure is not marked ready"
                ) from exc
            raise
        _run(
            (runtime.npm, "ls", "--all"),
            cwd=self.ui_root,
            environment=environment,
            capture=True,
            timeout=90,
        )
        _write_json_atomic(state_path, expected_state)

    def inspect(self, *, include_ui: bool = True) -> dict[str, Any]:
        python_version = _probe_version(self.venv_python, cwd=self.root) if self.venv_python.is_file() else None
        python_ready = False
        if python_version is not None:
            try:
                _run(
                    (self.venv_python, "-m", "pip", "check"),
                    cwd=self.root,
                    capture=True,
                    timeout=60,
                )
                _run(
                    (self.venv_python, "-c", "import jobslayer, pydantic"),
                    cwd=self.root,
                    capture=True,
                    timeout=30,
                )
                python_ready = _python_state_matches(
                    self.venv_root / STATE_FILENAME,
                    {
                        "schema_version": SCHEMA_VERSION,
                        "component": "python",
                        "platform": platform_key(),
                        "manifest_sha256": manifest_digest((self.root / "pyproject.toml",)),
                        "extras": list(self.extras),
                    },
                )
            except BootstrapError:
                python_ready = False

        runtime = self.find_node() if include_ui else None
        ui_ready = not include_ui
        if include_ui and runtime is not None:
            package_json = self.ui_root / "package.json"
            lockfile = self.ui_root / "package-lock.json"
            if package_json.is_file() and lockfile.is_file():
                state_path = self.ui_root / "node_modules" / STATE_FILENAME
                expected = {
                    "schema_version": SCHEMA_VERSION,
                    "component": "ui-framework",
                    "platform": platform_key(),
                    "manifest_sha256": manifest_digest((package_json, lockfile)),
                    "node_version": runtime.version,
                }
                if _state_matches(state_path, expected):
                    try:
                        _run(
                            (runtime.npm, "ls", "--all"),
                            cwd=self.ui_root,
                            environment=self._node_environment(runtime),
                            capture=True,
                            timeout=90,
                        )
                        ui_ready = True
                    except BootstrapError:
                        ui_ready = False

        ready = python_ready and ui_ready
        return {
            "schema_version": SCHEMA_VERSION,
            "ready": ready,
            "repository_root": str(self.root),
            "offline": self.offline,
            "python": {
                "ready": python_ready,
                "minimum_version": self._minimum_python(),
                "executable": str(self.venv_python),
                "version": python_version,
                "extras": list(self.extras),
            },
            "node": None
            if not include_ui
            else {
                "ready": runtime is not None,
                "minimum_version": self._minimum_node(),
                "version": None if runtime is None else runtime.version,
                "npm_version": None if runtime is None else runtime.npm_version,
                "executable": None if runtime is None else str(runtime.node),
                "source": None if runtime is None else runtime.source,
                "tool_cache": str(self.tool_cache),
            },
            "ui": {
                "included": include_ui,
                "ready": ui_ready,
                "path": str(self.ui_root),
            },
        }

    def install(self, *, include_ui: bool = True) -> tuple[Path, NodeRuntime | None]:
        python = self.ensure_python()
        runtime: NodeRuntime | None = None
        if include_ui:
            runtime = self.ensure_node()
            self.ensure_ui(runtime)
        return python, runtime

    def run_tool(
        self,
        command: Sequence[str],
        *,
        python: Path,
        runtime: NodeRuntime | None,
    ) -> int:
        if not command:
            return 0
        tool, *arguments = command
        if tool not in SUPPORTED_TOOLS:
            raise BootstrapError(
                f"unsupported initialized tool {tool!r}; choose one of {sorted(SUPPORTED_TOOLS)}"
            )
        environment = os.environ.copy()
        if runtime is not None:
            environment = self._node_environment(runtime)
        if tool == "python":
            executable: Path = python
        elif tool == "jobslayer":
            executable = python
            arguments = [str(self.root / "jobslayer"), *arguments]
        else:
            if runtime is None:
                raise BootstrapError(f"{tool} requires the UI toolchain")
            executable = runtime.node if tool == "node" else runtime.npm
        self._say(f"[run] {tool} {' '.join(arguments)}".rstrip())
        return subprocess.call(
            [str(executable), *arguments],
            cwd=self.root,
            env=environment,
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="initialize and inspect the JobSlayer development environment"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--check", action="store_true", help="detect only; do not install or download")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable check result")
    parser.add_argument("--offline", action="store_true", help="forbid network downloads and package index access")
    parser.add_argument("--skip-ui", action="store_true", help="initialize only the Python project")
    parser.add_argument("--force", action="store_true", help="reinstall manifest-managed project dependencies")
    parser.add_argument(
        "--extra",
        dest="extras",
        action="append",
        choices=("desktop", "observability", "postgres"),
        default=[],
        help="install one optional Python dependency group; may be repeated",
    )
    parser.add_argument("--tool-cache", type=Path, help="override the user-local Node cache")
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="after --, run node/npm/python/jobslayer in the initialized environment",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    if arguments.json and not arguments.check:
        print("--json is supported only with --check", file=sys.stderr)
        return 2
    command = list(arguments.command)
    if command and command[0] == "--":
        command.pop(0)
    if arguments.check and command:
        print("--check cannot be combined with a tool command", file=sys.stderr)
        return 2
    try:
        manager = BootstrapManager(
            arguments.root,
            tool_cache=arguments.tool_cache,
            offline=arguments.offline,
            force=arguments.force,
            extras=arguments.extras,
            verbose=not arguments.json,
        )
        if arguments.check:
            result = manager.inspect(include_ui=not arguments.skip_ui)
            if arguments.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                state = "ready" if result["ready"] else "not ready"
                print(f"JobSlayer development environment: {state}")
                print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ready"] else 1

        python, runtime = manager.install(include_ui=not arguments.skip_ui)
        if command:
            return manager.run_tool(command, python=python, runtime=runtime)
        print("JobSlayer development environment is ready.")
        if not arguments.skip_ui:
            if os.name == "nt":
                print(r"Start the UI: .\init.cmd -- npm --prefix ui-framework run dev")
            else:
                print("Start the UI: sh ./init.sh -- npm --prefix ui-framework run dev")
        return 0
    except (BootstrapError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"initialization failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
