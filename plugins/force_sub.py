# Force Subscribe helper functions

import logging
from pyrogram import Client
from pyrogram.errors import UserNotParticipant
from pyrogram.types import InlineKeyboardButton
from plugins.settings_db import (
    get_settings, force_sub_channel_id, force_sub_channel_mode,
    force_sub_channel_link, set_force_sub_link,
    record_join_request, has_join_request,
)

logger = logging.getLogger(__name__)


async def not_joined_channels(client: Client, user_id: int):
    """Return the list of force-sub channel entries this user has NOT joined.
    Returns [] if force_sub is disabled or the user has joined everything.

    For 'request' mode channels, sending a join request is enough on its
    own - we don't wait for the admin to manually approve it in Telegram."""
    settings = await get_settings()
    if not settings.get("force_sub"):
        return []
    entries = settings.get("force_sub_channels") or []
    missing = []
    for entry in entries:
        ch = force_sub_channel_id(entry)
        mode = force_sub_channel_mode(entry)
        try:
            member = await client.get_chat_member(ch, user_id)
            if member.status in ("kicked", "left"):
                missing.append(entry)
        except UserNotParticipant:
            if mode == "request" and await has_join_request(user_id, ch):
                # They tapped Join and a request is sitting there - that's
                # enough, don't make them wait for manual approval.
                continue
            missing.append(entry)
        except Exception:
            # Bot not admin there / invalid channel -> don't lock everyone out
            continue
    return missing


async def force_sub_join_buttons(client: Client, entries):
    """Build one 'Join <channel>' button per row for the given force-sub entries.

    Normal Mode -> regular invite/username link (instant join).
    Join Request Mode -> a link created with creates_join_request=True, so
    tapping it sends a join request instead of joining instantly. The bot
    does NOT auto-approve these - just sending the request is enough to
    pass the check (see record_join_request below)."""
    buttons = []
    for entry in entries:
        ch = force_sub_channel_id(entry)
        mode = force_sub_channel_mode(entry)
        try:
            chat = await client.get_chat(ch)
        except Exception:
            continue

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
            buttons.append([InlineKeyboardButton(f"🔔 Join {chat.title}", url=link)])
    return buttons


@Client.on_chat_join_request()
async def track_join_request(client: Client, update):
    """For channels added in 'Join Request Mode', just record that the user
    sent a request - no manual/auto approval needed, sending it is enough
    to pass the force-sub check. Then ping the user so the old 'Please
    Join' prompt doesn't need a manual Try Again tap."""
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
            "✅ <b>Verified!</b> Your join request has been received.\n\nTap /start again to continue.",
        )
    except Exception:
        pass
