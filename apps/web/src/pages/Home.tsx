import { useState } from 'react';
import { Link } from 'react-router-dom';
import { importChessComGames, ApiError } from '../api/client';

export default function Home() {
  const [username, setUsername] = useState('');
  const [status, setStatus] = useState<string | null>(null);
  const [isError, setIsError] = useState(false);
  const [loading, setLoading] = useState(false);

  const handleImport = async () => {
    if (!username.trim()) {
      setStatus('Please enter a Chess.com username');
      setIsError(true);
      return;
    }
    setLoading(true);
    setStatus('Fetching games from Chess.com... This may take a moment.');
    setIsError(false);
    
    try {
      const result = await importChessComGames(username);
      
      if (result.games_count === 0) {
        setStatus('No games found for this user.');
        setIsError(false);
      } else if (result.new_games === 0) {
        setStatus(`All ${result.games_count} games already imported.`);
        setIsError(false);
      } else {
        setStatus(
          `Imported ${result.new_games} new games. ` +
          `Total: ${result.games_count} games` +
          (result.skipped_duplicates > 0 ? ` (${result.skipped_duplicates} duplicates skipped)` : '')
        );
        setIsError(false);
      }
    } catch (error) {
      if (error instanceof ApiError) {
        setStatus(error.detail || error.message);
      } else {
        setStatus(error instanceof Error ? error.message : 'Unknown error');
      }
      setIsError(true);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-900 text-white">
      <nav className="bg-gray-800 p-4">
        <div className="container mx-auto flex gap-6">
          <Link to="/" className="text-xl font-bold text-emerald-400">KnightMind</Link>
          <Link to="/openings" className="hover:text-emerald-400">Openings</Link>
        </div>
      </nav>
      
      <main className="container mx-auto p-8">
        <h1 className="text-4xl font-bold mb-8">Welcome to KnightMind</h1>
        <p className="text-gray-400 mb-8">Your personal chess intelligence platform</p>
        
        <div className="bg-gray-800 rounded-lg p-6 max-w-md">
          <h2 className="text-xl font-semibold mb-4">Import Games from Chess.com</h2>
          <div className="flex gap-2 mb-4">
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="Chess.com username"
              className="flex-1 px-4 py-2 rounded bg-gray-700 border border-gray-600 focus:border-emerald-400 focus:outline-none"
            />
            <button
              onClick={handleImport}
              disabled={loading}
              className="px-6 py-2 bg-emerald-600 hover:bg-emerald-700 disabled:bg-gray-600 rounded font-medium transition-colors"
            >
              {loading ? 'Importing...' : 'Import'}
            </button>
          </div>
          {status && (
            <p className={`text-sm ${isError ? 'text-red-400' : 'text-emerald-400'}`}>
              {status}
            </p>
          )}
        </div>
      </main>
    </div>
  );
}
