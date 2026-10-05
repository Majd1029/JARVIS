"""File tools, sandboxed to a set of allowed root directories."""

from datetime import datetime
from pathlib import Path

from app.security.permissions import PermissionLevel
from app.tools.base import Tool, ToolError

MAX_READ_CHARS = 100_000
MAX_LIST_ENTRIES = 500


class Sandbox:
    def __init__(self, roots: tuple[Path, ...]):
        self.roots = tuple(r.resolve() for r in roots)

    def resolve(self, path: str) -> Path:
        """Resolve a user/model-supplied path and refuse anything outside the allowed roots."""
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.roots[0] / candidate
        resolved = candidate.resolve()  # follows symlinks and collapses '..'
        if not any(resolved == root or resolved.is_relative_to(root) for root in self.roots):
            allowed = ", ".join(str(r) for r in self.roots)
            raise ToolError(f"Access denied: {resolved} is outside the allowed folders ({allowed}).")
        return resolved


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def build_filesystem_tools(sandbox: Sandbox) -> list[Tool]:
    def list_directory(path: str) -> str:
        directory = sandbox.resolve(path)
        if not directory.is_dir():
            raise ToolError(f"Not a directory: {directory}")
        entries = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        lines = []
        for entry in entries[:MAX_LIST_ENTRIES]:
            try:
                stat = entry.stat()
            except OSError:
                continue
            modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            if entry.is_dir():
                lines.append(f"[dir]  {entry.name}/  (modified {modified})")
            else:
                lines.append(f"[file] {entry.name}  ({_human_size(stat.st_size)}, modified {modified})")
        header = f"{directory} - {len(entries)} entries"
        if len(entries) > MAX_LIST_ENTRIES:
            header += f" (showing first {MAX_LIST_ENTRIES})"
        return "\n".join([header, *lines]) if lines else f"{directory} is empty."

    def read_file(path: str) -> str:
        file = sandbox.resolve(path)
        if not file.is_file():
            raise ToolError(f"Not a file: {file}")
        try:
            text = file.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            raise ToolError(f"{file.name} is not a UTF-8 text file.") from e
        if len(text) > MAX_READ_CHARS:
            return text[:MAX_READ_CHARS] + f"\n\n[truncated: file has {len(text)} characters]"
        return text

    def write_file(path: str, content: str, overwrite: bool = False) -> str:
        file = sandbox.resolve(path)
        if file.is_dir():
            raise ToolError(f"{file} is a directory.")
        if file.exists() and not overwrite:
            raise ToolError(f"{file} already exists. Set overwrite=true to replace it.")
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} characters to {file}"

    path_property = {
        "type": "string",
        "description": "Absolute path, '~/...' for the home folder, or relative to the first allowed folder.",
    }
    return [
        Tool(
            name="list_directory",
            description="List the files and sub-folders in a directory, with sizes and modification dates.",
            input_schema={
                "type": "object",
                "properties": {"path": path_property},
                "required": ["path"],
                "additionalProperties": False,
            },
            permission=PermissionLevel.LOCAL_READ,
            handler=list_directory,
        ),
        Tool(
            name="read_file",
            description=f"Read a UTF-8 text file. Output is truncated after {MAX_READ_CHARS} characters.",
            input_schema={
                "type": "object",
                "properties": {"path": path_property},
                "required": ["path"],
                "additionalProperties": False,
            },
            permission=PermissionLevel.LOCAL_READ,
            handler=read_file,
        ),
        Tool(
            name="write_file",
            description=(
                "Create a text file (parent folders are created as needed). Refuses to replace an "
                "existing file unless overwrite is true. The user must approve each write."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": path_property,
                    "content": {"type": "string"},
                    "overwrite": {"type": "boolean", "description": "Replace the file if it exists."},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            permission=PermissionLevel.LOCAL_WRITE,
            handler=write_file,
        ),
    ]
