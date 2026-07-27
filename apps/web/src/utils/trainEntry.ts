/**
 * The single decision behind the Dashboard hero's primary CTA: what it should
 * say, and where it should go.
 *
 * These were computed in two places — the card chose the label, the page chose
 * the route — and drifted: a brand-new user got a button reading "Start First
 * Session" that navigated to the Train page, whose only available action was
 * "Go to Home". Keeping the rule here means the label and the destination
 * cannot disagree again.
 */
export interface TrainEntryState {
    /** Completed training sessions, ever. */
    totalSessions: number;
    /** Puzzles the user can train right now (due + never-reviewed). */
    dueCount: number;
    /** Server's "you've been away long enough for a warmup" flag. */
    needsWarmup: boolean;
}

/**
 * True when the user has nothing to train and has never trained — i.e. their
 * real next step is importing games, not opening the board.
 */
export function needsImportFirst({ totalSessions, dueCount }: Pick<TrainEntryState, 'totalSessions' | 'dueCount'>): boolean {
    return totalSessions === 0 && dueCount === 0;
}

/** Route the hero's primary CTA should navigate to. */
export function trainEntryDestination(state: TrainEntryState): string {
    if (needsImportFirst(state)) return '/';
    if (state.needsWarmup) return '/puzzles?warmup=true';
    return '/puzzles';
}
