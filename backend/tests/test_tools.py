import pytest

from app.security.permissions import Decision, PermissionLevel, PolicyEngine
from app.tools.base import ToolError
from app.tools.calculator import calculate
from app.tools.clock import current_datetime
from app.tools.filesystem import Sandbox, build_filesystem_tools


def test_calculator_basic():
    assert calculate("2 + 3 * 4") == "14"
    assert calculate("sqrt(16) + 2 ** 3") == "12.0"


@pytest.mark.parametrize("expr", ["__import__('os')", "open('x')", "(1).__class__", "9 ** 99999", "1/0"])
def test_calculator_rejects_unsafe_or_invalid(expr):
    with pytest.raises(ToolError):
        calculate(expr)


def test_clock_timezone():
    assert "UTC+0100" in current_datetime("Africa/Tunis")
    with pytest.raises(ToolError):
        current_datetime("Not/AZone")


@pytest.fixture
def fs(tmp_path):
    tools = {t.name: t for t in build_filesystem_tools(Sandbox((tmp_path,)))}
    return tools, tmp_path


def test_filesystem_roundtrip(fs):
    tools, root = fs
    tools["write_file"].execute({"path": str(root / "notes" / "a.txt"), "content": "hello"})
    assert tools["read_file"].execute({"path": "notes/a.txt"}) == "hello"
    assert "a.txt" in tools["list_directory"].execute({"path": "notes"})


def test_write_refuses_overwrite_without_flag(fs):
    tools, root = fs
    (root / "a.txt").write_text("old")
    with pytest.raises(ToolError):
        tools["write_file"].execute({"path": "a.txt", "content": "new"})
    tools["write_file"].execute({"path": "a.txt", "content": "new", "overwrite": True})
    assert (root / "a.txt").read_text() == "new"


@pytest.mark.parametrize("path", ["..", "../outside.txt", "C:/Windows/win.ini", "/etc/passwd"])
def test_sandbox_blocks_escape(fs, path):
    tools, _ = fs
    with pytest.raises(ToolError, match="Access denied|Not a"):
        tools["read_file"].execute({"path": path})


def test_permission_levels(fs):
    tools, _ = fs
    assert {name: t.permission for name, t in tools.items()} == {
        "list_directory": PermissionLevel.LOCAL_READ,
        "read_file": PermissionLevel.LOCAL_READ,
        "write_file": PermissionLevel.LOCAL_WRITE,
    }


def test_policy_engine():
    policy = PolicyEngine(PermissionLevel.LOCAL_READ)
    assert policy.evaluate(PermissionLevel.LOCAL_READ) is Decision.ALLOW
    assert policy.evaluate(PermissionLevel.LOCAL_WRITE) is Decision.CONFIRM
    # External actions always need confirmation, even if config tries to auto-approve everything.
    assert PolicyEngine(PermissionLevel.SENSITIVE).evaluate(PermissionLevel.EXTERNAL) is Decision.CONFIRM


def test_list_directory_sorting(fs):
    tools, root = fs
    (root / "small.txt").write_text("a")
    (root / "big.txt").write_text("a" * 5000)
    (root / "sub").mkdir()
    listing = tools["list_directory"].execute({"path": ".", "sort_by": "size"})
    lines = listing.splitlines()
    assert "2 files, 1 folders" in lines[0]
    assert "big.txt" in lines[1] and "small.txt" in lines[2] and "sub/" in lines[3]
