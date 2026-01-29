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
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-purple-900 to-slate-900">
      <nav className="bg-black/30 backdrop-blur-md p-4 border-b border-white/10">
        <div className="container mx-auto flex gap-6">
          <Link to="/" className="text-xl font-bold text-purple-400">KnightMind</Link>
          <Link to="/openings" className="hover:text-purple-300 text-gray-300">Openings</Link>
          <Link to="/puzzles" className="hover:text-purple-300 text-gray-300">Puzzles</Link>
          <Link to="/engine" className="hover:text-purple-300 text-gray-300">Engine</Link>
        </div>
      </nav>

      <main className="container mx-auto p-8">
        <h1 className="text-5xl font-bold mb-4 text-white">Welcome to KnightMind</h1>
        <p className="text-gray-300 mb-12 text-lg">Your personal chess intelligence platform</p>

        <div className="grid md:grid-cols-2 gap-6 mb-8">
          {/* Import Games Card */}
          <div className="bg-white/10 backdrop-blur-md rounded-lg p-6 border border-white/20">
            <h2 className="text-2xl font-semibold mb-4 text-white">Import Games from Chess.com</h2>
            <p className="text-gray-300 mb-4">Get started by importing your games</p>
            <div className="flex gap-2 mb-4">
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="Chess.com username"
                className="flex-1 px-4 py-2 rounded bg-white/20 border border-white/30 text-white placeholder-gray-400 focus:border-purple-400 focus:outline-none focus:ring-2 focus:ring-purple-500"
                onKeyPress={(e) => e.key === 'Enter' && handleImport()}
              />
              <button
                onClick={handleImport}
                disabled={loading}
                className="px-6 py-2 bg-purple-600 hover:bg-purple-700 disabled:bg-gray-600 rounded font-medium transition-colors text-white"
              >
                {loading ? 'Importing...' : 'Import'}
              </button>
            </div>
            {status && (
              <p className={`text-sm ${isError ? 'text-red-300' : 'text-green-300'}`}>
                {status}
              </p>
            )}
          </div>

          {/* Puzzles Card */}
          <Link to="/puzzles" className="bg-white/10 backdrop-blur-md rounded-lg p-6 border border-white/20 hover:border-purple-400 transition-all group">
            <div className="flex items-start justify-between mb-4">
              <h2 className="text-2xl font-semibold text-white group-hover:text-purple-300 transition-colors">Daily Puzzles</h2>
              <span className="text-4xl">🧩</span>
            </div>
            <p className="text-gray-300 mb-4">Solve tactical puzzles from your games</p>
            <div className="text-purple-400 group-hover:text-purple-300 font-medium">
              Start solving →
            </div>
          </Link>
        </div>

        <div className="grid md:grid-cols-2 gap-6">
          {/* Openings Card */}
          <Link to="/openings" className="bg-white/10 backdrop-blur-md rounded-lg p-6 border border-white/20 hover:border-purple-400 transition-all group">
            <div className="flex items-start justify-between mb-4">
              <h2 className="text-2xl font-semibold text-white group-hover:text-purple-300 transition-colors">Opening Explorer</h2>
              <span className="text-4xl">📊</span>
            </div>
            <p className="text-gray-300 mb-4">Analyze your opening repertoire</p>
            <div className="text-purple-400 group-hover:text-purple-300 font-medium">
              Explore openings →
            </div>
          </Link>

          {/* Engine Card */}
          <Link to="/engine" className="bg-white/10 backdrop-blur-md rounded-lg p-6 border border-white/20 hover:border-purple-400 transition-all group">
            <div className="flex items-start justify-between mb-4">
              <h2 className="text-2xl font-semibold text-white group-hover:text-purple-300 transition-colors">Engine Analysis</h2>
              <span className="text-4xl">⚙️</span>
            </div>
            <p className="text-gray-300 mb-4">Analyze positions with Stockfish</p>
            <div className="text-purple-400 group-hover:text-purple-300 font-medium">
              Analyze positions →
            </div>
          </Link>
        </div>
      </main>
    </div>
  );
}
