# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import os
import sys

from app.core.settings import get_settings


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "migrate":
        os.execvp("alembic", ["alembic", "upgrade", "head"])

    settings = get_settings()
    target = (
        "app.ingestor:create_ingestor_app"
        if settings.app_process_role == "ingestor"
        else "app.main:app"
    )
    command = [
        "uvicorn",
        target,
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        "--proxy-headers",
        "--forwarded-allow-ips",
        os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1"),
    ]
    if settings.app_process_role == "ingestor":
        command.append("--factory")
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
