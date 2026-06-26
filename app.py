import asyncio
import os
from aiohttp import web
import main as bot_main

async def health(request):
    return web.Response(text="OK")

async def run_http():
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

async def main():
    await asyncio.gather(run_http(), bot_main.main())

if __name__ == "__main__":
    asyncio.run(main())
