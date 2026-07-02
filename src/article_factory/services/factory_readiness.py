from __future__ import annotations

from typing import Any

import httpx

from article_factory.control_plane.client import ControlPlaneClient
from article_factory.models import FactoryRun
from article_factory.services.cms_connection import check_cms_connection
from article_factory.services.flow_tool_requirements import collect_flow_tool_requirements
from article_factory.services.puller_selection import is_idle_puller, puller_supports_model
from article_factory.services.runtime_settings import RuntimeSettings


def _check(
    check_id: str,
    label: str,
    ok: bool,
    message: str,
    *,
    action_label: str | None = None,
    action_path: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": check_id,
        "label": label,
        "ok": ok,
        "message": message,
    }
    if action_label and action_path:
        item["action_label"] = action_label
        item["action_path"] = action_path
    return item


def active_pullers_for_model(pullers: list[dict[str, Any]], model: str) -> list[dict[str, Any]]:
    return [
        p
        for p in pullers
        if p.get("is_active") and not p.get("is_stale") and puller_supports_model(p, model)
    ]


async def assess_factory_readiness(
    *,
    runtime: RuntimeSettings,
    loop_running: bool,
    active_run: FactoryRun | None,
    queue_counts: dict[str, int],
    active_run_count: int = 0,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    setup_blockers: list[str] = []

    checks.append(
        _check(
            "orchestrator",
            "Factory orchestrator",
            loop_running,
            "Running and will process the queue automatically."
            if loop_running
            else "Not running — restart the factory with ./run.sh",
            action_label="View setup" if not loop_running else None,
            action_path="/" if not loop_running else None,
        )
    )
    if not loop_running:
        setup_blockers.append("orchestrator")

    cp_url = runtime.control_plane_url.strip()
    has_cp_url = bool(cp_url)
    checks.append(
        _check(
            "control_plane_url",
            "Control plane URL",
            has_cp_url,
            cp_url if has_cp_url else "Not configured — set the control plane address in Settings.",
            action_label="Open Settings" if not has_cp_url else None,
            action_path="/settings" if not has_cp_url else None,
        )
    )
    if not has_cp_url:
        setup_blockers.append("control_plane_url")

    cp_reachable = False
    cp_error = ""
    pullers: list[dict[str, Any]] = []
    if has_cp_url:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.get(f"{cp_url.rstrip('/')}/health")
                response.raise_for_status()
            cp_reachable = True
            cp = ControlPlaneClient(base_url=cp_url)
            pullers = await cp.list_pullers(active_only=False)
        except Exception as exc:
            cp_error = str(exc)

    checks.append(
        _check(
            "control_plane_reachable",
            "Control plane connection",
            cp_reachable,
            f"Connected to {cp_url}" if cp_reachable else f"Cannot reach control plane: {cp_error}",
            action_label="Fix in Settings" if has_cp_url and not cp_reachable else None,
            action_path="/settings" if has_cp_url and not cp_reachable else None,
        )
    )
    if has_cp_url and not cp_reachable:
        setup_blockers.append("control_plane_reachable")

    model = runtime.default_model.strip()
    has_model = bool(model)
    checks.append(
        _check(
            "model",
            "Writing model",
            has_model,
            f"Using model “{model}”" if has_model else "No model selected — choose one in Settings or Queue.",
            action_label="Choose model" if not has_model else None,
            action_path="/settings" if not has_model else None,
        )
    )
    if not has_model:
        setup_blockers.append("model")

    active_for_model = active_pullers_for_model(pullers, model) if has_model and cp_reachable else []
    idle_for_model = [
        p for p in pullers if is_idle_puller(p) and puller_supports_model(p, model)
    ] if has_model and cp_reachable else []

    puller_ok = bool(active_for_model)
    if has_model and cp_reachable and not puller_ok:
        puller_msg = f"No active puller supports “{model}”. Register a puller on the control plane."
    elif puller_ok and idle_for_model:
        names = ", ".join(sorted(str(p.get("puller_name") or "") for p in idle_for_model[:3]))
        puller_msg = f"{len(idle_for_model)} idle puller(s) available ({names})."
    elif puller_ok:
        puller_msg = f"{len(active_for_model)} active puller(s) support this model (none idle right now)."
    else:
        puller_msg = "Configure control plane and model first."

    checks.append(
        _check(
            "pullers",
            "Active puller for model",
            puller_ok,
            puller_msg,
            action_label="Check Settings" if has_model and cp_reachable and not puller_ok else None,
            action_path="/settings" if has_model and cp_reachable and not puller_ok else None,
        )
    )
    if has_model and cp_reachable and not puller_ok:
        setup_blockers.append("pullers")

    cms_url = runtime.cms_url.strip()
    cms_api_key = runtime.cms_api_key.strip()
    has_cms_url = bool(cms_url)
    has_cms_key = bool(cms_api_key)
    cms_configured = has_cms_url and has_cms_key
    checks.append(
        _check(
            "cms_url",
            "Showroom CMS URL",
            cms_configured,
            cms_url if has_cms_url and has_cms_key else "Set Showroom URL and API key in Settings.",
            action_label="Open Settings" if not cms_configured else None,
            action_path="/settings" if not cms_configured else None,
        )
    )
    if not cms_configured:
        setup_blockers.append("cms_url")

    cms_ok = False
    cms_message = "Configure Showroom URL and API key first."
    if cms_configured:
        cms_ok, cms_message = await check_cms_connection(cms_url, cms_api_key)

    checks.append(
        _check(
            "cms_connection",
            "Showroom connection",
            cms_ok,
            cms_message,
            action_label="Fix in Settings" if cms_configured and not cms_ok else None,
            action_path="/settings" if cms_configured and not cms_ok else None,
        )
    )
    if cms_configured and not cms_ok:
        setup_blockers.append("cms_connection")

    tool_requirements = collect_flow_tool_requirements()
    needs_web_search = tool_requirements.get("needs_web_search", False)
    brave_key = runtime.brave_search_api_key.strip()
    brave_configured = bool(brave_key)
    brave_ok = brave_configured
    if brave_configured:
        if needs_web_search:
            brave_message = "Brave Search API key configured (web_search is available on every prompt)."
        else:
            brave_message = "Brave Search API key configured."
    elif needs_web_search:
        brave_message = "Add your Brave Search API key in Settings — web_search is available on every prompt."
    else:
        brave_message = (
            "Brave Search API key is not configured — add it in Settings. "
            "web_search is available on every factory prompt."
        )
    checks.append(
        _check(
            "brave_search",
            "Brave Search API",
            brave_ok,
            brave_message,
            action_label="Open Settings" if not brave_configured else None,
            action_path="/settings" if not brave_configured else None,
        )
    )
    if needs_web_search and not brave_configured:
        setup_blockers.append("brave_search")

    queued = queue_counts.get("queued", 0)
    topics_ok = queued > 0
    checks.append(
        _check(
            "topics",
            "Topics in queue",
            topics_ok,
            f"{queued} topic(s) waiting to be written."
            if topics_ok
            else "No topics yet — add article topics on the Queue page.",
            action_label="Add topics" if not topics_ok else None,
            action_path="/queue" if not topics_ok else None,
        )
    )

    setup_complete = len(setup_blockers) == 0
    processing = active_run_count > 0 or active_run is not None
    running_n = active_run_count if active_run_count > 0 else (1 if active_run else 0)

    if processing:
        phase = "processing"
        if running_n > 1:
            headline = f"Writing {running_n} articles now"
            summary = f"{running_n} articles are running in parallel on available pullers."
        else:
            headline = "Writing an article now"
            summary = "The factory is processing a topic through the pipeline stages."
        next_action = {"action_label": "View queue", "action_path": "/queue"}
    elif not setup_complete:
        phase = "setup_required"
        headline = "Setup required before the factory can write articles"
        summary = "Complete the items below, then add topics to the queue."
        next_action = next(
            (c for c in checks if not c["ok"] and c.get("action_path")),
            {"action_label": "Open Settings", "action_path": "/settings"},
        )
    elif topics_ok:
        phase = "ready"
        headline = "Ready — writing will begin shortly"
        summary = f"{queued} topic(s) are queued. The factory will start automatically."
        next_action = {"action_label": "View queue", "action_path": "/queue"}
    else:
        phase = "needs_topics"
        headline = "Factory is ready — add topics to start writing"
        summary = "Control plane, Showroom, and model are configured. Add one or more article topics to the queue."
        next_action = {"action_label": "Add topics", "action_path": "/start-flows"}

    return {
        "setup_complete": setup_complete,
        "can_write": setup_complete and (processing or topics_ok),
        "phase": phase,
        "headline": headline,
        "summary": summary,
        "next_action": {
            "label": next_action.get("action_label", "Continue"),
            "path": next_action.get("action_path", "/"),
        },
        "checks": checks,
        "issue_checks": [c for c in checks if not c["ok"] and c["id"] not in {"topics"}],
        "available_models": sorted(
            {
                m
                for p in pullers
                if p.get("is_active") and not p.get("is_stale")
                for m in (p.get("supported_models") or [])
            }
        ),
        "active_puller_count": len(active_for_model),
    }
