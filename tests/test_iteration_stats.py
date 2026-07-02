from __future__ import annotations

from article_factory.services.iteration_stats import attach_iteration_metadata, build_iteration_stats


def _writer(content: str = "draft") -> dict:
    return {
        "step_key": "writer",
        "step_name": "Writer",
        "content": content,
        "duration_ms": 1000,
        "usage": {"input_tokens": 100, "output_tokens": 200, "total_tokens": 300},
    }


def _review(content: str) -> dict:
    return {
        "step_key": "review",
        "step_name": "Review",
        "content": content,
        "duration_ms": 500,
        "usage": {"input_tokens": 50, "output_tokens": 50, "total_tokens": 100},
    }


def test_build_iteration_stats_single_pass() -> None:
    steps = [_writer(), _review("Looks good.\n\nVERDICT: ACCEPT")]
    iterations = build_iteration_stats(steps)
    assert len(iterations) == 1
    assert iterations[0]["accepted"] is True
    assert iterations[0]["stats"]["total_tokens"] == 400


def test_build_iteration_stats_multi_pass() -> None:
    steps = [
        _writer("v1"),
        _review("Fix intro.\n\nVERDICT: REJECT"),
        _writer("v2"),
        _review("Better.\n\n**VERDICT: ACCEPT**"),
    ]
    iterations = build_iteration_stats(steps)
    assert len(iterations) == 2
    assert iterations[0]["accepted"] is False
    assert iterations[0]["verdict"] == "reject"
    assert iterations[1]["accepted"] is True
    assert iterations[1]["verdict"] == "accept"


def test_attach_iteration_metadata_computes_production_summary() -> None:
    steps = [
        _writer("v1"),
        _review("Fix intro.\n\nVERDICT: REJECT"),
        _writer("v2"),
        _review("Better.\n\nVERDICT: ACCEPT"),
    ]
    manifest = attach_iteration_metadata({"steps": steps, "draft_number": 0, "review_round": 0})
    assert manifest["production"]["multi_pass"] is True
    assert manifest["production"]["draft_count"] == 2
    assert manifest["production"]["iteration_count"] == 2
    assert manifest["draft_number"] == 2
    assert manifest["review_round"] == 1
    assert len(manifest["iteration_stats"]) == 2
