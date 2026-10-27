## Publishing Pages With deed.page Over MCP

[deed.page](https://deed.page) is a static hosting service with a remote MCP
server at `https://deed.page/mcp` (Streamable HTTP). With LightAgent's existing
`transport: "streamable-http"` support, an agent can write an HTML page or a
small set of files and get back a public URL, without any new dependency.

> Affiliation: this example is contributed by the deed.page team (Avatar 8 LLC),
> which operates the service. Service documentation: https://deed.page/docs.
> An MIT-licensed agent skill with the same API is at
> https://github.com/f-o-x11/deed-page-skill.

The example lives in [`example/deed_page_mcp.py`](../example/deed_page_mcp.py).

### Configure

```python
import asyncio

from LightAgent import LightAgent
from example.deed_page_mcp import (
    DEFAULT_ALLOWED_TOOLS,
    deed_page_mcp_config,
    protect_publish_token,
    restrict_new_tools,
)

agent = LightAgent(model="your-model", api_key="your-api-key", base_url="https://your-endpoint/v1")

before = set(agent.tool_registry.function_mappings)
asyncio.run(agent.setup_mcp(mcp_setting=deed_page_mcp_config()))
restrict_new_tools(agent.tool_registry, before, DEFAULT_ALLOWED_TOOLS)  # publish_site, get_site
protect_publish_token(agent.tool_registry, ".deedpage-token")

print(agent.run("Write a one-page HTML status report and publish it with publish_site. Reply with the URL."))
```

`deed_page_mcp_config()` returns:

```python
{"mcpServers": {"deedpage": {"transport": "streamable-http", "url": "https://deed.page/mcp"}}}
```

When `DEEDPAGE_TOKEN` is set, Python adds `{"Authorization": "Bearer <token>"}`
to `headers`; LightAgent does not interpolate header strings itself.

### Run the example

```bash
# Check the connection and publish a sample page (no model needed).
PYTHONPATH=. python example/deed_page_mcp.py --direct --ttl 3600

# Let an agent write and publish a page (any OpenAI-compatible model).
export OPENAI_API_KEY=...            # plus OPENAI_BASE_URL / LIGHTAGENT_MODEL if needed
PYTHONPATH=. python example/deed_page_mcp.py
```

`publish_site` takes `files` as a map of path to content (for example
`{"index.html": "<h1>Hi</h1>"}`), plus optional `slug` and `ttl_seconds`.
Pages without `ttl_seconds` stay up until deleted. Plan limits and the full
tool list are described in the service documentation linked above and may
change.

### Security notes

- **Owner token.** Without `DEEDPAGE_TOKEN`, the first publish is anonymous
  and the result contains a one-time owner token, which is needed to update or
  delete the page later. `protect_publish_token` removes it from the tool
  result before the model sees it and writes it to the token file with mode
  `0600`. Export it as `DEEDPAGE_TOKEN` to publish later pages under the same
  owner; with the header set, results do not include a token.
- **Tool surface.** The server lists more tools than publishing (for example
  `delete_site`, `rotate_token`, `add_domain`). LightAgent registers every tool
  a server lists, so the example removes the ones the agent does not need right
  after `setup_mcp`. Calling `refresh_tools()` registers them again.
- **Public output.** Published pages are public URLs. Do not let the agent
  publish secrets, private data, or anything you would not post on the web.

### Compatibility evidence

Local, offline (no network): `tests/test_deed_page_mcp_example.py` covers the
config builder, token removal and storage, and tool filtering with fakes.

Live service, run against `https://deed.page/mcp` with the MCP SDK version
from `poetry.lock`: `MCPClientManager` registered the server's tools over
Streamable HTTP; after filtering, only `publish_site` and `get_site` remained
in `openai_function_schemas`; a direct `publish_site` call returned a URL that
served HTTP 200, the owner token was saved with mode `0600` and was absent from
the tool result, and a publish with the `Authorization` header returned no
token. The test pages were deleted afterwards. The agent mode's model call was
not run in this validation.
