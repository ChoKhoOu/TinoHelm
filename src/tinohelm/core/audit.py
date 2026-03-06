"""Simple audit logger for TinoHelm."""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from tinohelm.db.models import AuditLog

logger = logging.getLogger(__name__)


async def log_audit(db: AsyncSession, action: str, details: dict) -> None:
    """Insert an audit log entry into the database.

    Note: This only flushes the SQL; the caller's session must commit for the
    entry to persist.
    """
    entry = AuditLog(action=action, details_json=details)
    db.add(entry)
    await db.flush()
    logger.info("Audit: %s — %s", action, details)
