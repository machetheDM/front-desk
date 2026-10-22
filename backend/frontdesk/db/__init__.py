"""Database layer. `connect()` picks the implementation from the environment."""

from __future__ import annotations

import os

from .base import Database, Transaction, vector_literal

__all__ = ["Database", "Transaction", "connect", "vector_literal"]


def connect() -> Database:
    """FRONTDESK_DSN for local Postgres; otherwise the Data API variables set by CDK."""
    dsn = os.environ.get("FRONTDESK_DSN")
    if dsn:
        from .local import LocalDatabase

        return LocalDatabase(dsn)
    from .dataapi import DataApiDatabase

    return DataApiDatabase(
        cluster_arn=os.environ["FRONTDESK_DB_CLUSTER_ARN"],
        secret_arn=os.environ["FRONTDESK_DB_SECRET_ARN"],
        database=os.environ.get("FRONTDESK_DB_NAME", "frontdesk"),
    )
