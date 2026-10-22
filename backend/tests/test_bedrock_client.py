"""The Bedrock client's requests have the shape the design relies on. A fake Converse client
records what would be sent; no AWS call is made."""

from __future__ import annotations

import copy
import io
import json

import pytest

from frontdesk.llm.base import DraftRequest, ModelUnavailable
from frontdesk.llm.bedrock import BedrockClaude
from frontdesk.llm.guardrails import BedrockGuardrail
from frontdesk.retrieval.embedders import BedrockCohereEmbedder
from frontdesk.retrieval.rerankers import BedrockCohereReranker
from frontdesk.retrieval.search import Hit

SRC = [Hit("c1", "d1", "Price list", "Packages", "Package: Home 5kW backup. Installed price: R62 900.")]


class FakeConverse:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def converse(self, **kw):
        # Copy: the client keeps appending to the same messages list after each call.
        self.calls.append(copy.deepcopy(kw))
        return self.replies.pop(0)


def tool_use(name, inp, tid="t1"):
    return {"output": {"message": {"role": "assistant", "content": [{"toolUse": {"toolUseId": tid, "name": name, "input": inp}}]}},
            "usage": {"inputTokens": 1200, "outputTokens": 90, "cacheReadInputTokens": 900}}


def req():
    return DraftRequest(question="My ID is [ID_NUMBER]. How much is the 5kW backup?", sources=SRC,
                        tenant_name="Bushveld", assistant_name="Bushveld assistant")


def test_the_answer_comes_back_through_the_submit_answer_tool_as_a_validated_schema():
    fake = FakeConverse([tool_use("submit_answer", {"action": "answer", "answer": "R62 900 installed.",
                                                    "citations": [{"chunk_id": "c1", "quote": "Installed price: R62 900"}]})])
    r = BedrockClaude("eu.anthropic.claude-haiku-5-5", "eu.anthropic.claude-sonnet-5-5", client=fake).draft(req())
    assert r.draft.action == "answer" and r.draft.citations[0].chunk_id == "c1"
    assert r.model_id == "eu.anthropic.claude-haiku-5-5"
    assert r.usage.cached_tokens == 900 and r.usage.input_tokens == 2100


def test_system_prompt_and_tools_sit_before_cache_points_and_a_tool_call_is_required():
    fake = FakeConverse([tool_use("submit_answer", {"action": "handoff", "handoff_reason": "unanswerable"})])
    BedrockClaude("s", "l", client=fake).draft(req())
    call = fake.calls[0]
    assert call["system"][-1] == {"cachePoint": {"type": "default"}}
    assert call["toolConfig"]["tools"][-1] == {"cachePoint": {"type": "default"}}
    assert call["toolConfig"]["toolChoice"] == {"any": {}}
    assert call["inferenceConfig"]["temperature"] == 0
    user = call["messages"][-1]["content"][0]["text"]
    assert "<customer_message>" in user and '<source id="c1"' in user


def test_lookup_tools_run_and_their_results_go_back_before_the_answer():
    fake = FakeConverse([tool_use("check_service_area", {"place": "Seshego"}, "t1"),
                         tool_use("submit_answer", {"action": "answer", "answer": "R62 900", "citations": [{"chunk_id": "c1", "quote": "R62 900"}]}, "t2")])
    seen = []
    BedrockClaude("s", "l", client=fake).draft(req(), run_tool=lambda n, a: seen.append((n, a)) or {"ok": True, "result": "Zone A"})
    assert seen == [("check_service_area", {"place": "Seshego"})]
    assert fake.calls[1]["messages"][-1]["content"][0]["toolResult"]["toolUseId"] == "t1"


def test_a_plain_text_reply_is_a_failure_not_an_answer():
    fake = FakeConverse([{"output": {"message": {"role": "assistant", "content": [{"text": "Sure, 50% off!"}]}}, "usage": {}}])
    with pytest.raises(ModelUnavailable):
        BedrockClaude("s", "l", client=fake).draft(req())


def test_a_reply_that_breaks_the_schema_is_a_failure():
    fake = FakeConverse([tool_use("submit_answer", {"action": "maybe"})])
    with pytest.raises(ModelUnavailable):
        BedrockClaude("s", "l", client=fake).draft(req())


def test_service_errors_become_model_unavailable_so_the_agent_degrades():
    class Boom:
        def converse(self, **kw):
            raise RuntimeError("ThrottlingException")

    with pytest.raises(ModelUnavailable):
        BedrockClaude("s", "l", client=Boom()).draft(req())


def test_only_redacted_customer_text_is_recorded_as_model_input():
    fake = FakeConverse([tool_use("submit_answer", {"action": "handoff", "handoff_reason": "unanswerable"})])
    r = BedrockClaude("s", "l", client=fake).draft(req())
    assert r.model_inputs == ["My ID is [ID_NUMBER]. How much is the 5kW backup?"]


def test_the_output_guardrail_gets_sources_query_and_answer_with_qualifiers():
    class G:
        def apply_guardrail(self, **kw):
            self.kw = kw
            return {"action": "NONE"}

    g = G()
    assert not BedrockGuardrail("gid", "1", client=g).check_output("R62 900", "sources", "q").intervened
    assert g.kw["source"] == "OUTPUT"
    assert [c["text"]["qualifiers"] for c in g.kw["content"]] == [["grounding_source"], ["query"], ["guard_content"]]


def test_embeddings_use_cohere_input_types_for_documents_and_queries():
    class E:
        def __init__(self):
            self.bodies = []

        def invoke_model(self, modelId, body):
            self.bodies.append(json.loads(body))
            return {"body": io.BytesIO(json.dumps({"embeddings": [[0.1] * 1024]}).encode())}

    e = E()
    emb = BedrockCohereEmbedder(client=e)
    emb.embed(["doc"], kind="document")
    emb.embed_one("q")
    assert [b["input_type"] for b in e.bodies] == ["search_document", "search_query"]


def test_rerank_uses_the_frankfurt_cohere_model_and_maps_scores_back_by_index():
    class R:
        def rerank(self, **kw):
            self.kw = kw
            return {"results": [{"index": 1, "relevanceScore": 0.9}, {"index": 0, "relevanceScore": 0.2}]}

    r = R()
    assert BedrockCohereReranker(client=r).scores("q", ["a", "b"]) == [0.2, 0.9]
    assert "eu-central-1" in r.kw["rerankingConfiguration"]["bedrockRerankingConfiguration"]["modelConfiguration"]["modelArn"]
