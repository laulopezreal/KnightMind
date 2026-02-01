import { useState } from 'react';
import { Link } from 'react-router-dom';
import { importChessComGames, ApiError } from '../api';
import { useChessUsername } from '../context/ChessUsernameContext';


export default function Home() {
  const { username } = useChessUsername();
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
    setStatus('Fetching games...');
    setIsError(false);

    try {
      const result = await importChessComGames(username);
      if (result.games_count === 0) {
        setStatus('No games found.');
      } else if (result.new_games === 0) {
        setStatus('No new games found.');
      } else {
        setStatus(`Imported ${result.new_games} new games.`);
      }
      setIsError(false);
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
    <div className="space-y-20 animate-teedin">
      <section className="space-y-6">
        <h1 className="text-6xl md:text-8xl font-serif text-primary tracking-tight">
          KnightMind
        </h1>
        <p className="text-xl md:text-2xl font-light text-primary/60 max-w-2xl leading-relaxed">
          Your personal chess intelligence platform. <br />
          Analyze your games, find patterns, and master your intuition.
        </p>
      </section>

      <section className="grid grid-cols-1 md:grid-cols-2 gap-16">
        <div className="space-y-8">
          <div className="prose">
            <h3 className="text-2xl font-serif text-primary">
              {username ? 'Welcome Back' : 'Begin Analysis'}
            </h3>
            <p className="font-sans text-primary/60">
              {username
                ? 'Have you played more? Sync to import your newest games, or jump straight into insights.'
                : 'Import your games from Chess.com to generate personalized puzzles and insights.'}
            </p>
          </div>

          <div className="space-y-4">
            <div className="flex gap-4 border-b border-primary/20 pb-2 focus-within:border-primary/80 transition-colors">
              {username ? (
                <div className="flex-1 flex items-center text-lg font-sans text-primary">
                  <span className="opacity-60 mr-2">Chess.com:</span>
                  <span className="font-medium">{username}</span>
                </div>
              ) : (
                <div className="flex-1 flex items-center text-lg font-sans text-primary/40 italic">
                  Set your Chess.com username to import games
                </div>
              )}
              <button
                type="button"
                onClick={handleImport}
                disabled={loading || !username}
                className={`text-primary font-medium transition-opacity uppercase tracking-widest text-sm km-focus-visible rounded-sm px-2 py-1 ${loading || !username ? 'km-interactive-disabled disabled:opacity-30' : 'km-interactive'}`}
              >
                {loading ? '...' : username ? 'Sync' : 'Import'}
              </button>
            </div>

            {status && (
              <p className={`text-sm font-sans tracking-wide ${isError ? 'text-red-500/80' : 'text-primary/60'}`}>
                {status}
              </p>
            )}
          </div>
        </div>

        <div className="space-y-8">
          <h3 className="text-2xl font-serif text-primary">Explore</h3>
          <div className="flex flex-col gap-6 font-sans text-lg">
            <Link to="/puzzles" className="group flex items-center justify-between border-b border-primary/10 py-4 hover:border-primary/40 transition-colors">
              <span className="group-hover:translate-x-2 transition-transform duration-500">Daily Puzzles</span>
              <span className="opacity-0 group-hover:opacity-100 transition-opacity">→</span>
            </Link>
            <Link to="/openings" className="group flex items-center justify-between border-b border-primary/10 py-4 hover:border-primary/40 transition-colors">
              <span className="group-hover:translate-x-2 transition-transform duration-500">Opening Explorer</span>
              <span className="opacity-0 group-hover:opacity-100 transition-opacity">→</span>
            </Link>
          </div>
        </div>
      </section>
    </div>
  );
}
