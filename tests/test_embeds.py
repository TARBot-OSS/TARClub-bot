import discord

from tarclub.embeds import EMBED_TOTAL_LIMIT, FIELD_VALUE_LIMIT, add_line_fields


def test_add_line_fields_splits_long_lists_within_field_limit():
    embed = discord.Embed(title="Roster")
    lines = [f"• line {i} " + "x" * 80 for i in range(40)]

    assert add_line_fields(embed, "Roster", lines) is True
    assert len(embed.fields) > 1
    assert all(len(field.value) <= FIELD_VALUE_LIMIT + 1 for field in embed.fields)
    assert len(embed) <= EMBED_TOTAL_LIMIT


def test_add_line_fields_reports_dropped_lines_when_embed_is_full():
    embed = discord.Embed(title="Roster")
    lines = ["x" * 990 for _ in range(30)]

    assert add_line_fields(embed, "Roster", lines) is False
    assert len(embed) <= EMBED_TOTAL_LIMIT
