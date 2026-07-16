from __future__ import annotations

import argparse
import logging
import sys

import uvicorn

from article_factory.config import settings

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Article Factory")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="Run API server")
    serve.add_argument("--host", default=settings.host)
    serve.add_argument("--port", type=int, default=settings.port)

    sub.add_parser("init-db", help="Create database tables")

    worker = sub.add_parser("worker", help="Run a step worker process")
    worker.add_argument(
        "step_key",
        choices=["writer", "source_finder", "fact_asserter", "review"],
    )

    telemetry = sub.add_parser("telemetry", help="Telemetry utilities")
    telemetry_sub = telemetry.add_subparsers(dest="telemetry_command", required=True)
    rebuild = telemetry_sub.add_parser("rebuild", help="Rebuild telemetry from stored runs")
    rebuild.add_argument("--flow-path", default="")
    rebuild.add_argument("--flow-version-id", type=int, default=0)
    rebuild.add_argument("--run-id", default="")

    args = parser.parse_args()

    if args.command == "init-db":
        from article_factory.db import init_db

        init_db()
        print("Database initialized.")
        return

    if args.command == "telemetry" and args.telemetry_command == "rebuild":
        from article_factory.db import SessionLocal
        from article_factory.services.telemetry import capture_run_telemetry, rebuild_flow_telemetry

        db = SessionLocal()
        try:
            if args.run_id:
                row = capture_run_telemetry(db, args.run_id.strip())
                if row is None:
                    print(f"No telemetry captured for {args.run_id}")
                    sys.exit(1)
                print(f"Rebuilt telemetry for {args.run_id}")
                return
            if not args.flow_path or not args.flow_version_id:
                print("Provide --flow-path and --flow-version-id, or --run-id")
                sys.exit(2)
            stats = rebuild_flow_telemetry(db, args.flow_path, args.flow_version_id)
            print(
                "Rebuild complete: "
                f"parsed={stats['parsed']} skipped={stats['skipped']} "
                f"warnings={stats['warnings']} failed={stats['failed']} total={stats['total']}"
            )
        finally:
            db.close()
        return

    if args.command == "worker":
        from article_factory.workers.runner import run_worker

        run_worker(args.step_key)
        return

    if args.command == "serve":
        uvicorn.run(
            "article_factory.app:create_app",
            factory=True,
            host=args.host,
            port=args.port,
            reload=False,
            proxy_headers=settings.trust_proxy_headers,
            forwarded_allow_ips="127.0.0.1" if settings.trust_proxy_headers else None,
        )
        return

    parser.print_help()


if __name__ == "__main__":
    main()
