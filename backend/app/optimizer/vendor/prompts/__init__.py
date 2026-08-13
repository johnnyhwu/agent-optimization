"""Prompt loading utilities for ReflACT.

Prompts are stored as ``.md`` files and loaded at runtime:

- **Generic** prompts live in ``vendor/prompts/*.md``
- **Mode-specific** prompts live in ``vendor/prompts/<mode>/*.md``

``load_prompt(name, env)`` tries the mode-specific path first, then falls
back to the generic default.

Upstream keys the override on the *environment* (alfworld, searchqa, …) and
looks it up under ``skillopt/envs/<env>/prompts/``. There is one environment
here — an HTTP agent — and what varies instead is the optimization **mode**:
an `isolated` run rewrites a skill's body while a `routing` run rewrites its
description, and the two ask an analyst for different things. So the same
mechanism is kept and the lookup directory moves next to the generic prompts,
which is the whole difference from upstream (`VENDORED.md`).
"""
from __future__ import annotations

import os

_PROMPTS_DIR = os.path.dirname(os.path.abspath(__file__))

_cache: dict[str, str] = {}


def _read_file(path: str) -> str | None:
    if path in _cache:
        return _cache[path]
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        content = f.read()
    _cache[path] = content
    return content


def load_prompt(name: str, env: str | None = None) -> str:
    """Load a prompt by name with env-specific override and generic fallback.

    Lookup order:
      1. ``vendor/prompts/{env}/{name}.md``  (if *env* given)
      2. ``vendor/prompts/{name}.md``         (generic default)

    Raises ``FileNotFoundError`` if neither path exists.
    """
    if env is not None:
        env_path = os.path.join(_PROMPTS_DIR, env, f"{name}.md")
        content = _read_file(env_path)
        if content is not None:
            return content

    generic_path = os.path.join(_PROMPTS_DIR, f"{name}.md")
    content = _read_file(generic_path)
    if content is not None:
        return content

    searched = []
    if env is not None:
        searched.append(os.path.join("vendor/prompts", env, f"{name}.md"))
    searched.append(f"vendor/prompts/{name}.md")
    raise FileNotFoundError(
        f"Prompt '{name}' not found. Searched: {', '.join(searched)}"
    )


def clear_cache() -> None:
    """Clear the prompt file cache (useful for testing)."""
    _cache.clear()
