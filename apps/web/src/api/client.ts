const API_BASE = '/api';

export interface ImportResult {
  message: string;
  games_count: number;
  new_games: number;
  skipped_duplicates: number;
}

export class ApiError extends Error {
  statusCode: number;
  detail?: string;

  constructor(message: string, statusCode: number, detail?: string) {
    super(message);
    this.name = 'ApiError';
    this.statusCode = statusCode;
    this.detail = detail;
  }
}

export async function importChessComGames(username: string): Promise<ImportResult> {
  const response = await fetch(`${API_BASE}/import/chesscom?username=${encodeURIComponent(username)}`, {
    method: 'POST',
  });
  
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    const detail = errorData.detail || response.statusText;
    
    if (response.status === 404) {
      throw new ApiError('User not found', 404, detail);
    } else if (response.status === 429) {
      throw new ApiError('Rate limited by Chess.com', 429, detail);
    } else if (response.status === 502) {
      throw new ApiError('Network error connecting to Chess.com', 502, detail);
    } else {
      throw new ApiError(`Import failed: ${detail}`, response.status, detail);
    }
  }
  
  return response.json();
}

export interface OpeningNode {
  move_san: string;
  ply: number;
  games_count: number;
  wins: number;
  draws: number;
  losses: number;
  win_rate: number;
  children?: OpeningNode[];
}

export type ColorFilter = 'white' | 'black' | 'both';

export async function getOpenings(
  username: string,
  color: ColorFilter = 'both',
  maxPly: number = 12
): Promise<OpeningNode> {
  const params = new URLSearchParams({
    username,
    color,
    max_ply: maxPly.toString(),
  });
  
  const response = await fetch(`${API_BASE}/openings?${params}`);
  
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    const detail = errorData.detail || response.statusText;
    
    if (response.status === 404) {
      throw new ApiError('No games found', 404, detail);
    } else {
      throw new ApiError(`Failed to fetch openings: ${detail}`, response.status, detail);
    }
  }
  
  return response.json();
}

// Engine evaluation
export interface EvalResult {
  best_move_uci: string;
  eval: number;
}

export interface EngineStatus {
  available: boolean;
  message: string;
}

export async function evaluateFen(fen: string): Promise<EvalResult> {
  const response = await fetch(`${API_BASE}/engine/eval`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fen }),
  });
  
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    const detail = errorData.detail || response.statusText;
    throw new ApiError(`Evaluation failed: ${detail}`, response.status, detail);
  }
  
  return response.json();
}

export async function getEngineStatus(): Promise<EngineStatus> {
  const response = await fetch(`${API_BASE}/engine/status`);
  return response.json();
}
