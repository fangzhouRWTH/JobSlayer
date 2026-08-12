from __future__ import annotations

import sys

from jobslayer.cli import main as cli_main


def _configure_utf8_standard_streams() -> None:
    """Keep public CLI output Unicode-safe across redirected Windows consoles."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="strict")


def main(argv: list[str] | None = None) -> int:
    """Stable entry shared by source, module and installed console scripts."""

    _configure_utf8_standard_streams()
    return cli_main(argv)
