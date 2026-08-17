import { LOCALE } from '../utils/locale';
import { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams, useNavigate } from 'react-router-dom';
import { AccessibleChessboard } from '../components/AccessibleChessboard';
import { PageHeader } from '../components/PageHeader';
import { ConnectAccountEmpty } from '../components/ConnectAccountEmpty';
import { Chess } from 'chess.js';
import { generatePuzzles, getDailyPuzzles, cancelJob, checkPuzzle, revealPuzzle, requestMotifHint, ApiError } from '../api';
import { JobStatusCard } from '../components/JobStatusCard';
import { SessionSummaryCard } from '../components/SessionSummaryCard';
import { WarmupSummary } from '../components/WarmupSummary';
import { AchievementsList } from '../components/AchievementsList';
import { RecentSessionsCard } from '../components/RecentSessionsCard';
import { useJobPolling } from '../hooks/useJobPolling';
import { useChessUsername } from '../context/ChessUsernameContext';
import { usePuzzleMode } from '../context/PuzzleModeContext';
import { useClue } from '../hooks/useClue';
import { usePuzzleTimer } from '../hooks/usePuzzleTimer';
import { useAchievements } from '../hooks/useAchievements';
import { usePuzzleInsights } from '../hooks/usePuzzleInsights';
import { usePuzzleSession, type PuzzleStatus } from '../hooks/usePuzzleSession';
import { getModeLabels, getPuzzleActionA11yCopy, getSessionDetailsA11yCopy } from '../utils/a11yCopy';
import { formatMotifName } from '../utils/motif';
import { uciLineToSan } from '../utils/chess';

// Mastery ranks, styled from one lookup. The panel tint, the percentage and the
// bar used to be three parallel ternaries — the figure already used the theme
// tokens while the tint and bar were on raw palette colours, so the card read as
// three different greens. The bar takes `bg-current` from the figure's own
// colour, which makes drift impossible by construction.
const MOTIF_RANK_STYLE = {
    mastered: { panel: 'bg-status-mastered-soft border-status-mastered-soft', figure: 'text-positive' },
    learning: { panel: 'bg-status-learning-soft border-status-learning-soft', figure: 'text-warning' },
    needs_work: { panel: 'bg-negative-soft border-negative-soft', figure: 'text-negative' },
} as const;

const motifRankStyle = (rank: string) =>
    MOTIF_RANK_STYLE[rank as keyof typeof MOTIF_RANK_STYLE] ?? MOTIF_RANK_STYLE.needs_work;

export default function Puzzles() {
    const { username } = useChessUsername();
    const { sessionType, targetAccuracy, setTargetAccuracy, targetTimeMinutes, setTargetTimeMinutes } = usePuzzleMode();
    const navigate = useNavigate();
    const [userMove, setUserMove] = useState('');
    const [status, setStatus] = useState<PuzzleStatus>('solving');
    const [showUciInput, setShowUciInput] = useState(false);
    const [activeJobId, setActiveJobId] = useState<string | null>(() => {
        if (!username) return null;
        return localStorage.getItem(`knightmind:lastJob:${username}`);
    });
    const [prevUsername, setPrevUsername] = useState(username);
    const [game, setGame] = useState(new Chess());
    // Click-to-move: the square whose piece was selected by a click (the
    // standard chess-site input alongside drag and keyboard).
    const [clickFrom, setClickFrom] = useState<string | null>(null);
    // The solution is NOT pre-sent with the puzzle (audit gate 13). It is
    // fetched on demand (reveal / full clue) and held only for the current
    // puzzle, so the client never holds the answer before the user asks.
    const [revealedMove, setRevealedMove] = useState<string | null>(null);
    // Full solution line (fetched on reveal / full clue), for puzzles that store
    // a multi-move principal variation. Legacy single-move puzzles leave this empty.
    const [revealedPv, setRevealedPv] = useState<string[]>([]);
    // Multi-move solve progress. `linePlyIndex` is the index of the solver's NEXT
    // move within the line (0, 2, 4, ...); `attemptedLine` accumulates the moves
    // the user has played so the whole line can be server-verified on completion.
    // Legacy single-move puzzles simply solve at ply 0 and complete immediately.
    const [linePlyIndex, setLinePlyIndex] = useState(0);
    const [attemptedLine, setAttemptedLine] = useState<string[]>([]);
    // Transient "we couldn't reach the server" for a board action (check a move,
    // reveal, record a review). Deliberately separate from the puzzle `status`:
    // a failed request is NOT a wrong answer and must never be scored as one.
    const [actionError, setActionError] = useState<string | null>(null);

    // Get motif filter and warmup mode from URL query params
    const [searchParams] = useSearchParams();
    const motifFilter = searchParams.get('motif');
    // A bias rather than a filter, so — unlike motif — it never leaves the user
    // in a dead-end empty session and needs no escape hatch.
    const focusCause = searchParams.get('focus_cause');
    const focusOpening = searchParams.get('focus_opening');
    const focusOpeningScope = searchParams.get('focus_opening_scope');
    const isWarmupMode = searchParams.get('warmup') === 'true';

    // Warmup state
    const [warmupMode, setWarmupMode] = useState(isWarmupMode);
    const [showSessionDetails, setShowSessionDetails] = useState(false);

    // Shared state: activeSessionId is needed by both timer and session hooks
    const [activeSessionId, setActiveSessionId] = useState<string | null>(null);

    const { achievements, checkAchievements, checkSessionAchievements } = useAchievements(username);
    const insights = usePuzzleInsights(username);
    const {
        userStatus, isLoadingStatus, motifPerformance, recentSessions,
        insightsError, isRefreshingInsights,
        refreshUserStatus, refreshRecentSessions, refreshMotifPerformance,
        handleRefreshInsights,
    } = insights;

    const statusRef = useRef(status);
    // The in-flight review write for THIS puzzle's outcome, or null. Covers
    // the reveal (a self-reported fail) and the solve (a pass); an incorrect
    // attempt is recorded by the user's own "Mark as failed" button and is
    // deliberately not automatic.
    //
    // Recording at outcome rather than at move-on is what makes the
    // post-resolution panel possible: the diagnosis is gated on attempts > 0,
    // so a panel shown before the review landed would be withheld.
    //
    // Move-on awaits THIS rather
    // than a boolean, because during the flight no boolean is correct: the
    // outcome is not known yet, and both orderings of a flag lose a result in
    // one direction or the other (advance on an unlanded write, or skip a
    // retry after a failed one). Cleared on failure so move-on retries with
    // the idempotency key the session hook kept.
    const outcomeWriteRef = useRef<Promise<boolean> | null>(null);
    // Rung 0 of the hint ladder (§5.1): the motif, asked for explicitly.
    // Held here rather than in `useClue` because that hook is shared with
    // Engine analysis, which has no motif and no gate -- renumbering its rungs
    // would change a surface this feature has nothing to do with.
    const [motifHint, setMotifHint] = useState<string | null>(null);
    const [motifHintAsked, setMotifHintAsked] = useState(false);
    const [previousSessionId, setPreviousSessionId] = useState<string | null>(null);
    useEffect(() => {
        statusRef.current = status;
    }, [status]);

    // Keep advanced stats collapsed when a new session starts.
    if (activeSessionId !== previousSessionId) {
        setPreviousSessionId(activeSessionId);
        if (activeSessionId) {
            setShowSessionDetails(false);
        }
    }

    const handleReviewPuzzleRef = useRef<((result: 'pass' | 'fail', timeMs?: number) => Promise<boolean>)>(async () => false);
    const isAdvancingPuzzle = useRef(false);

    const timer = usePuzzleTimer({
        sessionType,
        activeSessionId,
        onPuzzleTimeout: () => {
            if (statusRef.current === 'solving') {
                handleReviewPuzzleRef.current('fail');
                setStatus('incorrect');
            }
        },
    });

    const session = usePuzzleSession({
        activeSessionId,
        setActiveSessionId,
        setStatus,
        username,
        sessionType,
        targetAccuracy,
        targetTimeMinutes,
        warmupMode,
        motifFilter,
        focusCause,
        focusOpening,
        focusOpeningScope,
        userStatus,
        timer,
        checkAchievements,
        checkSessionAchievements,
        refreshRecentSessions,
        refreshMotifPerformance,
        refreshUserStatus,
    });

    const {
        sessionState, sessionSummary, isResumingSession,
        streak, bestStreak, hintsUsed, reviewedCount, performanceHistory,
        puzzles, currentIndex, isLoading, error, lastFeedback,
        setPuzzles, setCurrentIndex, setError, setLastFeedback,
        setSessionSummary, setSessionState, setReviewedCount,
        handleStartSession, handleReviewPuzzle, handleCompleteSession, handleUseHint,
        calculateRecentPerformance, getPerformanceTrend,
    } = session;

    const startPuzzleTimer = timer.startPuzzleTimer;
    const currentPuzzle = puzzles[currentIndex];
    // The clue/board work off the on-demand-fetched solution, never a pre-sent
    // one — so the hint machinery only has the answer once the user asks for it.
    const clue = useClue(revealedMove ?? '', currentPuzzle?.fen ?? '', { maxStage: 3 });
    const clueReset = clue.reset;
    const puzzlesAvailable = puzzles.length > 0;
    const isFinalPuzzle = puzzlesAvailable && currentIndex >= puzzles.length - 1;
    // Disable only while the completion API call is in-flight. Once completed,
    // render a real post-session action instead of a dead final-puzzle CTA.
    const finishButtonDisabled = sessionState === 'completing';
    const controlsEnabled = sessionState === 'idle' || sessionState === 'error';

    // Sync activeJobId when username changes (during render, not in effect)
    if (prevUsername !== username) {
        setPrevUsername(username);
        const savedJobId = username
            ? localStorage.getItem(`knightmind:lastJob:${username}`)
            : null;
        setActiveJobId(savedJobId);
    }

    const { job, isPolling: isJobPolling } = useJobPolling(activeJobId, {
        enabled: !!activeJobId,
        onSuccess: async () => {
            try {
                const res = await getDailyPuzzles(username, 5);
                setPuzzles(res.puzzles);
                setCurrentIndex(0);
                setStatus('solving');
                outcomeWriteRef.current = null;
                setMotifHint(null);
                setMotifHintAsked(false);
            setMotifHint(null);
            setMotifHintAsked(false);
                setUserMove('');
                if (res.puzzles.length > 0) {
                    setSessionState('active');
                    setError(null);
                } else {
                    setSessionState('error');
                    setError('No puzzles returned from generation');
                }
            } catch (err) {
                console.error('Failed to refresh puzzles after generation:', err);
                const message = err instanceof Error ? err.message : 'Failed to refresh puzzles';
                setError(message);
            }

            localStorage.removeItem(`knightmind:lastJob:${username}`);

            // Refresh user status to update has_new_games flag
            await refreshUserStatus();
        },
        onError: (err) => {
            localStorage.removeItem(`knightmind:lastJob:${username}`);
            const message = err instanceof Error ? err.message : 'Failed to generate puzzles';
            setSessionState('error');
            setError(message);
        }
    });

    const isGenerating = isJobPolling || (job?.status === 'queued' || job?.status === 'running');
    const controlsDisabled = !controlsEnabled || isLoading || isGenerating;
    const generateNewDisabled = !controlsEnabled || isLoading || isGenerating || !userStatus?.has_new_games;
    const { selectedModeLabel, screenReaderModeLabel } = getModeLabels(sessionType);
    const modeAvailabilityLabel = sessionType === 'standard' ? 'Active' : 'Beta';
    // No `!username` arm: the page returns ConnectAccountEmpty above, so these
    // reasons are only ever read by a signed-in user.
    const startSessionDisabledReason =
        // A session is running (the Start button is hidden), so there is nothing
        // "loading or generating" to wait on — don't show a start reason at all.
        activeSessionId
            ? null
            : controlsDisabled
                ? 'Please wait for the current task to finish.'
                : !userStatus
                ? ((insightsError && !isLoadingStatus && !isRefreshingInsights) ? "Couldn't load your training data." : 'Loading your training data...')
                : userStatus.puzzles_count === 0
                    ? 'Generate puzzles first to unlock sessions.'
                    : userStatus.due_count === 0
                        ? 'No puzzles are due right now. Generate new puzzles to keep training.'
                        : sessionType !== 'standard'
                            ? 'Only Standard mode can start sessions for now. Switch mode in the sidebar.'
                            : null;
    const generateDisabledReason = isGenerating
            ? 'Puzzle generation is already in progress.'
            : activeSessionId
                ? 'Finish your current session before generating new puzzles.'
                : !controlsEnabled || isLoading
                    ? 'Please finish the current flow before generating more puzzles.'
                : !userStatus?.has_new_games
                    ? !userStatus
                        ? ((insightsError && !isLoadingStatus && !isRefreshingInsights) ? "Couldn't load your training data." : 'Loading your training data...')
                        : userStatus.games_count === 0
                            ? 'No games imported yet. Sync games from Chess.com to get started.'
                            : userStatus.due_count > 0
                                ? `All imported games are already processed. Train your ${userStatus.due_count} due puzzle${userStatus.due_count === 1 ? '' : 's'}, or sync newer games from Chess.com.`
                                : 'All imported games are already processed. Sync newer games from Chess.com to generate more puzzles.'
                    : null;
    const generateButtonLabel = isGenerating
        ? 'Generating...'
        : userStatus && !userStatus.has_new_games && userStatus.games_count > 0
            ? 'No new games to generate'
            : 'Generate New';
    const sessionDetailsA11yCopy = getSessionDetailsA11yCopy(showSessionDetails, screenReaderModeLabel);
    const puzzleActionA11yCopy = getPuzzleActionA11yCopy(clue.clueStage);

    const handleGeneratePuzzles = async () => {
        if (!username.trim()) {
            setError('Please enter a username');
            return;
        }
        setSessionState('loading');
        setError(null);

        try {
            const { job_id } = await generatePuzzles(username.trim());
            setActiveJobId(job_id);
            localStorage.setItem(`knightmind:lastJob:${username.trim()}`, job_id);
            // Polling will auto-start
        } catch (err) {
            if (err instanceof ApiError) {
                if (err.statusCode === 404) {
                    // Differentiate between no games at all vs no new games
                    if (userStatus?.games_count === 0) {
                        setError('No games found. Please import games first.');
                    } else {
                        setError('No new games available. All current games have been used for puzzles. Import more games to generate new puzzles.');
                    }
                } else {
                    // Show the friendly message; keep the raw cause (endpoints,
                    // timeouts, "backend is down") in the console for devs only.
                    if (err.detail) console.error('[puzzles:generate]', err.detail);
                    setError(err.message);
                }
            } else {
                setError(err instanceof Error ? err.message : 'Failed to generate puzzles');
            }
            setSessionState('error');
        }
    };

    const handleCancelJob = async () => {
        if (!activeJobId) return;

        try {
            await cancelJob(activeJobId);
            // Job status will be updated via polling
        } catch (err) {
            console.error('Failed to cancel job:', err);
            setError(err instanceof Error ? err.message : 'Failed to cancel job');
        }
    };

    // Keep ref in sync for timer timeout callback
    useEffect(() => {
        handleReviewPuzzleRef.current = handleReviewPuzzle;
    }, [handleReviewPuzzle]);

    // Auto-start warmup session when in warmup mode
    useEffect(() => {
        if (warmupMode && sessionState === 'idle' && username && userStatus && !isResumingSession) {
            handleStartSession();
        }
    }, [warmupMode, sessionState, username, userStatus, isResumingSession, handleStartSession]);

    const shouldShowJobStatusCard =
        !!job &&
        (job.status === 'queued' ||
            job.status === 'running' ||
            (!puzzlesAvailable && (job.status === 'succeeded' || job.status === 'failed')));
    const shouldShowErrorCard = sessionState === 'error' && !!error;
    const shouldShowLoadingCard =
        (isLoading || isLoadingStatus || isResumingSession) && !isGenerating && !shouldShowJobStatusCard;
    const shouldShowEmptyState =
        !isLoading &&
        !isGenerating &&
        !isLoadingStatus &&
        !puzzlesAvailable &&
        !shouldShowJobStatusCard &&
        !error;
    const shouldShowPartialDataCard =
        !!username &&
        !isLoadingStatus &&
        !!userStatus &&
        userStatus.puzzles_count > 0 &&
        (!motifPerformance || !!insightsError);
    const canRetryLoad = !!username && !isGenerating && !isLoading;
    // Status fetch failed (not merely still loading): userStatus is null, nothing
    // is loading/resuming/refreshing, no puzzles are already loaded, and an error
    // was recorded. Surfaces an error card instead of the misleading "Ready to
    // train" / "Loading your training data..." states — and never stacks on top
    // of a loading card or a working board.
    const statusLoadFailed =
        !!username &&
        !isLoadingStatus &&
        !isLoading &&
        !isResumingSession &&
        !userStatus &&
        !puzzlesAvailable &&
        // Stay true while a retry is in flight so the error card keeps its place
        // and its "Retrying..." button state shows, rather than unmounting.
        (!!insightsError || isRefreshingInsights) &&
        !isGenerating &&
        !shouldShowJobStatusCard &&
        !shouldShowErrorCard;

    // Ensure we have the solution for the current puzzle, fetching it once from
    // the server on demand (reveal / full clue). Returns the lowercased UCI move,
    // or null if unavailable.
    const ensureRevealedMove = async (): Promise<{ move: string | null; pv: string[] }> => {
        if (revealedMove) return { move: revealedMove, pv: revealedPv };
        if (!currentPuzzle || !username) return { move: null, pv: [] };
        try {
            const { best_move_uci, solution_pv } = await revealPuzzle(currentPuzzle.id, username);
            const move = best_move_uci.toLowerCase();
            const pv = (solution_pv ?? []).map((m) => m.toLowerCase());
            setRevealedMove(move);
            // Keep the whole line so Reveal can show the full combination, not
            // only the first move.
            setRevealedPv(pv);
            return { move, pv };
        } catch (err) {
            console.error('Failed to reveal solution:', err);
            return { move: null, pv: [] };
        }
    };

    // Reveal playback: step the solution line out on the board so the answer is
    // SEEN as chess, not just printed as text. One interval at a time; cleared
    // on a new reveal, on puzzle change, and on unmount.
    const playbackRef = useRef<ReturnType<typeof setInterval> | null>(null);
    const stopPlayback = () => {
        if (playbackRef.current) {
            clearInterval(playbackRef.current);
            playbackRef.current = null;
        }
    };
    useEffect(() => stopPlayback, []);
    const playSolutionLine = (fen: string, line: string[]) => {
        stopPlayback();
        const board = new Chess(fen);
        let i = 0;
        const step = () => {
            if (i >= line.length) return stopPlayback();
            const uci = line[i++];
            try {
                board.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci.slice(4, 5) || undefined });
                setGame(new Chess(board.fen()));
            } catch {
                stopPlayback();
            }
        };
        step();
        playbackRef.current = setInterval(step, 900);
    };

    // Verify one played move server-side and, for a multi-move line, auto-play
    // the opponent's forced reply and advance to the next ply — without the
    // client ever holding the solver's upcoming answer (audit gate 13). The
    // whole line is recorded (attemptedLine) so a completed solve is verified
    // end-to-end on review. `boardAfterMove` already has the user's move applied.
    const processUserMove = async (boardAfterMove: Chess, uciMove: string, fenBefore: string) => {
        if (!currentPuzzle) return;
        const normalized = uciMove.toLowerCase();
        // Reflect the user's move immediately.
        setGame(new Chess(boardAfterMove.fen()));
        setUserMove(normalized);
        setActionError(null);
        try {
            const res = await checkPuzzle(currentPuzzle.id, username, normalized, linePlyIndex);
            if (!res.correct) {
                setStatus('incorrect');
                return;
            }
            setAttemptedLine((prev) => [...prev, normalized]);
            // Play the opponent's forced reply — safe to show; it's the forced
            // response, not the solver's next answer (which the server withholds).
            if (res.reply) {
                try {
                    boardAfterMove.move({
                        from: res.reply.slice(0, 2),
                        to: res.reply.slice(2, 4),
                        promotion: res.reply.slice(4, 5) || undefined,
                    });
                    setGame(new Chess(boardAfterMove.fen()));
                } catch (replyErr) {
                    console.error('Failed to apply opponent reply:', replyErr);
                }
            }
            // The line continues only when the server hands back the next ply to
            // play; otherwise the puzzle is solved (a completed line, or a legacy
            // single-move puzzle whose response carries no next ply). Driving off
            // next_ply_index keeps a response that omits `complete` working too.
            if (typeof res.next_ply_index === 'number') {
                setLinePlyIndex(res.next_ply_index);
                setUserMove('');
                // status stays 'solving' — prompt the next move.
            } else {
                setStatus('correct');
                // Record the solve NOW, not at move-on. Same reason as the
                // reveal: the outcome is known, and the post-resolution panel
                // needs the review to have landed before it asks for a
                // diagnosis the gate keys on attempts > 0.
                const solvedLine = [...attemptedLine, normalized].join(' ');
                outcomeWriteRef.current = handleReviewPuzzle(
                    'pass',
                    undefined,
                    solvedLine || undefined,
                );
            }
        } catch (err) {
            // A failed REQUEST is not a failed ATTEMPT. Marking it 'incorrect'
            // told the user they blundered when the network dropped, broke
            // their streak, and offered "Mark as Failed & Try Again" — which
            // would have written a real fail. Roll the board back to where they
            // were and let them try the same move again.
            console.error('Failed to check move:', err);
            setGame(new Chess(fenBefore));
            setUserMove('');
            setActionError("We couldn't check that move — your attempt wasn't recorded. Check your connection and try again.");
        }
    };

    const handleCheckAnswer = async () => {
        if (!currentPuzzle) return;
        const normalizedUserMove = userMove.trim().toLowerCase();
        if (!normalizedUserMove) return;
        // Apply the typed move to a working board so a multi-move line can play
        // out its replies just like the drag path does.
        const fenBefore = game.fen();
        const board = new Chess(fenBefore);
        let move;
        try {
            move = board.move({
                from: normalizedUserMove.slice(0, 2),
                to: normalizedUserMove.slice(2, 4),
                promotion: normalizedUserMove.slice(4, 5) || undefined,
            });
        } catch { move = null; }
        if (!move) {
            // Illegal/malformed typed move — an incorrect attempt, never a crash.
            setStatus('incorrect');
            return;
        }
        clue.reset();
        const uciMove = `${move.from}${move.to}${move.promotion || ''}`;
        await processUserMove(board, uciMove, fenBefore);
    };

    const handleRevealSolution = async () => {
        setActionError(null);
        const { move: bestMove, pv } = await ensureRevealedMove();
        // Without a solution there is nothing to reveal. Flipping to 'revealed'
        // anyway printed an empty "Solution …", removed every solving control,
        // and left the puzzle queued to be recorded as a self-reported fail —
        // charging the user for a request that never landed. Same guard the
        // hint ladder already applies at rung 1.
        if (!bestMove) {
            setActionError("We couldn't load the solution — you're still on this puzzle. Check your connection and try again.");
            return;
        }
        setStatus('revealed');
        setUserMove(bestMove);
        // Drop any click-selected piece so its highlight doesn't linger over
        // the solution playback.
        setClickFrom(null);
        if (currentPuzzle) {
            // Animate the whole combination (or the single move for legacy
            // puzzles) rather than teleporting one move and printing the rest.
            playSolutionLine(currentPuzzle.fen, pv.length ? pv : [bestMove]);
        }
        // Record the fail NOW rather than when the user moves on. LibraryPuzzle
        // has always done this — `handleRecordResult('fail')` sits in its own
        // reveal handler — and the trainer deferring it cost two things.
        //
        // A user who revealed and then closed the tab had the attempt recorded
        // NOWHERE: they saw the answer and the scheduler never learned the
        // puzzle was failed, so it kept its old interval. And `attempts` stayed
        // 0 for the entire window in which the solution is on screen, which is
        // exactly the window the post-resolution panel exists to fill.
        //
        // The visual reveal above does not wait on this, matching the rule the
        // hint ladder already follows: the reveal never depends on a write
        // succeeding. If it does not land, `handleNextPuzzle` retries with the
        // same idempotency key.
        // Keep the PROMISE, not a boolean. `setStatus('revealed')` above has
        // already made the advance control clickable, so there is a window in
        // which move-on runs while this write is still in flight -- and no
        // flag can be correct during it, because the outcome is not known yet.
        //
        // A boolean written after the await read false in that window and
        // triggered a second call, which hit the session hook's in-flight
        // guard (`return true` WITHOUT posting), so the session advanced on a
        // write that had not landed. A boolean written before the await had
        // the opposite bug: move-on skipped, and a later failure was never
        // retried. Awaiting the same promise has neither -- it yields the real
        // outcome exactly once, with no second post.
        outcomeWriteRef.current = handleReviewPuzzle('fail');
        await outcomeWriteRef.current;
    };

    // One graduated hint ladder, identical with or without an active session:
    //   rung 1 → name/highlight the piece, rung 2 → highlight the destination,
    //   rung 3 → reveal the full solution line.
    // In a session we also record each rung server-side (an honest hint tally),
    // but the visual reveal never depends on that write succeeding.
    const handleHint = async () => {
        if (!currentPuzzle) return;

        // Rung 0: the motif, before the ladder starts. Only offered while the
        // payload does not already carry it -- with the gate off the chip is
        // on screen and spending a hint to be told what is visible would be
        // absurd.
        if (!motifHintAsked && !currentPuzzle.primary_motif) {
            setMotifHintAsked(true);
            try {
                const { primary_motif } = await requestMotifHint(
                    currentPuzzle.id,
                    username.trim(),
                    activeSessionId || undefined,
                );
                // null means no motif was identified. The rung is still spent
                // -- the user asked -- but there is nothing to show, so fall
                // through to rung 1 rather than leaving them with nothing.
                if (primary_motif) {
                    setMotifHint(primary_motif);
                    return;
                }
            } catch {
                // A failed request must not cost the rung: let the next press
                // try the ladder rather than stranding the user.
                setMotifHintAsked(false);
                return;
            }
        }

        if (clue.isExhausted) return;
        const stage = clue.clueStage;
        // Rung 1 needs the solution in hand so the piece name / squares resolve.
        // Bail if the fetch fails — advancing with nothing to show would be a lie.
        if (stage === 0) {
            const { move } = await ensureRevealedMove();
            if (!move) return;
        }
        // Force past advance()'s "no move known" guard: on the first press the
        // move was only just fetched, so this render's closure hasn't seen it.
        clue.advance(true);
        // Rung 3 hands over the whole line — same destination as the Reveal button.
        if (stage === 2) {
            await handleRevealSolution();
        }
        if (activeSessionId) {
            await handleUseHint();
        }
    };

    // Sync game board when puzzle changes (setState during render, not in effect)
    const [prevPuzzle, setPrevPuzzle] = useState(currentPuzzle);
    if (currentPuzzle && currentPuzzle !== prevPuzzle) {
        setPrevPuzzle(currentPuzzle);
        setGame(new Chess(currentPuzzle.fen));
        // Drop any solution held for the previous puzzle.
        setRevealedMove(null);
        setRevealedPv([]);
        // Restart the multi-move line for the new puzzle.
        setLinePlyIndex(0);
        setAttemptedLine([]);
        setClickFrom(null);
        setActionError(null);
    }

    // Bring the board into view when a session starts: it renders below the
    // setup panel, so without this the moment of highest intent ("Start
    // Session") lands on an apparently unchanged screen with the puzzle hidden
    // below the fold.
    // Scroll with a settle guarantee. Two real-world hazards: (1) smooth
    // scrolling is an animation and can be suppressed outright (reduced-motion
    // UAs, hidden tabs); (2) content around the target keeps rendering for a
    // moment, so a one-shot correction can land before a late reflow moves the
    // target thousands of pixels. Verify-and-retry a few times over ~2s, and
    // back off the moment the user scrolls themselves.
    const scrollWithSettle = (el: HTMLElement | null) => {
        if (!el) return;
        el.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
        let attempts = 0;
        let lastAutoY: number | null = null;
        const verify = () => {
            attempts += 1;
            // The user took over — never fight their scroll.
            if (lastAutoY !== null && Math.abs(window.scrollY - lastAutoY) > 40) return;
            const r = el.getBoundingClientRect();
            if (r.top < -8 || r.top > window.innerHeight * 0.5) {
                el.scrollIntoView?.({ block: 'start' });
                lastAutoY = window.scrollY;
            }
            if (attempts < 4) setTimeout(verify, 500);
        };
        setTimeout(verify, 500);
    };

    const boardSectionRef = useRef<HTMLElement>(null);
    const scrolledForSessionRef = useRef<string | null>(null);
    useEffect(() => {
        if (!activeSessionId || !currentPuzzle) return;
        if (scrolledForSessionRef.current === activeSessionId) return;
        scrolledForSessionRef.current = activeSessionId;
        // setTimeout, not requestAnimationFrame: rAF never fires in a hidden
        // tab, which would silently skip the scroll entirely.
        setTimeout(() => scrollWithSettle(boardSectionRef.current), 60);
    }, [activeSessionId, currentPuzzle]);

    // Same for the finish: the summary (stats + any achievements earned) is the
    // session's payoff and must be seen, not pointed at with "see below".
    const summaryRef = useRef<HTMLDivElement>(null);
    useEffect(() => {
        if (sessionState !== 'completed') return;
        setTimeout(() => scrollWithSettle(summaryRef.current), 60);
    }, [sessionState]);

    // Reset clue and start timer when puzzle changes (side effects in effect)
    useEffect(() => {
        if (currentPuzzle) {
            clueReset();
            startPuzzleTimer();
            // A still-running solution playback belongs to the previous puzzle
            // and would overwrite the fresh board with stale positions.
            stopPlayback();
        }
    }, [currentPuzzle, clueReset, startPuzzleTimer]);

    const onPieceDrop = (sourceSquare: string, targetSquare: string, promotion: string = 'q') => {
        if (!currentPuzzle || status === 'correct' || status === 'revealed') return false;
        try {
            // `game.move` mutates in place, so capture the position first —
            // processUserMove needs it to roll back if the check request fails.
            const fenBefore = game.fen();
            const move = game.move({ from: sourceSquare, to: targetSquare, promotion: promotion || 'q' });
            if (move === null) return false;
            clue.reset();
            const uciMove = `${move.from}${move.to}${move.promotion || ''}`;
            // The board applies the move locally (chess.js validates legality),
            // but whether it SOLVES the puzzle — and, for a multi-move line, the
            // opponent's forced reply — is decided server-side so the client
            // never holds the answer ahead of time (audit gate 13).
            void processUserMove(game, uciMove, fenBefore);
            return true;
        } catch { return false; }
    };

    const handleNextPuzzle = () => {
        if (currentIndex < puzzles.length - 1) {
            setCurrentIndex(currentIndex + 1);
            setStatus('solving');
            outcomeWriteRef.current = null;
            setMotifHint(null);
            setMotifHintAsked(false);
            setUserMove('');
            setLastFeedback('');
            setLinePlyIndex(0);
            setAttemptedLine([]);
            clue.reset();
        }
    };

    const handleAdvancePuzzle = async () => {
        if (sessionState === 'completing' || sessionState === 'completed') return;
        if (isAdvancingPuzzle.current) return;

        isAdvancingPuzzle.current = true;
        try {
            setActionError(null);
            let recorded = true;
            if (status === 'correct' || status === 'revealed') {
                // Await the write this puzzle's outcome already started rather
                // than firing a second one. If it is still in flight this
                // blocks on the real outcome; if it failed, the ref was
                // cleared and this retries with the key the hook kept.
                //
                // Both branches were separate before, and the 'correct' one
                // sent its own review here -- which is why the solve was not
                // recorded until the user moved on, and why a panel shown in
                // between saw attempts = 0.
                if (outcomeWriteRef.current) {
                    recorded = await outcomeWriteRef.current;
                } else {
                    const solvedLine = attemptedLine.length > 0
                        ? attemptedLine.join(' ')
                        : (userMove.trim().toLowerCase() || undefined);
                    recorded = status === 'correct'
                        ? await handleReviewPuzzle('pass', undefined, solvedLine)
                        : await handleReviewPuzzle('fail');
                }
                if (!recorded) {
                    outcomeWriteRef.current = null;
                }
            }
            // A review that failed to reach the server must NOT advance the
            // session: doing so silently discarded the attempt (and, on the last
            // puzzle, baked the loss into the summary). Stay put and let the
            // user press again — the idempotency key makes the retry safe.
            if (!recorded) {
                setActionError("We couldn't save that result — you're still on this puzzle. Check your connection and try again.");
                return;
            }

            // One step of progress per puzzle finished — so retries (mark-failed
            // / reveal) never advance or complete the session early. Complete
            // only after the last puzzle is done.
            setReviewedCount(prev => prev + 1);
            if (!isFinalPuzzle) {
                handleNextPuzzle();
            } else {
                await handleCompleteSession();
            }
        } finally {
            isAdvancingPuzzle.current = false;
        }
    };

    // formatMotifName, not the raw query param: the Dashboard link that sends
    // users here already says "Back rank mate", so printing "back_rank_mate"
    // made the two screens disagree mid-flow. Hoisted to a const so the visible
    // heading and its sr-only counterpart below can never drift apart.
    const pageTitle = motifFilter ? `${formatMotifName(motifFilter)} Puzzles` : 'Daily Puzzles';

    // Every other account-dependent page (Dashboard, Library, Insights, Rating
    // Insights, Openings) swaps to this in place rather than rendering its
    // controls disabled. Puzzles was the exception: a full training console with
    // every button dead and an inline sentence explaining why. Same state, same
    // answer — and it puts the one working way in (Home's onboarding) behind a
    // real button instead of an inline link.
    if (!username) {
        return (
            <div className="flex flex-col gap-12 animate-teedin">
                <PageHeader
                    title={pageTitle}
                    subtitle="Puzzles built from your own blunders, scheduled so they come back before you forget them."
                />
                <ConnectAccountEmpty description="Training sessions are drawn from puzzles generated out of your own games. Connect your Chess.com account to start building them." />
            </div>
        );
    }

    return (
        <div className="flex flex-col gap-12 animate-teedin pb-20 md:pb-0">
            {/* In-session below `lg` the header block collapses to give the board
                room — and it took the page's only <h1> with it, leaving mobile
                users mid-session on a page with no level-one heading (axe:
                page-has-heading-one; the outline started at the sr-only h2).
                This copy carries the heading at those widths and `lg:hidden`
                yields to the visible one above `lg`, so exactly one h1 is ever
                exposed. `sr-only` is absolutely positioned, so it adds no
                `gap-12` row. Caveat for tests: both are in the DOM at once
                in-session, and jsdom applies no breakpoints — query headings by
                text, not a bare getByRole('heading', { level: 1 }). */}
            {activeSessionId && currentPuzzle && (
                <h1 className="sr-only lg:hidden">{pageTitle}</h1>
            )}
            <section className={activeSessionId && currentPuzzle ? 'hidden lg:block' : ''}>
                <Link to="/dashboard" className="text-primary/70 hover:text-primary mb-4 inline-block font-sans text-sm tracking-widest uppercase transition-colors">
                    ← Back to Dashboard
                </Link>
                <div className="flex justify-between items-end">
                    <div>
                        <h1 className="text-4xl md:text-5xl font-serif text-primary mb-2">
                            {pageTitle}
                        </h1>
                        <div className={`${activeSessionId && currentPuzzle ? 'hidden lg:flex' : 'flex'} items-center gap-2 mb-3`}>
                            <span className="text-xs font-sans uppercase tracking-wider px-2 py-1 rounded-sm border border-primary/20 bg-primary/5 text-primary/80">
                                {selectedModeLabel} {modeAvailabilityLabel}
                            </span>
                            {sessionType !== 'standard' && (
                                <span className="text-xs font-sans text-primary/70">Switch to Standard to start sessions.</span>
                            )}
                        </div>
                        <p className={`${activeSessionId && currentPuzzle ? 'hidden lg:block' : ''} text-lg text-primary/70 font-sans`}>
                            {motifFilter
                                ? `Practice ${formatMotifName(motifFilter)} tactical patterns`
                                : 'Tactical patterns from your own games.'}
                        </p>
                        {/* A filtered queue can be empty while the unfiltered one
                            isn't, so the filter needs a visible exit — otherwise
                            the only way out of a dead-end targeted session is the
                            browser's back button. */}
                        {motifFilter && !activeSessionId && (
                            <Link
                                to="/puzzles"
                                className="mt-2 inline-block text-sm font-sans text-primary/70 km-interactive km-focus-visible km-inline-link underline decoration-primary/30 underline-offset-4"
                            >
                                Train everything that&apos;s due instead
                            </Link>
                        )}
                    </div>
                </div>
            </section>

            {/* Parents the status/session <h3>s so heading levels don't jump h1→h3. */}
            <h2 className="sr-only">Your training</h2>

            {/* Controls */}
            <section data-testid="training-controls" className={`bg-primary/5 border border-primary/10 rounded-sm p-6 backdrop-blur-sm space-y-6${activeSessionId && currentPuzzle ? ' hidden lg:block' : ''}`}>
                {/* Top row: Username and buttons */}
                <div className="flex flex-col md:flex-row gap-6 items-end">
                    <div className="flex-1 relative min-w-[300px]">
                        {/* No empty branch: the page returns ConnectAccountEmpty
                            above when there is no username, so this only ever
                            renders for a signed-in user. */}
                        <div className="text-xl font-serif text-primary py-2 border-b border-primary/20">
                            {username}
                        </div>
                    </div>
                    <div className="flex gap-4 flex-wrap">
                        {!activeSessionId && (
                            <button
                                type="button"
                                onClick={handleStartSession}
                                disabled={controlsDisabled || !userStatus || userStatus.puzzles_count === 0 || userStatus.due_count === 0 || sessionType !== 'standard'}
                                title={startSessionDisabledReason ?? 'Start a new training session'}
                                className={`px-6 py-2 bg-primary text-bg-primary rounded-sm font-serif transition-opacity km-focus-visible ${(controlsDisabled || !userStatus || userStatus.puzzles_count === 0 || userStatus.due_count === 0 || sessionType !== 'standard') ? 'km-interactive-disabled' : 'hover:opacity-90 cursor-pointer'}`}>
                                Start Session
                            </button>
                        )}
                        <button
                            type="button"
                            onClick={handleGeneratePuzzles}
                            disabled={generateNewDisabled}
                            title={generateDisabledReason ?? 'Generate puzzles from new games'}
                            className={`px-6 py-2 bg-primary text-bg-primary rounded-sm font-serif transition-colors km-focus-visible ${generateNewDisabled ? 'km-interactive-disabled' : 'km-interactive'}`}>
                            {generateButtonLabel}
                        </button>
                    </div>
                </div>

                {/* Suppressed without an account: both reasons are the connect
                    message, which the panel beside the buttons already states —
                    and states with a working link. Two near-identical sentences
                    stacked read as a rendering bug. The reasons still reach the
                    buttons' own title tooltips. */}
                {username && (startSessionDisabledReason || generateDisabledReason) && (
                    <p className="text-sm text-primary/70 font-sans" role="status" aria-live="polite">
                        {startSessionDisabledReason ?? generateDisabledReason}
                    </p>
                )}

                {/* User status - full width below buttons */}
                {username && userStatus && !isLoadingStatus && (
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm font-sans text-primary/70">
                        <span>Games: {userStatus.games_count}</span>
                        <span>Puzzles: {userStatus.puzzles_count}</span>
                        {userStatus.has_new_games ? (
                            <span className="text-positive">
                                ✓ New games available for puzzles
                            </span>
                        ) : userStatus.games_count > 0 ? (
                            <span className="text-primary/70">
                                All games used for puzzles
                            </span>
                        ) : null}
                    </div>
                )}

                {/* Mode Information Cards - full width below user status */}
                {!activeSessionId && (
                    <>
                        {sessionType === 'standard' ? (
                            <div className="p-4 bg-primary/5 border border-primary/20 rounded-sm">
                                <p className="text-sm text-primary/70 font-sans">
                                    <strong className="font-medium">Standard mode</strong> uses spaced repetition to help you master tactical patterns from your own games.
                                    Complete 5 puzzles per session with immediate feedback on each move.
                                </p>
                            </div>
                        ) : (
                            <div className="p-4 bg-primary/5 border border-primary/20 rounded-sm">
                                <p className="text-sm text-primary/70 font-sans mb-3">
                                    <strong className="font-medium">{sessionType === 'timed' ? 'Timed' : 'Accuracy Goal'} mode</strong> is currently in development.
                                    Try it out by adjusting the settings, but sessions can only be started in Standard mode for now.
                                </p>
                                {sessionType === 'timed' && (
                                    <div className="flex items-center gap-2">
                                        <label htmlFor="duration-input" className="text-sm text-primary/70 font-sans">Duration:</label>
                                        <input
                                            id="duration-input"
                                            type="number"
                                            min="1"
                                            max="60"
                                            value={targetTimeMinutes}
                                            onChange={(e) => setTargetTimeMinutes(Number(e.target.value))}
                                            className="px-3 py-2 border border-primary/20 rounded-sm bg-bg-primary text-primary w-20"
                                        />
                                        <span className="text-sm text-primary/70 font-sans">minutes</span>
                                    </div>
                                )}
                                {sessionType === 'accuracy_goal' && (
                                    <div className="flex items-center gap-2">
                                        <label htmlFor="accuracy-input" className="text-sm text-primary/70 font-sans">Target accuracy:</label>
                                        <input
                                            id="accuracy-input"
                                            type="number"
                                            min="50"
                                            max="100"
                                            value={targetAccuracy}
                                            onChange={(e) => setTargetAccuracy(Number(e.target.value))}
                                            className="px-3 py-2 border border-primary/20 rounded-sm bg-bg-primary text-primary w-20"
                                        />
                                        <span className="text-sm text-primary/70 font-sans">%</span>
                                    </div>
                                )}
                            </div>
                        )}
                    </>
                )}

                {/* Job Status / Error Area */}
                <div className="mt-6">
                    {shouldShowEmptyState && userStatus && (
                        <div className="bg-primary/5 border border-primary/10 rounded-sm p-6 backdrop-blur-sm text-center space-y-4">
                            {userStatus.games_count === 0 ? (
                                <>
                                    <h3 className="font-serif text-xl text-primary">No games imported yet</h3>
                                    <p className="text-primary/70 font-sans">
                                        Import your Chess.com games to generate personalized puzzles.
                                    </p>
                                    <Link
                                        to="/"
                                        className="inline-block px-6 py-2 bg-primary text-bg-primary rounded-sm font-serif transition-colors km-interactive km-focus-visible"
                                    >
                                        Go to Home
                                    </Link>
                                </>
                            ) : userStatus.puzzles_count === 0 ? (
                                <>
                                    <h3 className="font-serif text-xl text-primary">Ready to generate puzzles</h3>
                                    <p className="text-primary/70 font-sans">
                                        We found {userStatus.games_count} games. Let&apos;s create training puzzles.
                                    </p>
                                    <button
                                        type="button"
                                        onClick={handleGeneratePuzzles}
                                        disabled={controlsDisabled}
                                        className={`px-6 py-2 bg-primary text-bg-primary rounded-sm font-serif transition-colors km-focus-visible ${controlsDisabled ? 'km-interactive-disabled' : 'km-interactive'}`}
                                    >
                                        Generate Puzzles
                                    </button>
                                </>
                            ) : userStatus.due_count === 0 ? (
                                <>
                                    <h3 className="font-serif text-xl text-primary">All caught up</h3>
                                    <p className="text-primary/70 font-sans">
                                        {userStatus.next_due_at
                                            ? `Next review on ${new Date(userStatus.next_due_at).toLocaleDateString(LOCALE, { weekday: 'long', month: 'short', day: 'numeric' })}.`
                                            : 'No puzzles are due for review yet.'}
                                    </p>
                                    {userStatus.has_new_games && (
                                        <button
                                            type="button"
                                            onClick={handleGeneratePuzzles}
                                            disabled={controlsDisabled}
                                            className={`px-6 py-2 bg-primary text-bg-primary rounded-sm font-serif transition-colors km-focus-visible ${controlsDisabled ? 'km-interactive-disabled' : 'km-interactive'}`}
                                        >
                                            Generate from New Games
                                        </button>
                                    )}
                                </>
                            ) : (
                                <>
                                    <h3 className="font-serif text-xl text-primary">
                                        {userStatus.due_count} puzzle{userStatus.due_count === 1 ? '' : 's'} ready
                                    </h3>
                                    <p className="text-primary/70 font-sans">
                                        Start a session to review your due puzzles.
                                    </p>
                                </>
                            )}
                        </div>
                    )}
                    {/* No card for the no-username case. This slot used to say
                        "Ready to train — click Start Session", which was a dead
                        instruction (every control above is disabled without a
                        username). The fix is not a second card: the panel above
                        already states the problem AND links to Home to fix it,
                        so anything here is a duplicate of the control beside it. */}
                    {statusLoadFailed && (
                        <div className="bg-negative-soft border border-negative-soft rounded-sm p-6 text-center space-y-4" role="alert" aria-live="assertive">
                            <h3 className="font-serif text-xl text-primary">Couldn&apos;t load your training data</h3>
                            <p className="text-primary/70 font-sans">
                                We couldn&apos;t load your puzzles right now. Please try again.
                            </p>
                            <button
                                type="button"
                                onClick={handleRefreshInsights}
                                disabled={isRefreshingInsights}
                                className={`px-6 py-2 border border-primary/20 text-primary rounded-sm font-serif transition-all km-focus-visible ${isRefreshingInsights ? 'km-interactive-disabled' : 'km-interactive'}`}
                            >
                                {isRefreshingInsights ? 'Retrying...' : 'Retry'}
                            </button>
                        </div>
                    )}
                    {shouldShowErrorCard && (
                        <div className="space-y-4">
                            <JobStatusCard status="failed" error={error ?? 'Failed to generate puzzles'} />
                            <div className="flex flex-wrap justify-center gap-3">
                                <button
                                    type="button"
                                    onClick={handleStartSession}
                                    disabled={!canRetryLoad}
                                    className={`px-6 py-2 border border-primary/20 text-primary rounded-sm font-serif transition-all km-focus-visible ${!canRetryLoad ? 'km-interactive-disabled' : 'km-interactive'}`}
                                >
                                    Retry
                                </button>
                                {userStatus?.has_new_games && (
                                    <button
                                        type="button"
                                        onClick={handleGeneratePuzzles}
                                        disabled={!canRetryLoad}
                                        className={`px-6 py-2 bg-primary text-bg-primary rounded-sm font-serif transition-colors km-focus-visible ${!canRetryLoad ? 'km-interactive-disabled' : 'km-interactive'}`}
                                    >
                                        Generate New
                                    </button>
                                )}
                                {/* No connect control here. The panel above is
                                    rendered whenever there is no username and
                                    already offers one — a second link to the
                                    same place, worded differently, reads as two
                                    different actions. Same reasoning as the
                                    absent no-username card below. */}
                            </div>
                        </div>
                    )}
                    {shouldShowJobStatusCard && job && (
                        <JobStatusCard
                            status={job.status}
                            message={job.message}
                            progress={job.progress}
                            hint={job.status === 'queued' || job.status === 'running'
                                ? 'Analyzing your recent games with Stockfish; this usually takes 2-3 minutes.'
                                : undefined}
                            error={job.status === 'failed' ? (job.error || job.message) : undefined}
                            onCancel={handleCancelJob}
                        />
                    )}
                    {shouldShowLoadingCard && (
                        <JobStatusCard
                            status="running"
                            message={isResumingSession ? 'Resuming your session...' : isLoadingStatus ? 'Loading training status...' : 'Loading puzzles...'}
                        />
                    )}
                    {shouldShowPartialDataCard && (
                        <div className="bg-primary/5 border border-primary/10 rounded-sm p-6 backdrop-blur-sm text-center space-y-4">
                            <h3 className="font-serif text-xl text-primary">Some insights are unavailable</h3>
                            <p className="text-primary/70 font-sans">
                                {insightsError || 'We are still syncing your tactical insights. Refresh to try again.'}
                            </p>
                            <button
                                type="button"
                                onClick={handleRefreshInsights}
                                disabled={isRefreshingInsights}
                                className={`px-6 py-2 border border-primary/20 text-primary rounded-sm font-serif transition-all km-focus-visible ${isRefreshingInsights ? 'km-interactive-disabled' : 'km-interactive'}`}
                            >
                                {isRefreshingInsights ? 'Refreshing...' : 'Refresh Insights'}
                            </button>
                        </div>
                    )}
                </div>
            </section>

            {/* Weak Areas Card */}
            {motifPerformance && motifPerformance.weakest_motifs.length > 0 && (
                <section className={`bg-primary/5 border border-primary/10 rounded-sm p-6 backdrop-blur-sm${activeSessionId && currentPuzzle ? ' hidden lg:block' : ''}`}>
                    <h3 className="text-lg font-serif text-primary mb-4">
                        Your Weak Areas
                    </h3>
                    <div className="space-y-2">
                        {motifPerformance.motifs
                            .filter(m => m.rank === 'needs_work')
                            .map(motif => (
                                <div key={motif.name} className="flex justify-between items-center p-3 bg-negative-soft rounded-sm">
                                    <div>
                                        <span className="font-serif text-primary">{formatMotifName(motif.name)}</span>
                                        <span className="text-xs text-primary/70 ml-2">
                                            {motif.passed}/{motif.total_puzzles} correct
                                        </span>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        <span className="text-negative font-mono text-sm">
                                            {Math.round(motif.accuracy * 100)}%
                                        </span>
                                        <span className="text-xs text-primary/70">needs work</span>
                                    </div>
                                </div>
                            ))}
                    </div>
                </section>
            )}

            {/* Warmup Diagnostic Banner */}
            {warmupMode && sessionState === 'active' && (
                <div
                    className="bg-status-new-soft border border-status-new-soft rounded-sm p-4 mb-6 text-center animate-teedin"
                    role="status"
                    aria-live="polite"
                >
                    <p className="text-xs font-sans uppercase tracking-widest text-primary/70 mb-1">
                        Warmup
                    </p>
                    <p className="text-primary font-serif text-lg">
                        Diagnostic session
                    </p>
                    <p className="text-primary/70 text-sm font-sans">
                        Complete 5 puzzles to see what stuck while you were away
                    </p>
                </div>
            )}

            {currentPuzzle && ( // Make sure currentPuzzle is defined or access checked
                <section ref={boardSectionRef} className="-order-1 lg:order-6 grid lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] gap-6 lg:gap-16 scroll-mt-6">
                    {/* Chessboard */}
                    <div className="lg:order-1 scroll-mb-8">
                        <div className="aspect-square w-full max-w-[680px] mx-auto shadow-2xl shadow-primary/5 rounded-sm overflow-hidden border border-primary/10">
                            <AccessibleChessboard
                                onKeyboardMove={({ sourceSquare, targetSquare, promotion }) =>
                                    onPieceDrop(sourceSquare, targetSquare, promotion ?? 'q')
                                }
                                moveableSide={currentPuzzle.side_to_move === 'white' ? 'w' : 'b'}
                                options={{
                                    position: game.fen(),
                                    onPieceDrop: ({ sourceSquare, targetSquare }) => {
                                        setClickFrom(null);
                                        return targetSquare ? onPieceDrop(sourceSquare, targetSquare) : false;
                                    },
                                    // Click-to-move: click a piece, then its destination —
                                    // the standard chess-site input, alongside drag + keyboard.
                                    onSquareClick: ({ square, piece }) => {
                                        if (status !== 'solving') return;
                                        const side = currentPuzzle.side_to_move === 'white' ? 'w' : 'b';
                                        const ownPiece = piece && typeof piece.pieceType === 'string' && piece.pieceType.startsWith(side);
                                        if (!clickFrom) {
                                            if (ownPiece) setClickFrom(square);
                                            return;
                                        }
                                        if (square === clickFrom) return setClickFrom(null);
                                        // Clicking another of your own pieces re-selects it.
                                        if (ownPiece) return setClickFrom(square);
                                        onPieceDrop(clickFrom, square);
                                        setClickFrom(null);
                                    },
                                    boardOrientation: currentPuzzle.side_to_move === 'white' ? 'white' : 'black',
                                    darkSquareStyle: { backgroundColor: 'var(--color-chess-board-dark)' },
                                    lightSquareStyle: { backgroundColor: 'var(--color-chess-cream-300)' },
                                    squareStyles: {
                                        ...clue.squareStyles,
                                        ...(clickFrom
                                            ? { [clickFrom]: { boxShadow: 'inset 0 0 0 3px var(--border-primary)' } }
                                            : {}),
                                    },
                                }}
                            />
                        </div>
                        {/* Compact board-adjacent context: mobile-only, non-interactive meta */}
                        <div data-testid="mobile-puzzle-context" className="lg:hidden mt-3 flex items-center justify-between text-xs font-sans text-primary/70 px-1">
                            <span className="uppercase tracking-wide">
                                {currentPuzzle.side_to_move === 'white' ? 'White to move' : 'Black to move'}
                            </span>
                            {currentPuzzle.primary_motif && (
                                <span className="px-2 py-0.5 bg-primary/10 rounded-sm">
                                    {formatMotifName(currentPuzzle.primary_motif)}
                                </span>
                            )}
                            <span className="font-mono">{currentIndex + 1}/{puzzles.length}</span>
                        </div>

                        {/* Compact mobile session progress. The full panel below
                            is `hidden lg:block`, so below 1024px the streak,
                            hint tally and progress bar — the whole feedback loop
                            of a session — were invisible. Same numbers, sized
                            for a phone. */}
                        {activeSessionId && sessionSummary && (
                            <div data-testid="mobile-session-progress" className="lg:hidden mt-3 px-1 space-y-1.5">
                                <div className="flex items-center justify-between text-xs font-sans text-primary/70">
                                    <span className="uppercase tracking-wide">
                                        Streak <span className="font-mono text-primary/80">{streak}</span>
                                        <span className="mx-2 text-primary/30">·</span>
                                        Hints <span className="font-mono text-primary/80">{hintsUsed}</span>
                                    </span>
                                    <span className="font-mono">
                                        {reviewedCount} / {sessionSummary.requested_n}
                                    </span>
                                </div>
                                <div
                                    className="h-1.5 bg-primary/10 rounded-full overflow-hidden"
                                    role="progressbar"
                                    aria-valuenow={reviewedCount}
                                    aria-valuemin={0}
                                    aria-valuemax={sessionSummary.requested_n}
                                    aria-label="Session progress"
                                >
                                    <div
                                        className="h-full bg-primary transition-all duration-500 ease-out"
                                        style={{ width: `${Math.min(100, (reviewedCount / sessionSummary.requested_n) * 100)}%` }}
                                    />
                                </div>
                            </div>
                        )}
                    </div>

                    {/* Sidebar Controls */}
                    <div className="lg:order-2 space-y-8 flex flex-col justify-center">
                        <div className="hidden lg:block space-y-2">
                            {/* `w-full` on the inner column left no room for the
                                side-to-move caption, which overflowed the card and
                                collided with the session panel. The column now
                                flexes and the caption lives with the puzzle title,
                                which is where it belongs — it describes the
                                position, not the session. */}
                            <div className="bg-primary/5 p-4 rounded-sm border-l-2 border-primary">
                                <div className="flex flex-col">
                                    {activeSessionId && sessionSummary && (
                                        <div className="bg-primary/5 border border-primary/10 rounded-sm p-4 mb-4 w-full">
                                            <div className="flex justify-between items-center mb-2">
                                                <span className="font-serif text-primary font-medium">
                                                    Session in Progress
                                                    {sessionSummary.session_type && sessionSummary.session_type !== 'standard'
                                                        ? ` (${sessionSummary.session_type.replace('_', ' ')})`
                                                        : ''}
                                                </span>
                                                <span className="text-sm font-mono text-primary/70">
                                                    {reviewedCount} / {sessionSummary.requested_n}
                                                </span>
                                            </div>

                                            {/* Progress Bar */}
                                            <div className="h-2 bg-primary/10 rounded-full overflow-hidden">
                                                <div
                                                    className="h-full bg-primary transition-all duration-500 ease-out"
                                                    style={{ width: `${Math.min(100, (reviewedCount / sessionSummary.requested_n) * 100)}%` }}
                                                />
                                            </div>

                                            {/* Core session stats. Typographic, not emoji: the
                                                harmonised Dashboard cards (Consistency, Momentum)
                                                label their figures with small-caps sans and let the
                                                mono numeral carry the emphasis, which is the calm,
                                                intellectual register the design guide asks for. */}
                                            <div className="flex justify-between items-end mt-3 gap-4">
                                                <div className="flex items-end gap-4">
                                                    <div data-testid="session-stat-streak">
                                                        {/* key={streak} re-triggers the pop on every increase;
                                                            milestones (3+) also gain weight. Reduced-motion
                                                            users get the weight change without the pulse. */}
                                                        <p
                                                            key={streak}
                                                            className={`font-mono text-primary leading-none inline-block ${streak >= 3 ? 'animate-streakpop font-medium' : ''}`}
                                                        >
                                                            {streak}
                                                        </p>
                                                        <p className="text-[10px] uppercase tracking-widest text-primary/70 font-sans mt-1">
                                                            Streak
                                                        </p>
                                                    </div>
                                                    <div data-testid="session-stat-best">
                                                        <p className="font-mono text-primary/80 leading-none">{bestStreak}</p>
                                                        <p className="text-[10px] uppercase tracking-widest text-primary/70 font-sans mt-1">
                                                            Best
                                                        </p>
                                                    </div>
                                                </div>
                                                <div className="text-right" data-testid="session-stat-hints">
                                                    <p className="font-mono text-primary/80 leading-none">{hintsUsed}</p>
                                                    <p className="text-[10px] uppercase tracking-widest text-primary/70 font-sans mt-1">
                                                        Hints
                                                    </p>
                                                </div>
                                            </div>

                                            <div className="mt-3">
                                                <button
                                                    type="button"
                                                    onClick={() => setShowSessionDetails((prev) => !prev)}
                                                    aria-expanded={showSessionDetails}
                                                    aria-controls="session-details-panel"
                                                    aria-label={sessionDetailsA11yCopy.toggleLabel}
                                                    aria-describedby="session-details-helper"
                                                    className="text-xs font-sans font-normal text-primary/70 km-inline-link km-focus-visible"
                                                >
                                                    {showSessionDetails ? 'Hide details' : 'Show details'}
                                                </button>
                                                <span id="session-details-helper" className="sr-only">
                                                    {sessionDetailsA11yCopy.helperText}
                                                </span>
                                                <span className="sr-only" role="status" aria-live="polite" aria-atomic="true">
                                                    {sessionDetailsA11yCopy.liveStatus}
                                                </span>
                                            </div>

                                            {showSessionDetails && (
                                                <div id="session-details-panel" className="mt-3 space-y-3">
                                                    {/* Performance Visualization */}
                                                    {performanceHistory.length > 0 && (
                                                        <div>
                                                            <div className="flex justify-between text-xs text-primary/70 mb-1">
                                                                <span>Recent Performance:</span>
                                                                <span>{calculateRecentPerformance(performanceHistory)}% accuracy (5min)</span>
                                                            </div>
                                                            <div className="flex h-2 rounded-full overflow-hidden bg-primary/10">
                                                                {performanceHistory.slice(-10).map((item, index) => (
                                                                    <div
                                                                        key={index}
                                                                        className={`flex-1 ${item.result === 'pass' ? 'bg-positive-fill' : 'bg-negative-fill'}`}
                                                                        title={`${item.result.toUpperCase()} - ${new Date(item.time).toLocaleTimeString(LOCALE)}`}
                                                                    />
                                                                ))}
                                                            </div>
                                                            <div className="flex justify-between text-xs text-primary/70 mt-1">
                                                                <span>
                                                                    Trend:
                                                                    <span className={`ml-1 ${getPerformanceTrend(performanceHistory) === 'improving' ? 'text-positive' :
                                                                        getPerformanceTrend(performanceHistory) === 'declining' ? 'text-negative' : 'text-primary/70'
                                                                        }`}>
                                                                        {getPerformanceTrend(performanceHistory) === 'improving' ? '↗ Improving' :
                                                                            getPerformanceTrend(performanceHistory) === 'declining' ? '↘ Declining' : '→ Stable'}
                                                                    </span>
                                                                </span>
                                                                <span>
                                                                    Time: {timer.currentPuzzleTime}s
                                                                </span>
                                                            </div>
                                                        </div>
                                                    )}

                                                    {/* Timed Session Timer */}
                                                    {sessionSummary.session_type === 'timed' && timer.timeRemaining > 0 && (
                                                        <div className="text-center">
                                                            <span className={`font-mono text-sm ${timer.timeRemaining < 60 ? 'text-negative' : 'text-primary/80'}`}>
                                                                Time Remaining: {Math.floor(timer.timeRemaining / 60)}:{(timer.timeRemaining % 60).toString().padStart(2, '0')}
                                                            </span>
                                                        </div>
                                                    )}
                                                </div>
                                            )}

                                            {isResumingSession && (
                                                <div className="text-xs text-center mt-2 text-primary/70 animate-pulse">
                                                    Resuming previous session...
                                                </div>
                                            )}
                                        </div>
                                    )}

                                    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
                                        <div className="flex items-center gap-2 min-w-0">
                                            <span className="font-serif text-xl text-primary">
                                                {currentPuzzle.display_name}
                                                {/* text-primary/70, not opacity-50: axe measured the
                                                    latter at 3.56:1 on the card tint (needs 4.5).
                                                    Same fix the sidebar nav already made — an alpha
                                                    colour also lets tooling compute the ratio, which
                                                    element opacity defeats. */}
                                                <span className="text-base font-normal text-primary/70 ml-2 font-sans">
                                                    {currentIndex + 1} / {puzzles.length}
                                                </span>
                                            </span>
                                            {currentPuzzle.primary_motif && (
                                                <span className="text-sm font-sans text-primary/70 px-2 py-1 bg-primary/10 rounded-sm">
                                                    {formatMotifName(currentPuzzle.primary_motif)}
                                                </span>
                                            )}
                                        </div>
                                        <span className="font-sans text-xs tracking-widest uppercase text-primary/70 shrink-0">
                                            {currentPuzzle.side_to_move === 'white' ? 'White to Move' : 'Black to Move'}
                                        </span>
                                    </div>
                                </div>
                            </div>
                        </div>

                        {/* Status Area */}
                        <div className="min-h-[60px] md:min-h-[100px] flex items-center justify-center text-center p-4 md:p-6 border border-primary/10 rounded-sm relative overflow-hidden" role="status" aria-live="polite">
                            {status === 'solving' && clue.clueStage === 0 && (
                                motifHint
                                    ? <p className="text-primary/80 font-sans text-sm">
                                        Look for a {formatMotifName(motifHint).toLowerCase()}.
                                    </p>
                                    : linePlyIndex > 0
                                        ? <p className="text-positive font-serif text-lg italic">Good move — now find the next move in the line.</p>
                                        : <p className="text-primary/70 font-serif text-lg italic">Find the best move...</p>
                            )}
                            {status === 'solving' && clue.clueStage === 1 && (
                                <p className="text-primary/80 font-sans text-sm">
                                    {clue.pieceHint || 'Move the correct piece'}
                                </p>
                            )}
                            {status === 'solving' && clue.clueStage === 2 && (
                                <p className="text-primary/80 font-sans text-sm">
                                    {clue.moveHint || clue.pieceHint || 'Move the correct piece'}
                                </p>
                            )}
                            {status === 'correct' && (
                                <div className="text-center">
                                    <p className="text-positive font-serif text-2xl animate-teedin">Correct! Excellent.</p>
                                    {lastFeedback && (
                                        <p className="text-positive font-sans text-sm mt-2 animate-teedin">{lastFeedback}</p>
                                    )}
                                </div>
                            )}
                            {status === 'incorrect' && (
                                <div className="text-center">
                                    <p className="text-negative font-serif text-2xl animate-teedin">Not this one — take another look.</p>
                                    {lastFeedback && (
                                        <p className="text-negative font-sans text-sm mt-2 animate-teedin">{lastFeedback}</p>
                                    )}
                                </div>
                            )}
                            {status === 'revealed' && (
                                <div>
                                    <p className="text-primary/70 font-sans text-xs uppercase tracking-widest mb-1">
                                        {revealedPv.length > 1 ? 'Solution line' : 'Solution'}
                                    </p>
                                    {/* Human notation (SAN), not raw UCI — "Qxf7#" reads as
                                        chess; "h5f7" reads as coordinates. Played out on the
                                        board by the reveal playback at the same time. */}
                                    <p className="text-primary font-serif text-xl">
                                        {currentPuzzle
                                            ? uciLineToSan(
                                                currentPuzzle.fen,
                                                revealedPv.length > 0 ? revealedPv : (revealedMove ? [revealedMove] : []),
                                            ).join('  ') || '…'
                                            : '…'}
                                    </p>
                                </div>
                            )}
                        </div>

                        {/* Connectivity/action failures. Sits outside the status
                            region (which is polite and describes the puzzle) and
                            is announced assertively — it means an action the user
                            just took did NOT happen. */}
                        {actionError && (
                            <div
                                className="bg-negative-soft border border-negative-soft rounded-sm p-3 text-sm"
                                role="alert"
                                aria-live="assertive"
                            >
                                <p className="text-negative font-sans">{actionError}</p>
                            </div>
                        )}

                        {/* Actions */}
                        <div className="space-y-6">
                            {/* Type Move Toggle */}
                            <div className="flex justify-between items-center px-2">
                                <span className="text-xs text-primary/70 uppercase tracking-widest font-sans">Input Method</span>
                                <button
                                    type="button"
                                    onClick={() => setShowUciInput(!showUciInput)}
                                    className="km-interactive km-focus-visible km-inline-link text-primary text-xs font-serif underline decoration-primary/30 underline-offset-4 transition-colors">
                                    {showUciInput ? 'Switch to Drag & Drop' : 'Type Move Manually'}
                                </button>
                            </div>

                            {showUciInput && (
                                <div className="animate-switchedin">
                                    <input
                                        type="text"
                                        placeholder="e.g. e2e4"
                                        value={userMove}
                                        onChange={(e) => setUserMove(e.target.value)}
                                        className="w-full bg-primary/5 border border-primary/20 p-3 rounded-sm text-primary font-mono text-center focus:outline-none focus:border-primary/60 transition-colors"
                                        // onKeyDown, not the deprecated onKeyPress — which React no
                                        // longer fires reliably and which IMEs skip entirely.
                                        onKeyDown={(e) => {
                                            if (e.key === 'Enter') handleCheckAnswer();
                                        }}
                                    />
                                </div>
                            )}

                            {status === 'solving' && (
                                <div className="grid grid-cols-3 gap-2 md:gap-3">
                                    <button
                                        type="button"
                                        onClick={handleCheckAnswer}
                                        disabled={!userMove}
                                        aria-label={puzzleActionA11yCopy.checkMoveLabel}
                                        className={`px-2 py-3 md:px-6 md:py-4 bg-primary text-bg-primary rounded-sm font-serif text-sm md:text-lg transition-all shadow-lg shadow-primary/5 km-focus-visible ${!userMove ? 'km-interactive-disabled' : 'km-interactive'}`}>
                                        Check Move
                                    </button>
                                    <button
                                        type="button"
                                        onClick={handleHint}
                                        disabled={!currentPuzzle || clue.isExhausted}
                                        aria-label={puzzleActionA11yCopy.hintLabel}
                                        className="px-2 py-3 md:px-6 md:py-4 border border-primary/20 text-primary rounded-sm font-serif text-sm md:text-lg transition-all km-interactive km-focus-visible">
                                        {/* Short enough to stay on one line in the
                                            three-up action grid; the full "Hint 1 of 3:
                                            …" phrasing lives in the aria-label. */}
                                        {clue.isExhausted && motifHintAsked
                                            ? 'Hints used'
                                            : `Hint ${clue.clueStage + (motifHintAsked ? 1 : 0)}/4`}
                                    </button>
                                    <button
                                        type="button"
                                        onClick={handleRevealSolution}
                                        aria-label={puzzleActionA11yCopy.revealLabel}
                                        className="px-2 py-3 md:px-6 md:py-4 border border-primary/10 text-primary/70 rounded-sm font-serif text-sm md:text-lg transition-all km-interactive km-focus-visible hover:text-primary hover:border-primary/30">
                                        Reveal
                                    </button>
                                </div>
                            )}
                            {(status === 'correct' || status === 'revealed') && (
                                <div className="space-y-4">
                                    {/* Performance Stats for this puzzle */}
                                    {currentPuzzle?.attempts !== undefined && (
                                        <div className="bg-primary/5 p-3 rounded-sm text-sm">
                                            <div className="flex justify-between">
                                                <span className="text-primary/70">Puzzle Stats:</span>
                                                <span className="font-mono">
                                                    {currentPuzzle.pass_count || 0}/{currentPuzzle.attempts || 0}
                                                    {currentPuzzle.attempts ? ` (${Math.round(((currentPuzzle.pass_count || 0) / currentPuzzle.attempts) * 100)}%)` : ''}
                                                </span>
                                            </div>
                                            {currentPuzzle.next_due_at && (
                                                <div className="flex justify-between mt-1">
                                                    <span className="text-primary/70">Next Review:</span>
                                                    <span className="font-mono">
                                                        {new Date(currentPuzzle.next_due_at).toLocaleDateString(LOCALE)}
                                                    </span>
                                                </div>
                                            )}
                                        </div>
                                    )}

                                    {sessionState === 'completed' ? (
                                        sessionSummary ? (
                                            <p className="text-center text-primary/70 font-sans text-sm py-4">
                                                Session complete — see your summary below.
                                            </p>
                                        ) : (
                                            <Link
                                                to="/dashboard"
                                                className="w-full block text-center px-6 py-4 bg-primary text-bg-primary rounded-sm font-serif text-lg transition-opacity km-interactive km-focus-visible">
                                                Back to Dashboard
                                            </Link>
                                        )
                                    ) : (
                                        <button
                                            type="button"
                                            onClick={handleAdvancePuzzle}
                                            disabled={finishButtonDisabled}
                                            // The page's one primary action, styled like every other
                                            // primary action in the app. It used to be bg-green-600 +
                                            // text-white — a colour that exists nowhere else, and a
                                            // fixed pair that cannot clear contrast in both themes.
                                            className={`w-full px-6 py-4 bg-primary text-bg-primary rounded-sm font-serif text-lg transition-opacity km-focus-visible ${finishButtonDisabled ? 'km-interactive-disabled' : 'km-interactive'} flex items-center justify-center`}>
                                            {sessionState === 'completing' ? (
                                                <>
                                                    <span className="animate-spin h-5 w-5 border-2 border-current/20 border-t-current rounded-full mr-2"></span>
                                                    Recording Session...
                                                </>
                                            ) : isFinalPuzzle ? 'All Done' : 'Next Puzzle →'}
                                        </button>
                                    )}
                                </div>
                            )}
                            {status === 'incorrect' && (
                                <div className="space-y-4">
                                    {/* Detailed feedback for incorrect answers */}
                                    {lastFeedback && (
                                        <div className="bg-negative-soft border border-negative-soft p-3 rounded-sm text-sm">
                                            <p className="text-negative font-sans">{lastFeedback}</p>
                                        </div>
                                    )}

                                    <div className="grid grid-cols-2 gap-2 md:gap-3">
                                        <button
                                            type="button"
                                            onClick={async () => {
                                                setActionError(null);
                                                if (!await handleReviewPuzzle('fail')) {
                                                    setActionError("We couldn't save that result — nothing was recorded. Check your connection and try again.");
                                                    return;
                                                }
                                                setStatus('solving');
                                                setUserMove('');
                                                setGame(new Chess(currentPuzzle.fen));
                                                // Restart the line from the top for the retry.
                                                setLinePlyIndex(0);
                                                setAttemptedLine([]);
                                                clue.reset();
                                            }}
                                            className="px-2 py-3 md:px-6 md:py-4 border border-primary/20 text-primary rounded-sm font-serif text-sm md:text-lg transition-all km-interactive km-focus-visible">
                                            <span className="md:hidden">Try Again</span>
                                            <span className="hidden md:inline">Mark as Failed & Try Again</span>
                                        </button>
                                        <button
                                            type="button"
                                            onClick={handleRevealSolution}
                                            aria-label={puzzleActionA11yCopy.showSolutionLabel}
                                            // One primary per state. On the final puzzle "Finish
                                            // Session" below is the primary action, so this steps
                                            // down to the outline treatment rather than competing
                                            // with it (previously they were solid-ink and orange,
                                            // three button identities on one screen).
                                            className={`px-2 py-3 md:px-6 md:py-4 rounded-sm font-serif text-sm md:text-lg transition-all km-interactive km-focus-visible ${isFinalPuzzle ? 'border border-primary/20 text-primary' : 'bg-primary text-bg-primary'}`}>
                                            Show Solution
                                        </button>
                                    </div>

                                    {/* Special button for completing session when final puzzle is failed */}
                                    {isFinalPuzzle && (
                                        <button
                                            type="button"
                                            onClick={async () => {
                                                // Guard re-entry (same as handleAdvancePuzzle) so a fast
                                                // double-click can't fire two concurrent complete calls
                                                // before `disabled` catches up with sessionState.
                                                if (isAdvancingPuzzle.current) return;
                                                isAdvancingPuzzle.current = true;
                                                try {
                                                    setActionError(null);
                                                    // Don't close the session on an unrecorded
                                                    // review — the summary would be missing it.
                                                    if (!await handleReviewPuzzle('fail')) {
                                                        setActionError("We couldn't save that result — the session is still open. Check your connection and try again.");
                                                        return;
                                                    }
                                                    // Finishing the final puzzle (as a fail) ends the session.
                                                    setReviewedCount(prev => prev + 1);
                                                    await handleCompleteSession();
                                                } finally {
                                                    isAdvancingPuzzle.current = false;
                                                }
                                            }}
                                            disabled={sessionState === 'completing'}
                                            className="w-full px-6 py-4 bg-primary text-bg-primary rounded-sm font-serif text-lg transition-opacity km-focus-visible km-interactive mt-4">
                                            {sessionState === 'completing' ? (
                                                <>
                                                    <span className="animate-spin h-5 w-5 border-2 border-current/20 border-t-current rounded-full mr-2 inline-block"></span>
                                                    Recording Session...
                                                </>
                                            ) : (
                                                'Finish Session'
                                            )}
                                        </button>
                                    )}
                                </div>
                            )}
                        </div>
                    </div>
                </section>
            )}

            {/* Session Summary */}
            {sessionSummary && sessionState === 'completed' && (
                <div ref={summaryRef} className="lg:order-7 scroll-mt-6">
                    {warmupMode ? (
                        <WarmupSummary
                            sessionSummary={sessionSummary}
                            onContinue={() => {
                                setWarmupMode(false);
                                navigate('/dashboard');
                            }}
                        />
                    ) : (
                        <SessionSummaryCard
                            sessionSummary={sessionSummary}
                            achievements={achievements}
                            onStartNewSession={() => {
                                setSessionSummary(null);
                                setLastFeedback('');
                                handleStartSession();
                            }}
                        />
                    )}
                </div>
            )}

            {/* Recent Sessions */}
            <div className="lg:order-8"><RecentSessionsCard sessions={recentSessions} /></div>

            {/* Achievements Progress */}
            <div className="lg:order-9"><AchievementsList achievements={achievements} /></div>

            {/* Chess Pattern Mastery */}
            {motifPerformance && motifPerformance.motifs.length > 0 && (
                <section className="lg:order-10 bg-primary/5 border border-primary/10 rounded-sm p-6 backdrop-blur-sm">
                    <h3 className="text-lg font-serif text-primary mb-4">Chess Pattern Mastery</h3>
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                        {motifPerformance.motifs.map(motif => {
                            const rank = motifRankStyle(motif.rank);
                            return (
                                <div key={motif.name} className={`p-4 rounded-sm border ${rank.panel}`}>
                                    <h4 className="font-serif text-primary mb-1">{formatMotifName(motif.name)}</h4>
                                    <div className="flex justify-between text-sm">
                                        <span className="text-primary/70">
                                            {motif.passed}/{motif.total_puzzles} solved
                                        </span>
                                        <span className={`font-mono ${rank.figure}`}>
                                            {Math.round(motif.accuracy * 100)}%
                                        </span>
                                    </div>

                                    {/* Progress bar — bg-current inherits the rank colour set on
                                        the track, so the bar can never disagree with the figure. */}
                                    <div className={`mt-2 h-2 bg-primary/10 rounded-full overflow-hidden ${rank.figure}`}>
                                        <div
                                            className="h-full bg-current"
                                            style={{ width: `${motif.accuracy * 100}%` }}
                                        />
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                </section>
            )}
        </div>
    );
}
