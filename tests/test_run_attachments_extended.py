from __future__ import annotations

from pathlib import Path

import pytest

from article_factory.config import settings
from article_factory.services.run_attachments import (
    collect_run_workspace_attachments,
    list_run_workspace_attachment_summaries,
    read_run_workspace_file,
)


def test_guess_content_types(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "flow_run_outputs_root", str(tmp_path))
    run_id = "run-types"
    workspace = tmp_path / run_id / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "data.json").write_text('{"a": 1}', encoding="utf-8")
    (workspace / "sheet.csv").write_text("a,b\n1,2", encoding="utf-8")
    (workspace / "page.html").write_text("<p>hi</p>", encoding="utf-8")

    summaries = list_run_workspace_attachment_summaries(run_id)
    types = {s["path"]: s["content_type"] for s in summaries}
    assert types["data.json"] == "application/json"
    assert types["sheet.csv"] == "text/csv"
    assert types["page.html"] == "text/html"


def test_skip_large_and_binary_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "flow_run_outputs_root", str(tmp_path))
    run_id = "run-skip"
    workspace = tmp_path / run_id / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "huge.txt").write_text("x" * (600 * 1024), encoding="utf-8")
    (workspace / "bad.bin").write_bytes(b"\x00\xff\xfe")

    assert collect_run_workspace_attachments(run_id) == []


def test_read_run_workspace_file_errors(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "flow_run_outputs_root", str(tmp_path))
    run_id = "run-read"
    workspace = tmp_path / run_id / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "ok.txt").write_text("ok", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid workspace path"):
        read_run_workspace_file(run_id, "../escape.txt")

    with pytest.raises(FileNotFoundError):
        read_run_workspace_file(run_id, "missing.txt")

    large = workspace / "large.txt"
    large.write_text("a" * (600 * 1024), encoding="utf-8")
    with pytest.raises(ValueError, match="too large"):
        read_run_workspace_file(run_id, "large.txt")

    (workspace / "binary.bin").write_bytes(b"\x00\x01")
    with pytest.raises(ValueError, match="Only text"):
        read_run_workspace_file(run_id, "binary.bin")


def test_empty_file_is_text(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "flow_run_outputs_root", str(tmp_path))
    run_id = "run-empty"
    workspace = tmp_path / run_id / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "empty.txt").write_bytes(b"")

    payload = read_run_workspace_file(run_id, "empty.txt")
    assert payload["content"] == ""
    assert payload["size_bytes"] == 0
