"""Unit tests for TaskStore zip ingest and deletion."""

import io
import zipfile

import pytest

from app.services.task_store import TaskStore, ZipValidationError
from core.task_spec import sha256_hex

VALID_TOML = b"""
name = "cargo-rapx"
image = "rapx:latest"
cmd = "cargo rapx"
workspace = "/workspace"
desc = "run cargo rapx on crate"
"""

BAD_SYNTAX_TOML = b"name = [unclosed"

# Parses as TOML but fails spec validation (missing required fields).
SPEC_INVALID_TOML = b'name = "only-name"\n'


def build_zip(entries: dict) -> bytes:
    """Build an in-memory zip from {name: bytes|str} entries."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in entries.items():
            if isinstance(content, str):
                content = content.encode("utf-8")
            zf.writestr(name, content)
    return buffer.getvalue()


@pytest.fixture
def tasks_dir(tmp_path):
    return tmp_path / "tasks"


@pytest.fixture
def store(tasks_dir):
    return TaskStore(tasks_dir)


def test_zip_validation_error_is_value_error():
    assert issubclass(ZipValidationError, ValueError)


def test_ingest_zip_returns_spec_sha_and_writes_file(store, tasks_dir):
    zip_bytes = build_zip({"task.toml": VALID_TOML, "run.sh": "echo hi"})

    spec, sha256, zip_path = store.ingest_zip(zip_bytes)

    assert spec.name == "cargo-rapx"
    assert spec.image == "rapx:latest"
    assert spec.cmd == "cargo rapx"
    assert spec.desc == "run cargo rapx on crate"
    assert sha256 == sha256_hex(zip_bytes)
    assert zip_path == tasks_dir / f"{sha256}.zip"
    assert zip_path.read_bytes() == zip_bytes


def test_ingest_zip_is_idempotent_for_same_content(store):
    zip_bytes = build_zip({"task.toml": VALID_TOML})

    first = store.ingest_zip(zip_bytes)
    second = store.ingest_zip(zip_bytes)

    assert first[1] == second[1]
    assert first[2] == second[2]
    assert first[2].read_bytes() == zip_bytes


def test_ingest_zip_missing_task_toml_raises(store):
    zip_bytes = build_zip({"run.sh": "echo hi"})

    with pytest.raises(ZipValidationError, match="task.toml"):
        store.ingest_zip(zip_bytes)


def test_ingest_zip_task_toml_not_at_root_raises(store):
    zip_bytes = build_zip({"nested/task.toml": VALID_TOML})

    with pytest.raises(ZipValidationError, match="task.toml"):
        store.ingest_zip(zip_bytes)


def test_ingest_zip_bad_toml_syntax_raises(store):
    zip_bytes = build_zip({"task.toml": BAD_SYNTAX_TOML})

    with pytest.raises(ZipValidationError):
        store.ingest_zip(zip_bytes)


def test_ingest_zip_spec_validation_failure_raises(store):
    zip_bytes = build_zip({"task.toml": SPEC_INVALID_TOML})

    with pytest.raises(ZipValidationError):
        store.ingest_zip(zip_bytes)


def test_ingest_zip_rejects_parent_traversal_entry(store):
    zip_bytes = build_zip({"task.toml": VALID_TOML, "../evil.sh": "rm -rf /"})

    with pytest.raises(ZipValidationError):
        store.ingest_zip(zip_bytes)


def test_ingest_zip_rejects_absolute_path_entry(store):
    zip_bytes = build_zip({"task.toml": VALID_TOML, "/etc/passwd": "root"})

    with pytest.raises(ZipValidationError):
        store.ingest_zip(zip_bytes)


def test_ingest_zip_rejects_corrupted_zip(store):
    with pytest.raises(ZipValidationError):
        store.ingest_zip(b"this is not a zip archive")


def test_ingest_zip_does_not_write_file_on_validation_failure(store, tasks_dir):
    zip_bytes = build_zip({"run.sh": "echo hi"})

    with pytest.raises(ZipValidationError):
        store.ingest_zip(zip_bytes)

    assert not tasks_dir.exists() or list(tasks_dir.iterdir()) == []


def test_ingest_zip_leaves_no_tmp_files(store, tasks_dir):
    zip_bytes = build_zip({"task.toml": VALID_TOML})

    _, _, zip_path = store.ingest_zip(zip_bytes)

    assert zip_path.read_bytes() == zip_bytes
    assert list(tasks_dir.iterdir()) == [zip_path]


def test_write_zip_atomically_replaces_garbage_file(store, tasks_dir):
    """A truncated file at the target path must not survive the atomic write."""
    tasks_dir.mkdir(parents=True)
    target = store.zip_path("ab" * 32)
    target.write_bytes(b"truncated garbage")

    store._write_zip_atomically(target, b"full zip payload")

    assert target.read_bytes() == b"full zip payload"
    assert not (tasks_dir / f"{target.name}.tmp").exists()


def test_delete_zip_removes_file(store):
    zip_bytes = build_zip({"task.toml": VALID_TOML})
    _, sha256, zip_path = store.ingest_zip(zip_bytes)
    assert zip_path.exists()

    store.delete_zip(sha256)

    assert not zip_path.exists()


def test_delete_zip_unknown_sha_is_noop(store):
    store.delete_zip("0" * 64)
