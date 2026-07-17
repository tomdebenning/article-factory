from article_factory.services.shift_windows import rolling_shift_windows


def test_shift_board_and_save_activate(client, api_headers, configured_db) -> None:
    window = rolling_shift_windows(count=1)[0]
    board = client.get("/api/shifts/board", headers=api_headers)
    assert board.status_code == 200
    assert len(board.json()["windows"]) == 8

    save = client.post(
        "/api/shifts/plans/save",
        headers=api_headers,
        json={
            "window_key": window.window_key,
            "default_model": "test-model",
            "desks": [
                {
                    "desk_path": "test/debug-verdict.flow.json",
                    "topic_slug": "general",
                    "name": "Test desk",
                }
            ],
            "assignments_by_desk_index": {"0": ["Topic one", "Topic two"]},
        },
    )
    assert save.status_code == 200, save.text
    plan_id = save.json()["plan"]["id"]
    assert save.json()["plan"]["assignment_total"] == 2

    activate = client.post(f"/api/shifts/plans/{plan_id}/activate", headers=api_headers)
    assert activate.status_code == 200, activate.text
    assert activate.json()["plan"]["status"] == "active"


def test_legacy_queue_start_retired(client, api_headers) -> None:
    response = client.post(
        "/api/flow-queues/start",
        headers=api_headers,
        json={
            "name": "Legacy",
            "flow_path": "test/debug-verdict.flow.json",
            "default_model": "test-model",
            "topics": ["One"],
        },
    )
    assert response.status_code == 410
