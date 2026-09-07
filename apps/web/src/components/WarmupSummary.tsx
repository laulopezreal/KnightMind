import { Link } from 'react-router-dom';
import type { MissedPuzzleSummary, SessionSummary } from '../api/sessions';
import { calculateAccuracy } from '../utils/accuracy';

const WARMUP_RETURN_MAX_AGE_MS = 30 * 60 * 1000;
const WARMUP_RETURN_MAX_ENTRIES = 128;
const warmupReturnRegistry = new Map<string, WarmupReturnState>();

export type WarmupReturnSummary = Pick<
  SessionSummary,
  | 'session_id'
  | 'requested_n'
  | 'pass_count'
  | 'fail_count'
  | 'total_time_ms'
  | 'created_at'
  | 'completed_at'
  | 'current_streak'
  | 'best_streak'
  | 'hints_used'
  | 'missed_puzzles'
>;

export interface WarmupReturnState {
  version: 1;
  token: string;
  username: string;
  issuedAt: number;
  summary: WarmupReturnSummary;
}

interface WarmupSummaryProps {
  sessionSummary: WarmupReturnSummary;
  returnToken: string | null;
  onContinue: () => void;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isBoundedString(value: unknown, maxLength = 256): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= maxLength;
}

function isNullableBoundedString(value: unknown): value is string | null {
  return value === null || isBoundedString(value);
}

function readMissedPuzzles(value: unknown): MissedPuzzleSummary[] | null | undefined {
  if (value === undefined || value === null) return value;
  if (!Array.isArray(value) || value.length > 100) return undefined;

  const puzzles: MissedPuzzleSummary[] = [];
  for (const item of value) {
    if (
      !isRecord(item)
      || !isBoundedString(item.puzzle_id)
      || !isBoundedString(item.display_name)
      || !isNullableBoundedString(item.cause)
      || !isNullableBoundedString(item.cause_label)
    ) {
      return undefined;
    }
    puzzles.push({
      puzzle_id: item.puzzle_id,
      display_name: item.display_name,
      cause: item.cause,
      cause_label: item.cause_label,
    });
  }
  return puzzles;
}

function readSummary(value: unknown): WarmupReturnSummary | null {
  if (!isRecord(value)) return null;
  const numericKeys = [
    'requested_n',
    'pass_count',
    'fail_count',
    'total_time_ms',
    'current_streak',
    'best_streak',
    'hints_used',
  ] as const;
  if (numericKeys.some(key => !Number.isSafeInteger(value[key]) || (value[key] as number) < 0)) return null;
  if ((value.requested_n as number) === 0) return null;
  if ((value.pass_count as number) + (value.fail_count as number) > (value.requested_n as number)) return null;
  if (!isBoundedString(value.session_id) || !isBoundedString(value.created_at)) return null;
  if (!isBoundedString(value.completed_at)) return null;
  if (!Number.isFinite(Date.parse(value.created_at)) || !Number.isFinite(Date.parse(value.completed_at))) return null;

  const missedPuzzles = readMissedPuzzles(value.missed_puzzles);
  if (value.missed_puzzles !== undefined && missedPuzzles === undefined) return null;

  return {
    session_id: value.session_id,
    requested_n: value.requested_n as number,
    pass_count: value.pass_count as number,
    fail_count: value.fail_count as number,
    total_time_ms: value.total_time_ms as number,
    created_at: value.created_at,
    completed_at: value.completed_at,
    current_streak: value.current_streak as number,
    best_streak: value.best_streak as number,
    hints_used: value.hints_used as number,
    ...(missedPuzzles !== undefined ? { missed_puzzles: missedPuzzles } : {}),
  };
}

function hasReviewableMissedPuzzle(summary: WarmupReturnSummary): boolean {
  return Boolean(summary.missed_puzzles?.length);
}

function isCurrentIssuedAt(issuedAt: number, now: number): boolean {
  const age = now - issuedAt;
  return age >= -60_000 && age <= WARMUP_RETURN_MAX_AGE_MS;
}

/** Remove invalid lifecycles before every registry operation. */
function pruneWarmupReturnRegistry(now = Date.now()): void {
  for (const [token, state] of warmupReturnRegistry) {
    if (!isCurrentIssuedAt(state.issuedAt, now)) warmupReturnRegistry.delete(token);
  }
}

function createToken(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  return `warmup-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** Build a route-state projection containing only safe completed-summary fields. */
function createWarmupReturnLocationState(
  username: string,
  summary: WarmupReturnSummary,
): { warmupReturn: WarmupReturnState } {
  return {
    warmupReturn: {
      version: 1,
      token: createToken(),
      username,
      issuedAt: Date.now(),
      summary: {
        session_id: summary.session_id,
        requested_n: summary.requested_n,
        pass_count: summary.pass_count,
        fail_count: summary.fail_count,
        total_time_ms: summary.total_time_ms,
        created_at: summary.created_at,
        completed_at: summary.completed_at,
        current_streak: summary.current_streak,
        best_streak: summary.best_streak,
        hints_used: summary.hints_used,
        ...(summary.missed_puzzles !== undefined ? {
          missed_puzzles: summary.missed_puzzles?.map(puzzle => ({
            puzzle_id: puzzle.puzzle_id,
            display_name: puzzle.display_name,
            cause: puzzle.cause,
            cause_label: puzzle.cause_label,
          })) ?? null,
        } : {}),
      },
    },
  };
}

/** Register one bounded capability for a reviewable completed lifecycle. */
function createWarmupReturnToken(username: string, summary: WarmupReturnSummary): string | null {
  pruneWarmupReturnRegistry();
  const safeSummary = readSummary(summary);
  if (!safeSummary || !hasReviewableMissedPuzzle(safeSummary)) return null;

  for (const state of warmupReturnRegistry.values()) {
    if (state.username === username && state.summary.session_id === safeSummary.session_id) {
      state.summary = safeSummary;
      return state.token;
    }
  }

  while (warmupReturnRegistry.size >= WARMUP_RETURN_MAX_ENTRIES) {
    const oldestToken = warmupReturnRegistry.keys().next().value as string | undefined;
    if (!oldestToken) break;
    warmupReturnRegistry.delete(oldestToken);
  }

  const locationState = createWarmupReturnLocationState(username, safeSummary);
  warmupReturnRegistry.set(locationState.warmupReturn.token, locationState.warmupReturn);
  return locationState.warmupReturn.token;
}

/** Validate and sanitize route-owned return state for the current user. */
function readWarmupReturnState(value: unknown, username: string): WarmupReturnState | null {
  pruneWarmupReturnRegistry();
  if (!isRecord(value) || !isRecord(value.warmupReturn)) return null;
  const candidate = value.warmupReturn;
  if (
    candidate.version !== 1
    || !isBoundedString(candidate.token, 128)
    || !isBoundedString(candidate.username)
    || candidate.username !== username
    || typeof candidate.issuedAt !== 'number'
    || !Number.isFinite(candidate.issuedAt)
  ) return null;

  if (!isCurrentIssuedAt(candidate.issuedAt, Date.now())) return null;
  const summary = readSummary(candidate.summary);
  if (!summary) return null;

  const sanitized: WarmupReturnState = {
    version: 1,
    token: candidate.token,
    username: candidate.username,
    issuedAt: candidate.issuedAt,
    summary,
  };
  return sanitized;
}

/** Resolve and validate the opaque token carried by the review-return route. */
function readWarmupReturnToken(token: string | null | undefined, username: string): WarmupReturnState | null {
  pruneWarmupReturnRegistry();
  if (!token) return null;
  const state = warmupReturnRegistry.get(token);
  if (state && state.username !== username) {
    warmupReturnRegistry.delete(token);
    return null;
  }
  return state ? readWarmupReturnState({ warmupReturn: state }, username) : null;
}

/** Mark one return context closed so history cannot replay it. */
function consumeWarmupReturnState(state: WarmupReturnState): void {
  pruneWarmupReturnRegistry();
  warmupReturnRegistry.delete(state.token);
}

/**
 * WarmupSummary displays results after completing a warmup diagnostic session
 * Shows accuracy, pass/fail counts, and personalized feedback
 */
function WarmupSummaryComponent({ sessionSummary, returnToken, onContinue }: WarmupSummaryProps) {
  const accuracy = calculateAccuracy(sessionSummary.pass_count, sessionSummary.fail_count);
  const missedPuzzles = sessionSummary.missed_puzzles;
  const hasMissedPuzzles = Boolean(missedPuzzles?.length && returnToken);

  // Determine feedback based on performance
  const getFeedbackMessage = (acc: number): string => {
    if (acc >= 80) {
      return "Great retention! You're ready to continue where you left off.";
    } else if (acc >= 60) {
      return "Some patterns need brushing up. Let's get back into rhythm!";
    } else {
      return "Time to rebuild those neural pathways. Let's start with the basics.";
    }
  };

  return (
    <section
      className="bg-primary/5 border border-status-new-soft rounded-sm p-8 animate-teedin"
      role="region"
      aria-labelledby="warmup-summary-heading"
    >
      <div className="text-center mb-6">
        <h2 id="warmup-summary-heading" className="text-3xl font-serif text-primary mb-2">
          Warmup complete
        </h2>
        <p className="text-primary/70 font-sans">
          Here's how you did after your break
        </p>
      </div>

      {/* Accuracy Score */}
      <div className="text-center mb-8" role="status" aria-live="polite">
        <div className="text-6xl font-serif text-primary mb-2" aria-label={`Overall accuracy: ${accuracy} percent`}>
          {accuracy}%
        </div>
        <p className="text-primary/70 font-sans">Overall Accuracy</p>
      </div>

      {/* Pattern Retention Grid */}
      <div className="grid grid-cols-2 gap-6 mb-8">
        <div className="text-center">
          <div className="text-3xl font-serif text-positive" aria-label={`${sessionSummary.pass_count} patterns retained`}>
            {sessionSummary.pass_count}
          </div>
          <div className="text-xs uppercase tracking-widest text-primary/70 mt-1">
            Patterns Retained
          </div>
        </div>
        <div className="text-center">
          <div className="text-3xl font-serif text-negative" aria-label={`${sessionSummary.fail_count} patterns need review`}>
            {sessionSummary.fail_count}
          </div>
          <div className="text-xs uppercase tracking-widest text-primary/70 mt-1">
            Need Review
          </div>
        </div>
      </div>

      {/* Feedback Message */}
      <div className="bg-primary/5 border border-primary/10 rounded-sm p-6 mb-6">
        <p className="text-primary/70 font-sans text-center">
          {getFeedbackMessage(accuracy)}
        </p>
      </div>

      {hasMissedPuzzles && missedPuzzles && (
        <section className="mb-6 border-y border-primary/10 py-5" aria-labelledby="warmup-missed-puzzles-heading">
          <h3 id="warmup-missed-puzzles-heading" className="text-lg font-serif text-primary mb-1">
            {missedPuzzles.length === 1 ? 'Missed puzzle' : `Missed puzzles (${missedPuzzles.length})`}
          </h3>
          <p className="text-sm text-primary/70 mb-3">Review what to learn from this warmup.</p>
          <ul className="divide-y divide-primary/10" aria-label="Missed puzzles">
            {missedPuzzles.map((missedPuzzle) => (
              <li
                key={missedPuzzle.puzzle_id}
                className="flex flex-col items-stretch gap-1 py-3 sm:flex-row sm:items-center sm:gap-3"
              >
                <div className="min-w-0 flex-1">
                  <span className="block text-sm font-serif text-primary whitespace-normal break-words">
                    {missedPuzzle.display_name}
                  </span>
                  {missedPuzzle.cause_label && (
                    <span className="block text-xs text-primary/70 mt-1 whitespace-normal break-words">
                      {missedPuzzle.cause_label}
                    </span>
                  )}
                </div>
                <Link
                  to={`/library/${missedPuzzle.puzzle_id}?from=session&warmup_return=${encodeURIComponent(returnToken!)}`}
                  className="self-start sm:self-auto shrink-0 inline-flex items-center justify-center min-h-11 min-w-11 text-xs font-serif text-primary/70 underline underline-offset-2 hover:text-primary transition-colors km-focus-visible"
                  aria-label={`Review ${missedPuzzle.display_name}`}
                >
                  Review
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      <button
        type="button"
        onClick={onContinue}
        className="w-full px-6 py-3 bg-primary text-bg-primary rounded-sm font-serif transition-opacity hover:opacity-90 cursor-pointer km-focus-visible"
      >
        Back to Dashboard
      </button>
    </section>
  );
}

export const WarmupSummary = Object.assign(WarmupSummaryComponent, {
  createReturnToken: createWarmupReturnToken,
  readReturnState: readWarmupReturnState,
  readReturnToken: readWarmupReturnToken,
  consumeReturnState: consumeWarmupReturnState,
});
