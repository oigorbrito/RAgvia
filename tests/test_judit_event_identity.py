import pytest

from app.judit import parse_event


def _lawsuit() -> dict:
    return {
        "callback_id": "cb-1",
        "event_type": "response_created",
        "payload": {
            "request_id": "req-1",
            "response_id": "resp-1",
            "response_type": "lawsuit",
            "response_data": {"code": "0000000-00.0000.0.00.0000"},
        },
    }


def test_lawsuit_requires_request_id() -> None:
    body = _lawsuit()
    body["payload"].pop("request_id")

    with pytest.raises(ValueError, match="missing request id"):
        parse_event(body)


def test_lawsuit_requires_stable_response_identifier() -> None:
    body = _lawsuit()
    body.pop("callback_id")
    body["payload"].pop("response_id")

    with pytest.raises(ValueError, match="stable response identifier"):
        parse_event(body)


def test_callback_id_is_valid_stable_identifier_when_response_id_missing() -> None:
    body = _lawsuit()
    body["payload"].pop("response_id")

    event = parse_event(body)

    assert event.is_lawsuit_response is True
    assert event.response_id is None
    assert event.callback_id == "cb-1"
    assert event.request_id == "req-1"


def test_request_completion_requires_request_id() -> None:
    body = {
        "callback_id": "cb-complete",
        "event_type": "response_created",
        "payload": {
            "response_id": "resp-info",
            "response_type": "application_info",
            "response_data": {"code": 600, "message": "REQUEST_COMPLETED"},
        },
    }

    with pytest.raises(ValueError, match="completion missing request id"):
        parse_event(body)


def test_non_completion_application_info_does_not_require_request_id() -> None:
    event = parse_event(
        {
            "event_type": "response_created",
            "payload": {
                "response_type": "application_info",
                "response_data": {"code": 601, "message": "OTHER_INFO"},
            },
        }
    )

    assert event.request_completed is False
