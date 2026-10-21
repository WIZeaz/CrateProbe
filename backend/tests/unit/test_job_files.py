"""Tests for JobFileStore per-job file storage service."""

import io

import pytest

from app.services.job_files import (
    ArtifactTooLargeError,
    JobFileStore,
    PathSafetyError,
)

FULL_LIMIT = 1024
ARTIFACT_LIMIT = 1024


@pytest.fixture
def store(tmp_path):
    return JobFileStore(
        workspace=tmp_path,
        full_mode_max_bytes=FULL_LIMIT,
        artifact_max_bytes=ARTIFACT_LIMIT,
    )


class ChunkedStream:
    """File-like stream that returns predetermined chunks regardless of n."""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def read(self, n=-1):
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


# ---------------------------------------------------------------------------
# safe_join
# ---------------------------------------------------------------------------


def test_safe_join_rejects_absolute_path(store, tmp_path):
    with pytest.raises(PathSafetyError):
        store.safe_join(tmp_path, "/etc/passwd")


def test_safe_join_rejects_parent_traversal(store, tmp_path):
    with pytest.raises(PathSafetyError):
        store.safe_join(tmp_path, "a/../../b")


def test_safe_join_rejects_bare_parent(store, tmp_path):
    with pytest.raises(PathSafetyError):
        store.safe_join(tmp_path, "..")


def test_safe_join_rejects_empty_path(store, tmp_path):
    with pytest.raises(PathSafetyError):
        store.safe_join(tmp_path, "")


def test_path_safety_error_is_value_error():
    assert issubclass(PathSafetyError, ValueError)


def test_safe_join_accepts_nested_relative_path(store, tmp_path):
    result = store.safe_join(tmp_path, "sub/dir/file.txt")
    assert result == (tmp_path / "sub/dir/file.txt").resolve()
    assert tmp_path.resolve() in result.parents


def test_safe_join_rejects_symlink_escape(store, tmp_path):
    """A symlink inside base pointing outside must be rejected after resolve."""
    base = tmp_path / "base"
    base.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (base / "link").symlink_to(outside)

    with pytest.raises(PathSafetyError):
        store.safe_join(base, "link/secret.txt")

    (base / "ok.txt").write_text("hi")
    result = store.safe_join(base, "ok.txt")
    assert result == base.resolve() / "ok.txt"


# ---------------------------------------------------------------------------
# directory helpers
# ---------------------------------------------------------------------------


def test_files_dir_and_artifacts_dir_do_not_create(store):
    files = store.files_dir(7)
    artifacts = store.artifacts_dir(7)
    assert files.name == "files"
    assert artifacts.name == "artifacts"
    assert files.parent == artifacts.parent
    assert files.parent.name == "7"
    assert not files.exists()
    assert not artifacts.exists()


# ---------------------------------------------------------------------------
# append_file
# ---------------------------------------------------------------------------


def test_append_file_writes_and_appends(store):
    store.append_file(1, "stdout.log", "hello ")
    store.append_file(1, "stdout.log", "world")
    assert (store.files_dir(1) / "stdout.log").read_text() == "hello world"


def test_append_file_creates_parent_dirs(store):
    store.append_file(1, "logs/deep/out.log", "x")
    assert (store.files_dir(1) / "logs/deep/out.log").read_text() == "x"


def test_append_file_rejects_unsafe_path(store):
    with pytest.raises(PathSafetyError):
        store.append_file(1, "../evil.log", "x")


# ---------------------------------------------------------------------------
# write_full
# ---------------------------------------------------------------------------


def test_write_full_under_limit_keeps_whole_content(store):
    content = "line1\nline2\n"
    store.write_full(1, "watch.txt", content)
    assert (store.files_dir(1) / "watch.txt").read_text() == content


def test_write_full_over_limit_keeps_tail(store):
    content = "A" * 100 + "B" * 2000  # 2100 bytes > 1024 cap
    store.write_full(1, "watch.txt", content)
    target = store.files_dir(1) / "watch.txt"
    data = target.read_bytes()
    assert len(data) == FULL_LIMIT
    assert data == b"B" * FULL_LIMIT


def test_write_full_truncates_multibyte_content(store):
    # each char is 3 bytes in utf-8; 500 chars = 1500 bytes > cap
    content = "界" * 500
    store.write_full(1, "watch.txt", content)
    data = (store.files_dir(1) / "watch.txt").read_bytes()
    assert len(data) == FULL_LIMIT


# ---------------------------------------------------------------------------
# list_files / read_tail / read_raw
# ---------------------------------------------------------------------------


def test_list_files_empty_when_dir_missing(store):
    assert store.list_files(99) == []


def test_list_files_returns_sorted_entries(store):
    store.append_file(1, "b.log", "bb")
    store.append_file(1, "sub/a.log", "a")
    store.append_file(1, "c.log", "ccc")

    entries = store.list_files(1)

    assert [e["path"] for e in entries] == ["b.log", "c.log", "sub/a.log"]
    by_path = {e["path"]: e for e in entries}
    assert by_path["b.log"]["size"] == 2
    assert by_path["c.log"]["size"] == 3
    assert by_path["sub/a.log"]["size"] == 1
    assert all(isinstance(e["mtime"], float) for e in entries)


def test_read_tail_returns_last_lines(store):
    store.append_file(1, "stdout.log", "\n".join(f"line{i}" for i in range(10)))
    assert store.read_tail(1, "stdout.log", 3) == ["line7", "line8", "line9"]


def test_read_tail_missing_file_raises(store):
    with pytest.raises(FileNotFoundError):
        store.read_tail(1, "nope.log", 5)


def test_read_raw_returns_full_content(store):
    store.append_file(1, "stdout.log", "hello world")
    assert store.read_raw(1, "stdout.log") == "hello world"


def test_read_raw_missing_file_raises(store):
    with pytest.raises(FileNotFoundError):
        store.read_raw(1, "nope.log")


# ---------------------------------------------------------------------------
# artifacts
# ---------------------------------------------------------------------------


def test_save_artifact_streams_and_returns_size(store):
    data = b"x" * 600
    n = store.save_artifact(1, "out/result.bin", io.BytesIO(data))
    assert n == 600
    assert (store.artifacts_dir(1) / "out/result.bin").read_bytes() == data


def test_save_artifact_at_limit_succeeds(store):
    data = b"y" * ARTIFACT_LIMIT
    n = store.save_artifact(1, "exact.bin", io.BytesIO(data))
    assert n == ARTIFACT_LIMIT
    assert (store.artifacts_dir(1) / "exact.bin").read_bytes() == data


def test_save_artifact_over_limit_raises_and_removes_partial(store):
    data = b"z" * (ARTIFACT_LIMIT + 1)
    with pytest.raises(ArtifactTooLargeError):
        store.save_artifact(1, "big.bin", io.BytesIO(data))
    assert not (store.artifacts_dir(1) / "big.bin").exists()


def test_save_artifact_aborts_mid_stream_and_removes_partial(store):
    # second chunk pushes the total past the cap
    stream = ChunkedStream([b"a" * 600, b"b" * 600])
    with pytest.raises(ArtifactTooLargeError):
        store.save_artifact(1, "nested/dir/big.bin", stream)
    assert not (store.artifacts_dir(1) / "nested/dir/big.bin").exists()


def test_save_artifact_rejects_unsafe_path(store):
    with pytest.raises(PathSafetyError):
        store.save_artifact(1, "/etc/evil", io.BytesIO(b"x"))


def test_list_artifacts_empty_when_missing(store):
    assert store.list_artifacts(42) == []


def test_list_artifacts_returns_sorted_entries(store):
    store.save_artifact(1, "b.bin", io.BytesIO(b"12"))
    store.save_artifact(1, "sub/a.bin", io.BytesIO(b"1"))
    entries = store.list_artifacts(1)
    assert entries == [
        {"path": "b.bin", "size": 2},
        {"path": "sub/a.bin", "size": 1},
    ]


def test_artifact_file_path_resolves_inside_artifacts_dir(store):
    p = store.artifact_file_path(1, "out.bin")
    assert p == (store.artifacts_dir(1) / "out.bin").resolve()


def test_artifact_file_path_rejects_traversal(store):
    with pytest.raises(PathSafetyError):
        store.artifact_file_path(1, "../../etc/passwd")


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


def test_clear_removes_files_and_artifacts(store):
    store.append_file(1, "stdout.log", "x")
    store.save_artifact(1, "a.bin", io.BytesIO(b"y"))
    store.clear(1)
    assert not store.files_dir(1).exists()
    assert not store.artifacts_dir(1).exists()


def test_clear_is_idempotent_when_missing(store):
    store.clear(123)  # must not raise
    assert not store.files_dir(123).exists()
    assert not store.artifacts_dir(123).exists()
