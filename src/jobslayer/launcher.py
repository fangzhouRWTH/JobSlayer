from __future__ import annotations

from jobslayer.cli import main as cli_main


def main(argv: list[str] | None = None) -> int:
    """Stable entry shared by source, module and installed console scripts."""

    return cli_main(argv)
