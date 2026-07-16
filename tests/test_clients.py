from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from article_factory.cms_client import CmsClient, CmsRequestError, cms_error_message
from article_factory.control_plane.client import ControlPlaneClient


@pytest.mark.asyncio
async def test_cms_client_methods() -> None:
    client = CmsClient(base_url="http://cms.test", api_key="key")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"article_id": 1}

    mock_http = AsyncMock()
    mock_http.put = AsyncMock(return_value=mock_response)
    mock_http.post = AsyncMock(return_value=mock_response)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch("article_factory.cms_client.httpx.AsyncClient", return_value=mock_http):
        await client.put_factory_status({"state": "idle"})
        await client.post_run_event({"event": "x"})
        result = await client.post_run_complete({"run_id": "r"})

    assert result["article_id"] == 1
    assert mock_http.put.await_count == 1
    assert mock_http.post.await_count == 2


def test_cms_error_message_uses_detail() -> None:
    response = MagicMock()
    response.status_code = 404
    response.reason_phrase = "Not Found"
    response.json.return_value = {"detail": "Unknown topic general"}
    response.request = MagicMock(method="POST", url=MagicMock(path="/internal/runs/complete"))
    assert cms_error_message(response) == "Showroom CMS: Unknown topic general"


@pytest.mark.asyncio
async def test_cms_client_raises_readable_error() -> None:
    client = CmsClient(base_url="http://cms.test", api_key="key")
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.reason_phrase = "Not Found"
    mock_response.json.return_value = {"detail": "Unknown topic general"}
    mock_response.request = MagicMock(method="POST", url=MagicMock(path="/internal/runs/complete"))
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "404",
        request=MagicMock(),
        response=mock_response,
    )

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_response)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch("article_factory.cms_client.httpx.AsyncClient", return_value=mock_http):
        with pytest.raises(CmsRequestError, match="Unknown topic general"):
            await client.post_run_complete({"run_id": "r"})


@pytest.mark.asyncio
async def test_control_plane_submit_and_poll() -> None:
    client = ControlPlaneClient(base_url="http://cp.test")

    submit_response = MagicMock()
    submit_response.raise_for_status = MagicMock()
    submit_response.json.return_value = {"task_id": "t1"}

    poll_response = MagicMock()
    poll_response.status_code = 200
    poll_response.raise_for_status = MagicMock()
    poll_response.json.return_value = {"responses": [{"message": {"content": "hi"}}]}

    empty_response = MagicMock()
    empty_response.status_code = 204

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=submit_response)
    mock_http.get = AsyncMock(side_effect=[poll_response, empty_response])
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch("article_factory.control_plane.client.httpx.AsyncClient", return_value=mock_http):
        task_result = await client.submit_task({"agent_id": "a"})
        assert task_result["task_id"] == "t1"
        responses = await client.poll_responses("agent", conversation_id="c", round_num=1)
        assert responses[0]["message"]["content"] == "hi"
        empty = await client.poll_responses("agent", conversation_id="c", round_num=1)
        assert empty == []


@pytest.mark.asyncio
async def test_control_plane_list_pullers_and_activity() -> None:
    client = ControlPlaneClient(base_url="http://cp.test")

    pullers_response = MagicMock()
    pullers_response.raise_for_status = MagicMock()
    pullers_response.json.return_value = {
        "pullers": [{"puller_name": "gpu-01", "supported_models": ["llama3"]}]
    }

    activity_response = MagicMock()
    activity_response.status_code = 200
    activity_response.raise_for_status = MagicMock()
    activity_response.json.return_value = {
        "events": [{"details": {"conversation_id": "conv-abc"}}]
    }

    unavailable_response = MagicMock()
    unavailable_response.status_code = 503

    fetched_status_response = MagicMock()
    fetched_status_response.status_code = 200
    fetched_status_response.raise_for_status = MagicMock()
    fetched_status_response.json.return_value = {
        "found": True,
        "status": "fetched",
    }

    missing_status_response = MagicMock()
    missing_status_response.status_code = 200
    missing_status_response.raise_for_status = MagicMock()
    missing_status_response.json.return_value = {"found": False}

    missing_activity_response = MagicMock()
    missing_activity_response.status_code = 200
    missing_activity_response.raise_for_status = MagicMock()
    missing_activity_response.json.return_value = {"events": []}

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(
        side_effect=[
            pullers_response,
            activity_response,
            unavailable_response,
            fetched_status_response,
            missing_status_response,
            missing_activity_response,
        ]
    )
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch("article_factory.control_plane.client.httpx.AsyncClient", return_value=mock_http):
        pullers = await client.list_pullers(active_only=False)
        assert pullers[0]["puller_name"] == "gpu-01"

        events = await client.get_activity(kinds="task_fetched", max_items=50)
        assert events[0]["details"]["conversation_id"] == "conv-abc"

        empty_events = await client.get_activity()
        assert empty_events == []

        assert await client.task_was_fetched(conversation_id="conv-abc") is True
        assert await client.task_was_fetched(conversation_id="missing") is False


@pytest.mark.asyncio
async def test_control_plane_get_puller_and_task_status() -> None:
    client = ControlPlaneClient(base_url="http://cp.test")

    puller_response = MagicMock()
    puller_response.status_code = 200
    puller_response.raise_for_status = MagicMock()
    puller_response.json.return_value = {"puller_name": "gpu-01"}

    missing_puller = MagicMock()
    missing_puller.status_code = 404

    server_error = MagicMock()
    server_error.status_code = 503

    not_found_status = MagicMock()
    not_found_status.status_code = 404

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(
        side_effect=[puller_response, missing_puller, server_error, not_found_status]
    )
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch("article_factory.control_plane.client.httpx.AsyncClient", return_value=mock_http):
        puller = await client.get_puller("gpu-01")
        assert puller["puller_name"] == "gpu-01"
        assert await client.get_puller("missing") is None
        assert await client.get_task_status("conv-x") is None
        assert await client.get_task_status("conv-y") is None


@pytest.mark.asyncio
async def test_control_plane_heartbeats() -> None:
    client = ControlPlaneClient(base_url="http://cp.test")
    ok_response = MagicMock()
    ok_response.raise_for_status = MagicMock()

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=ok_response)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch("article_factory.control_plane.client.httpx.AsyncClient", return_value=mock_http):
        await client.post_node_heartbeat({"node": "factory"})
        await client.post_agent_heartbeat({"agent": "writer"})
    assert mock_http.post.await_count == 2
