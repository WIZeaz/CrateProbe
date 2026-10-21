"""Per-job file storage service.

Owns all path safety and file I/O for the two per-job trees:
``workspace/jobs/{job_id}/files/`` (runner-pushed files) and
``workspace/jobs/{job_id}/artifacts/`` (uploaded artifacts).
"""

import logging
import shutil
from pathlib import Path
from typing import Any, BinaryIO

from app.utils.file_utils import read_last_n_lines

logger = logging.getLogger(__name__)

_STREAM_CHUNK_SIZE = 1024 * 1024  # 1 MiB


class PathSafetyError(ValueError):
    """Raised when a relative path is unsafe (absolute, contains '..', empty,
    or resolves outside the base directory)."""


class ArtifactTooLargeError(Exception):
    """Raised when an artifact upload exceeds the configured size cap."""


class JobFileStore:
    """File store for a single workspace's per-job files and artifacts."""

    def __init__(
        self,
        workspace: Path,
        full_mode_max_bytes: int,
        artifact_max_bytes: int,
    ):
        self._workspace = Path(workspace)
        self._full_mode_max_bytes = full_mode_max_bytes
        self._artifact_max_bytes = artifact_max_bytes

    # -- directory helpers ---------------------------------------------------

    def files_dir(self, job_id: int) -> Path:
        """Directory holding runner-pushed files (not guaranteed to exist)."""
        return self._workspace / "jobs" / str(job_id) / "files"

    def artifacts_dir(self, job_id: int) -> Path:
        """Directory holding uploaded artifacts (not guaranteed to exist)."""
        return self._workspace / "jobs" / str(job_id) / "artifacts"

    # -- path safety -----------------------------------------------------------

    @staticmethod
    def safe_join(base: Path, rel: str) -> Path:
        """Join ``rel`` onto ``base``, rejecting unsafe relative paths.

        Raises PathSafetyError for absolute paths, paths containing '..',
        empty paths, and paths that resolve outside ``base`` (e.g. through
        a symlink inside ``base``).
        """
        if not rel:
            raise PathSafetyError("empty path")
        candidate = Path(rel)
        if candidate.is_absolute():
            raise PathSafetyError(f"absolute path not allowed: {rel!r}")
        if ".." in candidate.parts:
            raise PathSafetyError(f"parent traversal not allowed: {rel!r}")
        if not candidate.parts:
            raise PathSafetyError("empty path")

        base_resolved = base.resolve()
        resolved = (base_resolved / candidate).resolve()
        if resolved != base_resolved and base_resolved not in resolved.parents:
            raise PathSafetyError(f"path escapes base directory: {rel!r}")
        return resolved

    # -- pushed files (files/) -------------------------------------------------

    def append_file(self, job_id: int, path: str, content: str) -> None:
        """Append ``content`` to a pushed file, creating parents as needed."""
        target = self.safe_join(self.files_dir(job_id), path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(content)

    def write_full(self, job_id: int, path: str, content: str) -> None:
        """Overwrite a pushed file, keeping only the tail when the content
        exceeds ``full_mode_max_bytes``."""
        target = self.safe_join(self.files_dir(job_id), path)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        if len(data) > self._full_mode_max_bytes:
            logger.info(
                "full-mode file truncated to tail",
                extra={
                    "job_id": job_id,
                    "path": path,
                    "original_bytes": len(data),
                    "kept_bytes": self._full_mode_max_bytes,
                },
            )
            data = data[-self._full_mode_max_bytes :]
        with target.open("wb") as handle:
            handle.write(data)

    def list_files(self, job_id: int) -> list[dict[str, Any]]:
        """List pushed files recursively, sorted by relative path."""
        base = self.files_dir(job_id)
        if not base.is_dir():
            return []
        entries = []
        for entry in base.rglob("*"):
            if not entry.is_file():
                continue
            stat = entry.stat()
            entries.append(
                {
                    "path": entry.relative_to(base).as_posix(),
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                }
            )
        entries.sort(key=lambda item: item["path"])
        return entries

    def read_tail(self, job_id: int, path: str, lines: int) -> list[str]:
        """Read the last ``lines`` lines of a pushed file."""
        target = self.safe_join(self.files_dir(job_id), path)
        if not target.is_file():
            raise FileNotFoundError(f"file not found: {path}")
        return read_last_n_lines(str(target), lines)

    def read_raw(self, job_id: int, path: str) -> str:
        """Read the full content of a pushed file."""
        target = self.safe_join(self.files_dir(job_id), path)
        if not target.is_file():
            raise FileNotFoundError(f"file not found: {path}")
        return target.read_text(encoding="utf-8", errors="replace")

    # -- artifacts ---------------------------------------------------------------

    def save_artifact(self, job_id: int, path: str, stream: BinaryIO) -> int:
        """Stream an artifact to disk, enforcing the per-artifact size cap.

        Reads ``stream`` in chunks; when the total exceeds
        ``artifact_max_bytes`` the partial file is removed and
        ArtifactTooLargeError is raised. Returns the number of bytes written.
        """
        target = self.safe_join(self.artifacts_dir(job_id), path)
        target.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        try:
            with target.open("wb") as handle:
                while True:
                    chunk = stream.read(_STREAM_CHUNK_SIZE)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self._artifact_max_bytes:
                        raise ArtifactTooLargeError(
                            f"artifact exceeds {self._artifact_max_bytes} "
                            f"bytes: {path}"
                        )
                    handle.write(chunk)
        except ArtifactTooLargeError:
            target.unlink(missing_ok=True)
            logger.warning(
                "artifact rejected: size cap exceeded",
                extra={
                    "job_id": job_id,
                    "path": path,
                    "cap_bytes": self._artifact_max_bytes,
                },
            )
            raise
        return total

    def list_artifacts(self, job_id: int) -> list[dict[str, Any]]:
        """List artifacts recursively, sorted by relative path."""
        base = self.artifacts_dir(job_id)
        if not base.is_dir():
            return []
        entries = []
        for entry in base.rglob("*"):
            if not entry.is_file():
                continue
            entries.append(
                {
                    "path": entry.relative_to(base).as_posix(),
                    "size": entry.stat().st_size,
                }
            )
        entries.sort(key=lambda item: item["path"])
        return entries

    def artifact_file_path(self, job_id: int, path: str) -> Path:
        """Resolve an artifact's on-disk path (not guaranteed to exist)."""
        return self.safe_join(self.artifacts_dir(job_id), path)

    # -- cleanup -------------------------------------------------------------------

    def clear(self, job_id: int) -> None:
        """Remove both the files/ and artifacts/ directories for a job."""
        for directory in (self.files_dir(job_id), self.artifacts_dir(job_id)):
            shutil.rmtree(directory, ignore_errors=True)
        logger.info(
            "job files cleared",
            extra={"job_id": job_id, "path": str(self.files_dir(job_id).parent)},
        )
