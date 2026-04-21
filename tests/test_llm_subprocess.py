"""Subprocess LLM detector — env override + auto-detection."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from medium_ops import llm_subprocess
from medium_ops.llm_subprocess import SubprocessLLMNotFound, _detect, detect_name


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("MEDIUM_OPS_LLM_CMD", raising=False)


def test_env_override_stdin(monkeypatch):
    monkeypatch.setenv("MEDIUM_OPS_LLM_CMD", "my-cli --go")
    r = _detect()
    assert r.name == "my-cli"
    assert r.cmd == ["my-cli", "--go"]
    assert r.pass_via == "stdin"


def test_env_override_arg_mode(monkeypatch):
    monkeypatch.setenv("MEDIUM_OPS_LLM_CMD", "my-cli --prompt {prompt}")
    r = _detect()
    assert r.pass_via == "arg"
    assert "{prompt}" in " ".join(r.cmd)


def test_auto_detect_claude():
    with patch("shutil.which", side_effect=lambda name: "/usr/bin/claude" if name == "claude" else None):
        r = _detect()
        assert r.name == "claude"
        assert "--print" in r.cmd


def test_auto_detect_cursor_agent():
    def which(name):
        return "/usr/bin/cursor-agent" if name == "cursor-agent" else None

    with patch("shutil.which", side_effect=which):
        r = _detect()
        assert r.name == "cursor-agent"


def test_auto_detect_codex():
    def which(name):
        return "/usr/bin/codex" if name == "codex" else None

    with patch("shutil.which", side_effect=which):
        r = _detect()
        assert r.name == "codex"


def test_none_available_raises():
    with patch("shutil.which", return_value=None):
        with pytest.raises(SubprocessLLMNotFound):
            _detect()


def test_detect_name_none_when_missing():
    with patch("shutil.which", return_value=None):
        assert detect_name() is None
        assert not llm_subprocess.is_available()
