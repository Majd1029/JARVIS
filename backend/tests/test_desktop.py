"""Desktop and screen tools. Automated tests never click or type on the real desktop: actions are
tested through a fake; only read-only calls (listing windows, taking a screenshot) touch the system."""

import base64
import functools
import http.server
import io
import json
import sys
import threading

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.security.permissions import PermissionLevel
from app.tools.base import ToolError
from app.tools.desktop import DesktopSession, _escape_keys, _kind_label, build_desktop_tools
from app.tools.screen import MAX_WIDTH, ScreenReader, build_screen_tools, capture
from tests.conftest import FakeClient, response, text_block, tool_use

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows UI Automation")


# ---- pure helpers ---------------------------------------------------------------------------

def test_escape_keys_keeps_literal_braces():
    assert _escape_keys("a{b}c") == "a{{}b{}}c"
    assert _escape_keys("plain text") == "plain text"


def test_kind_labels():
    assert _kind_label("EditControl") == "text field"
    assert _kind_label("ButtonControl") == "button"


def test_permission_levels():
    levels = {t.name: t.permission for t in build_desktop_tools(DesktopSession())}
    assert levels == {
        "desktop_list_windows": PermissionLevel.LOCAL_READ,
        "desktop_read_window": PermissionLevel.LOCAL_READ,
        "desktop_focus_window": PermissionLevel.EXECUTE,
        "desktop_click": PermissionLevel.EXECUTE,
        "desktop_type": PermissionLevel.EXECUTE,
        "desktop_press_keys": PermissionLevel.EXECUTE,
        "desktop_open_app": PermissionLevel.EXECUTE,
    }
    [screen] = build_screen_tools(ScreenReader("http://x", "m"))
    assert screen.permission == PermissionLevel.LOCAL_READ


# ---- read-only calls against the real system ----------------------------------------------

@windows_only
def test_list_windows_and_errors_for_unknown_numbers():
    session = DesktopSession()
    try:
        listing = session.list_windows()
        assert "open windows:" in listing.splitlines()[0]
        with pytest.raises(ToolError, match="no window \\[999\\]"):
            session.read_window(999)
        with pytest.raises(ToolError, match="no control \\[1\\]"):
            session.click(1)
    finally:
        session.close()


@windows_only
def test_capture_is_a_downscaled_png():
    from PIL import Image
    png = capture(window_only=False)
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG" and image.width <= MAX_WIDTH


# ---- screen_describe against a fake Ollama ------------------------------------------------

class _FakeOllama(http.server.BaseHTTPRequestHandler):
    requests: list[dict] = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _FakeOllama.requests.append(body)
        payload = json.dumps({"message": {"role": "assistant", "content": "A code editor is open."}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_ollama():
    _FakeOllama.requests = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FakeOllama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_screen_describe_sends_the_screenshot_to_the_vision_model(fake_ollama, monkeypatch):
    monkeypatch.setattr("app.tools.screen.capture", lambda window_only: b"\x89PNG fake")
    answer = ScreenReader(fake_ollama, "qwen3-vl:4b-instruct").describe("What app is open?")
    assert "A code editor is open." in answer and "not instructions" in answer
    [request] = _FakeOllama.requests
    assert request["model"] == "qwen3-vl:4b-instruct"
    message = request["messages"][0]
    assert message["content"].startswith("What app is open?")
    assert base64.b64decode(message["images"][0]) == b"\x89PNG fake"


# ---- approvals through the agent (fake desktop, nothing real happens) ---------------------

class FakeDesktop:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args + tuple(kwargs.values())))
            return f"{name} done"
        return record


def test_reading_runs_but_actions_wait_for_approval(settings, engine, embedder):
    desktop = FakeDesktop()
    fake = FakeClient([
        response("tool_use", tool_use("t1", "desktop_list_windows", {}),
                 tool_use("t2", "desktop_open_app", {"target": "notepad"})),
        response("end_turn", text_block("Notepad is open.")),
    ])
    api = TestClient(create_app(settings, client=fake, engine=engine, embedder=embedder, desktop=desktop))
    body = api.post("/chat", json={"message": "open notepad"}).json()

    assert body["status"] == "waiting_for_user"
    assert [p["tool"] for p in body["pending"]] == ["desktop_open_app"]
    assert desktop.calls == [("list_windows", ())]  # reading already ran; opening did not

    api.post(f"/chat/{body['conversation_id']}/confirm", json={"decisions": {"t2": True}})
    assert desktop.calls[-1] == ("open_app", ("notepad",))
