"""uc4/.env: how a line becomes a setting, and that the key stays out of git."""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator import env_file                                      # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def test_lines_become_settings():
    text = """
# a comment
LLM_PROVIDER=groq
GROQ_API_KEY="quoted-value"
export WALLESTER_UC4_NARRATOR=live   # trailing comment
WALLESTER_UC4_MODEL=
"""
    assert env_file.parse(text) == {"LLM_PROVIDER": "groq", "GROQ_API_KEY": "quoted-value",
                                    "WALLESTER_UC4_NARRATOR": "live"}


def test_an_empty_value_never_wipes_a_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "from-windows")
    f = tmp_path / ".env"
    f.write_text("GROQ_API_KEY=\nLLM_PROVIDER=groq\n", encoding="utf-8")
    assert env_file.load(f) == ["LLM_PROVIDER"]
    assert os.environ["GROQ_API_KEY"] == "from-windows"


def test_the_file_wins_and_only_names_are_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "old")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    f = tmp_path / ".env"
    f.write_text("GROQ_API_KEY=new-value\n", encoding="utf-8")
    names = env_file.load(f)
    assert os.environ["GROQ_API_KEY"] == "new-value"
    assert "new-value" not in env_file.note(names)


def test_no_file_is_fine(tmp_path):
    assert env_file.load(tmp_path / "missing.env") == []


def test_the_real_env_is_ignored_and_the_template_is_not():
    def ignored(path):
        return subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT).returncode == 0
    assert ignored(".env"), "uc4/.env holds the key and must never be committed"
    assert not ignored(".env.example")
    template = env_file.parse((ROOT / ".env.example").read_text(encoding="utf-8"))
    assert "GROQ_API_KEY" not in template, "the template carries no key"
