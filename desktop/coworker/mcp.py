"""Optional MCP tool registry. Not the Gmail client. Not a core dependency.

Config is `mcp.json` under the state dir. Tests inject FakeMcp. Live servers are
opt-in; missing config means no extra tools.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from coworker.secrets import SecretStore

GRANOLA_URL = "https://mcp.granola.ai/mcp"
_WRITE = re.compile(r"write|create|delete|update", re.I)


def tool_result_payload(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return dict(result)
    for attribute in ("structuredContent", "structured_content"):
        structured = getattr(result, attribute, None)
        if isinstance(structured, dict):
            return dict(structured)
    content = getattr(result, "content", None)
    if isinstance(content, list):
        for item in content:
            text = getattr(item, "text", None)
            if not isinstance(text, str):
                continue
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, dict):
                return decoded
    return {"result": str(result)}


def _import_mcp_sdk() -> tuple[Any, Any]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    return ClientSession, streamable_http_client


def default_mcp_config() -> dict[str, Any]:
    return {
        "mcpServers": {
            "granola": {"type": "http", "url": GRANOLA_URL, "auth": "oauth"}
        }
    }


def load_mcp_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return default_mcp_config()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default_mcp_config()
    return data if isinstance(data, dict) else default_mcp_config()


def write_default_mcp_json(path: Path) -> None:
    if path.is_file():
        path.chmod(0o600)
        return
    path.write_text(json.dumps(default_mcp_config(), indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


class FakeMcp:
    """In-process MCP stand-in. Tools are named mcp__<server>__<tool>."""

    def __init__(self, tools: list[dict[str, Any]] | None = None) -> None:
        self._tools = list(tools or [])
        self.calls: list[dict[str, Any]] = []

    def schemas(self) -> list[dict[str, Any]]:
        out = []
        for spec in self._tools:
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": spec["name"],
                        "description": spec.get("description") or spec["name"],
                        "parameters": spec.get("parameters")
                        or {"type": "object", "properties": {}},
                    },
                }
            )
        return out

    def has(self, name: str) -> bool:
        return any(spec["name"] == name for spec in self._tools)

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        args = arguments or {}
        self.calls.append({"name": name, "arguments": args})
        for spec in self._tools:
            if spec["name"] == name:
                handler: Callable[..., dict[str, Any]] | None = spec.get("handler")
                if handler is None:
                    return {"ok": True, "echo": args}
                return handler(args)
        return {"error": f"unknown mcp tool {name}"}


class LiveMcp:
    def __init__(
        self,
        *,
        secrets: SecretStore,
        config_path: Path,
        oauth: Any | None = None,
    ) -> None:
        self.secrets = secrets
        self.config_path = config_path
        self.oauth = oauth

    def _tokens(self) -> dict[str, Any]:
        return self.secrets.get("mcp-oauth:granola") or {}

    def schemas(self) -> list[dict[str, Any]]:
        if not (self._tokens().get("access_token") or self._tokens().get("refresh_token")):
            return []
        return [
            {
                "type": "function",
                "function": {
                    "name": "mcp__granola__list_meetings",
                    "description": "List Granola meetings.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

    def has(self, name: str) -> bool:
        return any(s["function"]["name"] == name for s in self.schemas())

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        last = name.rsplit("__", 1)[-1]
        if _WRITE.search(last):
            return {"error": "granola writes are out of v1"}
        if not (self._tokens().get("access_token") or self._tokens().get("refresh_token")):
            return {"error": "Granola is not connected."}
        return self._call_live(name, arguments or {})

    def _call_live(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        import asyncio
        import threading

        result: dict[str, Any] = {}

        def worker() -> None:
            try:
                result.update(asyncio.run(self._async_call(name, arguments)))
            except Exception as exc:
                result["error"] = str(exc)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join(timeout=30)
        if thread.is_alive():
            return {"error": "Granola timed out"}
        return result or {"error": "Granola is not connected."}

    async def _async_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = name.rsplit("__", 1)[-1]
        try:
            ClientSession, streamable_http_client = _import_mcp_sdk()
        except Exception:
            return {"error": "mcp SDK is not installed"}
        # Official SDK path. Token adapter is the secrets profile.
        token = str(self._tokens().get("access_token") or "")
        headers = {"Authorization": f"Bearer {token}"} if token else None
        from mcp.shared._httpx_utils import create_mcp_http_client

        async with create_mcp_http_client(headers) as http_client:
            async with streamable_http_client(GRANOLA_URL, http_client=http_client) as (
                read,
                write,
                _,
            ):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    out = await session.call_tool(tool, arguments)
                    return {"ok": True, **tool_result_payload(out)}
