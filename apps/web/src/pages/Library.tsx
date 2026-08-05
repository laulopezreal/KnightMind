import { LOCALE } from '../utils/locale';
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAsyncData } from '../hooks/useAsyncData';
import { useChessUsername } from '../context/ChessUsernameContext';
import {
    getLibraryPuzzles,
    type LibraryPuzzle,
    type PuzzleDiagnosisSummary,
    type PuzzleStatus,
    type PuzzleDifficulty,
    type PuzzleSort,
} from '../api/puzzles';
import { PageHeader } from '../components/PageHeader';
import { DataStateError, DataStateLoading } from '../components/DataState';
import { ConnectAccountEmpty } from '../components/ConnectAccountEmpty';

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

    const cls = "text-xs font-sans text-primary/75 px-2 py-0.5 bg-primary/10 border border-primary/10 rounded-sm";

    if (summary.state === 'unclear') {
        return <span aria-label="Diagnosis cause" className={cls}>Cause unclear</span>;
    }
    if (summary.state === 'unavailable') {
        return <span aria-label="Diagnosis cause" className={cls}>Diagnosis unavailable</span>;
    }
    // state === 'ready': show the human-readable label, falling back to the raw key
    const label = summary.primary_cause_label || summary.primary_cause || 'Cause unknown';
    return (
        <span aria-label="Diagnosis cause" className={cls}>
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
    const { username } = useChessUsername();

    // Filters
    const [search, setSearch] = useState('');
    const [statusFilter, setStatusFilter] = useState<PuzzleStatus | ''>('');
    const [difficultyFilter, setDifficultyFilter] = useState<PuzzleDifficulty | ''>('');
    const [motifFilter, setMotifFilter] = useState('');
    // Seeded from the URL so the Insights "practise this" links land filtered.
    // Without this the CTA silently dropped its parameter and dumped the user
    // in the unfiltered library.
    const [causeFilter, setCauseFilter] = useState(
        () => new URLSearchParams(window.location.search).get('cause') ?? ''
    );
    const [phaseFilter, setPhaseFilter] = useState(
        () => new URLSearchParams(window.location.search).get('phase') ?? ''
    );
    const [openingFilter, setOpeningFilter] = useState(
        () => new URLSearchParams(window.location.search).get('opening') ?? ''
    );
    // Line-level, set only by the Openings explorer link. There is no control
    // for it: the family select is the browsable axis, and a dropdown of every
    // line the corpus contains would be unusable.
    const [openingLineFilter, setOpeningLineFilter] = useState(
        () => new URLSearchParams(window.location.search).get('opening_line') ?? ''
    );
    const [sort, setSort] = useState<PuzzleSort>('due_soonest');
    const [offset, setOffset] = useState(0);

    // Debounced search
    const [debouncedSearch, setDebouncedSearch] = useState('');
    useEffect(() => {
        const timer = setTimeout(() => setDebouncedSearch(search), 300);
        return () => clearTimeout(timer);
    }, [search]);

    // Reset paging when the filters change. Adjusted during render rather than
    // in an effect: `offset` is a dependency of the fetch below, so resetting it
    // from an effect fired a SECOND request on every filter change -- one for the
    // old offset, then one for 0. This is React's documented
    // adjusting-state-when-props-change pattern; the re-render happens before
    // anything commits, so no request goes out for the intermediate state.
    const filterKey = [
        debouncedSearch,
        statusFilter,
        difficultyFilter,
        motifFilter,
        causeFilter,
        phaseFilter,
        openingFilter,
        openingLineFilter,
        sort,
    ].join('\u0000');
    const [lastFilterKey, setLastFilterKey] = useState(filterKey);
    if (filterKey !== lastFilterKey) {
        setLastFilterKey(filterKey);
        setOffset(0);
    }

    // One request, guarded. Previously this page had no staleness guard at all:
    // typing in the search box or flipping filters quickly could let an earlier,
    // slower response land after a later one and repopulate the table with
    // results for filters no longer selected.
    const { data, error, loading, refreshing, reload } = useAsyncData(
        () =>
            getLibraryPuzzles({
                username: username!,
                q: debouncedSearch || undefined,
                status: statusFilter || undefined,
                motif: motifFilter || undefined,
                cause: causeFilter || undefined,
                phase: phaseFilter || undefined,
                opening: openingFilter || undefined,
                opening_line: openingLineFilter || undefined,
                difficulty: difficultyFilter || undefined,
                sort,
                limit: PAGE_SIZE,
                offset,
            }),
        [
            username,
            debouncedSearch,
            statusFilter,
            difficultyFilter,
            motifFilter,
            causeFilter,
            phaseFilter,
            openingFilter,
            openingLineFilter,
            sort,
            offset,
        ],
        { enabled: Boolean(username), errorMessage: 'Failed to load puzzles' },
    );

    const puzzles = data?.puzzles ?? [];
    const total = data?.total ?? 0;
    const availableMotifs = data?.available_motifs ?? [];
    const availableCauses = data?.available_causes ?? [];
    const availableOpenings = data?.available_openings ?? [];
    const corpusStats = data?.stats ?? null;
    // Every fetch showed the loading state here, not just the first -- a filter
    // change should visibly reload the table.
    const isLoading = loading || refreshing;

    const totalPages = Math.ceil(total / PAGE_SIZE);
    const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

    // No account connected. Was a bespoke block whose "Set Username" button
    // called setEditorOpen — but that editor lives in UsernameDisplay, which
    // Layout only mounts once a username exists, so the button did nothing in
    // the one state that rendered it. Home's onboarding is the way in.
    if (!username) {
        return (
            <div className="space-y-12 animate-teedin">
                <PageHeader
                    title="Library"
                    subtitle="Puzzles from your own games. Every position here is a moment you can learn from."
                />
                <ConnectAccountEmpty description="Your library collects the puzzles generated from your own games, with what you have solved and what is due for review. Connect your Chess.com account to start filling it." />
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

                    {/* Mistake cause — the target of the Insights "practise this" links */}
                    {availableCauses.length > 0 && (
                        <select
                            value={causeFilter}
                            onChange={(e) => setCauseFilter(e.target.value)}
                            aria-label="Filter by mistake cause"
                            className="bg-bg-primary border border-primary/20 rounded-sm px-3 py-1.5 text-primary focus:outline-none focus:border-primary/60"
                        >
                            <option value="">All causes</option>
                            {availableCauses.map(c => (
                                <option key={c.value} value={c.value}>{c.label}</option>
                            ))}
                        </select>
                    )}

                    {/* An arriving line filter has no select of its own — a
                        dropdown of every line in the corpus would be unusable —
                        so it appears as a removable chip. Without it the list is
                        narrowed with no visible reason and no way out. */}
                    {openingLineFilter && (
                        <span className="inline-flex items-center gap-2 bg-primary/10 border border-primary/20 rounded-sm px-3 py-1.5 text-primary font-sans text-sm">
                            {openingLineFilter}
                            <button
                                type="button"
                                onClick={() => setOpeningLineFilter('')}
                                aria-label={`Clear the ${openingLineFilter} filter`}
                                className="km-focus-visible text-primary/70 hover:text-primary transition-colors"
                            >
                                ×
                            </button>
                        </span>
                    )}

                    {/* Phase */}
                    <select
                        value={phaseFilter}
                        onChange={(e) => setPhaseFilter(e.target.value)}
                        aria-label="Filter by game phase"
                        className="bg-bg-primary border border-primary/20 rounded-sm px-3 py-1.5 text-primary focus:outline-none focus:border-primary/60"
                    >
                        <option value="">All phases</option>
                        <option value="opening">Opening</option>
                        <option value="middlegame">Middlegame</option>
                        <option value="endgame">Endgame</option>
                    </select>

                    {/* Opening family — only offered once games have been classified */}
                    {availableOpenings.length > 0 && (
                        <select
                            value={openingFilter}
                            onChange={(e) => setOpeningFilter(e.target.value)}
                            aria-label="Filter by opening"
                            className="bg-bg-primary border border-primary/20 rounded-sm px-3 py-1.5 text-primary focus:outline-none focus:border-primary/60"
                        >
                            <option value="">All openings</option>
                            {availableOpenings.map(o => (
                                <option key={o} value={o}>{o}</option>
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
                        {/* Only while refreshing a list that is already on screen.
                            On a first load the full-page loader below is showing,
                            and both carry role="status" aria-live="polite" — two
                            regions announcing the same sentence at once. This is
                            the refresh indicator; that one is the initial load. */}
                        {isLoading && puzzles.length > 0 ? (
                            <DataStateLoading label="Refreshing library puzzles..." compact />
                        ) : isLoading ? null : (
                            <span>{total} puzzle{total !== 1 ? 's' : ''}</span>
                        )}
                    </div>
                )}
            </section>

            {/* Error */}
            {error && (
                <DataStateError
                    message={error}
                    onRetry={reload}
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
                        className="px-4 py-2 border border-primary/20 rounded-sm text-primary km-interactive km-focus-visible"
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
                        className="px-4 py-2 border border-primary/20 rounded-sm text-primary km-interactive km-focus-visible"
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
