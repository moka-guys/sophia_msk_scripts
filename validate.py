#!/usr/bin/env python3
"""
Validator for the SOPHiA CLI wrapper.

This script executes a set of sanity checks against the bundled CLI helper to
confirm the local environment is ready to use the tooling.

Designed to run as a cron job per 24 hours to periodically assess any CLI
changes that may impact automated processing.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from logging.handlers import SysLogHandler

try:
    import yaml
except ModuleNotFoundError as exc:
    raise RuntimeError("PyYAML is required to load the validation configuration.") from exc

CONFIG_PATH = Path(__file__).with_name("validation_config.yaml")


def load_config(path: Path) -> Dict[str, Any]:
    """Load YAML configuration for validation checks."""
    try:
        with path.open("r", encoding="utf-8") as config_file:
            raw_config = yaml.safe_load(config_file) or {}
    except FileNotFoundError as exc:
        raise RuntimeError(f"Configuration file not found at {path}") from exc

    if not isinstance(raw_config, dict):
        raise RuntimeError("Validation configuration must be a mapping")

    return raw_config


CONFIG = load_config(CONFIG_PATH)

try:
    EXPECTED_LOGIN_IAM_MESSAGE = CONFIG["expected_login_iam_message"]
    RECENT_RUNS_TO_CHECK = int(CONFIG["recent_runs_to_check"])
    EXPECTED_PIPELINE_ID = str(CONFIG["expected_pipeline_id"])
    REFERENCE_NAME = CONFIG["reference_name"]
    FASTQ_TESTS = CONFIG["fastq_tests"]
except KeyError as exc:
    missing_key = exc.args[0]
    raise RuntimeError(f"Missing required configuration key: {missing_key}") from exc

if not isinstance(FASTQ_TESTS, list):
    raise RuntimeError("'fastq_tests' configuration must be a list")

SYSLOG_LOGGER_NAME = "automated_scripts.sophia_cli.validate"
SYSLOG_LOG_FORMAT = "%(asctime)s - PROD_MODE - %(name)s - %(levelname)s - %(message)s"
SYSLOG_ADDRESS = "/dev/log"

_SYSLOG_LOGGER: Optional[logging.Logger] = None


def run_command(command: List[str]) -> Tuple[int, str]:
    """Run a command and return its exit code with combined output."""
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    combined_output = "".join(filter(None, [result.stdout, result.stderr]))
    return result.returncode, combined_output


def get_syslog_logger() -> Optional[logging.Logger]:
    """Create (once) and return a configured logger that writes to syslog."""
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
    return _SYSLOG_LOGGER


def log_validation_failure(exc: Exception) -> None:
    """Send validation failure details to syslog without impacting exit flow."""
    try:
        logger = get_syslog_logger()
        if logger is None:
            return
        logger.error("Validation failed: %s", exc)
    except Exception:
        # Syslog issues should not mask the validation failure itself.
        pass


def validate_login_iam() -> None:
    """Ensure the CLI reports the user is already logged in with IAM."""
    command = ["python3", "sg-upload-v2-wrapper.py", "login-iam"]
    exit_code, output = run_command(command)
    if exit_code != 0:
        raise RuntimeError(
            f"'{' '.join(command)}' exited with {exit_code}. Output:\n{output}"
        )
    if EXPECTED_LOGIN_IAM_MESSAGE not in output:
        raise RuntimeError(
            "Expected IAM login message not found in output.\n"
            f"Searched for: {EXPECTED_LOGIN_IAM_MESSAGE!r}\n"
            f"Actual output:\n{output}"
        )


def validate_recent_runs_listed() -> None:
    """Confirm the latest runs reported by the CLI show the configured minimum."""
    command = ["python3", "sg-upload-v2-wrapper.py", "status", "-l", str(RECENT_RUNS_TO_CHECK)]
    exit_code, output = run_command(command)
    if exit_code != 0:
        raise RuntimeError(
            f"'{' '.join(command)}' exited with {exit_code}. Output:\n{output}"
        )

    # Match lines that look like "<number>: <status>"
    run_lines = [
        line
        for line in output.splitlines()
        if re.match(r"^\s*\d+:\s+\S+", line)
    ]

    if len(run_lines) < RECENT_RUNS_TO_CHECK:
        raise RuntimeError(
            "Unexpected status output.\n"
            f"Expected at least {RECENT_RUNS_TO_CHECK} runs but found {len(run_lines)}.\n"
            f"Captured lines:\n{output}"
        )


def validate_fastq_errors() -> None:
    """
    Run CLI tests to ensure proper error handling for empty or single FASTQ folders.
    """
    for index, test_case in enumerate(FASTQ_TESTS, start=1):
        if not isinstance(test_case, dict):
            raise RuntimeError(
                f"FASTQ test case #{index} must be a mapping with 'label', 'folder', and 'expected_error'"
            )

        for required_key in ("label", "folder", "expected_error"):
            if required_key not in test_case:
                raise RuntimeError(
                    f"FASTQ test case #{index} missing required key: {required_key}"
                )

        label = test_case["label"]
        expected_error = test_case["expected_error"]
        folder = test_case["folder"]
        command = [
            "python3",
            "sg-upload-v2-wrapper.py",
            "new",
            "--folder",
            folder,
            "--ref",
            REFERENCE_NAME,
            "--pipeline",
            EXPECTED_PIPELINE_ID,
        ]
        exit_code, output = run_command(command)
        if expected_error not in output:
            raise RuntimeError(
                f"Expected error for {label} not found.\n"
                f"Searched for: {expected_error!r}\n"
                f"Actual output:\n{output}"
            )
        if exit_code == 0:
            raise RuntimeError(
                f"CLI did not return an error exit code for {label} case.\n"
                f"Output:\n{output}"
            )


def validate_pipeline_available() -> None:
    """Ensure the configured pipeline ID appears in the available pipelines list."""
    command = ["python3", "sg-upload-v2-wrapper.py", "pipeline", "--list"]
    exit_code, output = run_command(command)
    if exit_code != 0:
        raise RuntimeError(
            f"'{' '.join(command)}' exited with {exit_code}. Output:\n{output}"
        )

    if EXPECTED_PIPELINE_ID not in output:
        raise RuntimeError(
            f"Expected pipeline ID {EXPECTED_PIPELINE_ID} not found in pipeline list.\n"
            f"Actual output:\n{output}"
        )


def main() -> None:
    try:
        validate_login_iam()
        validate_recent_runs_listed()
        validate_pipeline_available()
        validate_fastq_errors()
    except Exception as exc:  # noqa: BLE001 - need to surface the root cause.
        log_validation_failure(exc)
        print(f"Validation failed: {exc}")
        sys.exit(1)
    print(
        f"Validation passed: IAM login confirmed, {RECENT_RUNS_TO_CHECK} runs listed, "
        f"pipeline {EXPECTED_PIPELINE_ID} available, and FASTQ error handling verified."
    )


if __name__ == "__main__":
    main()
