from __future__ import annotations

from typing import Any

from article_factory.control_plane.client import ControlPlaneClient


def puller_supports_model(puller: dict[str, Any], model: str) -> bool:
    supported = list(puller.get("supported_models") or [])
    if not supported:
        return True
    return model in supported


def is_registered_puller(puller: dict[str, Any]) -> bool:
    """Puller is on the control plane and reachable (may still be busy)."""
    if not puller.get("is_active"):
        return False
    if puller.get("is_stale"):
        return False
    status = str(puller.get("status") or "unknown").lower()
    return status not in {"offline", "error", "down"}


def is_idle_puller(puller: dict[str, Any]) -> bool:
    if not is_registered_puller(puller):
        return False
    status = str(puller.get("status") or "unknown").lower()
    return status not in {"busy"}


def pick_puller(pullers: list[dict[str, Any]], model: str, *, exclude: set[str] | None = None) -> str:
    if not model.strip():
        raise RuntimeError("No model configured — select a model before running topics")

    excluded = exclude or set()
    idle = [
        p
        for p in pullers
        if is_idle_puller(p)
        and puller_supports_model(p, model)
        and str(p.get("puller_name") or "") not in excluded
    ]
    if idle:
        idle.sort(key=lambda p: str(p.get("puller_name") or ""))
        return str(idle[0]["puller_name"])

    active = [
        p
        for p in pullers
        if p.get("is_active")
        and not p.get("is_stale")
        and puller_supports_model(p, model)
        and str(p.get("puller_name") or "") not in excluded
    ]
    if active:
        active.sort(key=lambda p: str(p.get("puller_name") or ""))
        return str(active[0]["puller_name"])

    raise RuntimeError(f"No idle puller on the control plane supports model “{model}”")


def idle_pullers_for_model(
    pullers: list[dict[str, Any]],
    model: str,
    *,
    exclude: set[str] | None = None,
) -> list[dict[str, Any]]:
    excluded = exclude or set()
    idle = [
        p
        for p in pullers
        if is_idle_puller(p)
        and puller_supports_model(p, model)
        and str(p.get("puller_name") or "") not in excluded
    ]
    idle.sort(key=lambda p: str(p.get("puller_name") or ""))
    return idle


async def select_puller_for_model(cp: ControlPlaneClient, model: str) -> str:
    pullers = await cp.list_pullers(active_only=False)
    return pick_puller(pullers, model)


async def get_registered_puller_on_cp(cp: ControlPlaneClient, puller_name: str) -> dict[str, Any] | None:
    if not puller_name.strip():
        return None
    try:
        puller = await cp.get_puller(puller_name)
        if isinstance(puller, dict) and is_registered_puller(puller):
            return puller
    except Exception:
        pass
    try:
        pullers = await cp.list_pullers(active_only=False)
    except Exception:
        return None
    for puller in pullers:
        if str(puller.get("puller_name") or "") != puller_name:
            continue
        if is_registered_puller(puller):
            return puller
    return None


async def puller_is_registered_on_cp(cp: ControlPlaneClient, puller_name: str) -> bool:
    return await get_registered_puller_on_cp(cp, puller_name) is not None
