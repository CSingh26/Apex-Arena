# SPDX-License-Identifier: AGPL-3.0-only
import logging

from app.core.settings import Settings


def configure_logging(settings: Settings) -> None:
    """Configure application logging without serializing settings or secrets."""

    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("app").info(
        "Starting %s with %s", settings.app_name, settings.safe_runtime_metadata
    )
