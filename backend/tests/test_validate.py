"""An answer is sent only if every number in it is in a cited source and it commits nothing."""

from __future__ import annotations

from frontdesk.llm.schemas import AnswerDraft, Citation
from frontdesk.llm.validate import validate
from frontdesk.retrieval.search import Hit

SRC = [Hit("c1", "d1", "Price list", "Installed solar and backup packages",
           "Package: Home 5kW backup. Installed price: R62 900.")]


def draft(answer, cites=(("c1", "Installed price: R62 900"),), **kw):
    return AnswerDraft(action="answer", answer=answer, citations=[Citation(chunk_id=c, quote=q) for c, q in cites], **kw)


def test_a_grounded_answer_passes():
    assert validate(draft("The Home 5kW backup costs R62 900 installed."), SRC).ok


def test_a_price_that_is_not_in_the_cited_source_is_rejected():
    v = validate(draft("The Home 5kW backup costs R59 900 installed."), SRC)
    assert not v.ok and any(p.startswith("ungrounded_fact") for p in v.problems)


def test_no_source_no_answer():
    v = validate(draft("It costs R62 900.", cites=()), SRC)
    assert "no_source_no_answer" in v.problems


def test_a_citation_to_a_chunk_that_was_not_retrieved_is_rejected():
    v = validate(draft("It costs R62 900.", cites=(("c-other", "R62 900"),)), SRC)
    assert any(p.startswith("citation_not_in_sources") for p in v.problems)


def test_a_quote_that_is_not_in_the_source_is_rejected():
    v = validate(draft("It costs R62 900.", cites=(("c1", "free installation this week"),)), SRC)
    assert any(p.startswith("quote_not_in_source") for p in v.problems)


def test_commitment_language_is_rejected():
    for text in ("We will give you a discount on the R62 900 price.", "You are booked for Tuesday.", "I approve your refund."):
        assert not validate(draft(text), SRC).ok, text


def test_links_are_never_sent():
    assert "link_not_allowed" in validate(draft("Pay at https://pay.example R62 900"), SRC).problems


def test_guessing_a_title_is_rejected():
    assert "title_or_pronoun_guess" in validate(draft("Thank you Mrs Lerato, it is R62 900."), SRC).problems


def test_the_canary_is_never_sent():
    assert "canary_leak" in validate(draft("my reference is fd-canary-X R62 900"), SRC, canary="fd-canary-X").problems


def test_a_handoff_draft_is_valid_without_citations():
    assert validate(AnswerDraft(action="handoff", handoff_reason="unanswerable"), SRC).ok
