"""psycopg implementation, used by tests, the replay CLI and local development."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .base import PARAM_RE, Database, Transaction


def to_pyformat(sql: str) -> str:
    # psycopg treats % as a placeholder marker, so literal % must be doubled first.
    return PARAM_RE.sub(lambda m: f"%({m.group(1)})s", sql.replace("%", "%%"))


def _adapt(params: Mapping[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in (params or {}).items():
        if isinstance(v, (dict, list)) and not k.endswith("_array"):
            out[k] = json.dumps(v)
        else:
            out[k] = v
    return out


class _LocalTx(Transaction):
    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        from psycopg.rows import dict_row

        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(to_pyformat(sql), _adapt(params))
            if cur.description is None:
                return []
            return [dict(r) for r in cur.fetchall()]


class LocalDatabase(Database):
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def _begin(self) -> Transaction:
        import psycopg

        conn = psycopg.connect(self.dsn, autocommit=False)
        return _LocalTx(conn)

    def _commit(self, tx: Transaction) -> None:
        assert isinstance(tx, _LocalTx)
        try:
            tx.conn.commit()
        finally:
            tx.conn.close()

    def _rollback(self, tx: Transaction) -> None:
        assert isinstance(tx, _LocalTx)
        try:
            tx.conn.rollback()
        finally:
            tx.conn.close()
