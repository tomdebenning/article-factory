from __future__ import annotations

import argparse

import uvicorn

from article_factory.config import settings


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

    args = parser.parse_args()

    if args.command == "init-db":
        from article_factory.db import init_db

        init_db()
        print("Database initialized.")
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
        )
        return

    parser.print_help()


if __name__ == "__main__":
    main()
