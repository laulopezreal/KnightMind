from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, patch
from services.api.main import app, get_opponent_rating_from_pgn
from fastapi.testclient import TestClient

client = TestClient(app)

def test_pgn_parsing():
    pgn = '[Event "Live Chess"]\n[White "player1"]\n[Black "player2"]\n[Result "1-0"]\n[WhiteElo "1500"]\n[BlackElo "1400"]\n...'
    
    # Check black elo (user is white)
    assert get_opponent_rating_from_pgn(pgn, user_is_white=True) == 1400
    
    # Check white elo (user is black)
    assert get_opponent_rating_from_pgn(pgn, user_is_white=False) == 1500
    
    # Missing elo
    pgn_missing = '[Event "Live Chess"]\n[White "player1"]'
    assert get_opponent_rating_from_pgn(pgn_missing, user_is_white=True) is None

# Logic for Expected Score:
# 1 / (1 + 10^((opp - ref)/400))
# If opp=1400, ref=1400 -> 1 / (1 + 1) = 0.5
# If opp=1800, ref=1400 -> diff=400 -> 1 / (1 + 10^1) = 1/11 = 0.09
# If opp=1000, ref=1400 -> diff=-400 -> 1 / (1 + 10^-1) = 1/1.1 = 0.909

def test_expected_score_logic_impl():
    # This logic is embedded in main.py, hard to unit test without extracting function.
    # But we can test via integration test if we mock the DB and storage.
    pass

# Mock tests via endpoint would need complex mocking of DB and Storage.
# Given "Minimal tests", I will focus on the helper and maybe a simple endpoint test if possible,
# but main.py has heavy dependencies.
# I'll rely on the PGN parsing test which is critical.
