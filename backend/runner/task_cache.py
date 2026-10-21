import asyncio
import io
import logging
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

import httpx

from core.task_spec import sha256_hex
from runner.client import RunnerControlClient

logger = logging.getLogger(__name__)

_MARKER_NAME = ".sha256"


class TaskFetchError(RuntimeError):
    """Raised when a task zip cannot be downloaded, verified, or extracted."""


class TaskCache:
    """On-disk cache of extracted task zips, keyed by task_id.

    Layout: ``{cache_dir}/{task_id}/`` holds the extracted zip contents plus a
    ``.sha256`` marker file recording the sha256 of the zip bytes that produced
    it. ``ensure`` re-downloads whenever the marker does not match the expected
    sha256, and replaces the directory atomically (extract to a temp sibling,
    then rmtree + rename) so a crash never leaves a half-populated cache dir.
    """

    def __init__(self, cache_dir: Path, client: RunnerControlClient):
        self._cache_dir = Path(cache_dir)
        self._client = client
        self._locks: dict[int, asyncio.Lock] = {}

    async def ensure(self, task_id: int, sha256: str) -> Path:
        """Return the directory containing the extracted task zip.

        Downloads and extracts the zip on a cache miss or stale marker.
        Raises TaskFetchError on download failure, sha256 mismatch, or an
        invalid/unsafe zip.
        """
        task_dir = self._cache_dir / str(task_id)
        if self._is_hit(task_dir, sha256):
            logger.debug("task cache hit", extra={"task_id": task_id, "sha256": sha256})
            return task_dir

        async with self._lock_for(task_id):
            # Re-check: another coroutine may have populated the cache while
            # we waited on the lock.
            if self._is_hit(task_dir, sha256):
                return task_dir
            logger.info(
                "task cache miss, downloading",
                extra={"task_id": task_id, "sha256": sha256},
            )
            zip_bytes = await self._download(task_id)
            self._verify_sha256(task_id, zip_bytes, sha256)
            await asyncio.to_thread(self._install, task_id, task_dir, zip_bytes, sha256)
            logger.info(
                "task cache populated",
                extra={"task_id": task_id, "sha256": sha256},
            )
        return task_dir

    def _lock_for(self, task_id: int) -> asyncio.Lock:
        lock = self._locks.get(task_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[task_id] = lock
        return lock

    @staticmethod
    def _is_hit(task_dir: Path, sha256: str) -> bool:
        if not task_dir.is_dir():
            return False
        marker = task_dir / _MARKER_NAME
        if not marker.is_file():
            return False
        try:
            return marker.read_text().strip() == sha256
        except OSError:
            return False

    async def _download(self, task_id: int) -> bytes:
        try:
            return await self._client.download_task_zip(task_id)
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status == 404:
                raise TaskFetchError(
                    f"task {task_id} zip not found on backend (HTTP 404)"
                ) from e
            raise TaskFetchError(
                f"task {task_id} zip download failed: HTTP {status}"
            ) from e
        except httpx.HTTPError as e:
            raise TaskFetchError(f"task {task_id} zip download failed: {e}") from e

    @staticmethod
    def _verify_sha256(task_id: int, zip_bytes: bytes, expected: str) -> None:
        actual = sha256_hex(zip_bytes)
        if actual != expected:
            raise TaskFetchError(
                f"task {task_id} zip sha256 mismatch: "
                f"expected {expected}, got {actual}"
            )

    def _install(
        self, task_id: int, task_dir: Path, zip_bytes: bytes, sha256: str
    ) -> None:
        """Extract zip_bytes and atomically replace task_dir. Runs in a thread."""
        try:
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        except zipfile.BadZipFile as e:
            raise TaskFetchError(
                f"task {task_id} zip is not a valid zip archive: {e}"
            ) from e

        with zf:
            self._validate_entries(task_id, zf)
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            tmp_dir = Path(
                tempfile.mkdtemp(prefix=f".{task_dir.name}-", dir=self._cache_dir)
            )
            try:
                zf.extractall(tmp_dir)
                (tmp_dir / _MARKER_NAME).write_text(sha256 + "\n")
                if task_dir.exists():
                    shutil.rmtree(task_dir)
                tmp_dir.rename(task_dir)
            finally:
                # No-op once tmp_dir has been renamed into place.
                if tmp_dir.exists():
                    shutil.rmtree(tmp_dir, ignore_errors=True)

    @staticmethod
    def _validate_entries(task_id: int, zf: zipfile.ZipFile) -> None:
        """Reject zip entries with absolute paths or '..' parts (zip slip)."""
        for name in zf.namelist():
            path = PurePosixPath(name)
            if path.is_absolute():
                raise TaskFetchError(
                    f"task {task_id} zip entry {name!r} has an absolute path"
                )
            if ".." in path.parts:
                raise TaskFetchError(
                    f"task {task_id} zip entry {name!r} would escape "
                    f"the extraction directory"
                )
