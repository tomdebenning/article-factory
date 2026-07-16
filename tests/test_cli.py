from __future__ import annotations

import os
import sys
import time
from unittest.mock import patch

import pytest

import article_factory.db as db_module
from article_factory.workers.runner import run_worker


def test_get_db_yields_and_closes(configured_db) -> None:
    from article_factory.db import get_db

    gen = get_db()
    db = next(gen)
    assert db is not None
    try:
        next(gen)
    except StopIteration:
        pass


def test_create_app_specific_cors(monkeypatch) -> None:
    monkeypatch.setattr("article_factory.config.settings.cors_origins", "http://localhost:5174")
    from article_factory.app import create_app

    app = create_app()
    assert app.title == "Article Factory"


def test_cli_init_db(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "cli.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    db_module.configure_engine(f"sqlite:///{db_path}")

    with patch.object(sys, "argv", ["article-factory", "init-db"]):
        from article_factory.__main__ import main

        main()
    db_module.init_db()


def test_cli_serve(monkeypatch) -> None:
    with patch.object(sys, "argv", ["article-factory", "serve", "--port", "8199"]):
        with patch("uvicorn.run") as mock_run:
            from article_factory.__main__ import main

            main()
            assert mock_run.call_args.kwargs["port"] == 8199


def test_cli_worker(monkeypatch) -> None:
    with patch.object(sys, "argv", ["article-factory", "worker", "writer"]):
        with patch("article_factory.workers.runner.run_worker") as mock_worker:
            from article_factory.__main__ import main

            main()
            mock_worker.assert_called_once_with("writer")


def test_cli_no_command(capsys) -> None:
    with patch.object(sys, "argv", ["article-factory"]):
        from article_factory.__main__ import main

        main()
    captured = capsys.readouterr()
    assert "article-factory" in captured.out or "usage" in captured.out.lower()



def test_run_worker_stub(monkeypatch) -> None:
    def stop_sleep(_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(time, "sleep", stop_sleep)
    with pytest.raises(KeyboardInterrupt):
        run_worker("writer")


def test_module_main_entrypoint(tmp_path, monkeypatch) -> None:
    import subprocess

    db_path = tmp_path / "module.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_path}"
    result = subprocess.run(
        [sys.executable, "-m", "article_factory", "init-db"],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "Database initialized" in result.stdout


def test_cli_telemetry_rebuild_run(configured_db, monkeypatch) -> None:
    from article_factory.models import FactoryRun
    from article_factory.services.flow_storage import create_flow
    from article_factory.services.flow_versions import create_flow_version

    rel_path, _flow = create_flow(folder="", slug="cli-telemetry", display_name="CLI", step_count=2)
    db = db_module.SessionLocal()
    try:
        version = create_flow_version(db, rel_path, message="v1")
        db.add(
            FactoryRun(
                run_id="run-cli-telemetry",
                topic_slug="general",
                flow_path=rel_path,
                flow_version_id=version.id,
                status="completed",
                manifest={"steps": [{"step_key": "step_1"}, {"step_key": "step_2", "content": "VERDICT: ACCEPT"}]},
            )
        )
        db.commit()
    finally:
        db.close()

    with patch.object(sys, "argv", ["article-factory", "telemetry", "rebuild", "--run-id", "run-cli-telemetry"]):
        from article_factory.__main__ import main

        main()


def test_cli_telemetry_rebuild_missing_args(capsys) -> None:
    with patch.object(sys, "argv", ["article-factory", "telemetry", "rebuild"]):
        with pytest.raises(SystemExit) as exc:
            from article_factory.__main__ import main

            main()
        assert exc.value.code == 2


def test_cli_telemetry_rebuild_flow(configured_db) -> None:
    from article_factory.services.flow_storage import create_flow
    from article_factory.services.flow_versions import create_flow_version

    rel_path, _flow = create_flow(folder="", slug="cli-flow-tel", display_name="CLI Flow", step_count=2)
    db = db_module.SessionLocal()
    try:
        version = create_flow_version(db, rel_path, message="v1")
        db.commit()
        version_id = version.id
    finally:
        db.close()

    with patch.object(
        sys,
        "argv",
        [
            "article-factory",
            "telemetry",
            "rebuild",
            "--flow-path",
            rel_path,
            "--flow-version-id",
            str(version_id),
        ],
    ):
        from article_factory.__main__ import main

        main()
