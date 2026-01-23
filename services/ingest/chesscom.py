"""
Chess.com game import service.

This module handles fetching and parsing games from the Chess.com API.
Currently a placeholder - will be fully implemented in a future phase.
"""

import httpx
from dataclasses import dataclass
from typing import Iterator


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


async def get_player_archives(username: str) -> list[str]:
    """
    Get list of monthly archive URLs for a player.
    
    Args:
        username: Chess.com username
        
    Returns:
        List of archive URLs
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{CHESSCOM_API_BASE}/player/{username}/games/archives")
        response.raise_for_status()
        return response.json().get("archives", [])


async def fetch_games_from_archive(archive_url: str) -> list[dict]:
    """
    Fetch games from a monthly archive.
    
    Args:
        archive_url: URL to the monthly archive
        
    Returns:
        List of game data dictionaries
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(archive_url)
        response.raise_for_status()
        return response.json().get("games", [])


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


async def import_all_games(username: str) -> Iterator[ChessGame]:
    """
    Import all games for a Chess.com user.
    
    Args:
        username: Chess.com username
        
    Yields:
        ChessGame objects for each game
    """
    archives = await get_player_archives(username)
    for archive_url in archives:
        games = await fetch_games_from_archive(archive_url)
        for game_data in games:
            yield parse_game(game_data)


if __name__ == "__main__":
    import asyncio
    import sys
    
    async def main():
        if len(sys.argv) < 2:
            print("Usage: python chesscom.py <username>")
            sys.exit(1)
        
        username = sys.argv[1]
        print(f"Fetching games for {username}...")
        
        archives = await get_player_archives(username)
        print(f"Found {len(archives)} monthly archives")
        
        # Just fetch the most recent archive as a demo
        if archives:
            games = await fetch_games_from_archive(archives[-1])
            print(f"Most recent month has {len(games)} games")
    
    asyncio.run(main())
