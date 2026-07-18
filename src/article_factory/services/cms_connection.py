from __future__ import annotations

import logging
from urllib.parse import urlparse, urlunparse

import httpx
from sqlalchemy.orm import Session

from article_factory.cms_client import CmsClient, cms_http_verify

logger = logging.getLogger(__name__)

_LOCAL_SHOWROOM_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def alternate_local_cms_url(base: str) -> str | None:
    """Flip http/https for local Showroom URLs (run.sh defaults to HTTPS)."""
    parsed = urlparse(base.strip().rstrip("/"))
    if not parsed.scheme or not parsed.netloc:
        return None
    host = (parsed.hostname or "").lower()
    if host not in _LOCAL_SHOWROOM_HOSTS:
        return None
    if parsed.scheme == "http":
        return urlunparse(parsed._replace(scheme="https"))
    if parsed.scheme == "https":
        return urlunparse(parsed._replace(scheme="http"))
    return None


def _http_scheme_mismatch_message(base: str, exc: Exception) -> str | None:
    message = str(exc).lower()
    if not base.startswith("http://"):
        return None
    if any(
        token in message
        for token in (
            "empty reply",
            "wrong version number",
            "remoteprotocolerror",
            "connection reset",
            "ssl",
        )
    ):
        return (
            f"Showroom CMS unreachable at {base}. "
            "Showroom's ./run.sh serves HTTPS by default — set CMS URL to https://127.0.0.1:8200 "
            "(or start Showroom with ./run.sh --http for plain HTTP)."
        )
    return None


async def _probe_cms(base: str, cms_api_key: str) -> tuple[bool, str]:
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
        hint = _http_scheme_mismatch_message(base, exc)
        if hint:
            return False, hint
        return False, f"Showroom CMS unreachable: {exc}"


async def check_cms_connection(cms_url: str, cms_api_key: str) -> tuple[bool, str]:
    base = cms_url.strip().rstrip("/")
    ok, message = await _probe_cms(base, cms_api_key)
    if ok:
        return True, message

    alt = alternate_local_cms_url(base)
    if alt:
        ok_alt, message_alt = await _probe_cms(alt, cms_api_key)
        if ok_alt:
            return True, (
                f"Connected to Showroom CMS at {alt}. "
                f"Update CMS URL in Settings from {base} to {alt}."
            )
        return False, message_alt

    return False, message


async def resolve_cms_url(db: Session, *, persist: bool = False) -> str:
    """Return a reachable Showroom base URL, optionally persisting http→https fixes."""
    from article_factory.services.runtime_settings import load_runtime_settings, update_factory_settings

    runtime = load_runtime_settings(db)
    base = runtime.cms_url.strip().rstrip("/")
    ok, _ = await _probe_cms(base, runtime.cms_api_key)
    if ok:
        return base

    alt = alternate_local_cms_url(base)
    if alt:
        ok_alt, _ = await _probe_cms(alt, runtime.cms_api_key)
        if ok_alt:
            if persist:
                update_factory_settings(db, {"cms_url": alt})
                db.commit()
                logger.info("Auto-corrected CMS URL from %s to %s", base, alt)
            return alt

    return base
