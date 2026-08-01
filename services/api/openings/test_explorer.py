"""Unit tests for the opening-explorer client.

Everything here is the pure half — band arithmetic, score arithmetic, key
construction and response parsing. `fetch_stats` is exercised through a stubbed
transport rather than the network, so the suite never depends on lichess being
up or on a rate limit we do not control.
"""

import httpx
import pytest

from services.api.openings.explorer import (
    MIN_GAMES_FOR_BASELINE,
    SCHEME_VERSION,
    ExplorerStats,
    ExplorerUnavailable,
    RatingBand,
    band_for_rating,
    cache_key,
    fetch_stats,
    parse_stats,
)

START_EPD = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -"


class TestBandForRating:
    def test_places_a_rating_in_the_band_it_falls_in(self):
        assert band_for_rating(1650) == RatingBand(low=1600, high=1800)

    def test_a_rating_on_a_boundary_belongs_to_the_band_it_opens(self):
        assert band_for_rating(1600) == RatingBand(low=1600, high=1800)

    def test_the_top_band_is_open_ended(self):
        band = band_for_rating(2900)

        assert band == RatingBand(low=2500, high=None)
        assert band.label == "2500+"

    def test_a_beginner_lands_in_the_bottom_band(self):
        band = band_for_rating(700)

        assert band == RatingBand(low=0, high=1000)
        assert band.label == "under 1000"

    def test_an_unknown_rating_stays_unknown(self):
        # Not a failure: without a rating the honest answer is a baseline over
        # all ratings, said so. Defaulting to a band would put the user in a
        # peer group they may well not be in.
        assert band_for_rating(None) is None

    def test_labels_an_ordinary_band_as_a_range(self):
        assert RatingBand(low=1400, high=1600).label == "1400–1600"

    def test_only_offers_bands_the_explorer_accepts(self):
        # Anything else is a 400 upstream, which would surface as a dead
        # baseline for every user in that range.
        from services.api.openings.explorer import RATING_BANDS

        for rating in range(0, 3000, 37):
            band = band_for_rating(rating)
            assert band is not None
            assert band.low in RATING_BANDS


class TestExpectedScore:
    def stats(self, white: int, draws: int, black: int) -> ExplorerStats:
        return ExplorerStats(white=white, draws=draws, black=black)

    def test_counts_a_draw_as_half_a_point(self):
        # Chess score, not win rate — the same distinction the tree itself
        # makes, and the whole reason these two numbers are comparable.
        stats = self.stats(white=400, draws=200, black=400)

        assert stats.expected_score("white") == 50.0

    def test_reports_the_side_that_was_asked_for(self):
        stats = self.stats(white=600, draws=0, black=400)

        assert stats.expected_score("white") == 60.0
        assert stats.expected_score("black") == 40.0

    def test_says_nothing_when_the_sample_is_too_thin(self):
        # Three games saying 100% is worse than no baseline: it invites the
        # reader to change a repertoire over noise.
        stats = self.stats(white=3, draws=0, black=0)

        assert stats.games < MIN_GAMES_FOR_BASELINE
        assert stats.expected_score("white") is None

    def test_none_is_not_zero(self):
        # A thin sample must not render as "they score nothing here".
        assert self.stats(1, 0, 0).expected_score("white") is None
        assert self.stats(0, 0, 200).expected_score("white") == 0.0

    def test_counts_every_result_as_a_game(self):
        assert self.stats(white=10, draws=20, black=30).games == 60


class TestCacheKey:
    def test_a_scheme_change_cannot_read_old_rows(self):
        assert cache_key(START_EPD, None).startswith(f"{SCHEME_VERSION}|")

    def test_bands_do_not_share_a_row(self):
        # The whole point of the band is that the answers differ.
        low = cache_key(START_EPD, RatingBand(1000, 1200))
        high = cache_key(START_EPD, RatingBand(2200, 2500))

        assert low != high

    def test_no_band_is_its_own_key_rather_than_a_missing_one(self):
        key = cache_key(START_EPD, None)

        assert key.endswith("|all")
        assert key != cache_key(START_EPD, RatingBand(0, 1000))

    def test_positions_do_not_share_a_row(self):
        assert cache_key(START_EPD, None) != cache_key(START_EPD + "x", None)


class TestParseStats:
    def test_reads_the_three_counts(self):
        stats = parse_stats({"white": 5, "draws": 2, "black": 3})

        assert (stats.white, stats.draws, stats.black) == (5, 2, 3)

    def test_an_unseen_position_is_no_data_rather_than_an_error(self):
        # The explorer answers a position it has never seen with a body that
        # simply carries no counts. That is a fact about the position.
        assert parse_stats({"moves": []}).games == 0

    def test_rejects_a_body_that_is_not_an_object(self):
        with pytest.raises(ValueError):
            parse_stats([1, 2, 3])

    def test_rejects_counts_that_are_not_counts(self):
        with pytest.raises(ValueError):
            parse_stats({"white": "lots", "draws": 0, "black": 0})

    def test_rejects_a_negative_count(self):
        with pytest.raises(ValueError):
            parse_stats({"white": -1, "draws": 0, "black": 0})


def stub_transport(handler):
    """Run fetch_stats against a handler instead of the network."""
    return httpx.MockTransport(handler)


@pytest.fixture
def patched_client(monkeypatch):
    """Install a transport for the client fetch_stats builds."""

    def install(handler):
        real_client = httpx.AsyncClient

        def build(*args, **kwargs):
            kwargs.pop("verify", None)
            return real_client(*args, transport=stub_transport(handler), **kwargs)

        monkeypatch.setattr("services.api.openings.explorer.httpx.AsyncClient", build)

    return install


class TestFetchStats:
    @pytest.mark.asyncio
    async def test_asks_about_the_position_it_was_given(self, patched_client):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(dict(request.url.params))
            return httpx.Response(200, json={"white": 100, "draws": 50, "black": 100})

        patched_client(handler)
        await fetch_stats(START_EPD, RatingBand(1600, 1800))

        assert seen["fen"] == START_EPD
        assert seen["ratings"] == "1600"
        assert seen["variant"] == "standard"

    @pytest.mark.asyncio
    async def test_asks_for_no_game_lists(self, patched_client):
        # The aggregate is all this needs; game lists are the bulk of the
        # response body and none of them are ever read.
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(dict(request.url.params))
            return httpx.Response(200, json={"white": 1, "draws": 1, "black": 1})

        patched_client(handler)
        await fetch_stats(START_EPD, None)

        assert seen["topGames"] == "0"
        assert seen["recentGames"] == "0"
        assert seen["moves"] == "0"

    @pytest.mark.asyncio
    async def test_omits_the_band_when_there_is_none(self, patched_client):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(dict(request.url.params))
            return httpx.Response(200, json={"white": 1, "draws": 1, "black": 1})

        patched_client(handler)
        await fetch_stats(START_EPD, None)

        # Sending a band the user is not in would be worse than not filtering.
        assert "ratings" not in seen

    @pytest.mark.asyncio
    async def test_excludes_bullet(self, patched_client):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(dict(request.url.params))
            return httpx.Response(200, json={"white": 1, "draws": 1, "black": 1})

        patched_client(handler)
        await fetch_stats(START_EPD, None)

        assert "bullet" not in seen["speeds"]

    @pytest.mark.asyncio
    async def test_returns_the_aggregate(self, patched_client):
        patched_client(
            lambda request: httpx.Response(
                200, json={"white": 700, "draws": 200, "black": 100}
            )
        )

        stats = await fetch_stats(START_EPD, None)

        assert stats == ExplorerStats(white=700, draws=200, black=100)

    @pytest.mark.asyncio
    async def test_an_upstream_error_is_unavailable_not_a_crash(self, patched_client):
        patched_client(lambda request: httpx.Response(429))

        with pytest.raises(ExplorerUnavailable):
            await fetch_stats(START_EPD, None)

    @pytest.mark.asyncio
    async def test_a_network_failure_is_unavailable(self, patched_client):
        def handler(request):
            raise httpx.ConnectError("no route to host")

        patched_client(handler)

        with pytest.raises(ExplorerUnavailable):
            await fetch_stats(START_EPD, None)

    @pytest.mark.asyncio
    async def test_a_malformed_body_is_unavailable(self, patched_client):
        patched_client(lambda request: httpx.Response(200, json={"white": "many"}))

        with pytest.raises(ExplorerUnavailable):
            await fetch_stats(START_EPD, None)
