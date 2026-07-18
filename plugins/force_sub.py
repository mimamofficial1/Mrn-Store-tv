# Force Subscribe helper functions

import asyncio
import logging
from pyrogram import Client, enums
from pyrogram.errors import UserNotParticipant
from pyrogram.types import InlineKeyboardButton
from plugins.settings_db import (
    get_settings, force_sub_channel_id, force_sub_channel_mode,
    force_sub_channel_link, set_force_sub_link,
    record_join_request, has_join_request, clear_join_request,
)

logger = logging.getLogger(__name__)


async def _live_pending_check(client: Client, chat_id, user_id) -> bool:
    """Fallback only - ask Telegram directly whether this user currently has
    a pending join request. Used when our own DB record is missing (e.g.
    the bot was offline/restarting exactly when the request came in)."""
    try:
        async for _ in client.get_chat_join_requests(chat_id, user_id=user_id, limit=1):
            return True
        return False
    except Exception:
        return False


async def not_joined_channels(client: Client, user_id: int, settings=None):
    """Return the list of force-sub channel entries this user has NOT joined.
    Returns [] if force_sub is disabled or the user has joined everything.

    For 'request' mode channels, having sent a join request is enough on
    its own - we don't wait for the admin to manually approve it. This is
    primarily tracked via our own DB record (set the instant Telegram fires
    the join-request event - very reliable), falling back to a live
    Telegram lookup only if that record is missing."""
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
            if mode == "request":
                if await has_join_request(user_id, ch):
                    return None
                if await _live_pending_check(client, ch, user_id):
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
    does NOT auto-approve these - just sending the request is enough to
    pass the check (see record_join_request below)."""

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
async def track_join_request(client: Client, update):
    """For channels added in 'Join Request Mode', record that the user sent
    a request - no manual/auto approval needed, sending it is enough to
    pass the force-sub check. Then ping the user so the old 'Please Join'
    prompt doesn't need a manual Try Again tap."""
    settings = await get_settings()
    entries = settings.get("force_sub_channels") or []
    matched = any(
        isinstance(entry, dict) and entry.get("mode") == "request" and force_sub_channel_id(entry) == update.chat.id
        for entry in entries
    )
    if not matched:
        return

    await record_join_request(update.from_user.id, update.chat.id)

    try:
        await client.send_message(
            update.from_user.id,
            "✅ <b>Request received!</b> Tap /start again to continue.",
        )
    except Exception:
        pass


@Client.on_chat_member_updated()
async def handle_member_left(client: Client, update):
    """Best-effort: if someone leaves/is removed from a 'Join Request Mode'
    channel after having passed the force-sub check, wipe their recorded
    request so they need to send a fresh one to use the bot again."""
    new = update.new_chat_member
    if not new or new.status not in (enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED):
        return
    user = new.user or (update.old_chat_member.user if update.old_chat_member else None)
    if not user:
        return

    settings = await get_settings()
    entries = settings.get("force_sub_channels") or []
    matched = any(
        isinstance(entry, dict) and entry.get("mode") == "request" and force_sub_channel_id(entry) == update.chat.id
        for entry in entries
    )
    if matched:
        await clear_join_request(user.id, update.chat.id)
