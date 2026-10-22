import pytest

from app.judit import parse_event


def _tracking_lawsuit_without_request_id() -> dict:
    return {
        "callback_id": "cb-1",
        "event_type": "response_created",
        "reference_type": "tracking",
        "reference_id": "tracking-123",
        "payload": {
            "response_id": "response-1",
            "response_type": "lawsuit",
            "response_data": {"code": "0000000-00.2026.8.21.0001"},
        },
    }


def test_tracking_reference_id_is_not_used_as_lawsuit_request_id() -> None:
    with pytest.raises(ValueError, match="missing request id"):
        parse_event(_tracking_lawsuit_without_request_id())


def test_tracking_reference_id_is_not_used_as_completion_request_id() -> None:
    body = {
        "callback_id": "cb-done",
        "event_type": "response_created",
        "reference_type": "tracking",
        "reference_id": "tracking-123",
        "payload": {
            "response_id": "response-info-1",
            "response_type": "application_info",
            "response_data": {"code": 600, "message": "REQUEST_COMPLETED"},
        },
    }

    with pytest.raises(ValueError, match="completion missing request id"):
        parse_event(body)


def test_non_tracking_reference_id_remains_legacy_request_fallback() -> None:
    body = {
        "callback_id": "cb-legacy",
        "event_type": "response_created",
        "reference_type": "request",
        "reference_id": "request-legacy",
        "payload": {
            "response_id": "response-legacy",
            "response_type": "lawsuit",
            "response_data": {"code": "0000000-00.2026.8.21.0001"},
        },
    }

    event = parse_event(body)
    assert event.request_id == "request-legacy"


def test_explicit_tracking_payload_request_id_still_wins() -> None:
    body = _tracking_lawsuit_without_request_id()
    body["payload"]["request_id"] = "request-abc"

    event = parse_event(body)
    assert event.request_id == "request-abc"
