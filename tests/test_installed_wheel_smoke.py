from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "smoke_installed_wheel.py"
SPEC = spec_from_file_location("smoke_installed_wheel", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
SMOKE = module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)
clean_subprocess_env = SMOKE.clean_subprocess_env
find_single_wheel = SMOKE.find_single_wheel
venv_python = SMOKE.venv_python
venv_entrypoint = SMOKE.venv_entrypoint


def test_find_single_wheel_requires_exactly_one_candidate(tmp_path):
    with pytest.raises(RuntimeError, match="exactly one wheel"):
        find_single_wheel(tmp_path)

    wheel = tmp_path / "arxiv_mcp_server-0.6.2-py3-none-any.whl"
    wheel.touch()
    assert find_single_wheel(tmp_path) == wheel

    (tmp_path / "arxiv_mcp_server-0.6.3-py3-none-any.whl").touch()
    with pytest.raises(RuntimeError, match="exactly one wheel"):
        find_single_wheel(tmp_path)


def test_venv_python_is_cross_platform():
    root = Path("smoke-venv")
    assert venv_python(root, platform="posix") == root / "bin" / "python"
    assert venv_python(root, platform="nt") == root / "Scripts" / "python.exe"


def test_venv_entrypoint_is_cross_platform():
    root = Path("smoke-venv")
    assert venv_entrypoint(root, platform="posix") == root / "bin" / "arxiv-mcp-server"
    assert venv_entrypoint(root, platform="nt") == (
        root / "Scripts" / "arxiv-mcp-server.exe"
    )


def test_clean_subprocess_env_isolates_python_and_home(tmp_path):
    env = clean_subprocess_env(
        tmp_path,
        source={
            "PATH": "/usr/bin",
            "PYTHONPATH": "/leaky/site-packages",
            "PYTHONHOME": "/leaky/python",
            "VIRTUAL_ENV": "/leaky/venv",
            "APP_VERSION": "9.9.9",
            "TRANSPORT": "http",
            "UV_CONSTRAINT": "/leaky/constraints.txt",
        },
    )

    assert env["PATH"] == "/usr/bin"
    assert env["HOME"] == str(tmp_path)
    assert env["USERPROFILE"] == str(tmp_path)
    assert "PYTHONPATH" not in env
    assert "PYTHONHOME" not in env
    assert "VIRTUAL_ENV" not in env
    assert "APP_VERSION" not in env
    assert "TRANSPORT" not in env
    assert "UV_CONSTRAINT" not in env
