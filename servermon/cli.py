"""Command line entry point.

The Proxy Agent launches this via the ExecutablePath in settings.json and
appends ``--configOptions <...> --commandDir <dir>``. It can also be run by
hand with ``--check`` or ``--validate`` to test without a Proxy Agent.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from bigfix_proxyagent.cli import build_base_parser, setup_logging

from . import __version__
from .config import ConfigError, load_config
from .device import device_name
from .plugin import ServerMonPlugin

log = logging.getLogger(__name__)

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "servermon.toml"
DEFAULT_LOG_FILE = Path(__file__).resolve().parent.parent / "Logs" / "servermon.log"
DEFAULT_STATE_FILE = Path(__file__).resolve().parent.parent / "servermon-state.json"


def build_parser() -> argparse.ArgumentParser:
    # The SDK supplies the standard Proxy Agent arguments (--commandDir,
    # --configOptions, --config, --state-file, --log-file, --log-level,
    # --version); servermon adds its own manual-run flags.
    parser = build_base_parser(
        "servermon",
        "BigFix Proxy Agent plugin that monitors web server URLs.",
        version=__version__,
        default_config=DEFAULT_CONFIG,
        default_state_file=DEFAULT_STATE_FILE,
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "check all configured URLs once, print the results, and exit "
            "non-zero if any check failed"
        ),
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
        config_path = _resolve_config_path(args.config)
        log.info("using config file: %s", config_path)
        config = load_config(config_path)
    except ConfigError as error:
        if args.validate:
            print(f"INVALID: {error}")
            return 1
        log.error("%s", error)
        return 1

    if args.validate:
        print(f'"{config_path}" is valid: {len(config.urls)} URL(s) configured.')
        return 0

    plugin = ServerMonPlugin(
        config,
        state_file=Path(args.state_file).resolve(),
        config_path=config_path,
    )

    if args.check:
        return _run_check(plugin, as_json=args.json)

    if not args.command_dir:
        parser.error("--commandDir is required (or use --check / --validate)")

    plugin.process_command_dir(args.command_dir)
    return 0


def _resolve_config_path(requested: str) -> Path:
    """Resolve which config file to use, as an absolute path.

    Uses the requested file when it exists; otherwise falls back to the
    default config in the repository root (next to the plugin).
    """
    candidate = Path(requested).resolve()
    if candidate.is_file():
        return candidate
    if candidate != DEFAULT_CONFIG and DEFAULT_CONFIG.is_file():
        log.warning(
            "config file %s not found; falling back to default %s",
            candidate,
            DEFAULT_CONFIG,
        )
        return DEFAULT_CONFIG
    if candidate != DEFAULT_CONFIG:
        raise ConfigError(
            f"config file not found: {candidate} (also tried default: {DEFAULT_CONFIG})"
        )
    raise ConfigError(f"config file not found: {candidate}")


def _run_check(plugin: ServerMonPlugin, *, as_json: bool) -> int:
    rows = plugin.check_and_report(list(plugin.config.urls))
    if as_json:
        print(json.dumps([report for _, _, report in rows], indent=2, ensure_ascii=False))
    else:
        for entry, result, _ in rows:
            print(f"{device_name(entry.url)}: {result.detail}")
    return 0 if all(result.success for _, result, _ in rows) else 1


def _setup_logging(level: str, log_file: str | None) -> None:
    # Delegate to the SDK; servermon's default log file is the fallback.
    setup_logging(level, log_file, default_log_file=DEFAULT_LOG_FILE)
