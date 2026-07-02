from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from article_factory.config import settings
from article_factory.services.brave_search import brave_web_search, format_brave_results
from article_factory.services.web_fetch import fetch_web_page, format_fetch_result

TOOL_WRITE_FILE = "write_file"
TOOL_READ_FILE = "read_file"
TOOL_WEB_SEARCH = "web_search"
TOOL_WEB_FETCH = "web_fetch"

FACTORY_STEP_TOOL_NAMES: tuple[str, ...] = (
    TOOL_WRITE_FILE,
    TOOL_READ_FILE,
    TOOL_WEB_SEARCH,
    TOOL_WEB_FETCH,
)

MAX_READ_BYTES = 100 * 1024
MAX_TOOL_ROUNDS = 25


class WorkspaceViolation(ValueError):
    pass


def normalize_step_enabled_tools(raw: dict[str, Any] | None) -> dict[str, bool]:
    """Legacy per-step tool flags stored on flows (no longer used to restrict tools)."""
    enabled = {name: False for name in FACTORY_STEP_TOOL_NAMES}
    if raw:
        for name in FACTORY_STEP_TOOL_NAMES:
            if name in raw:
                enabled[name] = bool(raw[name])
    return enabled


def all_step_tools_enabled() -> dict[str, bool]:
    return {name: True for name in FACTORY_STEP_TOOL_NAMES}


def resolve_step_tools(raw: dict[str, Any] | None = None) -> dict[str, bool]:
    """Every factory step gets the full tool set regardless of flow JSON toggles."""
    _ = raw
    return all_step_tools_enabled()


def step_has_tools(enabled: dict[str, bool]) -> bool:
    return any(enabled.values())


def workspace_read_tools_enabled(enabled: dict[str, bool]) -> bool:
    return bool(enabled.get(TOOL_READ_FILE) or enabled.get(TOOL_WRITE_FILE))


def _append_workspace_read_tool_definitions(tools: list[dict[str, Any]]) -> None:
    tools.append(
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a text file from this run's workspace (relative path only).",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        }
    )
    tools.append(
        {
            "type": "function",
            "function": {
                "name": "list_files",
                "description": "List files in this run's workspace (relative path, default '.').",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "default": "."}},
                    "required": [],
                },
            },
        }
    )


def build_tool_system_guidance(enabled: dict[str, bool]) -> str:
    lines = [
        "## Factory tools for this step",
        "You have real tools wired to this run. Use them instead of saying you cannot browse, "
        "search the web, fetch pages, or access current information.",
    ]
    if enabled.get(TOOL_WRITE_FILE):
        lines.append("- write_file: write text files in this run's workspace.")
    if workspace_read_tools_enabled(enabled):
        lines.append("- read_file, list_files: read and list text files in this run's workspace.")
    if enabled.get(TOOL_WEB_SEARCH):
        lines.append(
            "- web_search: search the live public web (Brave Search) for current facts, news, and URLs. "
            "Call this tool when the task needs up-to-date or sourced information."
        )
    if enabled.get(TOOL_WEB_FETCH):
        lines.append(
            "- web_fetch: fetch a public http(s) URL and return readable page text (HTML is stripped). "
            "Use after web_search when you need the full content of a specific page."
        )
    lines.append(
        "When a tool is listed above, call it. Do not tell the user you lack web access or live browsing."
    )
    return "\n".join(lines)


def augment_system_prompt_for_tools(system_prompt: str, enabled: dict[str, bool]) -> str:
    if not step_has_tools(enabled):
        return system_prompt
    guidance = build_tool_system_guidance(enabled)
    body = system_prompt.strip()
    if not body:
        return guidance
    return f"{body}\n\n{guidance}"


_TOOL_REFUSAL_MARKERS: tuple[str, ...] = (
    "don't have the ability to search",
    "do not have the ability to search",
    "cannot browse",
    "can't browse",
    "cannot search the web",
    "can't search the web",
    "don't have access to the internet",
    "do not have access to the internet",
    "training cutoff",
    "knowledge cutoff",
    "cannot access current",
    "can't access current",
    "unable to search",
    "i don't have the ability to",
    "i do not have the ability to",
    "i cannot browse",
    "i can't browse",
)


def looks_like_tool_refusal(content: str) -> bool:
    lowered = content.lower()
    return any(marker in lowered for marker in _TOOL_REFUSAL_MARKERS)


def tool_use_nudge_message(enabled: dict[str, bool]) -> str:
    parts = ["You already have factory tools available for this step."]
    if enabled.get(TOOL_WEB_SEARCH):
        parts.append("Use the web_search tool now for current web results, then answer from those results.")
    if enabled.get(TOOL_WEB_FETCH):
        parts.append("Use web_fetch to read specific URLs when you need full page content.")
    if enabled.get(TOOL_WRITE_FILE):
        parts.append("Use write_file when saving workspace notes or drafts.")
    if workspace_read_tools_enabled(enabled):
        parts.append("Use read_file/list_files when reading workspace files.")
    parts.append("Do not claim you lack web access — call the tools.")
    return " ".join(parts)


def run_workspace_root(run_id: str) -> Path:
    root = Path(settings.flow_run_outputs_root).expanduser().resolve() / run_id / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_workspace_path(workspace_root: Path, relative_path: str) -> Path:
    cleaned = (relative_path or "").strip().replace("\\", "/")
    if not cleaned or cleaned.startswith("/"):
        raise WorkspaceViolation("Path must be a non-empty relative path")
    parts = [part for part in cleaned.split("/") if part not in ("", ".")]
    if ".." in parts:
        raise WorkspaceViolation("Path must stay inside the run workspace")
    target = (workspace_root / Path(*parts)).resolve()
    if workspace_root not in target.parents and target != workspace_root:
        raise WorkspaceViolation("Path must stay inside the run workspace")
    return target


async def read_workspace_file(target: Path, *, display_path: str) -> str:
    if not target.exists():
        return f"Error: file not found: {display_path}"
    if not target.is_file():
        return f"Error: not a file: {display_path}"
    data = target.read_bytes()
    if len(data) > MAX_READ_BYTES:
        return data[:MAX_READ_BYTES].decode("utf-8", errors="replace") + "\n\n[... truncated ...]"
    return data.decode("utf-8", errors="replace")


async def write_workspace_file(target: Path, *, display_path: str, content: str) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = content.encode("utf-8")
    target.write_bytes(encoded)
    return f"wrote {len(encoded)} bytes to {display_path}"


async def list_workspace_path(target: Path, *, display_path: str) -> str:
    if not target.is_dir():
        return f"Error: not a directory: {display_path}"
    entries = [entry.name + ("/" if entry.is_dir() else "") for entry in sorted(target.iterdir())]
    return "\n".join(entries) if entries else "(empty directory)"


def build_step_tool_definitions(enabled: dict[str, bool]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    if enabled.get(TOOL_WRITE_FILE):
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": (
                        "Write a text file in this run's workspace (relative path only). "
                        "Use for notes, drafts, or structured output files."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["path", "content"],
                    },
                },
            }
        )
    if workspace_read_tools_enabled(enabled):
        _append_workspace_read_tool_definitions(tools)
    if enabled.get(TOOL_WEB_SEARCH):
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": (
                        "Search the public web using Brave Search. "
                        "Use count (default 10, max 200) when more results are needed."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "count": {
                                "type": "integer",
                                "default": 10,
                                "minimum": 1,
                                "maximum": 200,
                            },
                        },
                        "required": ["query"],
                    },
                },
            }
        )
    if enabled.get(TOOL_WEB_FETCH):
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "web_fetch",
                    "description": (
                        "Fetch a public http or https URL and return readable text from the page. "
                        "HTML pages are converted to plain text. Use max_chars to limit response size."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"},
                            "max_chars": {
                                "type": "integer",
                                "default": 50000,
                                "minimum": 1000,
                                "maximum": 100000,
                            },
                        },
                        "required": ["url"],
                    },
                },
            }
        )
    return tools


def _parse_tool_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return {}
        try:
            parsed = json.loads(stripped)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


class StepToolRegistry:
    def __init__(self, *, workspace_root: Path, brave_api_key: str = "") -> None:
        self._workspace_root = workspace_root
        self._brave_api_key = brave_api_key.strip()

    async def execute(self, tool_call: dict[str, Any]) -> dict[str, str]:
        fn = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
        name = str(fn.get("name") or "tool")
        args = _parse_tool_arguments(fn.get("arguments"))
        tool_call_id = str(tool_call.get("id") or name)

        try:
            if name == "write_file":
                target = resolve_workspace_path(self._workspace_root, str(args.get("path") or ""))
                content = await write_workspace_file(
                    target,
                    display_path=str(args.get("path") or ""),
                    content=str(args.get("content") or ""),
                )
            elif name == "read_file":
                target = resolve_workspace_path(self._workspace_root, str(args.get("path") or ""))
                content = await read_workspace_file(target, display_path=str(args.get("path") or ""))
            elif name == "list_files":
                rel = str(args.get("path") or ".").strip() or "."
                target = resolve_workspace_path(self._workspace_root, rel)
                content = await list_workspace_path(target, display_path=rel)
            elif name == "web_search":
                if not self._brave_api_key:
                    content = "Error: Brave Search API key is not configured in Factory Settings."
                else:
                    payload = await brave_web_search(
                        api_key=self._brave_api_key,
                        query=str(args.get("query") or ""),
                        count=int(args.get("count") or 10),
                    )
                    content = format_brave_results(payload)
            elif name == "web_fetch":
                payload = await fetch_web_page(
                    str(args.get("url") or ""),
                    max_chars=int(args.get("max_chars") or 50_000),
                )
                content = format_fetch_result(payload)
            else:
                content = f"Error: unknown tool: {name}"
        except WorkspaceViolation as exc:
            content = f"Error: {exc}"
        except Exception as exc:
            content = f"Error: {exc}"

        return {
            "role": "tool",
            "content": content,
            "tool_call_id": tool_call_id,
            "name": name,
        }
