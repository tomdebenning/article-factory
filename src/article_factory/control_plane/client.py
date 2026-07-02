from __future__ import annotations

from typing import Any

import httpx

from article_factory.config import settings


class ControlPlaneClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.control_plane_url).rstrip("/")

    async def submit_task(self, task: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(f"{self.base_url}/tasks", json=task)
            response.raise_for_status()
            return response.json()

    async def list_pullers(self, *, active_only: bool = True) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.base_url}/pullers",
                params={"active_only": str(active_only).lower()},
            )
            response.raise_for_status()
            payload = response.json()
            return list(payload.get("pullers") or [])

    async def get_puller(self, puller_name: str) -> dict[str, Any] | None:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{self.base_url}/pullers/{puller_name}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else None

    async def get_activity(
        self,
        *,
        kinds: str | None = None,
        max_items: int = 100,
    ) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            params: dict[str, Any] = {"max": max_items}
            if kinds:
                params["kinds"] = kinds
            response = await client.get(f"{self.base_url}/activity", params=params)
            if response.status_code == 503:
                return []
            response.raise_for_status()
            payload = response.json()
            return list(payload.get("events") or [])

    async def get_task_status(self, conversation_id: str) -> dict[str, Any] | None:
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(
                    f"{self.base_url}/tasks/status",
                    params={"conversation_id": conversation_id},
                )
            except httpx.HTTPError:
                return None
            if response.status_code in {404, 405}:
                return None
            if response.status_code >= 500:
                return None
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not payload.get("found"):
                return None
            return payload

    async def task_was_fetched(self, *, conversation_id: str) -> bool:
        status = await self.get_task_status(conversation_id)
        if status:
            return str(status.get("status") or "") in {"fetched", "completed", "failed"}
        events = await self.get_activity(kinds="task_fetched", max_items=1000)
        for event in events:
            details = event.get("details") or {}
            if details.get("conversation_id") == conversation_id:
                return True
        return False

    async def poll_responses(
        self,
        agent_id: str,
        *,
        conversation_id: str,
        round_num: int,
        max_items: int = 10,
    ) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(
                f"{self.base_url}/responses",
                params={
                    "agent_id": agent_id,
                    "conversation_id": conversation_id,
                    "round": round_num,
                    "max": max_items,
                },
            )
            if response.status_code == 204:
                return []
            response.raise_for_status()
            payload = response.json()
            return list(payload.get("responses") or [])

    async def post_node_heartbeat(self, payload: dict[str, Any]) -> None:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(f"{self.base_url}/heartbeat/node", json=payload)
            response.raise_for_status()

    async def post_agent_heartbeat(self, payload: dict[str, Any]) -> None:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(f"{self.base_url}/heartbeat/agent", json=payload)
            response.raise_for_status()
