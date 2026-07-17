# Force Subscribe helper functions

import asyncio
import logging
from pyrogram import Client
from pyrogram.errors import UserNotParticipant
from pyrogram.types import InlineKeyboardButton
from plugins.settings_db import (
    get_settings, force_sub_channel_id, force_sub_channel_mode,
    force_sub_channel_link, set_force_sub_link,
)

logger = logging.getLogger(__name__)


async def _has_pending_request(client: Client, chat_id, user_id) -> bool:
    """Live-check Telegram directly for whether this user still has a
    pending join request in this channel right now. We deliberately don't
    cache this anywhere - a local 'they requested once' flag would never
    know if they later left/cancelled, which is exactly the bug this
    replaces. Always asking Telegram keeps it self-correcting."""
    try:
        async for _ in client.get_chat_join_requests(chat_id, user_id=user_id, limit=1):
            return True
        return False
    except Exception:
        # Bot lacks permission to list requests, or none exist -> treat as no request
        return False


async def not_joined_channels(client: Client, user_id: int, settings=None):
    """Return the list of force-sub channel entries this user has NOT joined.
    Returns [] if force_sub is disabled or the user has joined everything.

    For 'request' mode channels, having a currently-pending join request is
    enough on its own - we don't wait for the admin to manually approve it.
    This is checked live against Telegram every time (see
    _has_pending_request), so someone who left after being approved, or
    cancelled their request, correctly has to request again."""
    if settings is None:
        settings = await get_settings()
    if not settings.get("force_sub"):
        return []
    entries = settings.get("force_sub_channels") or []

    async def _check(entry):
        ch = force_sub_channel_id(entry)
        mode = force_sub_channel_mode(entry)
        try:
            member = await client.get_chat_member(ch, user_id)
            if member.status in ("kicked", "left"):
                return entry
            return None
        except UserNotParticipant:
            if mode == "request" and await _has_pending_request(client, ch, user_id):
                # They tapped Join and a request is sitting there right now
                # - that's enough, don't make them wait for manual approval.
                return None
            return entry
        except Exception:
            # Bot not admin there / invalid channel -> don't lock everyone out
            return None

    # Check every channel concurrently instead of one-by-one - with several
    # force-sub channels this used to mean many sequential Telegram API
    # round trips before /start could respond at all.
    results = await asyncio.gather(*[_check(entry) for entry in entries])
    return [entry for entry in results if entry is not None]


async def force_sub_join_buttons(client: Client, entries):
    """Build one 'Join <channel>' button per row for the given force-sub entries.

    Normal Mode -> regular invite/username link (instant join).
    Join Request Mode -> a link created with creates_join_request=True, so
    tapping it sends a join request instead of joining instantly. The bot
    does NOT auto-approve these - having a pending request is enough to
    pass the check (checked live, see _has_pending_request above)."""

    async def _build(entry):
        ch = force_sub_channel_id(entry)
        mode = force_sub_channel_mode(entry)
        try:
            chat = await client.get_chat(ch)
        except Exception:
            return None

        link = None
        if mode == "request":
            link = force_sub_channel_link(entry)
            if not link:
                # Legacy entry added before join-request links existed -> self-heal
                try:
                    invite = await client.create_chat_invite_link(ch, creates_join_request=True, name="Force Sub (Join Request)")
                    link = invite.invite_link
                    await set_force_sub_link(ch, link)
                except Exception as e:
                    logger.warning(f"Couldn't create join-request link for {ch}: {e}")
                    link = None
        else:
            link = chat.invite_link or (f"https://t.me/{chat.username}" if chat.username else None)

        if link:
            return [InlineKeyboardButton(f"🔔 Join {chat.title}", url=link)]
        return None

    # Build every button concurrently instead of one-by-one - this used to
    # be another string of sequential Telegram API calls slowing /start down.
    rows = await asyncio.gather(*[_build(entry) for entry in entries])
    return [row for row in rows if row is not None]


@Client.on_chat_join_request()
async def greet_join_request(client: Client, update):
    """Just a friendly nudge - the actual pass/fail check is done live
    against Telegram (see _has_pending_request), so nothing needs to be
    recorded here anymore."""
    try:
        await client.send_message(
            update.from_user.id,
            "✅ <b>Request received!</b> Tap /start again to continue.",
        )
    except Exception:
        pass
