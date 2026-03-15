import pytest
from pathlib import Path
from app.utils.file_utils import (
    read_last_n_lines,
    FileNotFoundError as CustomFileNotFoundError,
)


def test_read_last_n_lines_basic(tmp_path):
    """Test reading last N lines from a file"""
    test_file = tmp_path / "test.log"
    content = "\n".join([f"Line {i}" for i in range(1, 11)])
    test_file.write_text(content)

    result = read_last_n_lines(str(test_file), 5)

    assert len(result) == 5
    assert result[0] == "Line 6"
    assert result[-1] == "Line 10"


def test_read_last_n_lines_less_than_available(tmp_path):
    """Test reading more lines than available"""
    test_file = tmp_path / "test.log"
    test_file.write_text("Line 1\nLine 2\nLine 3")

    result = read_last_n_lines(str(test_file), 10)

    assert len(result) == 3
    assert result[0] == "Line 1"
    assert result[-1] == "Line 3"


def test_read_last_n_lines_empty_file(tmp_path):
    """Test reading from empty file"""
    test_file = tmp_path / "empty.log"
    test_file.write_text("")

    result = read_last_n_lines(str(test_file), 5)

    assert len(result) == 0


def test_read_last_n_lines_file_not_found(tmp_path):
    """Test reading from non-existent file"""
    with pytest.raises(CustomFileNotFoundError):
        read_last_n_lines(str(tmp_path / "nonexistent.log"), 5)


def test_read_last_n_lines_zero_lines(tmp_path):
    """Test reading zero lines"""
    test_file = tmp_path / "test.log"
    test_file.write_text("Line 1\nLine 2\nLine 3")

    result = read_last_n_lines(str(test_file), 0)

    assert len(result) == 0


def test_read_last_n_lines_large_file(tmp_path):
    """Test reading from a large file efficiently"""
    test_file = tmp_path / "large.log"
    # Create a file with 10000 lines
    content = "\n".join([f"Line {i}" for i in range(1, 10001)])
    test_file.write_text(content)

    result = read_last_n_lines(str(test_file), 100)

    assert len(result) == 100
    assert result[0] == "Line 9901"
    assert result[-1] == "Line 10000"
