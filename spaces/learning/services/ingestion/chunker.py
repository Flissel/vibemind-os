from __future__ import annotations

import hashlib

from spaces.learning.services.ingestion.parsers import ParsedBlock
from spaces.learning.services.ingestion.repository import SourceChunkInput


def chunk_blocks(
    blocks: list[ParsedBlock] | tuple[ParsedBlock, ...],
    *,
    maximum_characters: int = 1200,
) -> tuple[SourceChunkInput, ...]:
    if maximum_characters < 32:
        raise ValueError("maximum chunk size must be at least 32 characters")
    chunks: list[SourceChunkInput] = []
    for block in blocks:
        start = 0
        is_code = block.metadata.get("kind") == "code"
        while start < len(block.text):
            if not is_code:
                while start < len(block.text) and block.text[start].isspace():
                    start += 1
            if start >= len(block.text):
                break
            boundary = min(start + maximum_characters, len(block.text))
            if boundary < len(block.text):
                whitespace = max(
                    block.text.rfind(" ", start, boundary + 1),
                    block.text.rfind("\n", start, boundary + 1),
                )
                if whitespace > start:
                    boundary = whitespace
            content_end = boundary
            if not is_code:
                while content_end > start and block.text[content_end - 1].isspace():
                    content_end -= 1
            content = block.text[start:content_end]
            if content:
                locator = {
                    **block.locator,
                    "chunk_start": start,
                    "chunk_end": content_end,
                }
                chunks.append(
                    SourceChunkInput(
                        content=content,
                        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                        locator=locator,
                        metadata=dict(block.metadata),
                    )
                )
            start = boundary
    return tuple(chunks)
