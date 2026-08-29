"""Execução do daemon com uvicorn."""

from __future__ import annotations

import uvicorn

from eve.config import Settings, load_settings
from eve.logging import configure_logging
from eve.paths import paths


def run(settings: Settings | None = None) -> None:
    settings = settings or load_settings()
    p = paths().ensure()
    configure_logging(
        level=settings.log.level,
        json_format=settings.log.json_format,
        log_file=p.log_file,
        force=True,
    )
    uvicorn.run(
        "eve.daemon.app:create_app",
        factory=True,
        host=settings.server.host,
        port=settings.server.port,
        log_config=None,
        access_log=False,
    )
