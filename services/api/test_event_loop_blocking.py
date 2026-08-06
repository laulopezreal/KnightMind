"""Guard: only handlers that genuinely await async I/O may run on the event loop.

The API runs a single uvicorn worker (see Dockerfile), so one event loop serves
every request. A handler declared `async def` runs ON that loop; a plain `def`
handler is dispatched by Starlette to anyio's threadpool instead.

Because the DB session is synchronous SQLAlchemy, an `async def` handler that
queries it never yields -- it holds the loop for the whole request and every
other in-flight request waits behind it. `/openings` was the extreme case: it
loads and re-parses every stored PGN for a user.

This is easy to reintroduce, because `async def` looks like the more modern
choice and nothing about it fails loudly. So the invariant is asserted here.

Implementation note -- why this checks the route table rather than signatures:
an earlier version of this guard decided which handlers mattered by inspecting
each endpoint's parameter annotations for a `Session`. That coupled the guard to
FastAPI internals, and it broke: under FastAPI 0.141 `include_router()` no
longer flattens sub-routers into `app.routes`, so the guard silently stopped
seeing every route in sessions.py, dashboard.py, ops.py and auth_routes.py.
Asserting the exact set of coroutine endpoints needs no introspection beyond
`iscoroutinefunction`, and covers handlers with no DB session at all.
"""

import inspect

from fastapi.routing import APIRoute

from services.api.main import app

# Every route handler that is allowed to be `async def`, and what it awaits.
# Anything not listed here must be a plain `def` so Starlette runs it off-loop.
#
# NB: several of these still run synchronous DB work on the loop *between* their
# awaits -- explain_rating_changes most of all, with ~490 lines after its single
# await. Splitting those out is tracked separately; this records where the line
# sits today, not that these handlers are blameless.
ASYNC_ROUTES = {
    "complete_session": "awaits auto_snapshot (outbound Chess.com call)",
    "create_rating_snapshot": "awaits the Chess.com stats fetch",
    "evaluate_fen": "awaits asyncio.to_thread around Stockfish",
    "explain_rating_changes": "awaits auto_snapshot_throttled",
    "get_opening_baseline": "awaits fetch_explorer_stats (outbound lichess call)",
    "import_chesscom_games": "awaits the archive fetch and to_thread persistence",
    "root": "trivial constant response, touches nothing",
    "validate_user": "awaits the Chess.com profile lookup",
}

# One endpoint from each separately-registered router. If include_router() stops
# surfacing a sub-router's routes -- as it did on the FastAPI 0.141 upgrade --
# these vanish and the guard below would otherwise pass while checking nothing.
ROUTER_SENTINELS = {
    "list_puzzles": "main.py (routes declared directly on app)",
    "complete_session": "sessions.py router",
    "get_dashboard_summary": "dashboard.py router",
    "get_health": "ops.py router",
    "login": "auth_routes.py router",
    "explain_rating_changes": "ratings.py router",
}


def _iter_api_routes(obj, _seen=None):
    """Yield every APIRoute reachable from `obj`, descending into sub-routers.

    Deliberately structural rather than version-specific: FastAPI has changed
    how included routers appear in `app.routes` at least once (0.141 wraps them
    in a `_IncludedRouter` exposing `original_router`), and older versions
    flatten them. Following whichever attribute holds a nested `routes` works
    for both, and for whatever the next release does.
    """
    if _seen is None:
        _seen = set()
    if id(obj) in _seen:
        return
    _seen.add(id(obj))
    for route in getattr(obj, "routes", []) or []:
        if isinstance(route, APIRoute):
            yield route
            continue
        for attr in ("original_router", "router", "app"):
            sub = getattr(route, attr, None)
            if sub is not None and hasattr(sub, "routes"):
                yield from _iter_api_routes(sub, _seen)
                break
        else:
            if hasattr(route, "routes"):
                yield from _iter_api_routes(route, _seen)


def _endpoint_names():
    return {route.endpoint.__name__ for route in _iter_api_routes(app)}


def _async_endpoint_names():
    return {
        route.endpoint.__name__
        for route in _iter_api_routes(app)
        if inspect.iscoroutinefunction(route.endpoint)
    }


def test_route_discovery_reaches_every_router():
    """Run first: the guards below are vacuous if route discovery is broken."""
    names = _endpoint_names()
    missing = {
        sentinel: where
        for sentinel, where in ROUTER_SENTINELS.items()
        if sentinel not in names
    }
    assert not missing, (
        f"Route discovery is not reaching every router: {missing}. "
        "_iter_api_routes needs updating for this FastAPI version -- until it "
        "is, the event-loop guards below are checking only part of the app."
    )


def test_no_unexpected_handler_runs_on_the_event_loop():
    """Any handler not in ASYNC_ROUTES must be `def`, not `async def`."""
    unexpected = sorted(_async_endpoint_names() - set(ASYNC_ROUTES))
    assert not unexpected, (
        f"These handlers are `async def`, so they run on the event loop and "
        f"block every other request while they work: {unexpected}. Drop the "
        "`async` keyword -- Starlette will run them on the threadpool. If a "
        "handler genuinely awaits async I/O, add it to ASYNC_ROUTES with a note "
        "on what it awaits."
    )


def test_async_allowlist_has_no_stale_entries():
    """An entry that is no longer an async handler is rot.

    Left alone it keeps exempting a handler that was since converted, renamed,
    or deleted -- and the next `async def` reusing that name inherits the
    exemption silently.
    """
    stale = sorted(set(ASYNC_ROUTES) - _async_endpoint_names())
    assert not stale, (
        f"ASYNC_ROUTES lists handlers that are no longer async: {stale}. "
        "Remove them."
    )
