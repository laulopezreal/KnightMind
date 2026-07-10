"""
Chess.com game import service.

This module handles fetching and parsing games from the Chess.com API.
"""

import ssl
from dataclasses import dataclass
from typing import AsyncIterator

import httpx
import truststore

# Configure truststore to use system certificates.
# Chess.com currently resets TLS 1.3 handshakes from the live API container's
# OpenSSL stack, while the same endpoint succeeds with TLS 1.2. Keep all
# Chess.com API clients capped at TLS 1.2 so validation and imports use the
# same working transport behavior.
truststore.inject_into_ssl()
SSL_CONTEXT = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
SSL_CONTEXT.maximum_version = ssl.TLSVersion.TLSv1_2


class ImportError(Exception):
    """Base exception for import errors."""

    pass


class UserNotFoundError(ImportError):
    """Raised when the Chess.com user is not found."""

    def __init__(self, username: str):
        self.username = username
        super().__init__(f"Chess.com user '{username}' not found")


class RateLimitError(ImportError):
    """Raised when Chess.com API rate limit is exceeded."""

    def __init__(self, retry_after: int | None = None):
        self.retry_after = retry_after
        msg = "Chess.com API rate limit exceeded"
        if retry_after:
            msg += f", retry after {retry_after} seconds"
        super().__init__(msg)


class NetworkError(ImportError):
    """Raised when a network error occurs."""

    def __init__(self, message: str, original_error: Exception | None = None):
        self.original_error = original_error
        super().__init__(f"Network error: {message}")


@dataclass
class ChessGame:
    """Represents a chess game from Chess.com."""

    url: str
    pgn: str
    time_control: str
    end_time: int
    rated: bool
    white_username: str
    black_username: str
    white_result: str
    black_result: str


CHESSCOM_API_BASE = "https://api.chess.com/pub"

# Chess.com API requires a User-Agent header
DEFAULT_HEADERS = {
    "User-Agent": "KnightMind/1.0 (https://github.com/laulopezreal/KnightMind)"
}


def _chesscom_client(timeout: float) -> httpx.AsyncClient:
    """Create a Chess.com API client with the live-compatible TLS settings."""
    return httpx.AsyncClient(
        headers=DEFAULT_HEADERS,
        timeout=timeout,
        verify=SSL_CONTEXT,
        follow_redirects=True,
    )


def _handle_response_error(
    response: httpx.Response, username: str | None = None
) -> None:
    """Handle HTTP error responses from Chess.com API."""
    if response.status_code == 404:
        if username:
            raise UserNotFoundError(username)
        raise ImportError(f"Resource not found: {response.url}")
    elif response.status_code == 429:
        retry_after = response.headers.get("Retry-After")
        raise RateLimitError(int(retry_after) if retry_after else None)
    elif response.status_code >= 500:
        raise NetworkError(f"Chess.com server error: {response.status_code}")
    elif response.status_code >= 400:
        raise ImportError(
            f"Chess.com API error: {response.status_code} - {response.text}"
        )


async def get_player_archives(username: str) -> list[str]:
    """
    Get list of monthly archive URLs for a player.

    Args:
        username: Chess.com username

    Returns:
        List of archive URLs

    Raises:
        UserNotFoundError: If the user doesn't exist
        RateLimitError: If rate limited by Chess.com
        NetworkError: If a network error occurs
    """
    try:
        async with _chesscom_client(timeout=30.0) as client:
            response = await client.get(
                f"{CHESSCOM_API_BASE}/player/{username}/games/archives"
            )
            if not response.is_success:
                _handle_response_error(response, username)
            return response.json().get("archives", [])
    except httpx.TimeoutException as e:
        raise NetworkError("Request timed out", e) from e
    except httpx.ConnectError as e:
        raise NetworkError("Failed to connect to Chess.com", e) from e
    except httpx.HTTPError as e:
        raise NetworkError(str(e), e) from e


async def fetch_games_from_archive(archive_url: str) -> list[dict]:
    """
    Fetch games from a monthly archive.

    Args:
        archive_url: URL to the monthly archive

    Returns:
        List of game data dictionaries

    Raises:
        RateLimitError: If rate limited by Chess.com
        NetworkError: If a network error occurs
    """
    try:
        async with _chesscom_client(timeout=60.0) as client:
            response = await client.get(archive_url)
            if not response.is_success:
                _handle_response_error(response)
            return response.json().get("games", [])
    except httpx.TimeoutException as e:
        raise NetworkError("Request timed out", e) from e
    except httpx.ConnectError as e:
        raise NetworkError("Failed to connect to Chess.com", e) from e
    except httpx.HTTPError as e:
        raise NetworkError(str(e), e) from e


def parse_game(game_data: dict) -> ChessGame:
    """
    Parse raw game data into a ChessGame object.

    Args:
        game_data: Raw game data from Chess.com API

    Returns:
        Parsed ChessGame object
    """
    return ChessGame(
        url=game_data.get("url", ""),
        pgn=game_data.get("pgn", ""),
        time_control=game_data.get("time_control", ""),
        end_time=game_data.get("end_time", 0),
        rated=game_data.get("rated", False),
        white_username=game_data.get("white", {}).get("username", ""),
        black_username=game_data.get("black", {}).get("username", ""),
        white_result=game_data.get("white", {}).get("result", ""),
        black_result=game_data.get("black", {}).get("result", ""),
    )


async def import_all_games(username: str) -> AsyncIterator[ChessGame]:
    """
    Import all games for a Chess.com user.

    Args:
        username: Chess.com username

    Yields:
        ChessGame objects for each game

    Raises:
        UserNotFoundError: If the user doesn't exist
        RateLimitError: If rate limited by Chess.com
        NetworkError: If a network error occurs
    """
    archives = await get_player_archives(username)
    for archive_url in archives:
        games = await fetch_games_from_archive(archive_url)
        for game_data in games:
            yield parse_game(game_data)


async def get_player_stats(username: str) -> dict:
    """
    Get player stats including current ratings.

    Args:
        username: Chess.com username

    Returns:
        Dict containing stats from Chess.com API

    Raises:
        UserNotFoundError: If the user doesn't exist
        RateLimitError: If rate limited by Chess.com
        NetworkError: If a network error occurs
    """
    try:
        async with _chesscom_client(timeout=30.0) as client:
            response = await client.get(f"{CHESSCOM_API_BASE}/player/{username}/stats")
            if not response.is_success:
                _handle_response_error(response, username)
            return response.json()
    except httpx.TimeoutException as e:
        raise NetworkError("Request timed out", e) from e
    except httpx.ConnectError as e:
        raise NetworkError("Failed to connect to Chess.com", e) from e
    except httpx.HTTPError as e:
        raise NetworkError(str(e), e) from e


async def get_player_profile(username: str) -> dict:
    """Fetch a Chess.com player profile for username validation."""
    try:
        async with _chesscom_client(timeout=30.0) as client:
            response = await client.get(f"{CHESSCOM_API_BASE}/player/{username}")
            if not response.is_success:
                _handle_response_error(response, username)
            return response.json()
    except httpx.TimeoutException as e:
        raise NetworkError("Request timed out", e) from e
    except httpx.ConnectError as e:
        raise NetworkError("Failed to connect to Chess.com", e) from e
    except ValueError as e:
        raise NetworkError("Chess.com returned an invalid profile response", e) from e
    except httpx.HTTPError as e:
        raise NetworkError(str(e), e) from e


if __name__ == "__main__":
    import asyncio
    import sys

    async def main():
        if len(sys.argv) < 2:
            print("Usage: python chesscom.py <username>")
            sys.exit(1)

        username = sys.argv[1]
        print(f"Fetching games for {username}...")

        try:
            archives = await get_player_archives(username)
            print(f"Found {len(archives)} monthly archives")

            # Just fetch the most recent archive as a demo
            if archives:
                games = await fetch_games_from_archive(archives[-1])
                print(f"Most recent month has {len(games)} games")
        except UserNotFoundError as e:
            print(f"Error: {e}")
            sys.exit(1)
        except RateLimitError as e:
            print(f"Error: {e}")
            sys.exit(1)
        except NetworkError as e:
            print(f"Error: {e}")
            sys.exit(1)

    asyncio.run(main())
