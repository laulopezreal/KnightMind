import { Link } from 'react-router-dom';

export default function HowItWorks() {
  return (
    <div className="space-y-12 animate-teedin">
      <section className="space-y-5 max-w-3xl">
        <h1 className="text-5xl md:text-7xl font-serif text-primary tracking-tight">How KnightMind Works</h1>
        <p className="text-lg md:text-xl font-light text-primary/60 leading-relaxed">
          KnightMind turns your own games into a personal training system: import games, detect mistakes, practice
          custom puzzles, and track long-term improvement.
        </p>
      </section>

      <section className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <article className="bg-primary/5 border border-primary/10 rounded-sm p-6 space-y-3">
          <p className="text-xs tracking-[0.2em] uppercase text-primary/40 font-sans">Step 1</p>
          <h2 className="text-2xl font-serif text-primary">Import your games</h2>
          <p className="text-primary/60 font-sans">
            Connect your Chess.com username and sync your latest games into KnightMind.
          </p>
        </article>

        <article className="bg-primary/5 border border-primary/10 rounded-sm p-6 space-y-3">
          <p className="text-xs tracking-[0.2em] uppercase text-primary/40 font-sans">Step 2</p>
          <h2 className="text-2xl font-serif text-primary">Generate custom puzzles</h2>
          <p className="text-primary/60 font-sans">
            KnightMind uses Stockfish to create puzzles from your own missed opportunities and blunders.
          </p>
        </article>

        <article className="bg-primary/5 border border-primary/10 rounded-sm p-6 space-y-3">
          <p className="text-xs tracking-[0.2em] uppercase text-primary/40 font-sans">Step 3</p>
          <h2 className="text-2xl font-serif text-primary">Train and review</h2>
          <p className="text-primary/60 font-sans">
            Practice with spaced repetition and use insights pages to identify recurring motifs over time.
          </p>
        </article>
      </section>

      <section className="bg-primary/5 border border-primary/10 rounded-sm p-8 max-w-3xl space-y-4">
        <h2 className="text-3xl font-serif text-primary">Ready to start?</h2>
        <p className="text-primary/60 font-sans">
          Open the home page, set your Chess.com username, and run your first sync.
        </p>
        <div className="flex flex-wrap gap-4">
          <Link
            to="/"
            className="px-6 py-3 bg-primary text-bg-primary rounded-sm font-serif km-interactive km-focus-visible"
          >
            Go to Home
          </Link>
          <Link
            to="/puzzles"
            className="px-6 py-3 border border-primary/20 text-primary hover:bg-primary hover:text-bg-primary hover:border-transparent rounded-sm font-serif transition-all km-interactive km-focus-visible"
          >
            Explore Training
          </Link>
        </div>
      </section>
    </div>
  );
}
