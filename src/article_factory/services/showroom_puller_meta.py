from __future__ import annotations

from typing import Any

from article_factory.services.puller_selection import is_idle_puller, is_registered_puller


def build_pullers_system_meta(
    pullers: list[dict[str, Any]],
    *,
    factory_name: str = "",
) -> dict[str, Any]:
    """Serialize control-plane pullers for Showroom architecture diagram."""
    entries: list[dict[str, Any]] = []
    registered = 0
    for puller in pullers:
        name = str(puller.get("puller_name") or "").strip()
        if not name:
            continue
        online = is_registered_puller(puller)
        if online:
            registered += 1
        entry: dict[str, Any] = {
            "name": name,
            "puller_name": name,
            "status": str(puller.get("status") or "unknown"),
            "is_active": bool(puller.get("is_active")),
            "is_stale": bool(puller.get("is_stale")),
            "is_idle": is_idle_puller(puller),
            "supported_models": list(puller.get("supported_models") or []),
            "online": online,
        }
        current_task = puller.get("current_task")
        if isinstance(current_task, dict) and current_task:
            entry["current_task"] = current_task
        entries.append(entry)
    entries.sort(key=lambda item: item["name"])
    meta: dict[str, Any] = {
        "pullers": entries,
        "pullers_online": registered,
    }
    clean_name = factory_name.strip()
    if clean_name:
        meta["factory_name"] = clean_name
    return meta
