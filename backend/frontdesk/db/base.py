"""Database access with the tenant context set for every transaction.

Every query in Front Desk runs inside `db.transaction(tenant_id)`. The transaction sets
`app.tenant_id` transaction-locally before anything else, and Postgres row-level security does
the filtering. Application code never adds `WHERE tenant_id = ...` as its only protection: if a
developer forgets it, RLS still holds; if RLS were missing, the migration linter fails the build.

Two implementations share one interface so the replay, the tests and production run the same
SQL: `LocalDatabase` (psycopg, for tests and the laptop) and `DataApiDatabase` (RDS Data API,
in AWS). SQL uses `:name` parameters, which the Data API needs natively.
"""

from __future__ import annotations

import abc
import contextlib
import re
import uuid
from collections.abc import Iterator, Mapping
from typing import Any

# `:name` but not the `::type` cast and not inside `:=`.
PARAM_RE = re.compile(r"(?<![:\w]):([A-Za-z_][A-Za-z0-9_]*)\b")


class Transaction(abc.ABC):
    @abc.abstractmethod
    def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run one statement and return rows as dicts (empty list for no result set)."""

    def one(self, sql: str, params: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
        rows = self.execute(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: Mapping[str, Any] | None = None) -> Any:
        row = self.one(sql, params)
        return None if row is None else next(iter(row.values()))


class Database(abc.ABC):
    @abc.abstractmethod
    def _begin(self) -> Transaction: ...

    @abc.abstractmethod
    def _commit(self, tx: Transaction) -> None: ...

    @abc.abstractmethod
    def _rollback(self, tx: Transaction) -> None: ...

    @contextlib.contextmanager
    def transaction(self, tenant_id: uuid.UUID | str | None) -> Iterator[Transaction]:
        """Open a transaction scoped to one tenant.

        `tenant_id=None` is allowed and means "no tenant": every RLS policy then matches
        nothing. It exists for health checks, not for business queries.
        """
        tx = self._begin()
        try:
            tx.execute(
                "SELECT set_config('app.tenant_id', :tenant_id, true)",
                {"tenant_id": "" if tenant_id is None else str(uuid.UUID(str(tenant_id)))},
            )
            yield tx
        except BaseException:
            self._rollback(tx)
            raise
        else:
            self._commit(tx)


def vector_literal(vec: Any) -> str:
    """pgvector text form. Passed as a string and cast with ::vector in SQL, which works the
    same over psycopg and the Data API (the Data API has no vector type)."""
    return "[" + ",".join(f"{float(x):.6f}" for x in vec) + "]"
