"""Declarative REST tools.

Each entry in the YAML config (REST_TOOLS_CONFIG, default backend/rest_tools.yaml)
becomes a StructuredTool: the model-supplied arguments are interpolated into
{placeholders} in the url/query/headers/body, ${ENV_VARS} are resolved from the
environment at call time, and any tool referencing a missing env var
self-disables. New REST integrations are YAML entries — no Python required.
See rest_tools.example.yaml for the format.
"""

import os
import re
from pathlib import Path

import httpx
import yaml
from langchain_core.tools import StructuredTool
from pydantic import Field, create_model

from ..base import ToolSpec, register

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_DEFAULT_CONFIG = Path(__file__).parents[3] / "rest_tools.yaml"

MAX_RESPONSE_CHARS = int(os.getenv("REST_TOOL_MAX_CHARS", "6000"))
TIMEOUT = 30.0

_TYPE_MAP = {"string": str, "integer": int, "number": float, "boolean": bool}


def _render(value, args: dict):
    if isinstance(value, str):
        value = _ENV_RE.sub(lambda m: os.getenv(m.group(1), ""), value)
        for name, arg in args.items():
            value = value.replace("{%s}" % name, str(arg))
        return value
    if isinstance(value, dict):
        return {k: _render(v, args) for k, v in value.items()}
    if isinstance(value, list):
        return [_render(v, args) for v in value]
    return value


def _build_tool(spec: dict) -> tuple[StructuredTool, tuple[str, ...]]:
    fields = {}
    for name, meta in (spec.get("args") or {}).items():
        py_type = _TYPE_MAP.get(meta.get("type", "string"), str)
        fields[name] = (py_type, Field(description=meta.get("description", "")))
    args_model = create_model(f"{spec['name']}_args", **fields)

    required_env = tuple(sorted(set(_ENV_RE.findall(yaml.safe_dump(spec)))))

    async def _call(**kwargs) -> str:
        method = spec.get("method", "GET").upper()
        url = _render(spec["url"], kwargs)
        params = _render(spec.get("query") or {}, kwargs)
        headers = _render(spec.get("headers") or {}, kwargs)
        body = _render(spec["body"], kwargs) if "body" in spec else None
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.request(
                    method,
                    url,
                    params=params or None,
                    headers=headers or None,
                    json=body,
                )
        except httpx.HTTPError as exc:
            return f"Error: request failed: {exc}."
        text = resp.text
        if len(text) > MAX_RESPONSE_CHARS:
            text = text[:MAX_RESPONSE_CHARS] + "\n[...truncated]"
        return f"HTTP {resp.status_code}\n{text}"

    tool = StructuredTool.from_function(
        coroutine=_call,
        name=spec["name"],
        description=spec["description"],
        args_schema=args_model,
    )
    return tool, required_env


def _load_config() -> None:
    path = Path(os.getenv("REST_TOOLS_CONFIG", str(_DEFAULT_CONFIG)))
    if not path.exists():
        return
    specs = yaml.safe_load(path.read_text()) or []
    for spec in specs:
        tool, required_env = _build_tool(spec)
        register(
            ToolSpec(
                tool=tool,
                prompt_hint=spec.get("prompt_hint", ""),
                required_env=required_env,
            )
        )


_load_config()
