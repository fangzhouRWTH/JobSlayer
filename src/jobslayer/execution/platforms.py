"""Small, provider-neutral host platform identifiers for local adapters."""

from __future__ import annotations

import os
import sys
from typing import Literal


HostPlatform = Literal["linux", "windows", "macos"]


def local_host_platform() -> HostPlatform:
    if os.name == "nt":
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "macos"
    raise RuntimeError(f"unsupported local execution platform: {sys.platform}")


__all__ = ["HostPlatform", "local_host_platform"]
