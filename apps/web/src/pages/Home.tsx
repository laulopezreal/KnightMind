import { useState } from 'react';
import { Link } from 'react-router-dom';
import { importChessComGames } from '../api/client';

export default function Home() {
  const [username, setUsername] = useState('');
  const [status, setStatus] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleImport = async () => {
    if (!username.trim()) {
      setStatus('Please enter a Chess.com username');
      return;
    }
    setLoading(true);
    setStatus(null);
    try {
      const result = await importChessComGames(username);
      setStatus(`${result.message} (${result.games_count} games)`);
    } catch (error) {
      setStatus(`Error: ${error instanceof Error ? error.message : 'Unknown error'}`);
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
            <p className={`text-sm ${status.startsWith('Error') ? 'text-red-400' : 'text-emerald-400'}`}>
              {status}
            </p>
          )}
        </div>
      </main>
    </div>
  );
}
