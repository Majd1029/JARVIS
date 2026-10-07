"""Browser tools against a local test site, driven by a real (headless) browser."""

import functools
import http.server
import re
import threading

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.security.permissions import PermissionLevel
from app.tools.base import ToolError
from app.tools.browser import TEXT_PART_CHARS, BrowserSession, build_browser_tools
from tests.conftest import FakeClient, response, text_block, tool_use

PAGES = {
    "index.html": """<!doctype html><title>Home</title><body>
        <nav><a href="/nav.html">Nav junk</a></nav>
        <main><h1>Welcome to the test site</h1><p>The secret word is pineapple.</p>
        <a href="/second.html">Second page</a>
        <form action="/result.html"><input name="q" placeholder="Search the site"><button type="submit">Go</button></form>
        <a href="/long.html">Long page</a></main></body>""",
    "second.html": "<!doctype html><title>Second</title><main><p>You reached the second page.</p></main>",
    "result.html": """<!doctype html><title>Results</title><main><p id="out"></p></main>
        <script>document.getElementById('out').textContent =
          'You searched for ' + new URLSearchParams(location.search).get('q');</script>""",
    "covered.html": """<!doctype html><title>Covered</title><main>
        <button onclick="document.getElementById('out').textContent = 'Button worked'">Press me</button>
        <p id="out">Not pressed</p></main>
        <div style="position:fixed;inset:0;background:rgba(0,0,0,0.01)"></div>""",
    "long.html": "<!doctype html><title>Long</title><main>"
                 + "".join(f"<p>Paragraph {i}: " + "lorem ipsum " * 30 + "</p>" for i in range(20))
                 + "<p>THE END</p></main>",
}


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp("site")
    for name, html in PAGES.items():
        (root / name).write_text(html, encoding="utf-8")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_QuietHandler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture(scope="module")
def browser():
    session = BrowserSession(channel="msedge", headless=True)
    try:
        session._run(session._ensure_page)  # launch now, so a missing browser skips the module
    except ToolError as e:
        session.close()
        pytest.skip(f"No browser available: {e}")
    yield session
    session.close()


def element_number(listing: str, label: str) -> int:
    match = re.search(rf"\[(\d+)\] [^\n]*\"{re.escape(label)}\"", listing)
    assert match, f"{label!r} not in listing:\n{listing}"
    return int(match.group(1))


def test_open_shows_text_and_numbered_elements(browser, site):
    page = browser.open(f"{site}/index.html")
    assert "Title: Home" in page
    assert "The secret word is pineapple." in page
    assert "Nav junk" not in page.split("Interactive elements")[0]  # <main> text only
    assert "untrusted" in page
    assert re.search(r'\[\d+\] link "Second page" -> http://127\.0\.0\.1:\d+/second\.html', page)
    assert re.search(r'\[\d+\] input "Search the site"', page)
    assert re.search(r'\[\d+\] button "Go"', page)


def test_follow_link_and_back(browser, site):
    page = browser.open(f"{site}/index.html")
    second = browser.follow_link(element_number(page, "Second page"))
    assert "You reached the second page." in second
    assert "Title: Home" in browser.back()


def test_follow_link_rejects_non_links(browser, site):
    page = browser.open(f"{site}/index.html")
    with pytest.raises(ToolError, match="not a link"):
        browser.follow_link(element_number(page, "Go"))
    with pytest.raises(ToolError, match="no element \\[999\\]"):
        browser.follow_link(999)


def test_type_and_submit(browser, site):
    page = browser.open(f"{site}/index.html")
    result = browser.type(element_number(page, "Search the site"), "jarvis", press_enter=True)
    assert "You searched for jarvis" in result


def test_click_button(browser, site):
    page = browser.open(f"{site}/index.html")
    browser.type(element_number(page, "Search the site"), "via button", press_enter=False)
    result = browser.click(element_number(page, "Go"))
    assert "You searched for via button" in result


def test_click_reaches_a_button_under_an_overlay(browser, site):
    page = browser.open(f"{site}/covered.html")
    assert "Button worked" in browser.click(element_number(page, "Press me"))


def test_long_pages_are_read_in_parts(browser, site):
    page = browser.open(f"{site}/long.html")
    assert "part 1 of" in page and "browser_read with part=2" in page
    parts = int(re.search(r"part 1 of (\d+)", page).group(1))
    assert parts > 1
    assert "THE END" in browser.read(parts)
    with pytest.raises(ToolError, match="doesn't exist"):
        browser.read(parts + 1)
    assert len(browser.read(2)) <= TEXT_PART_CHARS + 300  # a part plus its header lines


def test_only_web_addresses(browser):
    for url in ("file:///C:/Windows/win.ini", "javascript:alert(1)", "ftp://example.com", "data:text/html,hi"):
        with pytest.raises(ToolError, match="Only http"):
            browser.open(url)


def test_bare_hosts_get_https():
    from app.tools.browser import _validate_url
    assert _validate_url("example.com/page") == "https://example.com/page"
    assert _validate_url("localhost:3000") == "https://localhost:3000"
    assert _validate_url("http://127.0.0.1:8000/docs") == "http://127.0.0.1:8000/docs"


# ---- permissions and the agent (no real browser needed) ------------------------------------

class FakeBrowser:
    running = False

    def __init__(self):
        self.clicked = []

    def click(self, element):
        self.clicked.append(element)
        return "clicked"

    def close(self):
        pass

    def __getattr__(self, name):  # open/read/follow_link/type/back are not used here
        return lambda *a, **k: "ok"


def test_browser_permission_levels():
    levels = {t.name: t.permission for t in build_browser_tools(FakeBrowser())}
    assert levels == {
        "browser_open": PermissionLevel.PUBLIC_READ,
        "browser_read": PermissionLevel.PUBLIC_READ,
        "browser_follow_link": PermissionLevel.PUBLIC_READ,
        "browser_back": PermissionLevel.PUBLIC_READ,
        "browser_click": PermissionLevel.EXTERNAL,
        "browser_type": PermissionLevel.EXTERNAL,
    }


def test_click_waits_for_approval(settings, engine, embedder):
    fake_browser = FakeBrowser()
    fake = FakeClient([
        response("tool_use", tool_use("t1", "browser_click", {"element": 4})),
        response("end_turn", text_block("Clicked it.")),
    ])
    api = TestClient(create_app(settings, client=fake, engine=engine, embedder=embedder, browser=fake_browser))
    body = api.post("/chat", json={"message": "click the buy button"}).json()
    assert body["status"] == "waiting_for_user"
    assert body["pending"][0]["permission"] == "EXTERNAL"
    assert fake_browser.clicked == []
    api.post(f"/chat/{body['conversation_id']}/confirm", json={"decisions": {"t1": True}})
    assert fake_browser.clicked == [4]
