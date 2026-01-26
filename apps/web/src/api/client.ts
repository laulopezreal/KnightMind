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
  name: string;
  moves: string;
  count: number;
  children?: OpeningNode[];
}

export async function getOpenings(): Promise<OpeningNode> {
  const response = await fetch(`${API_BASE}/openings`);
  if (!response.ok) {
    throw new Error(`Failed to fetch openings: ${response.statusText}`);
  }
  return response.json();
}
