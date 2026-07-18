from article_factory.services.step_trace import collapse_superseded_in_flight_steps


def test_collapse_superseded_in_flight_steps_drops_stale_attempts() -> None:
    steps = [
        {"step_key": "step_1", "status": "completed"},
        {"step_key": "step_2", "status": "completed"},
        {"step_key": "step_1", "status": "pulled"},
        {"step_key": "step_1", "status": "completed"},
        {"step_key": "step_2", "status": "pulled"},
    ]
    collapsed = collapse_superseded_in_flight_steps(steps)
    assert collapsed == [
        {"step_key": "step_1", "status": "completed"},
        {"step_key": "step_2", "status": "completed"},
        {"step_key": "step_1", "status": "completed"},
        {"step_key": "step_2", "status": "pulled"},
    ]
