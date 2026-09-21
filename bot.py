import asyncio
import logging
import logging.config
from datetime import date, datetime

import pytz
from aiohttp import web
from pyrogram import idle

# Get logging configurations
logging.config.fileConfig('logging.conf')
logging.getLogger().setLevel(logging.INFO)
logging.getLogger("pyrogram").setLevel(logging.ERROR)

from config import LOG_CHANNEL, ON_HEROKU, PORT
from Script import script
from TechVJ.bot import StreamBot
from TechVJ.server import web_server
from TechVJ.utils.keepalive import ping_server
from TechVJ.utils.watchdog import run_watchdog, mark_ok


StreamBot.start()
loop = asyncio.get_event_loop()


async def start():
    print('\n')
    print('Initalizing Tech VJ Bot')
    me = await StreamBot.get_me()
    StreamBot.username = me.username
    mark_ok()
    asyncio.create_task(run_watchdog(StreamBot))
    # NOTE: plugins are already loaded by Pyrogram itself (plugins={"root": "plugins"}
    # in TechVJ/bot/__init__.py) when StreamBot.start() runs above. The old manual
    # importlib loop re-executed every plugin a second time (duplicate Mongo clients).
    if ON_HEROKU:
        asyncio.create_task(ping_server())
    tz = pytz.timezone('Asia/Kolkata')
    today = date.today()
    now = datetime.now(tz)
    time = now.strftime("%H:%M:%S %p")
    app = web.AppRunner(await web_server())
    await StreamBot.send_message(chat_id=LOG_CHANNEL, text=script.RESTART_TXT.format(today, time))
    await app.setup()
    bind_address = "0.0.0.0"
    await web.TCPSite(app, bind_address, PORT).start()
    print("Bot Started Successfully!")
    await idle()


if __name__ == '__main__':
    try:
        loop.run_until_complete(start())
    except KeyboardInterrupt:
        logging.info('Service Stopped Bye 👋')
