const API_BASE = '/api';

export async function importChessComGames(username: string): Promise<{ message: string; games_count: number }> {
  const response = await fetch(`${API_BASE}/import/chesscom?username=${encodeURIComponent(username)}`, {
    method: 'POST',
  });
  if (!response.ok) {
    throw new Error(`Import failed: ${response.statusText}`);
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
