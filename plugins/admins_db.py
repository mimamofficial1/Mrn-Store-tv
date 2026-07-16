# Dynamic Admins system — lets the owner add/remove admins and control
# what each admin is allowed to do, from chat (via /settings -> Admins).
# The owner(s) listed in config.ADMINS always have full access and
# cannot be removed/limited from here.

import motor.motor_asyncio
from pyrogram import filters
from config import DB_URI, DB_NAME, ADMINS as OWNER_ADMINS

_client = motor.motor_asyncio.AsyncIOMotorClient(DB_URI)
_col = _client[DB_NAME].bot_admins

PERMISSIONS = {
    "can_broadcast": "📢 Can Broadcast",
    "can_settings": "⚙️ Can Use Settings",
    "can_manage_admins": "👥 Can Manage Admins",
}


async def add_admin(user_id, name=None):
    doc = {
        "_id": int(user_id),
        "name": name,
        "can_broadcast": True,
        "can_settings": True,
        "can_manage_admins": False,
    }
    await _col.update_one({"_id": int(user_id)}, {"$setOnInsert": doc}, upsert=True)


async def remove_admin(user_id):
    await _col.delete_one({"_id": int(user_id)})


async def get_admin(user_id):
    return await _col.find_one({"_id": int(user_id)})


async def get_all_admins():
    return await _col.find({}).to_list(length=None)


async def set_permission(user_id, perm, value):
    await _col.update_one({"_id": int(user_id)}, {"$set": {perm: value}})


async def is_admin(user_id):
    if int(user_id) in OWNER_ADMINS:
        return True
    return await get_admin(user_id) is not None


async def has_permission(user_id, perm):
    if int(user_id) in OWNER_ADMINS:
        return True
    doc = await get_admin(user_id)
    if not doc:
        return False
    return bool(doc.get(perm, False))


def dynamic_admin_filter(permission=None):
    """Pyrogram filter usable on both Message and CallbackQuery updates.
    Owners (config.ADMINS) always pass. DB-added admins pass only if they
    have the given `permission` (or any admin at all if permission=None)."""

    async def func(flt, client, update):
        user = getattr(update, "from_user", None)
        if not user:
            return False
        if user.id in OWNER_ADMINS:
            return True
        if flt.permission:
            return await has_permission(user.id, flt.permission)
        return await is_admin(user.id)

    return filters.create(func, permission=permission)
