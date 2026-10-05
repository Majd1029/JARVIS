"""Free web search through DuckDuckGo (no API key). Used when the model provider has no built-in search."""

from ddgs import DDGS
from ddgs.exceptions import DDGSException

from app.security.permissions import PermissionLevel
from app.tools.base import Tool, ToolError

MAX_RESULTS = 6


def search_web(query: str) -> str:
    results = []
    for _ in range(2):  # DuckDuckGo occasionally returns nothing on the first try
        try:
            results = DDGS(timeout=10).text(query, max_results=MAX_RESULTS)
            break
        except DDGSException:
            continue
    if not results:
        raise ToolError(f"No web results found for '{query}'.")
    return "\n\n".join(
        f"{i}. {r['title']}\n   {r['href']}\n   {r['body']}" for i, r in enumerate(results, 1)
    )


web_search_tool = Tool(
    name="web_search",
    description="Search the web. Returns titles, links and snippets for the top results.",
    input_schema={
        "type": "object",
        "properties": {"query": {"type": "string", "description": "What to search for."}},
        "required": ["query"],
        "additionalProperties": False,
    },
    permission=PermissionLevel.PUBLIC_READ,
    handler=search_web,
)
