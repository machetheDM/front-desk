"""Split a migration file into single statements.

The RDS Data API runs one statement per call, so migrations are split here rather than sent
whole. The splitter understands quotes, dollar-quoted bodies (`DO $$ ... $$`) and comments; a
naive split on ';' would cut a PL/pgSQL function in half and fail only in AWS.
"""

from __future__ import annotations

import re

_DOLLAR = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")


def split_sql(text: str) -> list[str]:
    stmts: list[str] = []
    buf: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "-" and text.startswith("--", i):
            j = text.find("\n", i)
            j = n if j == -1 else j
            i = j
            continue
        if c == "/" and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if c == "'":
            j = i + 1
            while j < n:
                if text[j] == "'" and j + 1 < n and text[j + 1] == "'":
                    j += 2
                    continue
                if text[j] == "'":
                    break
                j += 1
            buf.append(text[i : j + 1])
            i = j + 1
            continue
        if c == "$":
            m = _DOLLAR.match(text, i)
            if m:
                tag = m.group(0)
                end = text.find(tag, m.end())
                end = n if end == -1 else end + len(tag)
                buf.append(text[i:end])
                i = end
                continue
        if c == ";":
            stmt = "".join(buf).strip()
            if stmt:
                stmts.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        stmts.append(tail)
    return stmts
