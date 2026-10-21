"""Task definition zip storage service.

Owns validation and on-disk storage of uploaded task zips under
``workspace/tasks/{sha256}.zip``. Validation covers zip readability, entry
path safety (no absolute paths, no ``..``), the presence of ``task.toml`` at
the zip root, and ``task.toml`` spec validity.
"""

import io
import logging
import os
import zipfile
from pathlib import Path, PurePosixPath

from core.task_spec import TaskSpec, TaskSpecError, parse_task_spec, sha256_hex

logger = logging.getLogger(__name__)

# task.toml is script/config-level content; a decompressed member larger than
# this indicates a hostile archive rather than a real task definition.
_TASK_TOML_MAX_BYTES = 1024 * 1024  # 1 MiB


class ZipValidationError(ValueError):
    """Raised when an uploaded task zip fails validation."""


_EXAMPLE_TASK_TOML = """\
# Example task definition. A task zip must contain this file (task.toml)
# at the zip root; every other zip entry is copied into the job workspace.

# ---- Required ----
name = "example-task"        # Display name shown in the UI
image = "alpine:latest"      # Docker image the job runs in
cmd = "sh run.sh"            # Command run in the container (wrapped with sh -c)
workspace = "/workspace"     # In-container mount point of the job workspace

# ---- Optional ----
desc = "Demonstrates the task zip structure"
artifacts = ["output/"]      # Collected when the job finishes (relative to workspace)
watch = ["progress.log"]     # Viewable live while the job runs (relative to workspace)

[limit]                      # Every key is optional; a missing key means no limit
cpu = 1                      # CPU cores (float allowed)
mem = "512m"                 # Docker-style memory limit
timeout = "10m"              # Duration string (s/m/h) or integer seconds

[env]                        # Extra environment variables for the container
GREETING = "hello from example task"
"""

_EXAMPLE_RUN_SH = """\
#!/bin/sh
# The workspace (current directory) holds the crate source, overlaid with
# every zip entry except task.toml (zip entries win on name conflicts).
echo "$GREETING" > progress.log
echo "crate files:" >> progress.log
ls >> progress.log
mkdir -p output
echo "example result" > output/result.txt
echo "done" >> progress.log
"""


def build_example_task_zip() -> bytes:
    """Build the downloadable example task zip in memory."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("task.toml", _EXAMPLE_TASK_TOML)
        zf.writestr("run.sh", _EXAMPLE_RUN_SH)
    return buffer.getvalue()


class TaskStore:
    """Storage for task definition zips, keyed by their sha256 fingerprint."""

    def __init__(self, tasks_dir: Path):
        self._tasks_dir = Path(tasks_dir)

    def zip_path(self, sha256: str) -> Path:
        """On-disk path for a task zip (not guaranteed to exist)."""
        return self._tasks_dir / f"{sha256}.zip"

    def ingest_zip(self, zip_bytes: bytes) -> tuple[TaskSpec, str, Path]:
        """Validate a task zip and persist it.

        Returns (parsed spec, sha256 hex digest, path of the stored zip).
        Raises ZipValidationError when the archive is unreadable, contains
        unsafe entries, lacks a root ``task.toml``, or the spec is invalid.
        """
        spec = self._validate(zip_bytes)
        sha256 = sha256_hex(zip_bytes)
        zip_path = self.zip_path(sha256)
        self._tasks_dir.mkdir(parents=True, exist_ok=True)
        if not zip_path.exists():
            self._write_zip_atomically(zip_path, zip_bytes)
        logger.info(
            "task zip ingested",
            extra={"sha256": sha256, "size_bytes": len(zip_bytes)},
        )
        return spec, sha256, zip_path

    def delete_zip(self, sha256: str) -> None:
        """Remove the stored zip for ``sha256``; missing files are ignored."""
        self.zip_path(sha256).unlink(missing_ok=True)

    @staticmethod
    def read_task_toml(zip_path: Path) -> str:
        """Return the raw ``task.toml`` text stored inside a task zip.

        Raises ``FileNotFoundError`` when the zip or the member is missing,
        and ``ZipValidationError`` when the zip is unreadable.
        """
        with zipfile.ZipFile(zip_path) as zf:
            data = zf.read("task.toml")
        return data.decode("utf-8", errors="replace")

    @staticmethod
    def _write_zip_atomically(zip_path: Path, zip_bytes: bytes) -> None:
        """Write via a temp sibling + rename, so a crash mid-write can never
        leave a truncated zip behind that the exists-skip above would then
        treat as valid forever."""
        tmp_path = zip_path.with_name(zip_path.name + ".tmp")
        tmp_path.write_bytes(zip_bytes)
        os.replace(tmp_path, zip_path)

    def _validate(self, zip_bytes: bytes) -> TaskSpec:
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                for name in zf.namelist():
                    self._validate_entry_name(name)
                if "task.toml" not in zf.namelist():
                    raise ZipValidationError("task.toml missing at zip root")
                with zf.open("task.toml") as member:
                    toml_bytes = member.read(_TASK_TOML_MAX_BYTES + 1)
        except ZipValidationError:
            raise
        except (zipfile.BadZipFile, RuntimeError) as e:
            raise ZipValidationError(f"unreadable zip archive: {e}") from e

        if len(toml_bytes) > _TASK_TOML_MAX_BYTES:
            raise ZipValidationError("task.toml exceeds 1 MiB")

        try:
            return parse_task_spec(toml_bytes)
        except TaskSpecError as e:
            raise ZipValidationError(f"invalid task.toml: {e}") from e

    @staticmethod
    def _validate_entry_name(name: str) -> None:
        path = PurePosixPath(name)
        if path.is_absolute():
            raise ZipValidationError(f"zip entry has absolute path: {name!r}")
        if ".." in path.parts:
            raise ZipValidationError(f"zip entry escapes archive root: {name!r}")
