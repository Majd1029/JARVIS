"""Current date and time."""

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.security.permissions import PermissionLevel
from app.tools.base import Tool, ToolError


def current_datetime(timezone: str | None = None) -> str:
    if timezone:
        try:
            now = datetime.now(ZoneInfo(timezone))
        except ZoneInfoNotFoundError as e:
            raise ToolError(f"Unknown timezone '{timezone}'. Use an IANA name like 'Africa/Tunis'.") from e
    else:
        now = datetime.now().astimezone()
    return f"{now.strftime('%A %d %B %Y, %H:%M:%S')} ({now.tzname()}, UTC{now.strftime('%z')})"


clock_tool = Tool(
    name="get_current_datetime",
    description=(
        "Get the current date and time. Without a timezone, returns the user's local time."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "timezone": {"type": "string", "description": "Optional IANA timezone, e.g. 'Europe/Paris'."},
        },
        "additionalProperties": False,
    },
    permission=PermissionLevel.PUBLIC_READ,
    handler=current_datetime,
)
