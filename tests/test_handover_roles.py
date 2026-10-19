from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from tarclub.cogs.term_cog import can_confirm_handover, grant_alumni_roles


def test_only_incoming_chair_or_owner_can_confirm():
    pending = {"incoming_chair_id": 10}

    assert can_confirm_handover(10, pending, owner_id=99) is True
    assert can_confirm_handover(99, pending, owner_id=99) is True
    assert can_confirm_handover(11, pending, owner_id=99) is False


@pytest.mark.asyncio
async def test_grant_alumni_roles_creates_role_and_counts_failures():
    guild = MagicMock(spec=discord.Guild)
    guild.roles = []
    alumni_role = MagicMock(spec=discord.Role)
    guild.create_role = AsyncMock(return_value=alumni_role)

    ok_member = MagicMock(spec=discord.Member)
    ok_member.add_roles = AsyncMock()
    forbidden_member = MagicMock(spec=discord.Member)
    forbidden_member.add_roles = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "no"))
    guild.get_member.side_effect = {1: ok_member, 2: forbidden_member, 3: None}.get

    granted, failed = await grant_alumni_roles(guild, "2025/2026", [1, 2, 3])

    guild.create_role.assert_awaited_once()
    assert guild.create_role.await_args.kwargs["name"] == "Alumni Exco (2025/2026)"
    ok_member.add_roles.assert_awaited_once_with(alumni_role, reason="TARClub term handover")
    assert (granted, failed) == (1, 2)


@pytest.mark.asyncio
async def test_grant_alumni_roles_reuses_existing_role():
    existing = MagicMock(spec=discord.Role)
    existing.name = "Alumni Exco (2025/2026)"
    guild = MagicMock(spec=discord.Guild)
    guild.roles = [existing]
    guild.create_role = AsyncMock()

    member = MagicMock(spec=discord.Member)
    member.add_roles = AsyncMock()
    guild.get_member.return_value = member

    granted, failed = await grant_alumni_roles(guild, "2025/2026", [5])

    guild.create_role.assert_not_awaited()
    member.add_roles.assert_awaited_once_with(existing, reason="TARClub term handover")
    assert (granted, failed) == (1, 0)
