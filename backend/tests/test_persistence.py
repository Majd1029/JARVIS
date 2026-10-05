"""Conversations survive a server restart (a fresh app on the same database)."""

from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import FakeClient, FakeEmbedder, response, text_block, thinking_block, tool_use


def boot(settings, engine, *responses):
    fake = FakeClient(responses)
    api = TestClient(create_app(settings, client=fake, engine=engine, embedder=FakeEmbedder()))
    return api, fake


def test_history_survives_restart_and_replays_unchanged(settings, engine):
    api, first = boot(settings, engine,
                      response("tool_use", thinking_block("sig-1"), tool_use("t1", "calculator", {"expression": "1+1"})),
                      response("end_turn", text_block("Hi Majd.")))
    cid = api.post("/chat", json={"message": "My name is Majd"}).json()["conversation_id"]
    history_before = first.requests[-1]["messages"] + [
        {"role": "assistant", "content": [{"text": "Hi Majd.", "type": "text"}]}
    ]

    api, second = boot(settings, engine, response("end_turn", text_block("Majd.")))
    api.post("/chat", json={"message": "What's my name?", "conversation_id": cid})
    replayed = second.requests[0]["messages"]

    # Everything sent before the restart is sent again exactly, followed by the new message.
    assert replayed[:-1] == history_before
    assert replayed[-1] == {"role": "user", "content": "What's my name?"}
    # Thinking blocks keep their signature.
    assert replayed[1]["content"][0] == {"signature": "sig-1", "thinking": "", "type": "thinking"}


def test_stored_json_keeps_key_order(settings, engine):
    api, _ = boot(settings, engine,
                  response("tool_use", tool_use("t1", "get_current_datetime", {"zeta": 1, "alpha": 2})),
                  response("end_turn", text_block("ok")))
    cid = api.post("/chat", json={"message": "hi"}).json()["conversation_id"]
    stored = api.app.state.store.get(cid).messages[1]["content"][0]
    assert list(stored) == ["id", "input", "name", "type"]
    assert list(stored["input"]) == ["zeta", "alpha"]


def test_pending_approval_survives_restart(settings, engine):
    target = settings.allowed_roots[0] / "later.txt"
    api, _ = boot(settings, engine,
                  response("tool_use", tool_use("t1", "write_file", {"path": str(target), "content": "x"})))
    cid = api.post("/chat", json={"message": "write later.txt"}).json()["conversation_id"]

    api, _ = boot(settings, engine, response("end_turn", text_block("Written.")))
    convo = api.get(f"/conversations/{cid}").json()
    assert [p["tool"] for p in convo["pending"]] == ["write_file"]
    assert api.get("/conversations").json()[0]["waiting_for_user"] is True

    body = api.post(f"/chat/{cid}/confirm", json={"decisions": {"t1": True}}).json()
    assert body["reply"] == "Written."
    assert target.read_text() == "x"
    assert api.get(f"/conversations/{cid}").json()["pending"] == []


def test_interrupted_tool_calls_are_closed_on_next_message(settings, engine):
    api, _ = boot(settings, engine)
    store = api.app.state.store
    conversation = store.create()
    store.append_message(conversation, {"role": "user", "content": "do it"})
    # Simulate a crash right after Claude asked for a tool, before the result was saved.
    store.append_message(conversation, {"role": "assistant", "content": [
        {"id": "t9", "input": {"expression": "1"}, "name": "calculator", "type": "tool_use"}
    ]})

    api, fake = boot(settings, engine, response("end_turn", text_block("Sorry about that.")))
    api.post("/chat", json={"message": "hello?", "conversation_id": conversation.id})
    sent = fake.requests[0]["messages"]
    assert sent[2]["content"][0]["tool_use_id"] == "t9"
    assert sent[2]["content"][0]["is_error"] is True
    assert sent[3] == {"role": "user", "content": "hello?"}


def test_conversation_list_and_view(settings, engine):
    api, _ = boot(settings, engine, response("end_turn", text_block("Tokyo.")))
    cid = api.post("/chat", json={"message": "Capital of Japan?"}).json()["conversation_id"]

    [summary] = api.get("/conversations").json()
    assert summary["id"] == cid
    assert summary["title"] == "Capital of Japan?"
    assert summary["message_count"] == 2

    convo = api.get(f"/conversations/{cid}").json()
    assert convo["messages"] == [
        {"role": "user", "text": "Capital of Japan?"},
        {"role": "assistant", "text": "Tokyo."},
    ]
    assert api.get("/conversations/nope").status_code == 404
