import asyncio
import uuid
import structlog
from datetime import datetime, timezone
from typing import Callable, Any
from collections import defaultdict

log = structlog.get_logger()


# ============================================================
# THE HANDLER REGISTRY
#
# Maps event type strings to lists of async handler functions.
# Example:
#   {
#     "llm.call.completed": [billing_handler, anomaly_handler],
#     "anomaly.detected": [alert_handler, forecast_handler],
#   }
#
# defaultdict(list) means accessing a missing key returns []
# instead of raising KeyError. Safe to append without checking.
# ============================================================
_handlers: dict[str, list[Callable]] = defaultdict(list)


# ============================================================
# SUBSCRIPTION — DECORATOR AND FUNCTION FORM
# ============================================================

def subscribe(event_type: str) -> Callable:
    """
    Decorator that registers an async function as a handler
    for the given event type.

    Usage:
        @subscribe("llm.call.completed")
        async def handle_call(event: dict) -> None:
            await process(event["data"])

    The handler receives the full event envelope:
        {
            "event_id": str,
            "event_type": str,
            "published_at": str,
            "data": dict
        }
    """
    def decorator(func: Callable) -> Callable:
        if not asyncio.iscoroutinefunction(func):
            raise TypeError(
                f"Event handler '{func.__name__}' must be an async function. "
                f"Change 'def {func.__name__}' to 'async def {func.__name__}'."
            )
        _handlers[event_type].append(func)
        log.debug(
            "event_bus.handler_registered",
            event_type=event_type,
            handler=func.__name__,
        )
        return func
    return decorator


def register_handler(event_type: str, func: Callable) -> None:
    """
    Registers a handler function without using the decorator syntax.
    Useful when you need to register handlers programmatically
    or in testing scenarios.

    Usage:
        register_handler("llm.call.completed", my_handler_function)
    """
    if not asyncio.iscoroutinefunction(func):
        raise TypeError(
            f"Event handler '{func.__name__}' must be an async function."
        )
    _handlers[event_type].append(func)
    log.debug(
        "event_bus.handler_registered",
        event_type=event_type,
        handler=func.__name__,
    )


def unregister_handler(event_type: str, func: Callable) -> None:
    """
    Removes a specific handler for an event type.
    Primarily used in tests to clean up between test runs.
    """
    if event_type in _handlers and func in _handlers[event_type]:
        _handlers[event_type].remove(func)
        log.debug(
            "event_bus.handler_unregistered",
            event_type=event_type,
            handler=func.__name__,
        )


def clear_handlers(event_type: str | None = None) -> None:
    """
    Removes all handlers for a specific event type,
    or ALL handlers if no event_type is given.
    Used in tests to ensure clean state between test runs.
    """
    if event_type is None:
        _handlers.clear()
        log.debug("event_bus.all_handlers_cleared")
    else:
        _handlers[event_type].clear()
        log.debug("event_bus.handlers_cleared", event_type=event_type)


def get_handler_count(event_type: str) -> int:
    """Returns the number of handlers registered for an event type."""
    return len(_handlers[event_type])


def get_registered_events() -> list[str]:
    """Returns a list of all event types that have at least one handler."""
    return [event_type for event_type, handlers in _handlers.items()
            if len(handlers) > 0]


# ============================================================
# EVENT ENVELOPE BUILDER
# ============================================================

def _build_event(event_type: str, data: dict) -> dict:
    """
    Wraps raw data in a standard event envelope.
    Every handler receives this envelope — not raw data.
    """
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }


# ============================================================
# PUBLISHING — AWAITED FORM
# ============================================================

async def publish(event_type: str, data: dict) -> dict[str, Any]:
    """
    Publishes an event and WAITS for all handlers to complete.

    Use this when the caller needs confirmation that all downstream
    processing has finished before continuing.

    Returns a result summary:
        {
            "event_id": str,
            "event_type": str,
            "handlers_called": int,
            "handlers_succeeded": int,
            "handlers_failed": int,
            "errors": [{"handler": str, "error": str}, ...]
        }

    Errors in individual handlers are caught and reported —
    they never propagate to the caller. One bad handler
    never breaks the others or the publisher.
    """
    event = _build_event(event_type, data)
    handlers = _handlers[event_type]

    if not handlers:
        log.debug("event_bus.no_handlers", event_type=event_type,
                  event_id=event["event_id"])
        return {
            "event_id": event["event_id"],
            "event_type": event_type,
            "handlers_called": 0,
            "handlers_succeeded": 0,
            "handlers_failed": 0,
            "errors": [],
        }

    log.info(
        "event_bus.publishing",
        event_type=event_type,
        event_id=event["event_id"],
        handler_count=len(handlers),
    )

    errors = []
    succeeded = 0
    failed = 0

    async def run_handler(handler: Callable) -> None:
        nonlocal succeeded, failed
        try:
            await handler(event)
            succeeded += 1
            log.debug(
                "event_bus.handler_success",
                event_type=event_type,
                handler=handler.__name__,
                event_id=event["event_id"],
            )
        except Exception as e:
            failed += 1
            errors.append({
                "handler": handler.__name__,
                "error": str(e),
                "error_type": type(e).__name__,
            })
            log.error(
                "event_bus.handler_failed",
                event_type=event_type,
                handler=handler.__name__,
                event_id=event["event_id"],
                error=str(e),
            )

    # Run all handlers concurrently
    await asyncio.gather(*[run_handler(h) for h in handlers])

    log.info(
        "event_bus.published",
        event_type=event_type,
        event_id=event["event_id"],
        succeeded=succeeded,
        failed=failed,
    )

    return {
        "event_id": event["event_id"],
        "event_type": event_type,
        "handlers_called": len(handlers),
        "handlers_succeeded": succeeded,
        "handlers_failed": failed,
        "errors": errors,
    }


# ============================================================
# PUBLISHING — BACKGROUND FORM
# ============================================================

def publish_background(event_type: str, data: dict) -> None:
    """
    Publishes an event as a background task — returns IMMEDIATELY.
    Handlers run concurrently in the background.

    Use this for high-frequency events where you don't want to
    slow down the main request flow. The caller doesn't wait
    for handlers to finish.

    Example — in the LLM gateway after returning the response:
        publish_background("llm.call.completed", call_data)
        return api_response  # returns before handlers finish

    WARNING: You cannot await this function. Errors in handlers
    are logged but not returned to the caller.
    """
    event = _build_event(event_type, data)
    handlers = _handlers[event_type]

    if not handlers:
        log.debug("event_bus.no_handlers_background",
                  event_type=event_type, event_id=event["event_id"])
        return

    async def run_all_handlers() -> None:
        for handler in handlers:
            try:
                await handler(event)
            except Exception as e:
                log.error(
                    "event_bus.background_handler_failed",
                    event_type=event_type,
                    handler=handler.__name__,
                    event_id=event["event_id"],
                    error=str(e),
                )

    try:
        loop = asyncio.get_event_loop()
        loop.create_task(run_all_handlers())
        log.debug(
            "event_bus.background_task_created",
            event_type=event_type,
            event_id=event["event_id"],
        )
    except RuntimeError:
        # No running event loop — run synchronously as fallback
        log.warning(
            "event_bus.no_running_loop_fallback",
            event_type=event_type,
        )
        asyncio.run(run_all_handlers())