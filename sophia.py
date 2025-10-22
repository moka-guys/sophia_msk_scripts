#!/usr/bin/env python3
"""SOPHiA CLI uploader wrapper.

This script locates the run-specific SampleSheet, extracts the experiment
details and BDS-number, and launches the SOPHiA CLI upload via ``nohup``.
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import logging
from logging.handlers import SysLogHandler

try:
    import yaml
except ModuleNotFoundError as exc:
    raise RuntimeError("PyYAML is required to load the SOPHiA configuration.") from exc

CONFIG_PATH = Path(__file__).with_name("sophia_config.yaml")

SYSLOG_LOGGER_NAME = "automated_scripts.sophia_cli.upload"
SYSLOG_LOG_FORMAT = "%(asctime)s - PROD_MODE - %(name)s - %(levelname)s - %(message)s"
SYSLOG_ADDRESS = "/dev/log"

_SYSLOG_LOGGER: logging.Logger | None = None


def load_config(path: Path) -> Dict[str, Any]:
    """Load YAML configuration for the SOPHiA uploader."""
    try:
        with path.open("r", encoding="utf-8") as config_file:
            raw_config = yaml.safe_load(config_file) or {}
    except FileNotFoundError as exc:
        raise RuntimeError(f"Configuration file not found at {path}") from exc

    if not isinstance(raw_config, dict):
        raise RuntimeError("SOPHiA configuration must be a mapping")

    return raw_config


CONFIG = load_config(CONFIG_PATH)

try:
    DEFAULT_SAMPLESHEETS_ROOT = Path(str(CONFIG["samplesheets_root"]))
    PIPELINE_ID = str(CONFIG["pipeline_id"])
except KeyError as exc:
    missing_key = exc.args[0]
    raise RuntimeError(f"Missing required configuration key: {missing_key}") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Launch a SOPHiA CLI upload for the supplied run folder."
    )
    parser.add_argument(
        "run_folder",
        type=Path,
        help="Path to the sequencing run folder",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the upload command without executing it",
    )
    parser.add_argument(
        "--samplesheet-root",
        type=Path,
        default=DEFAULT_SAMPLESHEETS_ROOT,
        help="Root directory containing per-run SampleSheet.csv files",
    )
    parser.add_argument(
        "--nohup-log",
        type=Path,
        default=None,
        help="Optional path for nohup stdout/stderr (defaults to nohup.out)",
    )
    return parser.parse_args()


def locate_samplesheet(run_name: str, root: Path) -> Path:
    """Return the canonical SampleSheet path for the run."""
    candidate = root / f"{run_name}_SampleSheet.csv"
    if not candidate.exists():
        raise FileNotFoundError(
            f"SampleSheet not found at expected location: {candidate}"
        )
    return candidate


def extract_experiment_details(sample_sheet: Path) -> Tuple[str, str, str]:
    """Read the experiment identifiers from the SampleSheet.

    Returns a tuple of (experiment_name, bds_identifier, raw_value).
    """
    with sample_sheet.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if row and row[0].strip().lower() == "experiment name":
                if len(row) < 2 or not row[1].strip():
                    break
                raw_value = row[1].strip()
                prefix, separator, suffix = raw_value.partition("_")
                if not separator or not prefix or not suffix:
                    raise ValueError(
                        "ExperimentName value does not contain expected BDS component"
                    )
                experiment_name = prefix
                bds_identifier = suffix
                return experiment_name, bds_identifier, raw_value

    raise ValueError(
        f"Experiment Name entry not found or malformed in {sample_sheet}"
    )


def build_upload_command(run_folder: Path, experiment_name: str) -> List[str]:
    basecalls_path = run_folder / "Data" / "Intensities" / "BaseCalls"
    if not basecalls_path.is_dir():
        raise FileNotFoundError(
            f"BaseCalls directory not found at {basecalls_path}"
        )

    script_dir = Path(__file__).resolve().parent
    wrapper_path = script_dir / "sg-upload-v2-wrapper.py"
    if not wrapper_path.exists():
        raise FileNotFoundError(f"Wrapper script not found at {wrapper_path}")

    return [
        "python3",
        str(wrapper_path),
        "new",
        "--folder",
        str(basecalls_path),
        "--ref",
        experiment_name,
        "--pipeline",
        PIPELINE_ID,
        "--upload",
    ]


def launch_nohup(command: List[str], log_path: Path | None, cwd: Path) -> subprocess.Popen:
    nohup_command = ["nohup", *command]

    stdout_handle = None
    stdout_target = None

    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_handle = log_path.open("ab")
        stdout_target = stdout_handle

    try:
        process = subprocess.Popen(
            nohup_command,
            cwd=cwd,
            stdout=stdout_target,
            stderr=subprocess.STDOUT,
        )
    except Exception:
        if stdout_handle is not None:
            stdout_handle.close()
        raise

    if stdout_handle is not None:
        stdout_handle.close()
    return process


def get_syslog_logger() -> logging.Logger | None:
    """Return a configured logger that emits to syslog, caching the instance."""
    global _SYSLOG_LOGGER
    if _SYSLOG_LOGGER is not None:
        return _SYSLOG_LOGGER

    logger = logging.getLogger(SYSLOG_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not logger.handlers:
        try:
            handler = SysLogHandler(address=SYSLOG_ADDRESS)
        except OSError:
            return None
        handler.setFormatter(logging.Formatter(SYSLOG_LOG_FORMAT))
        logger.addHandler(handler)

    _SYSLOG_LOGGER = logger
    return logger


def log_error(message: str) -> None:
    """Log an error message to syslog, ignoring logging issues."""
    try:
        logger = get_syslog_logger()
        if logger is None:
            return
        logger.error(message)
    except Exception:
        pass


def main() -> None:
    args = parse_args()

    run_folder = args.run_folder.expanduser().resolve()
    if not run_folder.is_dir():
        log_error(f"Run folder not found: {run_folder}")
        print(f"Run folder not found: {run_folder}", file=sys.stderr)
        sys.exit(1)

    samplesheet_root = args.samplesheet_root.expanduser().resolve()
    if not samplesheet_root.is_dir():
        log_error(f"Samplesheet root not found: {samplesheet_root}")
        print(
            f"Samplesheet root not found: {samplesheet_root}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        samplesheet_path = locate_samplesheet(run_folder.name, samplesheet_root)
        experiment_name, bds_identifier, raw_experiment_name = extract_experiment_details(samplesheet_path)
        upload_command = build_upload_command(run_folder, experiment_name)
    except (FileNotFoundError, ValueError) as exc:
        log_error(f"Error preparing upload: {exc}")
        print(f"Error preparing upload: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Run folder: {run_folder}")
    print(f"SampleSheet: {samplesheet_path}")
    print(f"Experiment Name: {experiment_name}")
    print(f"SampleSheet Entry: {raw_experiment_name}")
    print(f"BDS Identifier: {bds_identifier}")
    print("Upload command:")
    print(" ".join(upload_command))

    if args.dry_run:
        return

    nohup_log = args.nohup_log
    if nohup_log is not None:
        nohup_log = nohup_log.expanduser().resolve()

    try:
        process = launch_nohup(upload_command, nohup_log, cwd=run_folder)
    except Exception as exc:
        log_error(f"Failed to launch upload: {exc}")
        print(f"Failed to launch upload: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Upload started under nohup (PID {process.pid}).")
    if nohup_log is None:
        print("Output will be written to 'nohup.out' in the working directory.")
    else:
        print(f"Output redirected to {nohup_log}")


if __name__ == "__main__":
    main()
