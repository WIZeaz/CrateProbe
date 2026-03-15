from pathlib import Path
from typing import List
import os


class FileNotFoundError(Exception):
    """Raised when file is not found"""

    pass


def read_last_n_lines(file_path: str, n: int) -> List[str]:
    """
    Read the last N lines from a file efficiently.

    Args:
        file_path: Path to the file
        n: Number of lines to read

    Returns:
        List of last N lines (without trailing newlines)

    Raises:
        FileNotFoundError: If file doesn't exist
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    if n <= 0:
        return []

    # Read file content
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        raise FileNotFoundError(f"Error reading file: {e}")

    # Strip newlines and return last n lines
    lines = [line.rstrip("\n\r") for line in lines]

    if len(lines) <= n:
        return lines

    return lines[-n:]
