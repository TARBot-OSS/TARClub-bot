from __future__ import annotations

import discord

FIELD_VALUE_LIMIT = 1000
EMBED_FIELD_LIMIT = 25
EMBED_TOTAL_LIMIT = 5800


def add_line_fields(embed: discord.Embed, title: str, lines: list[str]) -> bool:
    """Add lines as embed fields within Discord limits. Returns False when lines were dropped."""
    chunks: list[str] = []
    current = ""
    for line in lines:
        if current and len(current) + len(line) + 1 > FIELD_VALUE_LIMIT:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current:
        chunks.append(current)

    for index, chunk in enumerate(chunks, start=1):
        name = title if len(chunks) == 1 else f"{title} (Part {index})"
        if (
            len(embed.fields) >= EMBED_FIELD_LIMIT
            or len(embed) + len(name) + len(chunk) > EMBED_TOTAL_LIMIT
        ):
            return False
        embed.add_field(name=name, value=chunk, inline=False)
    return True
