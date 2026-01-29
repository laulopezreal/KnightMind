"""Tests for the game storage module."""

import pytest
import tempfile
import shutil

from .games import GameStorage


@pytest.fixture
def storage():
    """Create a temporary storage for tests."""
    temp_dir = tempfile.mkdtemp()
    storage = GameStorage(temp_dir)
    yield storage
    shutil.rmtree(temp_dir)


class TestGameStorage:
    """Tests for GameStorage class."""
    
    def test_store_game_creates_files(self, storage):
        """Test that storing a game creates PGN and metadata files."""
        is_new, game_id = storage.store_game(
            username="testuser",
            url="https://chess.com/game/12345",
            pgn='[Event "Test"]\n1. e4 e5 *',
            white_username="testuser",
            black_username="opponent",
            white_result="win",
            black_result="lose",
            time_control="600",
            end_time=1704067200,
            rated=True,
        )
        
        assert is_new is True
        assert game_id is not None
        
        # Check PGN file exists
        pgn_file = storage.pgn_path / "testuser" / f"{game_id}.pgn"
        assert pgn_file.exists()
        assert pgn_file.read_text() == '[Event "Test"]\n1. e4 e5 *'
        
        # Check metadata file exists
        metadata_file = storage.metadata_path / "testuser" / f"{game_id}.json"
        assert metadata_file.exists()
    
    def test_store_game_deduplication(self, storage):
        """Test that duplicate games are not stored twice."""
        url = "https://chess.com/game/12345"
        
        # First store
        is_new1, game_id1 = storage.store_game(
            username="testuser",
            url=url,
            pgn='[Event "Test"]\n1. e4 e5 *',
            white_username="testuser",
            black_username="opponent",
            white_result="win",
            black_result="lose",
            time_control="600",
            end_time=1704067200,
            rated=True,
        )
        
        # Second store with same URL
        is_new2, game_id2 = storage.store_game(
            username="testuser",
            url=url,
            pgn='[Event "Test"]\n1. e4 e5 *',
            white_username="testuser",
            black_username="opponent",
            white_result="win",
            black_result="lose",
            time_control="600",
            end_time=1704067200,
            rated=True,
        )
        
        assert is_new1 is True
        assert is_new2 is False
        assert game_id1 == game_id2
        assert storage.get_game_count("testuser") == 1
    
    def test_game_exists(self, storage):
        """Test checking if a game exists."""
        url = "https://chess.com/game/12345"
        
        assert storage.game_exists("testuser", url) is False
        
        storage.store_game(
            username="testuser",
            url=url,
            pgn='[Event "Test"]\n1. e4 e5 *',
            white_username="testuser",
            black_username="opponent",
            white_result="win",
            black_result="lose",
            time_control="600",
            end_time=1704067200,
            rated=True,
        )
        
        assert storage.game_exists("testuser", url) is True
    
    def test_get_game_count(self, storage):
        """Test counting games for a user."""
        assert storage.get_game_count("testuser") == 0
        
        for i in range(3):
            storage.store_game(
                username="testuser",
                url=f"https://chess.com/game/{i}",
                pgn=f'[Event "Test {i}"]\n1. e4 e5 *',
                white_username="testuser",
                black_username="opponent",
                white_result="win",
                black_result="lose",
                time_control="600",
                end_time=1704067200 + i,
                rated=True,
            )
        
        assert storage.get_game_count("testuser") == 3
    
    def test_get_pgn(self, storage):
        """Test retrieving PGN for a game."""
        pgn = '[Event "Test"]\n1. e4 e5 *'
        is_new, game_id = storage.store_game(
            username="testuser",
            url="https://chess.com/game/12345",
            pgn=pgn,
            white_username="testuser",
            black_username="opponent",
            white_result="win",
            black_result="lose",
            time_control="600",
            end_time=1704067200,
            rated=True,
        )
        
        retrieved_pgn = storage.get_pgn("testuser", game_id)
        assert retrieved_pgn == pgn
    
    def test_get_pgn_not_found(self, storage):
        """Test retrieving PGN for non-existent game."""
        assert storage.get_pgn("testuser", "nonexistent") is None
    
    def test_get_all_metadata(self, storage):
        """Test retrieving all metadata for a user."""
        for i in range(3):
            storage.store_game(
                username="testuser",
                url=f"https://chess.com/game/{i}",
                pgn=f'[Event "Test {i}"]\n1. e4 e5 *',
                white_username="testuser",
                black_username=f"opponent{i}",
                white_result="win",
                black_result="lose",
                time_control="600",
                end_time=1704067200 + i * 1000,
                rated=True,
            )
        
        metadata = storage.get_all_metadata("testuser")
        assert len(metadata) == 3
        
        # Should be sorted by end_time descending
        assert metadata[0].end_time > metadata[1].end_time
        assert metadata[1].end_time > metadata[2].end_time
    
    def test_username_case_insensitive(self, storage):
        """Test that username handling is case-insensitive."""
        storage.store_game(
            username="TestUser",
            url="https://chess.com/game/12345",
            pgn='[Event "Test"]\n1. e4 e5 *',
            white_username="testuser",
            black_username="opponent",
            white_result="win",
            black_result="lose",
            time_control="600",
            end_time=1704067200,
            rated=True,
        )
        
        assert storage.get_game_count("testuser") == 1
        assert storage.get_game_count("TESTUSER") == 1
        assert storage.get_game_count("TestUser") == 1
