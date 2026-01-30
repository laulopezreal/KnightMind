-- Migration 003: Add FEN evaluation cache table
-- This table caches Stockfish evaluations to improve performance

CREATE TABLE IF NOT EXISTS fen_eval_cache (
    key TEXT PRIMARY KEY,
    fen TEXT NOT NULL,
    best_move_uci TEXT NOT NULL,
    eval_pawns REAL NOT NULL,
    depth INTEGER,
    movetime_ms INTEGER,
    engine_name TEXT,
    engine_version TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Index on FEN for debugging queries (optional but useful)
CREATE INDEX IF NOT EXISTS idx_fen_eval_cache_fen ON fen_eval_cache(fen);

-- Index on created_at for potential future TTL cleanup
CREATE INDEX IF NOT EXISTS idx_fen_eval_cache_created_at ON fen_eval_cache(created_at);
