"""Chunks follow the document: one per FAQ answer, one per table row with its column names."""

from frontdesk.retrieval.chunking import chunk_markdown

DOC = """---
title: Price list
---
# Price list

DEMO DATA: this line is for humans.

## Packages

| Package | Price |
|---|---|
| Home 5kW backup | R62 900 |
| Family 8kW | R148 000 |

## How long does it take?

One day.
"""


def test_each_table_row_becomes_a_chunk_that_carries_its_column_names():
    d = chunk_markdown(DOC)
    rows = [c for c in d.chunks if c.heading == "Packages"]
    assert [c.text for c in rows] == ["Package: Home 5kW backup. Price: R62 900.", "Package: Family 8kW. Price: R148 000."]


def test_front_matter_sets_the_title_and_demo_markers_are_not_knowledge():
    d = chunk_markdown(DOC)
    assert d.title == "Price list"
    assert all("DEMO DATA" not in c.text for c in d.chunks)


def test_a_faq_answer_is_one_chunk_under_its_question():
    d = chunk_markdown(DOC)
    assert [(c.heading, c.text) for c in d.chunks if c.heading.startswith("How long")] == [("How long does it take?", "One day.")]
