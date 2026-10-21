import json

import pytest

from core.task_spec import (
    TaskLimits,
    TaskSpec,
    TaskSpecError,
    parse_task_spec,
    sha256_hex,
    spec_from_dict,
    spec_to_dict,
)

FULL_TOML = b"""
name = "cargo-rapx"
image = "rapx:latest"
cmd = "cargo rapx"
workspace = "/workspace"
desc = "run cargo rapx on crate"
artifacts = ["stdout.log", "stderr.log", "results/"]
watch = ["stdout.log", "stderr.log"]

[limit]
cpu = 2
mem = "4g"
timeout = "2h"

[env]
RUSTFLAGS = "-Z unstable-options"
"""

MINIMAL_TOML = b"""
name = "t"
image = "img"
cmd = "true"
workspace = "/workspace"
"""


def test_parse_full_spec():
    spec = parse_task_spec(FULL_TOML)
    assert spec.name == "cargo-rapx"
    assert spec.image == "rapx:latest"
    assert spec.cmd == "cargo rapx"
    assert spec.workspace == "/workspace"
    assert spec.desc == "run cargo rapx on crate"
    assert spec.artifacts == ["stdout.log", "stderr.log", "results/"]
    assert spec.watch == ["stdout.log", "stderr.log"]
    assert spec.limits.cpu == 2.0
    assert spec.limits.mem == "4g"
    assert spec.limits.timeout_seconds == 7200
    assert spec.env == {"RUSTFLAGS": "-Z unstable-options"}


def test_parse_minimal_spec_defaults():
    spec = parse_task_spec(MINIMAL_TOML)
    assert spec.name == "t"
    assert spec.image == "img"
    assert spec.cmd == "true"
    assert spec.workspace == "/workspace"
    assert spec.desc is None
    assert spec.artifacts == []
    assert spec.watch == []
    assert spec.limits == TaskLimits(cpu=None, mem=None, timeout_seconds=None)
    assert spec.env == {}


def test_missing_name_raises():
    with pytest.raises(TaskSpecError):
        parse_task_spec(b'image = "img"\ncmd = "true"\nworkspace = "/ws"\n')


def test_empty_cmd_raises():
    with pytest.raises(TaskSpecError):
        parse_task_spec(b'name = "t"\nimage = "img"\ncmd = ""\nworkspace = "/ws"\n')


def test_relative_workspace_raises():
    with pytest.raises(TaskSpecError):
        parse_task_spec(b'name = "t"\nimage = "img"\ncmd = "true"\nworkspace = "ws"\n')


def test_watch_parent_dir_raises():
    with pytest.raises(TaskSpecError):
        parse_task_spec(MINIMAL_TOML + b'watch = ["../secret"]\n')


def test_watch_absolute_path_raises():
    with pytest.raises(TaskSpecError):
        parse_task_spec(MINIMAL_TOML + b'watch = ["/etc/passwd"]\n')


def test_artifacts_parent_dir_raises():
    with pytest.raises(TaskSpecError):
        parse_task_spec(MINIMAL_TOML + b'artifacts = ["a/../../b"]\n')


def test_artifacts_absolute_path_raises():
    with pytest.raises(TaskSpecError):
        parse_task_spec(MINIMAL_TOML + b'artifacts = ["/abs"]\n')


def test_cpu_negative_raises():
    with pytest.raises(TaskSpecError):
        parse_task_spec(MINIMAL_TOML + b"[limit]\ncpu = -1\n")


def test_mem_invalid_raises():
    with pytest.raises(TaskSpecError):
        parse_task_spec(MINIMAL_TOML + b'[limit]\nmem = "abc"\n')


def test_timeout_invalid_suffix_raises():
    with pytest.raises(TaskSpecError):
        parse_task_spec(MINIMAL_TOML + b'[limit]\ntimeout = "10x"\n')


def test_env_non_string_value_raises():
    with pytest.raises(TaskSpecError):
        parse_task_spec(MINIMAL_TOML + b"[env]\nFOO = 1\n")


def test_invalid_toml_raises():
    with pytest.raises(TaskSpecError):
        parse_task_spec(b"not = [valid")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('"2h"', 7200),
        ('"30m"', 1800),
        ('"45s"', 45),
        ("90", 90),
    ],
)
def test_timeout_units(raw, expected):
    spec = parse_task_spec(MINIMAL_TOML + f"[limit]\ntimeout = {raw}\n".encode())
    assert spec.limits.timeout_seconds == expected


@pytest.mark.parametrize("mem", ["4g", "512m", "4096"])
def test_mem_accepted_formats(mem):
    spec = parse_task_spec(MINIMAL_TOML + f'[limit]\nmem = "{mem}"\n'.encode())
    assert spec.limits.mem == mem


def test_unknown_fields_ignored():
    spec = parse_task_spec(
        MINIMAL_TOML + b'schema_version = 3\nfoo = "bar"\n[extra]\nx = 1\n'
    )
    assert spec.name == "t"


def test_dict_round_trip():
    for toml in (FULL_TOML, MINIMAL_TOML):
        spec = parse_task_spec(toml)
        restored = spec_from_dict(json.loads(json.dumps(spec_to_dict(spec))))
        assert restored == spec


def test_sha256_hex_empty():
    assert (
        sha256_hex(b"")
        == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
