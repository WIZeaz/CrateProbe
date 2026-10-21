import asyncio
import logging
from pathlib import Path

from runner.client import RunnerControlClient

logger = logging.getLogger(__name__)

_VALID_MODES = ("append", "full")


class TaskReporter:
    """Periodically pushes local files to the control plane.

    ``file_specs`` maps the remote relative path (sent as the payload's
    ``path``) to ``(local file path, mode)``. Mode is ``"append"`` for
    offset-tracked incremental chunks with a per-path ``chunk_seq``, or
    ``"full"`` to re-send the whole file on every flush. Missing files are
    skipped; push failures are logged as warnings and never break the loop.
    ``stop()`` triggers one final flush with a 2s per-file timeout.
    """

    def __init__(
        self,
        client: RunnerControlClient,
        job_id: int,
        lease_token: str,
        file_specs: dict[str, tuple[Path, str]],
        flush_interval: float = 3.0,
    ):
        for remote_path, (_local_path, mode) in file_specs.items():
            if mode not in _VALID_MODES:
                raise ValueError(
                    f"invalid mode {mode!r} for {remote_path!r}; "
                    f"expected one of {_VALID_MODES}"
                )
        self.client = client
        self.job_id = job_id
        self.lease_token = lease_token
        self.file_specs = file_specs
        self.flush_interval = flush_interval
        self._stop_event = asyncio.Event()
        self._next_chunk_seq: dict[str, int] = {}
        self._sent_offsets: dict[str, int] = {}

    async def run(self) -> None:
        try:
            while not self._stop_event.is_set():
                await self._flush_files()

                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self.flush_interval
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            await self._flush_files()

    def stop(self) -> None:
        self._stop_event.set()

    async def _flush_files(self) -> None:
        for remote_path, (local_path, mode) in self.file_specs.items():
            if not local_path.exists():
                continue

            try:
                current_size = local_path.stat().st_size
            except OSError:
                continue

            sent_offset = self._sent_offsets.get(remote_path, 0)

            if mode == "append":
                # Append-only files: byte size is a reliable change signal, so
                # skip when nothing new was written and only send the tail.
                if current_size == sent_offset:
                    continue
                if current_size < sent_offset:
                    sent_offset = 0
                    self._next_chunk_seq[remote_path] = 1
            else:
                # full mode (e.g. progress.txt) is rewritten in place; an
                # in-place edit can change content without changing the byte
                # length, so size comparison is unreliable. Always re-read the
                # whole file and re-upload it.
                sent_offset = 0

            chunk_seq = self._next_chunk_seq.get(remote_path, 1)

            try:
                with open(local_path, "rb") as f:
                    f.seek(sent_offset)
                    new_bytes = f.read()
            except OSError:
                continue

            if not new_bytes:
                continue

            payload = {
                "lease_token": self.lease_token,
                "path": remote_path,
                "mode": mode,
                "content": new_bytes.decode("utf-8", errors="replace"),
            }
            if mode == "append":
                payload["chunk_seq"] = chunk_seq

            try:
                if self._stop_event.is_set():
                    await asyncio.wait_for(
                        self.client.push_file(self.job_id, payload),
                        timeout=2.0,
                    )
                else:
                    await self.client.push_file(self.job_id, payload)
                if mode == "append":
                    self._next_chunk_seq[remote_path] = chunk_seq + 1
                self._sent_offsets[remote_path] = current_size
            except asyncio.TimeoutError:
                logger.warning(
                    "file push timed out during shutdown",
                    extra={
                        "job_id": self.job_id,
                        "path": remote_path,
                        "mode": mode,
                    },
                )
            except Exception as exc:
                logger.warning(
                    "file push failed: %s",
                    exc,
                    extra={
                        "job_id": self.job_id,
                        "path": remote_path,
                        "mode": mode,
                    },
                )
