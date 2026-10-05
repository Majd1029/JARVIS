"""Long-term facts, document memory (RAG), and how memory reaches the model."""

import pytest
from fastapi.testclient import TestClient

from app.db.session import make_session_factory
from app.main import create_app
from app.memory.long_term import FactStore
from app.memory.retrieval import MEMORY_TAG
from app.memory.semantic import CHUNK_CHARS, CHUNK_OVERLAP, DocumentStore, chunk_text, extract_text
from app.tools.base import ToolError
from tests.conftest import FakeClient, response, text_block, tool_use


@pytest.fixture
def facts(engine, embedder):
    return FactStore(make_session_factory(engine), embedder)


@pytest.fixture
def documents(engine, embedder):
    return DocumentStore(make_session_factory(engine), embedder)


@pytest.fixture
def boot(settings, engine, embedder):
    def _boot(*responses):
        fake = FakeClient(responses)
        return TestClient(create_app(settings, client=fake, engine=engine, embedder=embedder)), fake
    return _boot


# ---- chunking -----------------------------------------------------------------------------

def test_chunk_text_packs_paragraphs_and_splits_long_ones():
    paragraphs = [f"Paragraph {i} " + "word " * 40 for i in range(20)]
    long_paragraph = " ".join(f"w{i}" for i in range(900))  # ~4500 chars, no blank lines
    chunks = chunk_text("\n\n".join(paragraphs) + "\n\n" + long_paragraph)
    assert len(chunks) > 3
    assert all(len(c) <= CHUNK_CHARS + CHUNK_OVERLAP + 2 for c in chunks)
    # Consecutive chunks overlap (so text at a boundary keeps its context), starting at a word boundary.
    for previous, current in zip(chunks, chunks[1:]):
        overlap = current.split("\n\n")[0]
        assert previous.endswith(overlap) and previous[-len(overlap) - 1].isspace()
    # Every word of the long paragraph survives the split.
    assert set(long_paragraph.split()) <= set(" ".join(chunks).split())


def test_extract_text_rejects_unsupported_files(tmp_path):
    image = tmp_path / "photo.jpg"
    image.write_bytes(b"\xff\xd8\xff")
    with pytest.raises(ToolError, match="Can't index"):
        extract_text(image)


# ---- facts --------------------------------------------------------------------------------

def test_facts_add_dedupe_search_delete(facts, embedder):
    fact, created = facts.add("Majd prefers Python for backend work")
    assert created
    again, created = facts.add("Majd prefers Python for backend work.")
    assert not created and again.id == fact.id
    facts.add("The weekly team meeting is on Friday")

    found = facts.search(embedder.embed_query("Which language does Majd prefer for backend work?"), 5, 0.3)
    assert [f.content for f in found] == ["Majd prefers Python for backend work"]

    assert facts.delete(fact.id)
    assert not facts.delete(fact.id)
    assert [f.content for f in facts.all()] == ["The weekly team meeting is on Friday"]


# ---- documents ----------------------------------------------------------------------------

def test_document_index_reindex_search_delete(documents, embedder, tmp_path):
    notes = tmp_path / "notes.md"
    notes.write_text("The JARVIS database runs on port 5433.\n\nThe chat model is Qwen3 4B Instruct.")
    document, status = documents.index(notes)
    assert status == "indexed" and document.chunk_count == 1
    assert documents.index(notes)[1] == "unchanged"

    notes.write_text("The JARVIS database runs on port 6543 now.")
    assert documents.index(notes)[1] == "updated"
    [chunk] = documents.search(embedder.embed_query("Which port does the JARVIS database use?"), 3, 0.3)
    assert "6543" in chunk.content and chunk.document_path == str(notes)

    assert documents.delete(document.id)
    assert documents.all() == []
    assert documents.search(embedder.embed_query("port"), 3, 0.0) == []


# ---- memory in conversations --------------------------------------------------------------

def test_relevant_memory_is_attached_to_the_message(boot, facts):
    facts.add("Majd prefers Python for backend work")
    facts.add("The weekly team meeting is on Friday")
    api, fake = boot(response("end_turn", text_block("Python.")))

    cid = api.post("/chat", json={"message": "Which language do I prefer for backend work?"}).json()["conversation_id"]

    [memory_block, user_text] = fake.requests[0]["messages"][0]["content"]
    assert memory_block["text"].startswith(MEMORY_TAG)
    assert "Majd prefers Python" in memory_block["text"]
    assert "meeting" not in memory_block["text"]  # unrelated facts stay out
    assert user_text == {"type": "text", "text": "Which language do I prefer for backend work?"}

    # The readable history and the title show what the user typed, not the memory block.
    convo = api.get(f"/conversations/{cid}").json()
    assert convo["title"] == "Which language do I prefer for backend work?"
    assert convo["messages"][0] == {"role": "user", "text": "Which language do I prefer for backend work?"}
    events = [e["event"] for e in api.get(f"/conversations/{cid}/trace").json()]
    assert "memory_recalled" in events


def test_no_memory_block_when_nothing_relevant(boot, facts):
    facts.add("The weekly team meeting is on Friday")
    api, fake = boot(response("end_turn", text_block("Hi!")))
    api.post("/chat", json={"message": "hello there"})
    assert fake.requests[0]["messages"][0]["content"] == "hello there"


def test_chat_still_works_when_embeddings_are_down(boot, embedder):
    embedder.available = False
    api, fake = boot(response("end_turn", text_block("Hi!")))
    body = api.post("/chat", json={"message": "hello"}).json()
    assert body["reply"] == "Hi!"
    assert fake.requests[0]["messages"][0]["content"] == "hello"
    events = [e["event"] for e in api.get(f"/conversations/{body['conversation_id']}/trace").json()]
    assert "memory_unavailable" in events


def test_remember_runs_automatically_and_forget_asks(boot, facts):
    api, _ = boot(
        response("tool_use", tool_use("t1", "remember", {"fact": "Majd's favourite editor is VS Code"})),
        response("end_turn", text_block("Noted.")),
        response("tool_use", tool_use("t2", "forget", {"memory_id": 1})),
        response("end_turn", text_block("Forgotten.")),
    )
    cid = api.post("/chat", json={"message": "Remember that I use VS Code"}).json()["conversation_id"]
    assert [f.content for f in facts.all()] == ["Majd's favourite editor is VS Code"]

    body = api.post("/chat", json={"message": "Forget that", "conversation_id": cid}).json()
    assert body["status"] == "waiting_for_user"
    assert len(facts.all()) == 1  # nothing deleted before approval
    api.post(f"/chat/{cid}/confirm", json={"decisions": {"t2": True}})
    assert facts.all() == []


# ---- API ----------------------------------------------------------------------------------

def test_memory_api(boot, settings):
    api, _ = boot()
    fact = api.post("/memory/facts", json={"content": "Majd lives in Tunisia"}).json()
    assert [f["content"] for f in api.get("/memory/facts").json()] == ["Majd lives in Tunisia"]

    doc = settings.allowed_roots[0] / "plan.txt"
    doc.write_text("Roadmap step 5 adds browser control with Playwright.")
    indexed = api.post("/memory/documents", json={"path": str(doc)}).json()
    assert indexed["status"] == "indexed"
    assert api.post("/memory/documents", json={"path": "../outside.txt"}).status_code == 400

    found = api.get("/memory/search", params={"q": "Where Majd lives"}).json()
    assert found["facts"][0]["content"] == "Majd lives in Tunisia"
    found = api.get("/memory/search", params={"q": "browser control Playwright"}).json()
    assert "Playwright" in found["chunks"][0]["content"]

    assert api.delete(f"/memory/facts/{fact['id']}").status_code == 204
    assert api.delete(f"/memory/documents/{indexed['document']['id']}").status_code == 204
    assert api.get("/memory/facts").json() == [] and api.get("/memory/documents").json() == []
