from __future__ import annotations

from pathlib import Path

from article_factory.config import settings
from article_factory.services.run_attachments import (
    collect_run_workspace_attachments,
    list_run_workspace_attachment_summaries,
    read_run_workspace_file,
)


def test_collect_run_workspace_attachments(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "flow_run_outputs_root", str(tmp_path))
    run_id = "run-files"
    workspace = tmp_path / run_id / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "notes.txt").write_text("plain notes", encoding="utf-8")
    nested = workspace / "research"
    nested.mkdir()
    (nested / "summary.md").write_text("# Summary\n\nDetails", encoding="utf-8")
    (workspace / ".hidden").write_text("skip", encoding="utf-8")
    (workspace / "binary.bin").write_bytes(b"\x00\xff")

    attachments = collect_run_workspace_attachments(run_id)
    assert len(attachments) == 2
    assert attachments[0]["path"] == "notes.txt"
    assert attachments[0]["content"] == "plain notes"
    assert attachments[0]["content_type"] == "text/plain"
    assert attachments[1]["path"] == "research/summary.md"
    assert attachments[1]["content_type"] == "text/markdown"


def test_collect_run_workspace_attachments_empty_when_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "flow_run_outputs_root", str(tmp_path))
    assert collect_run_workspace_attachments("run-missing") == []


def test_list_run_workspace_attachment_summaries(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "flow_run_outputs_root", str(tmp_path))
    run_id = "run-files"
    workspace = tmp_path / run_id / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "notes.txt").write_text("plain notes", encoding="utf-8")

    summaries = list_run_workspace_attachment_summaries(run_id)
    assert len(summaries) == 1
    assert summaries[0]["path"] == "notes.txt"
    assert summaries[0]["size_bytes"] == len("plain notes")
    assert "content" not in summaries[0]

    payload = read_run_workspace_file(run_id, "notes.txt")
    assert payload["content"] == "plain notes"
