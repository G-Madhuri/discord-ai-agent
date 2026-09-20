from __future__ import annotations

import re

from app.core.config import settings
from app.rag.interfaces import Chunk

_PARAGRAPH = re.compile(r"\n\s*\n")


def estimate_tokens(text: str) -> int:
    """Rough 4-chars-per-token heuristic — good enough for budgeting, and it
    avoids pulling in a tokenizer dependency."""
    return max(1, len(text) // 4)


class ParagraphChunker:
    """Splits on blank lines and packs paragraphs up to `chunk_size` characters.

    Paragraph boundaries are respected where possible so retrieved excerpts read
    as coherent statements ('the dashboard uses React + Recharts') instead of
    sentence fragments.
    """

    def __init__(self, chunk_size: int | None = None, overlap: int | None = None) -> None:
        self.chunk_size = chunk_size or settings.rag_chunk_size
        self.overlap = overlap if overlap is not None else settings.rag_chunk_overlap
        if self.overlap >= self.chunk_size:
            raise ValueError("chunk overlap must be smaller than chunk size")

    def split(self, text: str, *, metadata: dict | None = None) -> list[Chunk]:
        cleaned = text.strip()
        if not cleaned:
            return []

        pieces = [p.strip() for p in _PARAGRAPH.split(cleaned) if p.strip()]
        packed: list[str] = []
        buffer = ""
        for piece in pieces:
            for part in self._hard_split(piece):
                if not buffer:
                    buffer = part
                elif len(buffer) + len(part) + 2 <= self.chunk_size:
                    buffer = f"{buffer}\n\n{part}"
                else:
                    packed.append(buffer)
                    buffer = self._carry_over(buffer, part)
        if buffer:
            packed.append(buffer)

        return [
            Chunk(
                index=i,
                content=content,
                token_estimate=estimate_tokens(content),
                metadata=dict(metadata or {}),
            )
            for i, content in enumerate(packed)
        ]

    def _hard_split(self, piece: str) -> list[str]:
        """Break a paragraph that is larger than one chunk on its own."""
        if len(piece) <= self.chunk_size:
            return [piece]
        step = self.chunk_size - self.overlap
        return [piece[i : i + self.chunk_size] for i in range(0, len(piece), step)]

    def _carry_over(self, previous: str, nxt: str) -> str:
        if not self.overlap:
            return nxt
        tail = previous[-self.overlap :].lstrip()
        return f"{tail}\n\n{nxt}" if tail else nxt
