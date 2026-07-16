# Telemetry V1

Telemetry V1 derives structured performance and quality metrics from existing factory
runs without replacing manifests, step executions, pipeline state, or completed articles.

## Flow identity

A discrete flow is always identified by **`flow_path` + `flow_version_id`**. Either field
alone is insufficient for telemetry queries or CSV export.

## Source of truth

Raw artifacts remain authoritative:

- `factory_runs`
- `step_executions`
- `manifest`
- reviewer `response_content`

Telemetry tables store explicit queryable fields plus optional issue rows. Telemetry can be
rebuilt at any time via:

```bash
python -m article_factory telemetry rebuild --flow-path MyFlow.flow.json --flow-version-id 3
python -m article_factory telemetry rebuild --run-id run-abc123
```

## Database tables

| Table | Purpose |
|-------|---------|
| `run_telemetry` | One row per terminal run |
| `iteration_telemetry` | One row per writer→reviewer cycle |
| `criterion_telemetry` | Rubric criterion scores per review iteration |
| `review_issue_telemetry` | Previous issues and required changes when parsed |

## Metric definitions

- **success** — run status is `completed` (system finished without pipeline failure)
- **accepted** — final gate/reviewer verdict is accepted
- **first_pass_accept** — first gate review accepted (from run field, verified against history)
- **llm_duration_ms** — sum of completed step `duration_ms`
- **wall_clock_duration_ms** — `finished_at - started_at`
- **iteration_count** — number of gate/review decisions, not raw step rows

## Iteration grouping

Iterations are grouped using flow role metadata from `flow_roles.resolve_flow_roles()`:

- gate/reviewer step — last looping step or `performance.gate_step_key`
- producer/writer steps — steps from loop target through gate

Works for `writer`/`review`, `step_1`/`step_2`, and custom labels.

## Review parsing

1. Prefer JSON between `BEGIN REVIEW JSON` / `END REVIEW JSON`
2. Fall back to legacy text (`TOTAL SCORE`, category lines, `VERDICT:`)
3. Runtime loop control still uses `VERDICT:` via `verdict.parse_verdict()`
4. JSON/legacy verdict mismatches log warnings but do not change runtime decisions

Reviewer prompts receive JSON instructions at runtime for review-loop steps.

## CSV export

`GET /api/flows/telemetry/export?flow_path=...&flow_version_id=...`

- One row per run
- Flat `iteration_N_score` columns (default 11) plus `iteration_scores_json`
- Final criterion columns and `criterion_scores_json`
- No article bodies or full reviewer responses
- CSV injection mitigation for topic, error, model, puller, flow path

## API

`GET /api/flows/telemetry` — paginated JSON summary for future dashboards.

`POST /api/flows/telemetry/rebuild` — administrative rebuild for a flow version.

## Known limitations

- `attempt_number` defaults to `1` (reserved for future topic restarts)
- Historical reviews without JSON use legacy parsing only
- Non-standard rubric max scores in legacy text may not validate as structured JSON
- Telemetry capture failures are logged and never fail an otherwise valid run

## Future extension points

Explicit columns and flow-version scoping support later:

- model / prompt / flow-version comparisons
- throughput and convergence dashboards
- public factory statistics
- repeated attempts per topic
