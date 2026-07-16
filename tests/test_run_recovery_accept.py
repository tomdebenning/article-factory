from __future__ import annotations

from article_factory.models import FactoryRun
from article_factory.services.run_recovery import build_recovery_from_missed_accept
from article_factory.services.verdict import Verdict, parse_verdict


def test_parse_verdict_markdown_heading_in_run_tail() -> None:
    text = "Score: 90/100\n\n---\n\n## VERDICT: ACCEPT"
    assert parse_verdict(text) == Verdict.ACCEPT


def test_build_recovery_from_missed_accept(configured_db) -> None:
    import article_factory.db as db_module

    from article_factory.models import StepExecution

    db = db_module.SessionLocal()
    try:
        run = FactoryRun(
            run_id="recover-test",
            topic_slug="general",
            flow_path="test/writer-review-copy.flow.json",
            status="failed",
            error="Last step response missing VERDICT: ACCEPT or VERDICT: REJECT",
        )
        db.add(run)
        db.flush()
        db.add(
            StepExecution(
                run_id="recover-test",
                step_key="writer",
                status="completed",
                response_content="# Article\n\nBody text.",
                turns=1,
            )
        )
        db.add(
            StepExecution(
                run_id="recover-test",
                step_key="review",
                status="completed",
                response_content="Looks good.\n\n## VERDICT: ACCEPT",
                turns=1,
            )
        )
        db.commit()

        recovery = build_recovery_from_missed_accept(db, run)
        assert recovery is not None
        draft, records = recovery
        assert "Article" in draft
        assert len(records) == 2
    finally:
        db.close()
