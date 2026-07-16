from __future__ import annotations

import threading
import webbrowser

import uvicorn

from .config import settings


def _open_browser() -> None:
    webbrowser.open(f"http://{settings.app_host}:{settings.app_port}")


if __name__ == "__main__":
    if settings.open_browser:
        threading.Timer(1.3, _open_browser).start()
    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_reload,
        log_level="info",
    )
