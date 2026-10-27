"""Publish LightAgent output as a static page through deed.page's remote MCP server.

deed.page (https://deed.page) is a static hosting service with a Streamable
HTTP MCP endpoint at https://deed.page/mcp. This example uses LightAgent's
existing ``transport: "streamable-http"`` support; it adds no dependency and
makes no network request at import time.

Two modes:

* ``--direct`` calls ``publish_site`` through ``MCPClientManager`` without a
  model, which is a quick way to check the connection.
* the default mode runs a LightAgent agent (any OpenAI-compatible model) that
  writes a page and publishes it with ``publish_site``.

Credentials:

* ``DEEDPAGE_TOKEN`` (optional) is sent as a Bearer header. Without it the first
  publish is anonymous and the service returns a new owner token once. The
  helpers below remove that token from the tool result before it reaches the
  model or the console, and write it to ``--token-file`` with mode 0600.
* ``LIGHTAGENT_MODEL``, ``OPENAI_API_KEY`` and ``OPENAI_BASE_URL`` configure the
  model for the agent mode.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

DEED_PAGE_MCP_URL = "https://deed.page/mcp"
SERVER_NAME = "deedpage"
TOKEN_ENV = "DEEDPAGE_TOKEN"
DEFAULT_TOKEN_FILE = ".deedpage-token"
# publish_site and get_site are enough for "write a page, return the link".
# The server also exposes delete_site, rotate_token, add_domain and others;
# keep those away from the model unless the application needs them.
DEFAULT_ALLOWED_TOOLS = ("publish_site", "get_site")

SAMPLE_PAGE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>LightAgent + deed.page</title></head>
<body>
<h1>Published from LightAgent</h1>
<p>This page was published through the deed.page MCP server.</p>
</body>
</html>
"""


def deed_page_mcp_config(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Return an ``mcp_setting`` dict for ``LightAgent.setup_mcp``.

    The token is read by Python from the environment; LightAgent does not
    interpolate header strings.
    """
    env = os.environ if env is None else env
    server: dict[str, Any] = {"transport": "streamable-http", "url": DEED_PAGE_MCP_URL}
    token = (env.get(TOKEN_ENV) or "").strip()
    if token:
        server["headers"] = {"Authorization": f"Bearer {token}"}
    return {"mcpServers": {SERVER_NAME: server}}


def split_token(result_text: str) -> tuple[str, str | None]:
    """Remove the one-time owner ``token`` from a publish result.

    Returns the cleaned result text and the token (or ``None``). Non-JSON text
    is returned unchanged.
    """
    try:
        payload = json.loads(result_text)
    except (TypeError, ValueError):
        return result_text, None
    if not isinstance(payload, dict) or "token" not in payload:
        return result_text, None
    token = payload.pop("token")
    payload["token_saved"] = True
    return json.dumps(payload), str(token) if token else None


def save_token(token: str, token_file: str | os.PathLike[str]) -> Path:
    """Write the owner token to ``token_file`` readable only by the current user."""
    path = Path(token_file)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token)
    os.chmod(path, 0o600)
    return path


def restrict_new_tools(tool_registry: Any, before: Iterable[str], allowed: Iterable[str]) -> list[str]:
    """Drop tools registered since ``before`` unless they are in ``allowed``.

    Call this right after ``setup_mcp`` with the tool names that existed before
    it, so local tools are never touched. Returns the removed names.
    """
    before_set = set(before)
    allowed_set = set(allowed)
    removed = [
        name for name in list(tool_registry.function_mappings)
        if name not in before_set and name not in allowed_set
    ]
    for name in removed:
        tool_registry.function_mappings.pop(name, None)
        tool_registry.function_info.pop(name, None)
    tool_registry.openai_function_schemas = [
        schema for schema in tool_registry.openai_function_schemas
        if schema.get("function", {}).get("name") not in removed
    ]
    return removed


def protect_publish_token(tool_registry: Any, token_file: str | os.PathLike[str]) -> bool:
    """Wrap the registered ``publish_site`` so a returned owner token is saved, not shown."""
    original = tool_registry.function_mappings.get("publish_site")
    if original is None:
        return False

    async def publish_site(**kwargs: Any) -> Any:
        result = original(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, dict) and isinstance(result.get("result"), str):
            cleaned, token = split_token(result["result"])
            if token:
                save_token(token, token_file)
            result = {**result, "result": cleaned}
        return result

    tool_registry.function_mappings["publish_site"] = publish_site
    return True


async def publish_direct(html: str, ttl_seconds: int | None, token_file: str) -> dict[str, Any]:
    """Publish ``html`` as index.html without a model and return the cleaned result."""
    from LightAgent.mcp_client_manager import MCPClientManager
    from LightAgent.tools import ToolRegistry

    manager = MCPClientManager(deed_page_mcp_config(), ToolRegistry())
    arguments: dict[str, Any] = {"files": {"index.html": html}}
    if ttl_seconds:
        arguments["ttl_seconds"] = ttl_seconds
    response = await manager.call_tool("publish_site", arguments, target_server=SERVER_NAME)
    cleaned, token = split_token(response["result"])
    if token:
        save_token(token, token_file)
    return json.loads(cleaned)


def run_agent(task: str, token_file: str) -> str:
    """Run a LightAgent agent that can publish with ``publish_site``."""
    from LightAgent import LightAgent

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Set OPENAI_API_KEY (and OPENAI_BASE_URL / LIGHTAGENT_MODEL) for the agent mode.")
    agent = LightAgent(
        model=os.environ.get("LIGHTAGENT_MODEL", "gpt-4o-mini"),
        api_key=api_key,
        base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        role="You write small, self-contained HTML pages and publish them when asked.",
    )
    before = set(agent.tool_registry.function_mappings)
    asyncio.run(agent.setup_mcp(mcp_setting=deed_page_mcp_config()))
    restrict_new_tools(agent.tool_registry, before, DEFAULT_ALLOWED_TOOLS)
    protect_publish_token(agent.tool_registry, token_file)
    return agent.run(task, stream=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--direct", action="store_true", help="publish a sample page without a model")
    parser.add_argument("--ttl", type=int, default=3600, help="expire the page after N seconds (0 = permanent)")
    parser.add_argument("--token-file", default=DEFAULT_TOKEN_FILE)
    parser.add_argument(
        "--task",
        default=(
            "Write a one-page HTML summary of what LightAgent is, then publish it with publish_site "
            "using files {\"index.html\": <the html>} and ttl_seconds 3600. Reply with the URL."
        ),
    )
    args = parser.parse_args()
    if args.direct:
        result = asyncio.run(publish_direct(SAMPLE_PAGE, args.ttl or None, args.token_file))
        print(json.dumps({key: result.get(key) for key in ("url", "slug", "version", "expires_at")}, indent=2))
    else:
        print(run_agent(args.task, args.token_file))


if __name__ == "__main__":
    main()
