from __future__ import annotations

from typing import Any

TOOL_LABELS: dict[str, str] = {
    "write_file": "Write file",
    "read_file": "Read file",
    "list_files": "List files",
    "web_search": "Web search",
    "web_fetch": "Web fetch",
}


def tool_label(name: str) -> str:
    return TOOL_LABELS.get(name, name.replace("_", " ").title() or "Tool")


def summarize_tool_detail(name: str, args: dict[str, Any]) -> str:
    if name == "web_search":
        query = str(args.get("query") or "").strip()
        count = args.get("count")
        if query and count:
            return f'"{query}" ({count} results)'
        if query:
            return f'"{query}"'
        return "search"
    if name == "web_fetch":
        url = str(args.get("url") or "").strip()
        return url[:120] if url else "url"
    if name == "write_file":
        path = str(args.get("path") or "").strip()
        return path or "file"
    if name == "read_file":
        return str(args.get("path") or "").strip() or "file"
    if name == "list_files":
        path = str(args.get("path") or ".").strip() or "."
        return path
    if args:
        first = next(iter(args.values()), "")
        text = str(first).strip()
        if text:
            return text[:80]
    return ""


def tool_use_entry(name: str, args: dict[str, Any], *, result: str = "", round_num: int = 1) -> dict[str, Any]:
    ok = not str(result).startswith("Error:")
    return {
        "tool": name,
        "label": tool_label(name),
        "detail": summarize_tool_detail(name, args),
        "round": round_num,
        "ok": ok,
    }


def aggregate_tool_use_by_step(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for step in steps:
        uses = step.get("tools_used") if isinstance(step.get("tools_used"), list) else []
        if not uses:
            continue
        tools: list[str] = []
        seen: set[str] = set()
        for entry in uses:
            if not isinstance(entry, dict):
                continue
            tool_name = str(entry.get("tool") or entry.get("name") or "").strip()
            if not tool_name or tool_name in seen:
                continue
            seen.add(tool_name)
            tools.append(tool_label(tool_name))
        if tools:
            summary.append(
                {
                    "step_key": str(step.get("step_key") or ""),
                    "step_name": str(step.get("step_name") or step.get("step_key") or ""),
                    "tools": tools,
                }
            )
    return summary


def flatten_tool_labels(summary: list[dict[str, Any]]) -> list[str]:
    labels: list[str] = []
    for entry in summary:
        step_name = str(entry.get("step_name") or entry.get("step_key") or "Step")
        for tool in entry.get("tools") or []:
            labels.append(f"{tool} ({step_name})")
    return labels


def unique_tool_labels(summary: list[dict[str, Any]]) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for entry in summary:
        for tool in entry.get("tools") or []:
            name = str(tool).strip()
            if name and name not in seen:
                seen.add(name)
                labels.append(name)
    return labels
