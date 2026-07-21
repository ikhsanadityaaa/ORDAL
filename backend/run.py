import sys
import os
import asyncio

# Windows: paksa ProactorEventLoop sebelum uvicorn load apapun
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
    # Production: reload=False supaya scheduler & session tidak terputus
    # Development: set ORDAL_DEV=1 untuk enable reload
    reload = os.getenv("ORDAL_DEV", "0") == "1"
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=reload,
        loop="asyncio",
    )
