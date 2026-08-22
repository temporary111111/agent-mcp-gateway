"""`python -m agent_gateway` entry point."""

from __future__ import annotations

import argparse
import sys

from .config import CONFIG_FILE, Config
from .server import run


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="agent-gateway",
        description="Agent Gateway — MCP tools for ChatGPT",
    )
    parser.add_argument(
        "folder",
        nargs="?",
        default=None,
        help="Directory to expose (sets allowed_roots).",
    )
    parser.add_argument(
        "--commands",
        action="store_true",
        default=None,
        help="Enable process execution.",
    )
    parser.add_argument(
        "--public",
        action="store_true",
        default=None,
        help="Expose publicly via Cloudflare (disables host checking).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="MCP server port (default: 8000).",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help=f"Path to config file (default: {CONFIG_FILE}).",
    )

    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv()

    config_path = None
    if args.config:
        from pathlib import Path

        config_path = Path(args.config)

    config = Config.build(
        folder=args.folder,
        commands=args.commands if args.commands else None,
        public=args.public if args.public else None,
        port=args.port,
        config_file=config_path,
    )
    run(config)


if __name__ == "__main__":
    main()
