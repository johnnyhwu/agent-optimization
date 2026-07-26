"""§6.7 truncation: cut the BODY, never the SPAN.

Keep every span in the trace (root causes are often early, symptoms late). Only a
single span's over-long input/output body is shortened — head + tail kept, middle
elided — so the span skeleton and both ends of the evidence survive.
"""
from __future__ import annotations

from app.config import settings


def truncate_body(text: str, max_chars: int | None = None) -> tuple[str, bool]:
    """Return (possibly-truncated text, was_truncated)."""
    limit = max_chars if max_chars is not None else settings.span_body_max_chars
    if text is None or len(text) <= limit:
        return text, False
    half = max(1, (limit - 40) // 2)
    head, tail = text[:half], text[-half:]
    elided = len(text) - len(head) - len(tail)
    return f"{head}\n… [{elided} chars truncated] …\n{tail}", True
