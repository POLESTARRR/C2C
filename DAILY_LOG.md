# Daily Log

## 2026-10-11
The `httpx.AsyncClient` is configured with a 30-second timeout for all Swiggy API calls.

## 2026-10-16
Retry logic in `swiggy_mcp_client.py` implements max_retries=3 with a 1-second sleep between retries using a simple loop.

## 2026-10-16
Multiple LangChain tool definitions are decorated with `@tool` and registered for function-calling within the agent graph.

## 2026-10-28
The `swiggy_mcp_client.py` defines four MCP tools: `search_restaurants`, `get_menu`, `get_restaurant_details`, and `place_order`.

## 2026-10-28
A system prompt in `semantic.py` provides examples like 'kaddu' -> 'pumpkin' and 'jhinga' -> 'prawns' for ingredient canonicalization.

## 2026-10-28
The `SwiggyMCPServer` class acts as a local MCP server facade, bridging Swiggy API calls with LangChain tool schemas.

## 2026-10-28
The `canonicalise()` function in `semantic.py` never raises exceptions, returning an empty dict on API failure to avoid breaking the ingredient matching pipeline.
