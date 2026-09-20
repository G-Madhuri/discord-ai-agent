from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """Normalise a free-text label into a stable lookup key ('React.js' -> 'react-js')."""
    return _NON_ALNUM.sub("-", value.strip().lower()).strip("-")


def normalize_task_key(value: str) -> str:
    return value.strip().upper()
