"""
Settings from uc4/.env.

The app reads its settings (GROQ_API_KEY, LLM_PROVIDER, WALLESTER_UC4_*) from
the environment. This puts the lines of uc4/.env into the environment when a
server or tool starts, so a key pasted into that file is the key used.

    KEY=value          one setting per line
    # comment          ignored, as are blank lines
    KEY="value"        quotes around a value are removed
    KEY=               an empty value is skipped, so a blank line in the
                       template never wipes a key set elsewhere

A value in .env wins over the same name already set in Windows, because the
file is where you said the settings live. The file is git-ignored and never
committed; .env.example is the template that is.

Called first thing by the start-up scripts in tools/, before anything reads a
setting - orchestrator/settings.py reads its values once, at import. Tests do
not call it, so a developer's .env cannot change a test result.
"""

import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def parse(text: str) -> dict[str, str]:
    values = {}
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            raise ValueError(f".env line {n} has no '=': {line[:40]!r}")
        name, value = (part.strip() for part in line.split("=", 1))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:                       # KEY=value   # comment
            value = value.split(" #", 1)[0].rstrip()
        if name and value:
            values[name] = value
    return values


def load(path: Path = ENV_FILE) -> list[str]:
    """Put the settings from .env into the environment. Returns the names set
    (never the values), for a one-line note at start-up."""
    if not path.exists():
        return []
    values = parse(path.read_text(encoding="utf-8-sig"))
    os.environ.update(values)
    return sorted(values)


def note(names: list[str]) -> str:
    if not names:
        return f"No .env file at {ENV_FILE} - using the environment as it is."
    return f"Settings from {ENV_FILE.name}: " + ", ".join(names)
