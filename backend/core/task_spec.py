import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Optional

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

_MEM_RE = re.compile(r"^\d+(\.\d+)?[bkmgBKMG]?$")
_TIMEOUT_RE = re.compile(r"^(\d+(\.\d+)?)([smh])$")
_TIMEOUT_MULTIPLIERS = {"s": 1, "m": 60, "h": 3600}


class TaskSpecError(ValueError):
    pass


@dataclass
class TaskLimits:
    cpu: Optional[float] = None
    mem: Optional[str] = None
    timeout_seconds: Optional[int] = None


@dataclass
class TaskSpec:
    name: str
    image: str
    cmd: str
    workspace: str
    desc: Optional[str] = None
    artifacts: list[str] = field(default_factory=list)
    watch: list[str] = field(default_factory=list)
    limits: TaskLimits = field(default_factory=TaskLimits)
    env: dict[str, str] = field(default_factory=dict)


def parse_task_spec(data: bytes) -> TaskSpec:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise TaskSpecError(f"task.toml is not valid UTF-8: {e}") from e
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise TaskSpecError(f"task.toml parse error: {e}") from e

    name = _required_str(raw, "name")
    image = _required_str(raw, "image")
    cmd = _required_str(raw, "cmd")
    workspace = _required_str(raw, "workspace")
    if not PurePosixPath(workspace).is_absolute():
        raise TaskSpecError(f"workspace must be an absolute path: {workspace!r}")

    desc = raw.get("desc")
    if desc is not None and not isinstance(desc, str):
        raise TaskSpecError("desc must be a string")

    artifacts = _rel_path_list(raw, "artifacts")
    watch = _rel_path_list(raw, "watch")
    limits = _parse_limits(raw.get("limit", {}))
    env = _parse_env(raw.get("env", {}))

    return TaskSpec(
        name=name,
        image=image,
        cmd=cmd,
        workspace=workspace,
        desc=desc,
        artifacts=artifacts,
        watch=watch,
        limits=limits,
        env=env,
    )


def spec_to_dict(spec: TaskSpec) -> dict:
    return {
        "name": spec.name,
        "image": spec.image,
        "cmd": spec.cmd,
        "workspace": spec.workspace,
        "desc": spec.desc,
        "artifacts": list(spec.artifacts),
        "watch": list(spec.watch),
        "limits": {
            "cpu": spec.limits.cpu,
            "mem": spec.limits.mem,
            "timeout_seconds": spec.limits.timeout_seconds,
        },
        "env": dict(spec.env),
    }


def spec_from_dict(d: dict) -> TaskSpec:
    limits = d.get("limits") or {}
    return TaskSpec(
        name=d["name"],
        image=d["image"],
        cmd=d["cmd"],
        workspace=d["workspace"],
        desc=d.get("desc"),
        artifacts=list(d.get("artifacts") or []),
        watch=list(d.get("watch") or []),
        limits=TaskLimits(
            cpu=limits.get("cpu"),
            mem=limits.get("mem"),
            timeout_seconds=limits.get("timeout_seconds"),
        ),
        env=dict(d.get("env") or {}),
    )


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _required_str(raw: dict, key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise TaskSpecError(f"{key} is required and must be a non-empty string")
    return value


def _rel_path_list(raw: dict, key: str) -> list[str]:
    value = raw.get(key, [])
    if not isinstance(value, list):
        raise TaskSpecError(f"{key} must be a list of relative paths")
    for entry in value:
        if not isinstance(entry, str):
            raise TaskSpecError(f"{key} entries must be strings: {entry!r}")
        path = PurePosixPath(entry)
        if path.is_absolute():
            raise TaskSpecError(f"{key} entry must be relative: {entry!r}")
        if ".." in path.parts:
            raise TaskSpecError(f"{key} entry must not contain '..': {entry!r}")
    return list(value)


def _parse_limits(raw: dict) -> TaskLimits:
    if not isinstance(raw, dict):
        raise TaskSpecError("limit must be a table")

    cpu = raw.get("cpu")
    if cpu is not None:
        if isinstance(cpu, bool) or not isinstance(cpu, (int, float)) or cpu <= 0:
            raise TaskSpecError(f"limit.cpu must be a positive number: {cpu!r}")
        cpu = float(cpu)

    mem = raw.get("mem")
    if mem is not None:
        if not isinstance(mem, str) or not _MEM_RE.match(mem):
            raise TaskSpecError(f"limit.mem has invalid format: {mem!r}")

    timeout = raw.get("timeout")
    timeout_seconds = None
    if timeout is not None:
        timeout_seconds = _parse_timeout(timeout)

    return TaskLimits(cpu=cpu, mem=mem, timeout_seconds=timeout_seconds)


def _parse_timeout(value) -> int:
    if isinstance(value, bool):
        raise TaskSpecError(f"limit.timeout has invalid format: {value!r}")
    if isinstance(value, int):
        if value <= 0:
            raise TaskSpecError(f"limit.timeout must be positive: {value!r}")
        return value
    if isinstance(value, str):
        match = _TIMEOUT_RE.match(value)
        if match:
            seconds = int(
                round(float(match.group(1)) * _TIMEOUT_MULTIPLIERS[match.group(3)])
            )
            if seconds > 0:
                return seconds
    raise TaskSpecError(f"limit.timeout has invalid format: {value!r}")


def _parse_env(raw: dict) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise TaskSpecError("env must be a table")
    for key, value in raw.items():
        if not isinstance(value, str):
            raise TaskSpecError(f"env.{key} must be a string: {value!r}")
    return dict(raw)
