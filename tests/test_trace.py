from __future__ import annotations

from unittest.mock import AsyncMock, patch

from article_factory.models import FactoryRun, StepExecution
from article_factory.services.step_trace import (
    StepTracer,
    enrich_steps_with_responses,
    list_step_executions,
    step_execution_to_dict,
)


def test_step_tracer_lifecycle(configured_db) -> None:
    import article_factory.db as db_module

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-trace", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="run-trace", step_key="writer", puller="p1", model="m1")
        tracer.mark_submitted(agent_id="factory-worker-writer", conversation_id="conv-1", queue_depth=2)
        tracer.mark_waiting()
        tracer.mark_pulled()
        tracer.mark_completed(
            response_content="# Article\n\nBody text.",
            usage={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            duration_ms=2500,
        )
        step = db.query(StepExecution).filter_by(run_id="run-trace").one()
        data = step_execution_to_dict(step)
        assert data["status"] == "completed"
        assert data["response_content"] == "# Article\n\nBody text."
        assert data["cp_queue_depth"] == 2
        assert data["pulled_at"] is not None
        assert data["duration_ms"] == 2500
        assert data["usage"]["input_tokens"] == 100
        assert data["usage"]["total_tokens"] == 150
    finally:
        db.close()


def test_step_tracer_live_progress(configured_db) -> None:
    import article_factory.db as db_module

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-live", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="run-live", step_key="writer", puller="p1", model="m1")
        tracer.mark_submitted(agent_id="a", conversation_id="conv-1", queue_depth=1, cp_round=1)
        tracer.mark_pulled()
        tracer.record_cp_round(
            cp_round=2,
            agent_id="a",
            conversation_id="conv-2",
            queue_depth=2,
        )
        tracer.record_tool_start("web_search", {"query": "Cowboys preseason"}, round_num=2)
        tracer.append_tool_use(
            {
                "tool": "web_search",
                "label": "Web search",
                "detail": '"Cowboys preseason"',
                "round": 2,
                "ok": True,
            }
        )
        step = db.query(StepExecution).filter_by(run_id="run-live").one()
        data = step_execution_to_dict(step)
        assert data["progress"]["activity"] == "Used Web search"
        assert data["progress"]["cp_round"] == 2
        assert len(data["tools_used"]) == 1
        assert data["tools_used"][0]["tool"] == "web_search"
        assert data["conversation_id"] == "conv-2"
    finally:
        db.close()


def test_step_tracer_waiting_and_failed(configured_db) -> None:
    import article_factory.db as db_module

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-wait", topic_slug="sports", status="running"))
        db.commit()
        tracer = StepTracer(db, run_id="run-wait", step_key="writer", puller="p1", model="m1")
        tracer.mark_submitted(agent_id="a", conversation_id="c", queue_depth=1)
        tracer.mark_waiting()
        assert tracer.execution.status == "waiting"
        tracer.mark_pulled()
        assert tracer.execution.status == "pulled"
        tracer.mark_failed("boom")
        assert tracer.execution.status == "failed"
        assert tracer.execution.error == "boom"

        steps = list_step_executions(db, "run-wait")
        assert len(steps) == 1
    finally:
        db.close()


def test_list_control_plane_pullers(client, api_headers) -> None:
    mock_pullers = [
        {
            "puller_name": "gpu-01",
            "status": "ok",
            "supported_models": ["llama3", "mistral"],
            "is_active": True,
            "is_stale": False,
        }
    ]

    with patch("article_factory.routes.admin.ControlPlaneClient") as mock_cls:
        mock_cls.return_value.list_pullers = AsyncMock(return_value=mock_pullers)
        response = client.get("/api/control-plane/pullers", headers=api_headers)

    assert response.status_code == 200
    assert response.json()["pullers"][0]["puller_name"] == "gpu-01"
    assert "llama3" in response.json()["pullers"][0]["supported_models"]


def test_get_run_with_steps(client, api_headers, configured_db) -> None:
    import article_factory.db as db_module

    db = db_module.SessionLocal()
    try:
        db.add(FactoryRun(run_id="run-detail", topic_slug="sports", status="running", current_step="writer"))
        db.add(
            StepExecution(
                run_id="run-detail",
                step_key="writer",
                status="submitted",
                puller="gpu-01",
                model="llama3",
                agent_id="factory-worker-writer",
                conversation_id="conv-x",
                cp_queue_depth=1,
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.get("/api/runs/missing-run", headers=api_headers)
    assert response.status_code == 404

    response = client.get("/api/runs/run-detail", headers=api_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["run"]["run_id"] == "run-detail"
    assert body["steps"][0]["status"] == "submitted"


def test_list_control_plane_pullers_error(client, api_headers) -> None:
    with patch("article_factory.routes.admin.ControlPlaneClient") as mock_cls:
        mock_cls.return_value.list_pullers = AsyncMock(side_effect=RuntimeError("down"))
        response = client.get("/api/control-plane/pullers", headers=api_headers)

    assert response.status_code == 502


def test_enrich_steps_with_responses_from_pipeline_state(configured_db) -> None:
    import article_factory.db as db_module

    db = db_module.SessionLocal()
    try:
        db.add(
            FactoryRun(
                run_id="run-enrich",
                topic_slug="sports",
                status="running",
                pipeline_state={
                    "step_records": [
                        {"step_key": "writer", "content": "Recovered draft"},
                    ]
                },
            )
        )
        db.add(
            StepExecution(
                run_id="run-enrich",
                step_key="writer",
                status="completed",
            )
        )
        db.commit()
        steps = enrich_steps_with_responses(
            db,
            "run-enrich",
            [step_execution_to_dict(db.query(StepExecution).one())],
        )
        assert steps[0]["response_content"] == "Recovered draft"
    finally:
        db.close()
