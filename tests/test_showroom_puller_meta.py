from __future__ import annotations

from article_factory.services.showroom_puller_meta import build_pullers_system_meta


def test_build_pullers_system_meta() -> None:
    meta = build_pullers_system_meta(
        [
            {
                "puller_name": "gpu-west",
                "status": "idle",
                "is_active": True,
                "is_stale": False,
                "supported_models": ["llama3"],
            },
            {
                "puller_name": "gpu-east",
                "status": "busy",
                "is_active": True,
                "is_stale": False,
                "supported_models": ["gemma4:31b"],
                "current_task": {"model": "gemma4:31b", "conversation_id": "conv-1"},
            },
            {
                "puller_name": "offline-node",
                "status": "offline",
                "is_active": False,
                "is_stale": False,
                "supported_models": [],
            },
        ],
        factory_name="Night Factory",
    )
    assert meta["factory_name"] == "Night Factory"
    assert meta["pullers_online"] == 2
    assert [item["name"] for item in meta["pullers"]] == ["gpu-east", "gpu-west", "offline-node"]
    assert meta["pullers"][0]["online"] is True
    assert meta["pullers"][0]["is_idle"] is False
    assert meta["pullers"][0]["current_task"]["model"] == "gemma4:31b"
    assert meta["pullers"][1]["is_idle"] is True
    assert meta["pullers"][2]["online"] is False
