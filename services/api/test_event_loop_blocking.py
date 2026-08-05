"""Guard: handlers that do blocking DB work must not sit on the event loop.

The API runs a single uvicorn worker (see Dockerfile), so there is exactly one
event loop serving every request. A handler declared `async def` runs ON that
loop; a handler declared plain `def` is dispatched by Starlette to anyio's
threadpool instead.

Because the DB session is synchronous SQLAlchemy, an `async def` handler that
takes one never yields at its queries -- it occupies the loop for the whole
request and every other in-flight request waits behind it. `/openings` was the
extreme case: it loads and re-parses every stored PGN for a user.

This is easy to reintroduce, because `async def` looks like the more modern
choice and nothing about it fails loudly. So the invariant is asserted here
rather than left to review.
"""

import inspect

from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from services.api.main import app

# Handlers that legitimately stay `async def`: each awaits real async I/O
# (an outbound HTTP call, or work already delegated to a thread), so it does
# yield the loop rather than hold it.
#
# NB: several of these still run synchronous DB work on the loop *between*
# their awaits -- explain_rating_changes most of all. Splitting that out is
# tracked separately; this allowlist records where the line is today, not that
# these handlers are perfect.
ASYNC_BY_DESIGN = {
    "import_chesscom_games",
    "get_opening_baseline",
    "create_rating_snapshot",
    "explain_rating_changes",
    "complete_session",
}


def _takes_db_session(endpoint) -> bool:
    """True when the handler receives a synchronous SQLAlchemy Session."""
    try:
        params = inspect.signature(endpoint).parameters
    except (TypeError, ValueError):  # pragma: no cover - builtins etc.
        return False
    return any(p.annotation is Session for p in params.values())


def _db_backed_routes():
    for route in app.routes:
        if isinstance(route, APIRoute) and _takes_db_session(route.endpoint):
            yield route


def test_db_backed_handlers_are_not_coroutines():
    """Any handler holding a sync Session must be `def`, not `async def`."""
    offenders = sorted(
        route.endpoint.__name__
        for route in _db_backed_routes()
        if inspect.iscoroutinefunction(route.endpoint)
        and route.endpoint.__name__ not in ASYNC_BY_DESIGN
    )
    assert not offenders, (
        "These handlers take a synchronous DB Session but are declared "
        f"`async def`, so their queries block the event loop: {offenders}. "
        "Drop the `async` keyword -- Starlette will run them on the threadpool. "
        "If a handler genuinely awaits async I/O, add it to ASYNC_BY_DESIGN "
        "with a note on what it awaits."
    )


def test_allowlist_has_no_stale_entries():
    """A name in ASYNC_BY_DESIGN that is no longer an async DB handler is rot.

    Without this, an entry silently keeps exempting a handler that was since
    converted, renamed, or deleted -- and the next `async def` reusing that name
    inherits the exemption.
    """
    live = {
        route.endpoint.__name__
        for route in _db_backed_routes()
        if inspect.iscoroutinefunction(route.endpoint)
    }
    stale = sorted(ASYNC_BY_DESIGN - live)
    assert not stale, (
        f"ASYNC_BY_DESIGN lists handlers that are no longer async DB "
        f"handlers: {stale}. Remove them."
    )


def test_guard_actually_sees_the_routes():
    """Self-check: the two tests above pass trivially if this finds nothing."""
    routes = list(_db_backed_routes())
    assert len(routes) > 25, (
        f"Only found {len(routes)} DB-backed routes; the signature inspection "
        "has probably stopped matching and the guard above is now vacuous."
    )
