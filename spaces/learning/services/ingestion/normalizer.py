from __future__ import annotations

import hashlib
import re
import unicodedata

from spaces.learning.services.ingestion.parsers import ParsedBlock


_WHITESPACE = re.compile(r"\s+")
NORMALIZATION_VERSION = "2"


def normalize_blocks(blocks: list[ParsedBlock] | tuple[ParsedBlock, ...]) -> tuple[ParsedBlock, ...]:
    normalized: list[ParsedBlock] = []
    for block in blocks:
        if block.metadata.get("kind") == "code":
            text = block.text.replace("\r\n", "\n").replace("\r", "\n")
            text = "\n".join(line.rstrip() for line in text.splitlines()).rstrip("\n")
        else:
            text = unicodedata.normalize("NFKC", block.text).replace("\r\n", "\n")
            text = _WHITESPACE.sub(" ", text).strip()
        if text:
            locator = dict(block.locator)
            locator.update(
                {
                    "coordinate_space": "normalized-block-v2",
                    "normalization_version": NORMALIZATION_VERSION,
                    "normalized_block_hash": hashlib.sha256(
                        text.encode("utf-8")
                    ).hexdigest(),
                }
            )
            normalized.append(
                ParsedBlock(
                    text=text,
                    locator=locator,
                    metadata=dict(block.metadata),
                )
            )
    return tuple(normalized)
