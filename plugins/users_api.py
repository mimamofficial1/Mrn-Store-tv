import aiohttp
import motor.motor_asyncio
from config import DB_URI

# Async Mongo client (was blocking pymongo before, which froze the whole bot
# for every user while any single DB call was in flight)
_mongo_client = motor.motor_asyncio.AsyncIOMotorClient(DB_URI)
_mongo_db = _mongo_client["cloned_vjbotz"]


async def get_short_link(user, link):
    api_key = user["shortener_api"]
    base_site = user["base_site"]
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(f"https://{base_site}/api", params={"api": api_key, "url": link}) as response:
                data = await response.json(content_type=None)
        if data.get("status") == "success" or response.status == 200:
            return data.get("shortenedUrl") or link
    except Exception:
        pass
    return link


async def get_user(user_id):
    user_id = int(user_id)
    user = await _mongo_db.user.find_one({"user_id": user_id})
    if not user:
        res = {
            "user_id": user_id,
            "shortener_api": None,
            "base_site": None,
        }
        await _mongo_db.user.insert_one(res)
        user = res
    return user


async def update_user_info(user_id, value: dict):
    user_id = int(user_id)
    await _mongo_db.user.update_one({"user_id": user_id}, {"$set": value}, upsert=True)
