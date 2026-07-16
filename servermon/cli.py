"""Command line entry point.

The Proxy Agent launches this via the ExecutablePath in settings.json and
appends ``--configOptions <...> --commandDir <dir>``. It can also be run by
hand with ``--check`` or ``--validate`` to test without a Proxy Agent.
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
from pathlib import Path

from . import __version__
from .config import ConfigError, load_config
from .device import build_report, device_name
from .plugin import ServerMonPlugin

log = logging.getLogger(__name__)

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "servermon.toml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="servermon",
        description="BigFix Proxy Agent plugin that monitors web server URLs.",
    )
    parser.add_argument(
        "--commandDir",
        dest="command_dir",
        metavar="DIR",
        help="Proxy Agent command directory to process",
    )
    parser.add_argument(
        "--configOptions",
        dest="config_options",
        default="",
        help="options passed by the Proxy Agent (accepted, ignored)",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        metavar="FILE",
        help=f"servermon TOML config file (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="check all configured URLs once, print the results, and exit "
        "non-zero if any check failed",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="with --check: print the device reports as JSON instead of text",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="validate the config file and exit",
    )
    parser.add_argument("--log-file", metavar="FILE", help="also log to this file (rotating)")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="log verbosity (default: INFO)",
    )
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    # parse_known_args keeps us working if a future Proxy Agent passes
    # arguments this version does not know about.
    args, unknown = parser.parse_known_args(argv)
    _setup_logging(args.log_level, args.log_file)
    if unknown:
        log.debug("ignoring unrecognized arguments: %s", unknown)

    try:
        config = load_config(args.config)
    except ConfigError as error:
        if args.validate:
            print(f"INVALID: {error}")
            return 1
        log.error("%s", error)
        return 1

    if args.validate:
        print(f'"{args.config}" is valid: {len(config.urls)} URL(s) configured.')
        return 0

    plugin = ServerMonPlugin(config)

    if args.check:
        return _run_check(plugin, as_json=args.json)

    if not args.command_dir:
        parser.error("--commandDir is required (or use --check / --validate)")

    plugin.process_command_dir(args.command_dir)
    return 0


def _run_check(plugin: ServerMonPlugin, *, as_json: bool) -> int:
    results = plugin.run_checks(list(plugin.config.urls))
    if as_json:
        reports = [build_report(entry, result) for entry, result in results]
        print(json.dumps(reports, indent=2, ensure_ascii=False))
    else:
        for entry, result in results:
            print(f"{device_name(entry.url)}: {result.detail}")
    return 0 if all(result.success for _, result in results) else 1


def _setup_logging(level: str, log_file: str | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        handlers.append(
            logging.handlers.RotatingFileHandler(
                log_file, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8"
            )
        )
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
