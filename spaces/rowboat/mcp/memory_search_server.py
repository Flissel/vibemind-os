"""
memory_search MCP server (direct Qdrant backend).

Exposes a single tool `memory_search(query, top_k)` that queries the
"vibemind-memory" Qdrant collection semantically. Uses sentence-transformers
embeddings directly — bypasses Rowboat's chat/workflow engine entirely.

Markdown remains the truth. Qdrant is the vector index, populated by
`vibemind_shared.sync_memory_to_qdrant()` (called from /memory-review).

If Qdrant or the embedding model is unavailable, the tool returns an error
message so agents degrade gracefully.
"""

from __future__ import annotations

import asyncio
import json
import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

server = Server("memory-search")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="memory_search",
            description=(
                "Semantic search over the user's persistent memory "
                "(Qdrant vector index of markdown memory files). Returns "
                "top-k matches with source-file refs, memory_type, and "
                "short snippets. Use when you need facts about the user, "
                "their preferences (feedback rules), or ongoing projects "
                "that aren't in your immediate system-prompt context."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "natural-language query — what you want to know",
                    },
                    "top_k": {
                        "type": "integer",
                        "default": 5,
                        "minimum": 1,
                        "maximum": 20,
                    },
                },
                "required": ["query"],
            },
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name != "memory_search":
        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    query = (arguments.get("query") or "").strip()
    if not query:
        return [TextContent(type="text", text="error: empty query")]
    top_k = int(arguments.get("top_k", 5))

    try:
        from vibemind_shared import search_memory
    except ImportError as e:
        return [
            TextContent(
                type="text",
                text=f"error: vibemind_shared not importable: {e}",
            )
        ]

    try:
        hits = search_memory(query, top_k=top_k)
    except Exception as e:
        return [TextContent(type="text", text=f"error: search failed: {e}")]

    return [TextContent(type="text", text=json.dumps(hits, ensure_ascii=False, indent=2))]


async def _main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        sys.exit(0)
