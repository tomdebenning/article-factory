from __future__ import annotations

from pathlib import Path

from article_factory.services.step_tools import run_workspace_root

MAX_ATTACHMENT_BYTES = 512 * 1024


def _guess_content_type(path: str) -> str:
    lowered = path.lower()
    if lowered.endswith((".md", ".markdown")):
        return "text/markdown"
    if lowered.endswith(".json"):
        return "application/json"
    if lowered.endswith(".csv"):
        return "text/csv"
    if lowered.endswith(".html"):
        return "text/html"
    return "text/plain"


def _is_text_file(data: bytes) -> bool:
    if not data:
        return True
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _iter_run_workspace_files(run_id: str) -> list[tuple[Path, str, bytes]]:
    root = run_workspace_root(run_id)
    if not root.is_dir():
        return []

    files: list[tuple[Path, str, bytes]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        data = path.read_bytes()
        if len(data) > MAX_ATTACHMENT_BYTES:
            continue
        if not _is_text_file(data):
            continue
        files.append((path, relative, data))
    return files


def list_run_workspace_attachment_summaries(run_id: str) -> list[dict]:
    """List text files from a run workspace without loading full content."""
    return [
        {
            "path": relative,
            "filename": path.name,
            "content_type": _guess_content_type(relative),
            "size_bytes": len(data),
        }
        for path, relative, data in _iter_run_workspace_files(run_id)
    ]


def read_run_workspace_file(run_id: str, relative_path: str) -> dict:
    root = run_workspace_root(run_id)
    safe_path = Path(relative_path.strip().replace("\\", "/")).as_posix().lstrip("/")
    if not safe_path or ".." in Path(safe_path).parts:
        raise ValueError("Invalid workspace path")
    target = (root / safe_path).resolve()
    if root not in target.parents or not target.is_file():
        raise FileNotFoundError(safe_path)
    data = target.read_bytes()
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise ValueError("Workspace file is too large to preview")
    if not _is_text_file(data):
        raise ValueError("Only text workspace files can be previewed")
    return {
        "path": safe_path,
        "filename": target.name,
        "content_type": _guess_content_type(safe_path),
        "size_bytes": len(data),
        "content": data.decode("utf-8"),
    }


def collect_run_workspace_attachments(run_id: str) -> list[dict]:
    """Collect text files from a run workspace for Showroom publish."""
    attachments: list[dict] = []
    for path, relative, data in _iter_run_workspace_files(run_id):
        attachments.append(
            {
                "path": relative,
                "filename": path.name,
                "content": data.decode("utf-8"),
                "content_type": _guess_content_type(relative),
                "size_bytes": len(data),
            }
        )
    return attachments
