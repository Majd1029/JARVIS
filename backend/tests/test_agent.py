from dataclasses import replace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import FakeClient, response, text_block, tool_use


@pytest.fixture
def make(settings, engine):
    def _make(*responses):
        fake = FakeClient(responses)
        return TestClient(create_app(settings, client=fake, engine=engine)), fake
    return _make


def test_plain_chat(make):
    api, fake = make(response("end_turn", text_block("Tokyo.")))
    body = api.post("/chat", json={"message": "Capital of Japan?"}).json()
    assert body["status"] == "done"
    assert body["reply"] == "Tokyo."
    request = fake.requests[0]
    assert request["model"] == "claude-opus-5-5"
    assert request["fallbacks"] == "default"
    assert {t["name"] for t in request["tools"]} >= {"calculator", "read_file", "web_search"}


def test_auto_approved_tool_runs_without_asking(make):
    api, fake = make(
        response("tool_use", tool_use("t1", "calculator", {"expression": "6*7"})),
        response("end_turn", text_block("42")),
    )
    body = api.post("/chat", json={"message": "6 times 7?"}).json()
    assert body == {"conversation_id": body["conversation_id"], "status": "done", "reply": "42", "pending": []}
    tool_results = fake.requests[1]["messages"][-1]["content"]
    assert tool_results == [{"type": "tool_result", "tool_use_id": "t1", "content": "42"}]


def test_write_requires_approval_then_runs(make, settings):
    target = settings.allowed_roots[0] / "hello.txt"
    api, fake = make(
        response("tool_use", text_block("Writing it now."),
                 tool_use("t1", "get_current_datetime", {}),
                 tool_use("t2", "write_file", {"path": str(target), "content": "hi"})),
        response("end_turn", text_block("Done.")),
    )
    body = api.post("/chat", json={"message": "Write hi to hello.txt"}).json()
    assert body["status"] == "waiting_for_user"
    assert [p["tool"] for p in body["pending"]] == ["write_file"]
    assert not target.exists()

    cid = body["conversation_id"]
    # A new message is refused while approvals are outstanding.
    assert api.post("/chat", json={"message": "hey", "conversation_id": cid}).status_code == 409

    body = api.post(f"/chat/{cid}/confirm", json={"decisions": {"t2": True}}).json()
    assert body["status"] == "done"
    assert target.read_text() == "hi"
    # Both results (the auto-run clock call and the approved write) go back together, in order.
    results = fake.requests[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]

    events = [e["event"] for e in api.get(f"/conversations/{cid}/trace").json()]
    assert "user_decision" in events and events[-1] == "final_response"


def test_denied_tool_is_reported_to_model(make, settings):
    target = settings.allowed_roots[0] / "nope.txt"
    api, fake = make(
        response("tool_use", tool_use("t1", "write_file", {"path": str(target), "content": "x"})),
        response("end_turn", text_block("Okay, I won't.")),
    )
    cid = api.post("/chat", json={"message": "write"}).json()["conversation_id"]
    body = api.post(f"/chat/{cid}/confirm", json={"decisions": {"t1": False}}).json()
    assert body["reply"] == "Okay, I won't."
    assert not target.exists()
    result = fake.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True


def test_missing_decision_rejected(make):
    api, _ = make(
        response("tool_use", tool_use("t1", "write_file", {"path": "a.txt", "content": "x"})),
    )
    cid = api.post("/chat", json={"message": "write"}).json()["conversation_id"]
    assert api.post(f"/chat/{cid}/confirm", json={"decisions": {}}).status_code == 422


def test_step_limit(make, settings):
    loop = [response("tool_use", tool_use(f"t{i}", "calculator", {"expression": "1+1"}))
            for i in range(settings.max_agent_steps)]
    api, _ = make(*loop)
    assert api.post("/chat", json={"message": "loop"}).json()["status"] == "step_limit"


def test_ollama_provider_sends_plain_request(settings, engine):
    local = replace(settings, provider="ollama", model="jarvis-qwen3")
    fake = FakeClient([
        response("tool_use", tool_use("t1", "web_search", {"query": "x"})),
        response("end_turn", text_block("Found it.")),
    ])
    api = TestClient(create_app(local, client=fake, engine=engine))

    with patch("app.tools.web.DDGS") as ddgs:
        ddgs.return_value.text.return_value = [{"title": "T", "href": "https://x.test", "body": "B"}]
        body = api.post("/chat", json={"message": "search x"}).json()

    assert body["reply"] == "Found it."
    request = fake.requests[0]
    assert request["model"] == "jarvis-qwen3"
    # Claude-only options are not sent to the local model.
    assert not {"fallbacks", "betas", "thinking", "output_config", "cache_control"} & request.keys()
    # Web search runs locally (a normal tool), not as an Anthropic server tool.
    search = next(t for t in request["tools"] if t["name"] == "web_search")
    assert "input_schema" in search and "type" not in search
    assert "https://x.test" in fake.requests[1]["messages"][-1]["content"][0]["content"]
