import pytest
import asyncio
from core.event_bus import (
    publish,
    publish_background,
    subscribe,
    register_handler,
    unregister_handler,
    clear_handlers,
    get_handler_count,
    get_registered_events,
    _build_event,
)


# ============================================================
# TEST FIXTURE — clean state between tests
# ============================================================

@pytest.fixture(autouse=True)
def clean_event_bus():
    """Clear all handlers before and after every test."""
    clear_handlers()
    yield
    clear_handlers()


# ============================================================
# SUBSCRIPTION TESTS
# ============================================================

@pytest.mark.asyncio
async def test_subscribe_decorator_registers_handler():
    """@subscribe registers a handler for the event type."""
    @subscribe("test.event")
    async def my_handler(event: dict) -> None:
        pass

    assert get_handler_count("test.event") == 1


@pytest.mark.asyncio
async def test_register_handler_function_registers_handler():
    """register_handler() registers a handler without decorator."""
    async def my_handler(event: dict) -> None:
        pass

    register_handler("test.event", my_handler)
    assert get_handler_count("test.event") == 1


@pytest.mark.asyncio
async def test_multiple_handlers_registered_for_same_event():
    """Multiple handlers can subscribe to the same event."""
    @subscribe("test.event")
    async def handler_one(event: dict) -> None:
        pass

    @subscribe("test.event")
    async def handler_two(event: dict) -> None:
        pass

    @subscribe("test.event")
    async def handler_three(event: dict) -> None:
        pass

    assert get_handler_count("test.event") == 3


@pytest.mark.asyncio
async def test_sync_handler_raises_type_error():
    """Registering a sync function raises TypeError immediately."""
    with pytest.raises(TypeError, match="must be an async function"):
        @subscribe("test.event")
        def sync_handler(event: dict) -> None:  # not async
            pass


@pytest.mark.asyncio
async def test_unregister_handler_removes_it():
    """unregister_handler removes a specific handler."""
    async def my_handler(event: dict) -> None:
        pass

    register_handler("test.event", my_handler)
    assert get_handler_count("test.event") == 1

    unregister_handler("test.event", my_handler)
    assert get_handler_count("test.event") == 0


@pytest.mark.asyncio
async def test_clear_handlers_removes_all_for_event():
    """clear_handlers(event_type) removes all handlers for that event."""
    @subscribe("test.event.a")
    async def handler_a(event: dict) -> None:
        pass

    @subscribe("test.event.b")
    async def handler_b(event: dict) -> None:
        pass

    clear_handlers("test.event.a")
    assert get_handler_count("test.event.a") == 0
    assert get_handler_count("test.event.b") == 1


@pytest.mark.asyncio
async def test_clear_all_handlers_removes_everything():
    """clear_handlers() with no argument clears all event types."""
    @subscribe("test.event.a")
    async def handler_a(event: dict) -> None:
        pass

    @subscribe("test.event.b")
    async def handler_b(event: dict) -> None:
        pass

    clear_handlers()
    assert get_handler_count("test.event.a") == 0
    assert get_handler_count("test.event.b") == 0


# ============================================================
# EVENT ENVELOPE TESTS
# ============================================================

@pytest.mark.asyncio
async def test_event_envelope_has_required_fields():
    """Built events have all required envelope fields."""
    event = _build_event("test.event", {"key": "value"})

    assert "event_id" in event
    assert "event_type" in event
    assert "published_at" in event
    assert "data" in event


@pytest.mark.asyncio
async def test_event_envelope_contains_correct_data():
    """Event envelope contains the correct event type and data."""
    data = {"tenant_id": "abc", "tokens": 1500}
    event = _build_event("llm.call.completed", data)

    assert event["event_type"] == "llm.call.completed"
    assert event["data"]["tenant_id"] == "abc"
    assert event["data"]["tokens"] == 1500


@pytest.mark.asyncio
async def test_each_event_has_unique_id():
    """Two events built from same data have different event_ids."""
    event_1 = _build_event("test.event", {"x": 1})
    event_2 = _build_event("test.event", {"x": 1})

    assert event_1["event_id"] != event_2["event_id"]


# ============================================================
# PUBLISH TESTS
# ============================================================

@pytest.mark.asyncio
async def test_publish_calls_handler():
    """Publishing an event calls the registered handler."""
    received = []

    @subscribe("test.event")
    async def handler(event: dict) -> None:
        received.append(event)

    await publish("test.event", {"value": 42})

    assert len(received) == 1
    assert received[0]["data"]["value"] == 42


@pytest.mark.asyncio
async def test_publish_calls_all_handlers():
    """Publishing calls every registered handler for the event."""
    call_log = []

    @subscribe("test.event")
    async def handler_one(event: dict) -> None:
        call_log.append("one")

    @subscribe("test.event")
    async def handler_two(event: dict) -> None:
        call_log.append("two")

    @subscribe("test.event")
    async def handler_three(event: dict) -> None:
        call_log.append("three")

    await publish("test.event", {})

    assert len(call_log) == 3
    assert "one" in call_log
    assert "two" in call_log
    assert "three" in call_log


@pytest.mark.asyncio
async def test_publish_returns_result_summary():
    """publish() returns a result summary with handler counts."""
    @subscribe("test.event")
    async def handler(event: dict) -> None:
        pass

    result = await publish("test.event", {"x": 1})

    assert result["handlers_called"] == 1
    assert result["handlers_succeeded"] == 1
    assert result["handlers_failed"] == 0
    assert result["errors"] == []
    assert "event_id" in result
    assert result["event_type"] == "test.event"


@pytest.mark.asyncio
async def test_publish_with_no_handlers_returns_zero_counts():
    """Publishing with no handlers returns a valid empty result."""
    result = await publish("event.with.no.handlers", {"x": 1})

    assert result["handlers_called"] == 0
    assert result["handlers_succeeded"] == 0
    assert result["handlers_failed"] == 0


@pytest.mark.asyncio
async def test_failing_handler_does_not_stop_other_handlers():
    """A handler that raises an exception does not prevent others from running."""
    call_log = []

    @subscribe("test.event")
    async def good_handler_one(event: dict) -> None:
        call_log.append("good_one")

    @subscribe("test.event")
    async def bad_handler(event: dict) -> None:
        raise ValueError("I am broken")

    @subscribe("test.event")
    async def good_handler_two(event: dict) -> None:
        call_log.append("good_two")

    result = await publish("test.event", {})

    # Both good handlers ran despite the bad one
    assert "good_one" in call_log
    assert "good_two" in call_log

    # The failure is reported but did not raise
    assert result["handlers_failed"] == 1
    assert result["handlers_succeeded"] == 2
    assert result["errors"][0]["handler"] == "bad_handler"
    assert result["errors"][0]["error_type"] == "ValueError"


@pytest.mark.asyncio
async def test_handler_receives_full_event_envelope():
    """Handler receives the full event envelope, not just raw data."""
    received_event = {}

    @subscribe("test.event")
    async def handler(event: dict) -> None:
        received_event.update(event)

    await publish("test.event", {"tenant_id": "xyz"})

    assert "event_id" in received_event
    assert "event_type" in received_event
    assert "published_at" in received_event
    assert "data" in received_event
    assert received_event["data"]["tenant_id"] == "xyz"


@pytest.mark.asyncio
async def test_different_events_do_not_cross_trigger():
    """A handler for event A is not called when event B is published."""
    call_log = []

    @subscribe("event.type.a")
    async def handler_a(event: dict) -> None:
        call_log.append("a")

    @subscribe("event.type.b")
    async def handler_b(event: dict) -> None:
        call_log.append("b")

    await publish("event.type.a", {})

    assert call_log == ["a"]  # only A was called, not B


@pytest.mark.asyncio
async def test_get_registered_events():
    """get_registered_events returns event types with active handlers."""
    @subscribe("event.alpha")
    async def handler_alpha(event: dict) -> None:
        pass

    @subscribe("event.beta")
    async def handler_beta(event: dict) -> None:
        pass

    events = get_registered_events()
    assert "event.alpha" in events
    assert "event.beta" in events


# ============================================================
# REAL-WORLD SCENARIO TEST
# ============================================================

@pytest.mark.asyncio
async def test_llm_call_completed_scenario():
    """
    Simulates the real Module 1 → Module 2, 3 flow.
    Gateway publishes llm.call.completed.
    Analytics and Detection both receive it.
    """
    analytics_received = []
    detection_received = []

    @subscribe("llm.call.completed")
    async def analytics_handler(event: dict) -> None:
        analytics_received.append(event["data"])

    @subscribe("llm.call.completed")
    async def detection_handler(event: dict) -> None:
        detection_received.append(event["data"])

    # Gateway publishes — does not know about analytics or detection
    call_data = {
        "tenant_id": "tenant_abc",
        "model": "gpt-4o-mini",
        "input_tokens": 450,
        "output_tokens": 280,
        "total_tokens": 730,
        "cost_usd": 0.000365,
        "duration_ms": 1240,
    }
    result = await publish("llm.call.completed", call_data)

    # Both modules received the event
    assert len(analytics_received) == 1
    assert len(detection_received) == 1

    # Both received the correct data
    assert analytics_received[0]["tenant_id"] == "tenant_abc"
    assert detection_received[0]["total_tokens"] == 730

    # Both handlers succeeded
    assert result["handlers_succeeded"] == 2
    assert result["handlers_failed"] == 0