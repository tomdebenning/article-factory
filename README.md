# Article Factory

Headless **newsroom orchestrator**: runs multi-step LLM pipelines through a **control plane**, manages desks/shifts/queues, and publishes finished articles to [Showroom CMS](../showroom-cms/) (**The Edition**).

The admin UI is branded **The Newsroom** — same codebase, editorial framing for operators.

## Architecture

```
Control plane ←── Article Factory only (tasks, responses, gateway heartbeats)
                      │
                      ├── Showroom CMS (status, alerts, events, publish — HTTP only)
                      │
                      ├── Shift planning → Assignment Desk → topic queues
                      └── Desk flow JSON → step runner → pullers → publish
```

- **Control plane** — only the factory connects (submit tasks, poll responses, register as gateway).
- **Showroom CMS** — receives articles, manifests, factory status, and operational alerts. It never calls the control plane.
- **Desks** — `.flow.json` prompt pipelines under `data/flows/` (beats, steps, reviewer loops). Operators work in terms of desks and beats; templates live under `_templates/`.
- **Shifts** — six-hour UTC windows (night / morning / afternoon / evening). Plans hold desk slots, standing orders, and reporter assignments.
- **Workers** — one logical role per step; prompts and routing live in the flow file.

## Newsroom concepts

| Concept | What it is |
|---------|------------|
| **Desk** | A flow file — writer/reviewer pipeline for a beat (e.g. sports, tech). Has a **beat brief** (coverage topics) separate from step prompts. |
| **Template** | Starter pipeline under `data/flows/_templates/` — cloned when creating a new desk. |
| **Shift** | A planned window with desk slots, topic targets, and optional reporter personas. |
| **Standing order** | Recurring topic list + target count for a desk on a given shift key. |
| **Assignment Desk** | At **T−15** minutes before a shift, suggests a roster from standing orders + AI; editors review before dispatch. |
| **Active queue** | Topics waiting or running; supports **pause/stop-and-clear** per queue group. |
| **Staff** | Personas (reporter pools) — selected at dispatch; bylines and provenance publish to The Edition. |

Shift boundaries can **auto-activate** plans and **hard-stop** runs at window edges. Operational issues surface as **newsroom alerts** on Showroom’s *Behind the Edition* page.

## Flow files

```
data/flows/
├── _templates/              # starter pipelines (not run directly)
├── sports/
│   └── standard-4-step.flow.json
└── test/
    └── SimpleTest.flow.json
```

Each flow defines numbered steps with stable `step_id` values, loop routing, optional save-to-disk, and last-step complete/loop behavior. Step outputs saved to disk appear under `data/runs/{run_id}/steps/` and on the run detail page.

Configure defaults in **Settings**; queue items and shift dispatch can override per batch.

## Admin UI (The Newsroom)

Default ports: API **8100**, web UI **5174** (proxies `/api` → 8100).

| Area | Purpose |
|------|---------|
| **Home** | Desk-centric dashboard — beats, shift state, quick actions |
| **Desks** | Browse and open desks; edit beat brief, standing orders, shift view |
| **Templates** | Pipeline templates for new desks |
| **Shifts** | Shift board, roster review, activation |
| **Staff** | Personas / reporter pools |
| **Active** | Running and queued topics; clear queue & stop |
| **Stats / Settings** | Factory stats, puller defaults, onboarding wizard |

Additional tools: **flow performance** and telemetry, **prompt improvement** (LLM-driven flow version suggestions), **batch comparison**, run detail with step traces and tool use.

## Quick start

```bash
cd article-factory
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Edit CONTROL_PLANE_URL, CMS_URL, CMS_API_KEY, FACTORY_API_KEY

./run.sh --local
```

Use `./run.sh` without `--local` for LAN-friendly binding. See `./run.sh --help`.

## Environment

| Variable | Description |
|----------|-------------|
| `CONTROL_PLANE_URL` | Task puller control plane base URL |
| `CMS_URL` | Showroom CMS base URL (use `https://` when Showroom runs via `./run.sh`) |
| `CMS_API_KEY` | Key for factory → CMS internal API |
| `FACTORY_API_KEY` | Key for admin UI / API |
| `GATEWAY_DISPLAY_NAME` | Label shown in UI and pushed to Showroom (default: `Article Factory`) |
| `DATABASE_URL` | Default `sqlite:///./data/factory.db` |
| `FLOWS_ROOT` | Flow JSON root (default `./data/flows`) |
| `FLOW_RUN_OUTPUTS_ROOT` | Saved step markdown (default `./data/runs`) |
| `BRAVE_SEARCH_API_KEY` | Optional — web search tool in flows |

## Showroom integration

Factory calls Showroom CMS internal endpoints:

- `PUT /internal/factory/status` — heartbeat, active run, **newsroom alerts**
- `POST /internal/runs/events` — step lifecycle (optional)
- `POST /internal/runs/complete` — article + manifest on publish (**edition headline**, byline, provenance, tools)
- `POST /internal/flows/batch-complete` — desk batch statistics when a shift batch finishes

Publish path generates an **edition headline** before send. Manifests include reporter attribution, shift context, token usage, and tool-use summaries for The Edition reader UI.

See [showroom-cms/README.md](../showroom-cms/README.md).

## Telemetry

Structured run metrics (success, accept rate, iteration counts, rubric scores) are derived from existing runs and stored in queryable tables. Rebuild anytime:

```bash
python -m article_factory telemetry rebuild --flow-path sports/standard-4-step.flow.json --flow-version-id 3
python -m article_factory telemetry rebuild --run-id run-abc123
```

Details: [docs/telemetry-v1.md](docs/telemetry-v1.md).

## Tests

```bash
pytest   # coverage gate: 97% (see pyproject.toml)
```
