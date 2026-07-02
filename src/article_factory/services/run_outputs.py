from __future__ import annotations

from pathlib import Path

from article_factory.services.flow_storage import run_outputs_root


def _run_steps_dir(run_id: str) -> Path:
    return run_outputs_root() / run_id.strip() / "steps"


def list_run_step_files(run_id: str) -> list[dict[str, str | int]]:
    folder = _run_steps_dir(run_id)
    if not folder.is_dir():
        return []
    files: list[dict[str, str | int]] = []
    for entry in sorted(folder.iterdir(), key=lambda item: item.name):
        if not entry.is_file() or entry.suffix.lower() != ".md":
            continue
        stat = entry.stat()
        files.append(
            {
                "name": entry.name,
                "path": entry.name,
                "size_bytes": stat.st_size,
            }
        )
    return files


def read_run_step_file(run_id: str, filename: str) -> str:
    safe_name = Path(filename).name
    if safe_name != filename or not safe_name.endswith(".md"):
        raise ValueError("Invalid step file name")
    target = _run_steps_dir(run_id) / safe_name
    if not target.is_file():
        raise FileNotFoundError(safe_name)
    return target.read_text(encoding="utf-8")
