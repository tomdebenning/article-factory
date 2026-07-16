from __future__ import annotations

from typing import Any

from article_factory.services.puller_selection import is_idle_puller, is_registered_puller


def build_pullers_system_meta(pullers: list[dict[str, Any]]) -> dict[str, Any]:
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
        entries.append(
            {
                "name": name,
                "status": str(puller.get("status") or "unknown"),
                "is_active": bool(puller.get("is_active")),
                "is_stale": bool(puller.get("is_stale")),
                "is_idle": is_idle_puller(puller),
                "supported_models": list(puller.get("supported_models") or []),
                "online": online,
            }
        )
    entries.sort(key=lambda item: item["name"])
    return {
        "pullers": entries,
        "pullers_online": registered,
    }
