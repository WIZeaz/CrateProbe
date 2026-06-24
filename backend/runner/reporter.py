import asyncio
import logging
from pathlib import Path
from typing import Dict

from runner.client import RunnerControlClient

logger = logging.getLogger(__name__)


class TaskReporter:
    def __init__(
        self,
        client: RunnerControlClient,
        task_id: int,
        lease_token: str,
        log_paths: dict[str, Path],
        workspace_dir: Path,
        log_flush_interval: float = 3.0,
        upload_config: Dict[str, str] | None = None,
    ):
        self.client = client
        self.task_id = task_id
        self.lease_token = lease_token
        self.log_paths = log_paths
        self.workspace_dir = workspace_dir
        self.log_flush_interval = log_flush_interval
        self.upload_config = upload_config or {}
        self._stop_event = asyncio.Event()
        self._next_chunk_seq: dict[str, int] = {}
        self._sent_offsets: dict[str, int] = {}

    async def run(self) -> None:
        try:
            while not self._stop_event.is_set():
                await self._flush_logs()

                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self.log_flush_interval
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            await self._flush_logs()

    def stop(self) -> None:
        self._stop_event.set()

    async def _flush_logs(self) -> None:
        for log_type, path in self.log_paths.items():
            if not path.exists():
                continue

            try:
                current_size = path.stat().st_size
            except OSError:
                continue

            upload_mode = self._resolve_upload_mode(log_type)
            sent_offset = self._sent_offsets.get(log_type, 0)

            if upload_mode == "chunk":
                # Append-only logs: byte size is a reliable change signal, so
                # skip when nothing new was written and only send the tail.
                if current_size == sent_offset:
                    continue
                if current_size < sent_offset:
                    sent_offset = 0
                    self._next_chunk_seq[log_type] = 1
            else:
                # full mode (e.g. stats.yaml) is rewritten in place; an
                # in-place edit can change content without changing the byte
                # length, so size comparison is unreliable. Always re-read the
                # whole file and re-upload it.
                sent_offset = 0

            chunk_seq = self._next_chunk_seq.get(log_type, 1)

            try:
                with open(path, "rb") as f:
                    f.seek(sent_offset)
                    new_bytes = f.read()
            except OSError:
                continue

            if not new_bytes:
                continue

            new_content = new_bytes.decode("utf-8", errors="replace")

            try:
                if upload_mode == "chunk":
                    if self._stop_event.is_set():
                        await asyncio.wait_for(
                            self.client.send_log_chunk(
                                self.task_id,
                                log_type,
                                {
                                    "lease_token": self.lease_token,
                                    "chunk_seq": chunk_seq,
                                    "content": new_content,
                                },
                            ),
                            timeout=2.0,
                        )
                    else:
                        await self.client.send_log_chunk(
                            self.task_id,
                            log_type,
                            {
                                "lease_token": self.lease_token,
                                "chunk_seq": chunk_seq,
                                "content": new_content,
                            },
                        )
                    self._next_chunk_seq[log_type] = chunk_seq + 1
                else:
                    if self._stop_event.is_set():
                        await asyncio.wait_for(
                            self.client.send_log(
                                self.task_id,
                                log_type,
                                {
                                    "lease_token": self.lease_token,
                                    "content": path.read_text(
                                        encoding="utf-8", errors="replace"
                                    ),
                                },
                            ),
                            timeout=2.0,
                        )
                    else:
                        await self.client.send_log(
                            self.task_id,
                            log_type,
                            {
                                "lease_token": self.lease_token,
                                "content": path.read_text(
                                    encoding="utf-8", errors="replace"
                                ),
                            },
                        )
                self._sent_offsets[log_type] = current_size
            except asyncio.TimeoutError:
                logger.warning(
                    "log send timed out during shutdown",
                    extra={
                        "task_id": self.task_id,
                        "log_type": log_type,
                        "upload_mode": upload_mode,
                    },
                )
            except Exception as exc:
                logger.warning(
                    "log send failed: %s",
                    exc,
                    extra={
                        "task_id": self.task_id,
                        "log_type": log_type,
                        "upload_mode": upload_mode,
                    },
                )

    def _resolve_upload_mode(self, log_type: str) -> str:
        mode = self.upload_config.get(log_type, "full")
        if mode in ("chunk", "full"):
            return mode
        logger.warning(
            "invalid log upload mode configured, falling back to full",
            extra={
                "task_id": self.task_id,
                "log_type": log_type,
                "upload_mode": mode,
            },
        )
        return "full"
