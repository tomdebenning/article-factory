# Article Factory

Headless edition factory: orchestrates dynamic multi-step flows via **control plane** and **lightweight step workers**. Publishes finished articles and live factory status to [Showroom CMS](../showroom-cms/).

## Architecture

```
Control plane ←── Article Factory only (tasks, responses, gateway heartbeats)
                      │
                      ├── Showroom CMS (status, events, publish — HTTP only)
                      └── Topic queue → flow JSON → step runner → pullers
```

- **Control plane**: only the factory connects — submit tasks, poll responses, register as gateway.
- **Showroom CMS**: receives published articles and live status from the factory; it never calls the control plane.
- **Flows**: prompt pipelines stored as `.flow.json` files under `data/flows/` (folder tree, templates, import/export).
- **Workers**: one logical role per step; each step's prompts live in the flow file.
- **Admin UI**: browse/edit flows, queue topics, pick flow + Showroom category, review runs.

## Flows (prompt pipelines)

Flows live on disk, e.g.:

```
data/flows/
├── _templates/          # starter templates (not run directly)
├── sports/
│   └── standard-4-step.flow.json
└── test/
    └── SimpleTest.flow.json
```

Each flow defines numbered steps with stable `step_id` values, loop routing, optional save-to-disk, and last-step complete/loop behavior. Configure the **default flow** in Settings; queue items can override per batch.

Step outputs saved to disk appear under `data/runs/{run_id}/steps/` and are browsable on the run detail page.

## Quick start

```bash
cd article-factory
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Edit CONTROL_PLANE_URL, CMS_URL, CMS_API_KEY, FACTORY_API_KEY

# API + orchestrator + admin UI
./run.sh --local
```

Default ports: API `8100`, admin UI `5174` (proxies `/api` → 8100).

## Environment

| Variable | Description |
|----------|-------------|
| `CONTROL_PLANE_URL` | Task puller control plane base URL |
| `CMS_URL` | Showroom CMS base URL |
| `CMS_API_KEY` | Key for factory → CMS internal API |
| `FACTORY_API_KEY` | Key for admin UI / API |
| `DATABASE_URL` | Default `sqlite:///./data/factory.db` |
| `FLOWS_ROOT` | Flow JSON root (default `./data/flows`) |
| `FLOW_RUN_OUTPUTS_ROOT` | Saved step markdown (default `./data/runs`) |

## CMS integration

Factory calls Showroom CMS internal endpoints:

- `PUT /internal/factory/status` — heartbeat + active run
- `POST /internal/runs/events` — step lifecycle (optional)
- `POST /internal/runs/complete` — article + manifest on publish

See [showroom-cms/README.md](../showroom-cms/README.md).
