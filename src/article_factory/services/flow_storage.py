from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from article_factory.config import settings
from article_factory.services.flow_schema import (
    FlowDefinition,
    flow_from_dict,
    flow_to_dict,
    new_flow_definition,
    strip_runtime_overrides,
)

FLOW_SUFFIX = ".flow.json"
TEMPLATES_FOLDER = "_templates"


def flows_root() -> Path:
    root = Path(settings.flows_root)
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def run_outputs_root() -> Path:
    root = Path(settings.flow_run_outputs_root)
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _resolve_under_root(rel_path: str) -> Path:
    rel = rel_path.strip().replace("\\", "/").lstrip("/")
    if not rel:
        return flows_root()
    target = (flows_root() / rel).resolve()
    root = flows_root()
    if target != root and root not in target.parents:
        raise ValueError("Path escapes flows root")
    return target


def is_flow_file(path: Path) -> bool:
    return path.is_file() and path.name.endswith(FLOW_SUFFIX)


def normalize_flow_rel_path(rel_path: str) -> str:
    rel = rel_path.strip().replace("\\", "/").lstrip("/")
    if not rel.endswith(FLOW_SUFFIX):
        rel = f"{rel}{FLOW_SUFFIX}"
    return rel


def _tree_node_for_folder(folder: Path, *, is_root: bool = False) -> dict[str, Any]:
    children: list[dict[str, Any]] = []
    for entry in sorted(folder.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        if entry.name.startswith("."):
            continue
        child_rel = entry.relative_to(flows_root()).as_posix()
        if entry.is_dir():
            children.append(_tree_node_for_folder(entry))
        elif is_flow_file(entry):
            stat = entry.stat()
            children.append(
                {
                    "name": entry.name,
                    "path": child_rel,
                    "type": "file",
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    "size_bytes": stat.st_size,
                }
            )

    return {
        "name": "flows" if is_root else folder.name,
        "path": "" if is_root else folder.relative_to(flows_root()).as_posix(),
        "type": "folder",
        "children": children,
    }


def list_tree(rel_path: str = "") -> dict[str, Any]:
    folder = _resolve_under_root(rel_path)
    if not folder.exists():
        raise FileNotFoundError(rel_path or "/")
    if not folder.is_dir():
        raise NotADirectoryError(rel_path)
    is_root = folder == flows_root()
    return _tree_node_for_folder(folder, is_root=is_root)


def read_flow(rel_path: str) -> FlowDefinition:
    target = _resolve_under_root(normalize_flow_rel_path(rel_path))
    if not target.is_file():
        raise FileNotFoundError(rel_path)
    data = json.loads(target.read_text(encoding="utf-8"))
    return flow_from_dict(data)


def write_flow(rel_path: str, flow: FlowDefinition) -> FlowDefinition:
    rel = normalize_flow_rel_path(rel_path)
    target = _resolve_under_root(rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    cleaned = strip_runtime_overrides(flow)
    payload = flow_to_dict(cleaned)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return cleaned


def delete_flow(rel_path: str) -> None:
    target = _resolve_under_root(normalize_flow_rel_path(rel_path))
    if not target.is_file():
        raise FileNotFoundError(rel_path)
    target.unlink()


def create_folder(rel_path: str) -> dict[str, str]:
    folder = _resolve_under_root(rel_path.strip("/"))
    folder.mkdir(parents=True, exist_ok=False)
    return {"path": folder.relative_to(flows_root()).as_posix()}


def delete_folder(rel_path: str) -> None:
    folder = _resolve_under_root(rel_path.strip("/"))
    if folder == flows_root():
        raise ValueError("Cannot delete flows root")
    if not folder.exists():
        raise FileNotFoundError(rel_path)
    if not folder.is_dir():
        raise NotADirectoryError(rel_path)
    if any(folder.iterdir()):
        raise ValueError("Folder is not empty")
    folder.rmdir()


def duplicate_flow(rel_path: str, *, slug: str | None = None, display_name: str | None = None) -> tuple[str, FlowDefinition]:
    source = normalize_flow_rel_path(rel_path)
    flow = read_flow(source)
    source_path = Path(source)
    folder_part = source_path.parent.as_posix() if source_path.parent != Path(".") else ""
    new_slug = (slug or f"{flow.slug}-copy").strip()
    new_name = display_name or f"{flow.display_name} (copy)"
    rel = normalize_flow_rel_path(f"{folder_part}/{new_slug}" if folder_part else new_slug)
    target = _resolve_under_root(rel)
    if target.exists():
        raise FileExistsError(rel)

    steps = []
    old_steps = sorted(flow.steps, key=lambda item: item.order)
    id_map: dict[str, str] = {}
    for step in old_steps:
        copied = step.model_copy(deep=True)
        old_id = copied.step_id
        copied.step_id = str(uuid.uuid4())
        id_map[old_id] = copied.step_id
        steps.append(copied)

    for step in steps:
        if step.loop and step.loop.goto_step_id:
            step.loop.goto_step_id = id_map.get(step.loop.goto_step_id, step.loop.goto_step_id)
        if step.completion and step.completion.loop_goto_step_id:
            step.completion.loop_goto_step_id = id_map.get(
                step.completion.loop_goto_step_id,
                step.completion.loop_goto_step_id,
            )

    article_step_id = id_map.get(flow.article_step_id or "", flow.article_step_id)
    duplicated = FlowDefinition(
        slug=new_slug,
        display_name=new_name,
        max_iterations=flow.max_iterations,
        article_step_id=article_step_id,
        steps=steps,
    )
    write_flow(rel, duplicated)
    return rel, duplicated


def is_template_path(rel_path: str) -> bool:
    normalized = rel_path.strip().replace("\\", "/").lstrip("/")
    return normalized == TEMPLATES_FOLDER or normalized.startswith(f"{TEMPLATES_FOLDER}/")


def move_flow(
    rel_path: str,
    *,
    folder: str,
    slug: str | None = None,
) -> tuple[str, FlowDefinition]:
    """Move a flow file to another folder. Moves out of _templates are supported; moves into _templates are blocked."""
    source = normalize_flow_rel_path(rel_path)
    source_target = _resolve_under_root(source)
    if not source_target.is_file():
        raise FileNotFoundError(rel_path)

    dest_folder = folder.strip().replace("\\", "/").strip("/")
    if dest_folder == TEMPLATES_FOLDER or dest_folder.startswith(f"{TEMPLATES_FOLDER}/"):
        raise ValueError("Cannot move flows into _templates")

    flow = read_flow(source)
    source_path = Path(source)
    file_slug = (slug or flow.slug or source_path.name.replace(".flow.json", "")).strip()
    if not file_slug:
        raise ValueError("slug is required")

    dest_rel = normalize_flow_rel_path(f"{dest_folder}/{file_slug}" if dest_folder else file_slug)
    dest_target = _resolve_under_root(dest_rel)
    if dest_target.exists():
        raise FileExistsError(dest_rel)
    if dest_target.resolve() == source_target.resolve():
        raise ValueError("Flow is already at that location")

    moved = flow.model_copy(deep=True)
    moved.slug = file_slug
    write_flow(dest_rel, moved)
    delete_flow(source)
    return dest_rel, read_flow(dest_rel)


def _flow_catalog_entry(child: dict[str, Any]) -> dict[str, Any] | None:
    path = str(child.get("path") or "")
    if not path:
        return None
    fallback_slug = Path(path).name.replace(".flow.json", "").replace(".flow", "")
    try:
        target = _resolve_under_root(normalize_flow_rel_path(path))
        raw = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("flow JSON must be an object")
        display_name = str(raw.get("display_name") or "").strip() or fallback_slug
        slug = str(raw.get("slug") or "").strip() or fallback_slug
        steps = raw.get("steps")
        step_count = len(steps) if isinstance(steps, list) else 0
        beat_brief = str(raw.get("beat_brief") or "").strip()
        entry = {
            "path": path,
            "display_name": display_name,
            "slug": slug,
            "step_count": step_count,
            "modified_at": child.get("modified_at"),
        }
        if beat_brief:
            entry["beat_brief"] = beat_brief
        return entry
    except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError):
        return {
            "path": path,
            "display_name": child.get("name", path),
            "slug": fallback_slug,
            "step_count": 0,
            "modified_at": child.get("modified_at"),
        }


def list_folder_flows(rel_path: str = "") -> list[dict[str, Any]]:
    folder_path = rel_path.strip("/")

    if folder_path:
        node = list_tree(folder_path)
        return [
            entry
            for child in node.get("children") or []
            if child.get("type") == "file"
            for entry in [_flow_catalog_entry(child)]
            if entry is not None
        ]

    node = list_tree("")
    flows: list[dict[str, Any]] = []

    def walk(folder: dict[str, Any]) -> None:
        for child in folder.get("children") or []:
            if child.get("type") == "file":
                entry = _flow_catalog_entry(child)
                if entry is not None:
                    flows.append(entry)
            elif child.get("type") == "folder":
                walk(child)

    walk(node)
    return flows


def create_flow(*, folder: str, slug: str, display_name: str, step_count: int) -> tuple[str, FlowDefinition]:
    if step_count < 1 or step_count > 50:
        raise ValueError("step_count must be between 1 and 50")
    flow = new_flow_definition(slug=slug, display_name=display_name, step_count=step_count)
    rel = normalize_flow_rel_path(f"{folder.strip('/')}/{slug}" if folder.strip("/") else slug)
    target = _resolve_under_root(rel)
    if target.exists():
        raise FileExistsError(rel)
    write_flow(rel, flow)
    return rel, flow


def save_step_response_to_disk(*, run_id: str, step_order: int, step_key: str, content: str) -> Path:
    folder = run_outputs_root() / run_id / "steps"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{step_order:02d}-{step_key}.md"
    path.write_text(content or "", encoding="utf-8")
    return path


def ensure_default_flows() -> str:
    sports_dir = flows_root() / "sports"
    sports_dir.mkdir(parents=True, exist_ok=True)
    rel = "sports/standard-4-step.flow.json"
    target = flows_root() / rel
    if not target.exists():
        from article_factory.services.flow_defaults import build_standard_sports_flow

        flow = build_standard_sports_flow()
        write_flow(rel, flow)
    ensure_default_templates()
    return rel


def ensure_default_templates() -> None:
    from article_factory.services.flow_defaults import (
        build_ai_news_desk_flow,
        build_business_news_desk_flow,
        build_single_writer_flow,
        build_sports_desk_flow,
        build_standard_sports_flow,
        build_tech_news_desk_flow,
        build_writer_review_flow,
    )

    templates_dir = flows_root() / TEMPLATES_FOLDER
    templates_dir.mkdir(parents=True, exist_ok=True)
    seeds = [
        ("standard-4-step.flow.json", build_standard_sports_flow),
        ("sports.flow.json", build_sports_desk_flow),
        ("business-news.flow.json", build_business_news_desk_flow),
        ("tech-news.flow.json", build_tech_news_desk_flow),
        ("ai-news.flow.json", build_ai_news_desk_flow),
        ("single-writer.flow.json", build_single_writer_flow),
        ("writer-review.flow.json", build_writer_review_flow),
    ]
    for filename, builder in seeds:
        target = templates_dir / filename
        if target.exists():
            continue
        flow = builder()
        write_flow(f"{TEMPLATES_FOLDER}/{filename}", flow)


def list_templates() -> list[dict[str, Any]]:
    folder = flows_root() / TEMPLATES_FOLDER
    if not folder.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    for entry in sorted(folder.iterdir(), key=lambda item: item.name.lower()):
        if not is_flow_file(entry):
            continue
        rel = entry.relative_to(flows_root()).as_posix()
        stat = entry.stat()
        entry_meta = _flow_catalog_entry(
            {
                "path": rel,
                "name": entry.name,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            }
        )
        if entry_meta is None:
            continue
        entries.append(entry_meta)
    return entries


def create_flow_from_template(
    *,
    template_path: str,
    folder: str,
    slug: str,
    display_name: str,
) -> tuple[str, FlowDefinition]:
    source = normalize_flow_rel_path(template_path)
    if not source.startswith(f"{TEMPLATES_FOLDER}/"):
        raise ValueError("Template path must be under _templates/")
    flow = read_flow(source)
    rel = normalize_flow_rel_path(f"{folder.strip('/')}/{slug}" if folder.strip("/") else slug)
    target = _resolve_under_root(rel)
    if target.exists():
        raise FileExistsError(rel)

    steps = []
    old_steps = sorted(flow.steps, key=lambda item: item.order)
    id_map: dict[str, str] = {}
    for step in old_steps:
        copied = step.model_copy(deep=True)
        old_id = copied.step_id
        copied.step_id = str(uuid.uuid4())
        id_map[old_id] = copied.step_id
        steps.append(copied)

    for step in steps:
        if step.loop and step.loop.goto_step_id:
            step.loop.goto_step_id = id_map.get(step.loop.goto_step_id, step.loop.goto_step_id)
        if step.completion and step.completion.loop_goto_step_id:
            step.completion.loop_goto_step_id = id_map.get(
                step.completion.loop_goto_step_id,
                step.completion.loop_goto_step_id,
            )

    article_step_id = id_map.get(flow.article_step_id or "", flow.article_step_id)
    created = FlowDefinition(
        slug=slug,
        display_name=display_name,
        max_iterations=flow.max_iterations,
        article_step_id=article_step_id,
        steps=steps,
    )
    write_flow(rel, created)
    return rel, created


def import_flow(
    flow: FlowDefinition,
    *,
    folder: str,
    slug: str | None = None,
    overwrite: bool = False,
) -> str:
    file_slug = (slug or flow.slug).strip()
    if not file_slug:
        raise ValueError("slug is required")
    rel = normalize_flow_rel_path(f"{folder.strip('/')}/{file_slug}" if folder.strip("/") else file_slug)
    target = _resolve_under_root(rel)
    if target.exists() and not overwrite:
        raise FileExistsError(rel)
    imported = flow.model_copy(deep=True)
    imported.slug = file_slug
    write_flow(rel, imported)
    return rel


def export_flow(rel_path: str) -> dict[str, Any]:
    flow = read_flow(rel_path)
    return {
        "path": normalize_flow_rel_path(rel_path),
        "flow": flow_to_dict(flow),
    }
