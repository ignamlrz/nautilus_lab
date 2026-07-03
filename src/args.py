"""CLI argument parsing for the Nautilus Trader lab."""

import argparse
from pathlib import Path


def parse_args() -> argparse.Namespace:
    """Parse the CLI flags.

    Returns a namespace with two :class:`~pathlib.Path` attributes:

    * ``system_config`` — node/system YAML (data/exec clients, cache, ...).
    * ``strategies_config`` — strategies/actors YAML.

    Both paths are resolved relative to the current working directory.
    """
    parser = argparse.ArgumentParser(description="Nautilus Trader lab runner")
    parser.add_argument(
        "-c",
        "--system-config",
        type=Path,
        required=True,
        help=(
            "YAML with the node/system config (dataclients, execclients, "
            "cache, ...). Resolved from the current working directory."
        ),
    )
    parser.add_argument(
        "-s",
        "--strategies-config",
        type=Path,
        required=True,
        help=(
            "YAML with the strategies/actors config. Resolved from the current working directory."
        ),
    )
    return parser.parse_args()
