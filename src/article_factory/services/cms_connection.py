from __future__ import annotations

import httpx

from article_factory.cms_client import CmsClient, cms_http_verify


async def check_cms_connection(cms_url: str, cms_api_key: str) -> tuple[bool, str]:
    base = cms_url.strip().rstrip("/")
    if not base:
        return False, "Not configured — set the Showroom URL in Settings."
    if not cms_api_key.strip():
        return False, "Not configured — set the Showroom CMS API key in Settings."

    health_url = f"{base}/api/health"
    verify = cms_http_verify(base)
    try:
        async with httpx.AsyncClient(timeout=10.0, verify=verify) as client:
            response = await client.get(health_url)
            response.raise_for_status()

        cms = CmsClient(base_url=base, api_key=cms_api_key)
        async with httpx.AsyncClient(timeout=10.0, verify=verify) as client:
            auth_response = await client.put(
                f"{cms.base_url}/internal/factory/status",
                json={"state": "idle", "queue_depth": 0},
                headers=cms._headers(),
            )
            if auth_response.status_code == 401:
                return False, "Showroom reachable but CMS API key was rejected (401)"
            if auth_response.status_code == 503:
                detail = auth_response.text
                return False, f"Showroom has no integration key yet. Generate one at /admin — {detail}"
            auth_response.raise_for_status()
        return True, f"Connected to Showroom CMS at {base}"
    except Exception as exc:
        return False, f"Showroom CMS unreachable: {exc}"
