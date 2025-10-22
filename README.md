# SophiaCLI MSK-ACCESS automation scripts v1.0

Automation helpers for preparing and launching SOPHiA DDM uploads for MSK-ACCESS requests. 
The repo currently ships two maintained scripts:

- `sophia.py` – locates the correct SampleSheet for a run folder, extracts the
  experiment identifiers, and starts the SOPHiA CLI upload under `nohup`.
- `validate.py` – performs environment checks against the SOPHiA CLI wrapper to
  make sure routinely scheduled uploads will succeed (e.g. daily cron probe).

Both scripts live alongside YAML configuration files that capture local paths
and SOPHiA pipeline identifiers.

## Prerequisites

- Python 3.8+ with the `PyYAML` package available.
- SOPHiA upload helper scripts in this repository:
  - `sg-upload-v2-wrapper.py`
  - `sg-upload-v2-latest.jar` (called indirectly by the wrapper)
- Access to the shared SampleSheet directory defined in `sophia_config.yaml`.

## Configuration

| File | Purpose |
| --- | --- |
| `sophia_config.yaml` | Location of SampleSheet files and the default SOPHiA pipeline id (`pipeline_id`). |
| `validation_config.yaml` | Expectations for validation (IAM login banner, pipeline id, reference, and FASTQ folder probes). |

Update these YAML files to match the environment before running either script.
The defaults assume SampleSheets live under `/media/data1/share/samplesheets`
and that pipeline `7043` is available.

## SOPHiA upload wrapper (`sophia.py`)

Launch the wrapper by pointing it at the Illumina run folder:

```bash
python3 sophia.py /path/to/run/251022_A01229_XXXX_BHCT53DRX7
```

### Behaviour

1. Resolves the run folder and SampleSheet root (overridable via
   `--samplesheet-root`).
2. Finds `<RunName>_SampleSheet.csv` and reads the `Experiment Name` entry.
3. Splits the entry into the SOPHiA reference nickname and BDS identifier.
4. Validates the presence of `Data/Intensities/BaseCalls` and the wrapper
   script.
5. Prints the resolved metadata and the SOPHiA CLI command it will run.
6. Starts the upload via `nohup` unless `--dry-run` is supplied.

### Key arguments

- `run_folder` (positional): Path to the sequencing run directory.
- `--dry-run`: Show the upload command without starting it.
- `--samplesheet-root`: Override the SampleSheet search directory.
- `--nohup-log`: Write `nohup` output to a custom file (defaults to
  `nohup.out` inside the run folder).

When the upload starts successfully the script reports the `nohup` PID and the
location of stdout/stderr capture.

## Environment validation (`validate.py`)

The validator runs a sequence of tests to detect configuration drift in the Sophia CLI:

- Confirms `sg-upload-v2-wrapper.py login-iam` reports the
  `expected_login_iam_message`.
- Ensures `status -l <recent_runs_to_check>` returns at least the configured
  number of runs.
- Verifies the configured pipeline id appears in `pipeline --list`.
- Executes `sg-upload-v2-wrapper.py new` against local test FASTQ fixtures and
  checks the expected error strings appear (e.g. empty folder, single FASTQ).
  The sample folders referenced in the config live under `validation_runs/`.

Run it manually with:

```bash
python3 validate.py
```

On failure the script prints the root cause, exits with a non-zero status, and
attempts to emit the details to syslog (`automated_scripts.sophia_cli.validate`).
This makes it safe to schedule via cron and monitor through syslog alerts.

## Troubleshooting

- **SampleSheet not found** – confirm `sophia_config.yaml.samplesheets_root`
  points to the directory containing `<RunName>_SampleSheet.csv`.
- **Wrapper errors** – ensure the repository contains the latest
  `sg-upload-v2-wrapper.py` and its associated JAR; the validator is a good
  quick check.
- **Pipeline mismatch** – keep `pipeline_id` in both YAML files aligned with
  the pipeline configured in SOPHiA DDM.

For issues with SOPHiA credentials or CLI behaviour escalate to the SOPHiA DDM
support team. For SampleSheet or run folder layout problems contact the
sequencing informatics group.