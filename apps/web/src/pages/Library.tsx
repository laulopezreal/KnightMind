import { LOCALE } from '../utils/locale';
import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useChessUsername } from '../context/ChessUsernameContext';
import {
    getLibraryPuzzles,
    type LibraryPuzzle,
    type LibraryCorpusStats,
    type PuzzleDiagnosisSummary,
    type PuzzleStatus,
    type PuzzleDifficulty,
    type PuzzleSort,
} from '../api/puzzles';
import { PageHeader } from '../components/PageHeader';
import { DataStateError, DataStateLoading } from '../components/DataState';

const PAGE_SIZE = 50;

const STATUS_OPTIONS: { value: PuzzleStatus | ''; label: string }[] = [
    { value: '', label: 'All' },
    { value: 'due', label: 'Due' },
    { value: 'new', label: 'New' },
    { value: 'learning', label: 'Learning' },
    { value: 'mastered', label: 'Mastered' },
];

const DIFFICULTY_OPTIONS: { value: PuzzleDifficulty | ''; label: string }[] = [
    { value: '', label: 'All' },
    { value: 'easy', label: 'Easy' },
    { value: 'medium', label: 'Medium' },
    { value: 'hard', label: 'Hard' },
];

const SORT_OPTIONS: { value: PuzzleSort; label: string }[] = [
    { value: 'due_soonest', label: 'Due soonest' },
    { value: 'last_attempted', label: 'Last attempted' },
    { value: 'most_failed', label: 'Most failed' },
    { value: 'difficulty_asc', label: 'Difficulty ↑' },
    { value: 'difficulty_desc', label: 'Difficulty ↓' },
    { value: 'newest', label: 'Newest' },
];

function StatusBadge({ status }: { status: PuzzleStatus }) {
    const styles: Record<PuzzleStatus, string> = {
        new: 'bg-status-new-soft text-status-new',
        due: 'bg-status-due-soft text-status-due',
        learning: 'bg-status-learning-soft text-status-learning',
        mastered: 'bg-status-mastered-soft text-status-mastered',
    };
    return (
        <span className={`text-xs font-sans px-2 py-0.5 rounded-sm uppercase tracking-wider ${styles[status]}`}>
            {status}
        </span>
    );
}

function DifficultyBadge({ difficulty }: { difficulty: PuzzleDifficulty }) {
    const styles: Record<PuzzleDifficulty, string> = {
        easy: 'text-status-mastered',
        medium: 'text-status-learning',
        hard: 'text-negative',
    };
    return (
        <span className={`text-xs font-mono uppercase ${styles[difficulty]}`}>
            {difficulty}
        </span>
    );
}

function DiagnosisBadge({ summary }: { summary: PuzzleDiagnosisSummary | null }) {
    if (!summary) return null;

    const label = summary.primary_cause_label
        || (summary.state === 'unclear' ? 'Cause unclear' : summary.state === 'unavailable' ? 'Diagnosis unavailable' : 'Cause unknown');

    return (
        <span
            aria-label="Diagnosis cause"
            className="text-xs font-sans text-primary/75 px-2 py-0.5 bg-primary/10 border border-primary/10 rounded-sm"
        >
            Cause: {label}
        </span>
    );
}

function PuzzleRow({ puzzle }: { puzzle: LibraryPuzzle }) {
    const successRate = puzzle.attempts > 0
        ? Math.round((puzzle.pass_count / puzzle.attempts) * 100)
        : null;

    return (
        <Link
            to={`/library/${puzzle.id}`}
            className="block bg-primary/5 border border-primary/10 rounded-sm p-4 km-interactive transition-all hover:border-primary/30 hover:bg-primary/8"
        >
            <div className="flex items-center justify-between gap-4">
                {/* Left: title + badges */}
                <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-serif text-primary truncate">
                            {puzzle.title || puzzle.id.slice(0, 8)}
                        </span>
                        <StatusBadge status={puzzle.status} />
                        <DifficultyBadge difficulty={puzzle.difficulty} />
                        {puzzle.primary_motif && (
                            <span className="text-xs font-sans text-primary/70 px-2 py-0.5 bg-primary/10 rounded-sm">
                                {puzzle.primary_motif}
                            </span>
                        )}
                        <DiagnosisBadge summary={puzzle.diagnosis_summary} />
                    </div>
                    {/* Stats row */}
                    <div className="flex items-center gap-4 mt-1 text-xs font-sans text-primary/70">
                        {puzzle.attempts > 0 && (
                            <span>
                                {puzzle.pass_count}/{puzzle.attempts} solved
                                {successRate !== null && ` (${successRate}%)`}
                            </span>
                        )}
                        {puzzle.fail_count > 0 && (
                            <span className="text-negative">{puzzle.fail_count} failed</span>
                        )}
                        {puzzle.last_reviewed_at && (
                            <span>
                                Last: {new Date(puzzle.last_reviewed_at).toLocaleDateString(LOCALE)}
                            </span>
                        )}
                        {puzzle.next_due_at && (
                            <span>
                                Due: {new Date(puzzle.next_due_at).toLocaleDateString(LOCALE)}
                            </span>
                        )}
                    </div>
                </div>

                {/* Right: side to move indicator */}
                <div className="flex-shrink-0">
                    <span className="text-xs font-sans text-primary/70 uppercase tracking-wider">
                        {puzzle.side_to_move === 'white' ? 'W' : 'B'}
                    </span>
                </div>
            </div>
        </Link>
    );
}

export default function Library() {
    const { username, setEditorOpen } = useChessUsername();

    // Data
    const [puzzles, setPuzzles] = useState<LibraryPuzzle[]>([]);
    const [total, setTotal] = useState(0);
    const [availableMotifs, setAvailableMotifs] = useState<string[]>([]);
    const [corpusStats, setCorpusStats] = useState<LibraryCorpusStats | null>(null);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Filters
    const [search, setSearch] = useState('');
    const [statusFilter, setStatusFilter] = useState<PuzzleStatus | ''>('');
    const [difficultyFilter, setDifficultyFilter] = useState<PuzzleDifficulty | ''>('');
    const [motifFilter, setMotifFilter] = useState('');
    const [sort, setSort] = useState<PuzzleSort>('due_soonest');
    const [offset, setOffset] = useState(0);

    // Debounced search
    const [debouncedSearch, setDebouncedSearch] = useState('');
    useEffect(() => {
        const timer = setTimeout(() => setDebouncedSearch(search), 300);
        return () => clearTimeout(timer);
    }, [search]);

    // Reset offset when filters change
    useEffect(() => {
        setOffset(0);
    }, [debouncedSearch, statusFilter, difficultyFilter, motifFilter, sort]);

    const fetchPuzzles = useCallback(async () => {
        if (!username) return;
        setIsLoading(true);
        setError(null);
        try {
            const res = await getLibraryPuzzles({
                username,
                q: debouncedSearch || undefined,
                status: statusFilter || undefined,
                motif: motifFilter || undefined,
                difficulty: difficultyFilter || undefined,
                sort,
                limit: PAGE_SIZE,
                offset,
            });
            setPuzzles(res.puzzles);
            setTotal(res.total);
            setAvailableMotifs(res.available_motifs);
            setCorpusStats(res.stats);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to load puzzles');
        } finally {
            setIsLoading(false);
        }
    }, [username, debouncedSearch, statusFilter, difficultyFilter, motifFilter, sort, offset]);

    useEffect(() => {
        fetchPuzzles();
    }, [fetchPuzzles]);

    const totalPages = Math.ceil(total / PAGE_SIZE);
    const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

    if (!username) {
        return (
            <div className="space-y-12 animate-teedin">
                <PageHeader
                    title="Library"
                    subtitle="Puzzles from your own games. Every position here is a moment you can learn from."
                />
                <div className="bg-primary/5 border border-primary/10 rounded-sm p-6 text-center space-y-4">
                    <h3 className="font-serif text-xl text-primary">Set your username to get started</h3>
                    <button
                        type="button"
                        onClick={() => setEditorOpen(true)}
                        className="px-6 py-2 bg-primary text-bg-primary rounded-sm font-serif transition-colors km-interactive km-focus-visible"
                    >
                        Set Username
                    </button>
                </div>
            </div>
        );
    }

    return (
        <div className="space-y-8 animate-teedin">
            {/* Header */}
            <PageHeader
                title="Library"
                subtitle="Puzzles from your own games. Every position here is a moment you can learn from."
            />

            {/* Corpus stats */}
            {corpusStats && corpusStats.total > 0 && (
                <section className="grid grid-cols-2 md:grid-cols-5 gap-3">
                    {[
                        // Labels are neutral text-primary/70: a coloured /60 label
                        // rendered ~2:1 (fails AA) in both themes, and a single
                        // colour token can't pass on both the light and dark card
                        // backgrounds. The colour coding stays on the large value +
                        // the container tint, which do carry the category.
                        { label: 'Total', value: corpusStats.total, containerClasses: 'bg-primary/5 border-primary/10', valueClasses: 'text-primary', labelClasses: 'text-primary/70' },
                        { label: 'Due', value: corpusStats.due, containerClasses: 'bg-orange-500/5 border-orange-500/15', valueClasses: 'text-status-due', labelClasses: 'text-primary/70' },
                        { label: 'New', value: corpusStats.new, containerClasses: 'bg-blue-500/5 border-blue-500/15', valueClasses: 'text-status-new', labelClasses: 'text-primary/70' },
                        { label: 'Learning', value: corpusStats.learning, containerClasses: 'bg-yellow-500/5 border-yellow-500/15', valueClasses: 'text-status-learning', labelClasses: 'text-primary/70' },
                        { label: 'Mastered', value: corpusStats.mastered, containerClasses: 'bg-green-500/5 border-green-500/15', valueClasses: 'text-status-mastered', labelClasses: 'text-primary/70' },
                    ].map(stat => (
                        <div key={stat.label} className={`${stat.containerClasses} border rounded-sm p-3 text-center`}>
                            <span className={`block text-2xl font-serif ${stat.valueClasses}`}>{stat.value}</span>
                            <span className={`text-xs font-sans ${stat.labelClasses} uppercase tracking-wider`}>{stat.label}</span>
                        </div>
                    ))}
                </section>
            )}

            {/* Search + Filters */}
            <section className="bg-primary/5 border border-primary/10 rounded-sm p-4 space-y-4">
                {/* Search */}
                <input
                    type="text"
                    placeholder="Search by title or ID..."
                    aria-label="Search puzzles by title or ID"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    className="w-full bg-transparent border-b border-primary/20 py-2 text-primary font-sans placeholder:text-primary/70 focus:outline-none focus:border-primary/60 transition-colors"
                />

                {/* Filter row */}
                <div className="flex flex-wrap gap-3 items-center text-sm font-sans">
                    {/* Status */}
                    <select
                        value={statusFilter}
                        onChange={(e) => setStatusFilter(e.target.value as PuzzleStatus | '')}
                        aria-label="Filter by status"
                        className="bg-bg-primary border border-primary/20 rounded-sm px-3 py-1.5 text-primary focus:outline-none focus:border-primary/60"
                    >
                        {STATUS_OPTIONS.map(o => (
                            <option key={o.value} value={o.value}>{o.label}</option>
                        ))}
                    </select>

                    {/* Difficulty */}
                    <select
                        value={difficultyFilter}
                        onChange={(e) => setDifficultyFilter(e.target.value as PuzzleDifficulty | '')}
                        aria-label="Filter by difficulty"
                        className="bg-bg-primary border border-primary/20 rounded-sm px-3 py-1.5 text-primary focus:outline-none focus:border-primary/60"
                    >
                        {DIFFICULTY_OPTIONS.map(o => (
                            <option key={o.value} value={o.value}>{o.label}</option>
                        ))}
                    </select>

                    {/* Motif */}
                    {availableMotifs.length > 0 && (
                        <select
                            value={motifFilter}
                            onChange={(e) => setMotifFilter(e.target.value)}
                            aria-label="Filter by motif"
                            className="bg-bg-primary border border-primary/20 rounded-sm px-3 py-1.5 text-primary focus:outline-none focus:border-primary/60"
                        >
                            <option value="">All motifs</option>
                            {availableMotifs.map(m => (
                                <option key={m} value={m}>{m}</option>
                            ))}
                        </select>
                    )}

                    {/* Sort */}
                    <select
                        value={sort}
                        onChange={(e) => setSort(e.target.value as PuzzleSort)}
                        aria-label="Sort puzzles"
                        className="bg-bg-primary border border-primary/20 rounded-sm px-3 py-1.5 text-primary focus:outline-none focus:border-primary/60 ml-auto"
                    >
                        {SORT_OPTIONS.map(o => (
                            <option key={o.value} value={o.value}>{o.label}</option>
                        ))}
                    </select>
                </div>

                {/* Result count — hidden on error so it can't contradict the error box */}
                {!error && (
                    <div className="text-xs font-sans text-primary/70">
                        {isLoading ? (
                            <DataStateLoading label="Loading library puzzles..." compact />
                        ) : (
                            <span>{total} puzzle{total !== 1 ? 's' : ''}</span>
                        )}
                    </div>
                )}
            </section>

            {/* Error */}
            {error && (
                <DataStateError
                    message={error}
                    onRetry={fetchPuzzles}
                    retryLabel="Retry"
                    ariaLabel="Retry loading library puzzles"
                    compact
                />
            )}


            {isLoading && puzzles.length === 0 && !error && (
                <DataStateLoading label="Loading library puzzles..." />
            )}

            {/* Puzzle list */}
            <section className="space-y-2">
                {puzzles.map(puzzle => (
                    <PuzzleRow key={puzzle.id} puzzle={puzzle} />
                ))}

                {!isLoading && puzzles.length === 0 && !error && (
                    <div className="bg-primary/5 border border-primary/10 rounded-sm p-8 text-center space-y-4">
                        <p className="text-primary/70 font-sans">
                            {/* Key off the whole corpus, not individual filters:
                                total > 0 means filters excluded everything; total 0
                                means the library is genuinely empty (even mid-search).
                                Robust to future filters too. */}
                            {corpusStats && corpusStats.total > 0
                                ? 'No puzzles match your filters.'
                                : "You don't have any puzzles yet. Generate some from your games to start building your library."}
                        </p>
                        {/* A genuinely-empty library was a dead end (copy told the
                            user to generate, with nowhere to do it). Point at the
                            Train page, which owns generation and itself routes a
                            games-less user onward to import. */}
                        {(!corpusStats || corpusStats.total === 0) && (
                            <Link
                                to="/puzzles"
                                className="inline-block px-6 py-2 bg-primary text-bg-primary rounded-sm font-serif transition-colors km-interactive km-focus-visible"
                            >
                                Generate Puzzles
                            </Link>
                        )}
                    </div>
                )}
            </section>

            {/* Pagination — hidden on error so stale totals can't contradict the
                error box (same guard as the result count above). */}
            {!error && totalPages > 1 && (
                <div className="flex justify-center items-center gap-4 font-sans text-sm">
                    <button
                        type="button"
                        onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                        disabled={offset === 0}
                        className="px-4 py-2 border border-primary/20 rounded-sm text-primary km-interactive km-focus-visible disabled:opacity-30 disabled:cursor-default"
                    >
                        Previous
                    </button>
                    <span className="text-primary/70">
                        Page {currentPage} of {totalPages}
                    </span>
                    <button
                        type="button"
                        onClick={() => setOffset(offset + PAGE_SIZE)}
                        disabled={currentPage >= totalPages}
                        className="px-4 py-2 border border-primary/20 rounded-sm text-primary km-interactive km-focus-visible disabled:opacity-30 disabled:cursor-default"
                    >
                        Next
                    </button>
                </div>
            )}

            {/* Training nudge */}
            <div className="text-center text-sm font-sans text-primary/70">
                {corpusStats && corpusStats.due > 0 ? (
                    <Link to="/puzzles" className="km-interactive km-inline-link underline decoration-primary/20 underline-offset-4 hover:text-primary/70 transition-colors">
                        {corpusStats.due} puzzle{corpusStats.due !== 1 ? 's' : ''} due for review — Start Training
                    </Link>
                ) : (
                    <Link to="/puzzles" className="km-interactive km-inline-link underline decoration-primary/20 underline-offset-4 hover:text-primary/70 transition-colors">
                        Go to Training
                    </Link>
                )}
            </div>
        </div>
    );
}
