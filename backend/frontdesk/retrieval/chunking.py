"""Turn an approved Markdown document into retrievable chunks.

Chunks follow the document's own structure: one per FAQ answer, one per price-list table row.
A table row carries its column names ("Package: Home 5kW backup. Installed price: R62 900.")
so a chunk is self-explanatory when it is quoted back as a citation, and the price is never
separated from the product it belongs to.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import yaml

MAX_WORDS = 120


@dataclass
class Chunk:
    ordinal: int
    heading: str
    text: str


@dataclass
class ParsedDoc:
    title: str
    meta: dict
    chunks: list[Chunk]
    sha256: str


def _split_front_matter(text: str) -> tuple[dict, str]:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return yaml.safe_load(text[4:end]) or {}, text[end + 5 :]
    return {}, text


def _table_rows(lines: list[str]) -> list[str]:
    rows = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in lines]
    rows = [r for r in rows if not all(re.fullmatch(r":?-{3,}:?", c) for c in r)]
    if len(rows) < 2:
        return []
    header, body = rows[0], rows[1:]
    return [". ".join(f"{h}: {v}" for h, v in zip(header, r) if v) + "." for r in body]


def chunk_markdown(text: str, default_title: str = "") -> ParsedDoc:
    meta, body = _split_front_matter(text)
    title = meta.get("title") or default_title
    chunks: list[Chunk] = []
    heading = title
    paragraphs: list[str] = []
    table: list[str] = []

    def flush_paragraphs() -> None:
        nonlocal paragraphs
        buf: list[str] = []
        for p in paragraphs:
            if buf and len(" ".join(buf + [p]).split()) > MAX_WORDS:
                chunks.append(Chunk(len(chunks), heading, " ".join(buf)))
                buf = []
            buf.append(p)
        if buf:
            chunks.append(Chunk(len(chunks), heading, " ".join(buf)))
        paragraphs = []

    def flush_table() -> None:
        nonlocal table
        for row in _table_rows(table):
            chunks.append(Chunk(len(chunks), heading, row))
        table = []

    para: list[str] = []
    for line in body.splitlines() + [""]:
        s = line.strip()
        if s.startswith("|"):
            if para:
                paragraphs.append(" ".join(para))
                para = []
            table.append(s)
            continue
        if table:
            flush_table()
        if m := re.match(r"^(#{1,3})\s+(.*)$", s):
            if para:
                paragraphs.append(" ".join(para))
                para = []
            flush_paragraphs()
            heading = m.group(2).strip()
            if m.group(1) == "#" and not meta.get("title"):
                title = heading
            continue
        if not s:
            if para:
                paragraphs.append(" ".join(para))
                para = []
            continue
        if s.startswith("DEMO DATA:"):
            continue  # a marker for human readers, not knowledge
        para.append(s)
    flush_paragraphs()
    return ParsedDoc(title, meta, chunks, hashlib.sha256(text.encode()).hexdigest())


def embedding_text(doc_title: str, chunk: Chunk) -> str:
    return f"{doc_title}. {chunk.heading}. {chunk.text}"
