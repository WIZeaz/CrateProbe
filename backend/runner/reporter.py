import asyncio
import logging
from pathlib import Path
from typing import Tuple

from runner.client import RunnerControlClient

logger = logging.getLogger(__name__)


class TaskReporter:
    LOG_FLUSH_INTERVAL = 3.0
    PROGRESS_INTERVAL = 10.0

    def __init__(
        self,
        client: RunnerControlClient,
        task_id: int,
        lease_token: str,
        log_paths: dict[str, Path],
        workspace_dir: Path,
    ):
        self.client = client
        self.task_id = task_id
        self.lease_token = lease_token
        self.log_paths = log_paths
        self.workspace_dir = workspace_dir
        self._stop_event = asyncio.Event()
        self._next_chunk_seq: dict[str, int] = {}
        self._sent_offsets: dict[str, int] = {}
        self._next_event_seq = 2  # started uses 1
        self._last_counts: Tuple[int, int, int | None] = (0, 0, None)
        self._last_progress_time = 0.0

    async def run(self) -> None:
        try:
            while not self._stop_event.is_set():
                await self._flush_logs()
                await self._maybe_send_progress()

                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self.LOG_FLUSH_INTERVAL
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            await self._flush_logs()

    def stop(self) -> int:
        self._stop_event.set()
        seq = self._next_event_seq
        self._next_event_seq += 1
        return seq

    async def _flush_logs(self) -> None:
        for log_type, path in self.log_paths.items():
            if not path.exists():
                continue

            try:
                current_size = path.stat().st_size
            except OSError:
                continue

            sent_offset = self._sent_offsets.get(log_type, 0)

            if current_size == sent_offset:
                continue

            if current_size < sent_offset:
                sent_offset = 0
                self._next_chunk_seq[log_type] = 1

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
                await self.client.send_log_chunk(
                    self.task_id,
                    log_type,
                    {
                        "lease_token": self.lease_token,
                        "chunk_seq": chunk_seq,
                        "content": new_content,
                    },
                )
                self._sent_offsets[log_type] = current_size
                self._next_chunk_seq[log_type] = chunk_seq + 1
            except Exception as exc:
                logger.warning(
                    "log chunk send failed: %s",
                    exc,
                    extra={
                        "task_id": self.task_id,
                        "log_type": log_type,
                        "chunk_seq": chunk_seq,
                    },
                )

    async def _maybe_send_progress(self) -> None:
        now = asyncio.get_running_loop().time()
        if now - self._last_progress_time < self.PROGRESS_INTERVAL:
            return

        case_count, poc_count = self._count_generated_items()
        compile_failed = self._get_compile_failed_count()
        if (case_count, poc_count, compile_failed) == self._last_counts:
            return

        self._last_counts = (case_count, poc_count, compile_failed)
        self._last_progress_time = now

        event_seq = self._next_event_seq
        self._next_event_seq += 1

        try:
            await self.client.send_event(
                self.task_id,
                {
                    "lease_token": self.lease_token,
                    "event_seq": event_seq,
                    "event_type": "progress",
                    "case_count": case_count,
                    "poc_count": poc_count,
                    "compile_failed": compile_failed,
                },
            )
        except Exception as exc:
            logger.warning(
                "progress event send failed: %s",
                exc,
                extra={
                    "task_id": self.task_id,
                    "event_seq": event_seq,
                },
            )

    def _get_compile_failed_count(self) -> int | None:
        stats_yaml_path = self.workspace_dir / "testgen" / "stats.yaml"
        if not stats_yaml_path.exists():
            return None
        try:
            lines = stats_yaml_path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
        except Exception:
            return None
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if not line.startswith("CompileFailed:") and not line.startswith(
                "compile_failed:"
            ):
                continue
            value = line.split(":", 1)[1].strip()
            if value.startswith('"') and value.endswith('"') and len(value) >= 2:
                value = value[1:-1].strip()
            if value.startswith("'") and value.endswith("'") and len(value) >= 2:
                value = value[1:-1].strip()
            if value.isdigit():
                return int(value)
            return None
        return None

    def _count_generated_items(self) -> Tuple[int, int]:
        testgen_dir = self.workspace_dir / "testgen"
        case_count = 0
        poc_count = 0

        tests_dir = testgen_dir / "tests"
        if tests_dir.exists():
            case_count = len([d for d in tests_dir.iterdir() if d.is_dir()])

        poc_dir = testgen_dir / "poc"
        if poc_dir.exists():
            poc_count = len([d for d in poc_dir.iterdir() if d.is_dir()])

        return case_count, poc_count
