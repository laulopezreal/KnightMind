import { Link } from 'react-router-dom';
import { LOCALE } from '../utils/locale';
import { type SessionSummary } from '../api';
import { AnimatedNumber } from './AnimatedNumber';

interface Achievement {
    id: string;
    name: string;
    description: string;
    icon: string;
    earned: boolean;
    earnedAt?: Date;
}

interface SessionSummaryCardProps {
    sessionSummary: SessionSummary;
    achievements: Achievement[];
    onStartNewSession: () => void;
}

const calculateAccuracy = (passCount: number, failCount: number): number => {
    const total = passCount + failCount;
    return total > 0 ? Math.round((passCount / total) * 100) : 0;
};

export function SessionSummaryCard({
    sessionSummary,
    achievements,
    onStartNewSession
}: SessionSummaryCardProps) {
    // The finish is the session's emotional peak — headline it like a result,
    // not a database receipt ("Successfully Recorded"). Tone tracks accuracy so
    // a rough session isn't greeted with false cheer.
    const total = sessionSummary.pass_count + sessionSummary.fail_count;
    const accuracy = total > 0 ? sessionSummary.pass_count / total : 0;
    const headline = total === 0
        ? 'Session complete'
        : accuracy >= 0.8
        ? 'Sharp session!'
        : accuracy >= 0.5
        ? 'Session complete — solid work'
        : 'Session complete — tough one, keep at it';

    const missedPuzzles = sessionSummary.missed_puzzles;
    const hasMissed = missedPuzzles && missedPuzzles.length > 0;
    const earnedAchievements = achievements.filter(achievement => achievement.earned);
    const completionTime = `${Math.floor(sessionSummary.total_time_ms / 60000)}m ${Math.floor((sessionSummary.total_time_ms % 60000) / 1000)}s`;

    // Token-only success treatment: a raw green-500 fill with a white tick is a
    // fixed pair that cannot clear contrast in both themes, and it was the only
    // green of its kind in the app. The soft tint + positive ink reads as
    // "complete" in day and night alike.
    return (
        <section className="bg-primary/5 border border-positive-soft rounded-sm p-6 sm:p-8 backdrop-blur-sm animate-teedin">
            <header className="mb-6">
                <div className="flex items-center">
                    <div className="shrink-0 size-8 rounded-full bg-positive-fill flex items-center justify-center mr-3">
                        <svg className="size-5 text-positive" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                        </svg>
                    </div>
                    <h2 className="text-2xl font-serif text-primary">{headline}</h2>
                </div>
            </header>

            <div className="grid grid-cols-3 gap-3 sm:gap-6 mb-6" aria-label="Session result">
                <div className="text-center">
                    <div className="text-3xl font-serif text-positive"><AnimatedNumber value={sessionSummary.pass_count} /></div>
                    <div className="text-xs uppercase tracking-widest text-primary/70 mt-1">Passed</div>
                </div>
                <div className="text-center">
                    <div className="text-3xl font-serif text-negative"><AnimatedNumber value={sessionSummary.fail_count} /></div>
                    <div className="text-xs uppercase tracking-widest text-primary/70 mt-1">Failed</div>
                </div>
                <div className="text-center">
                    <div className="text-3xl font-serif text-primary">
                        <AnimatedNumber value={calculateAccuracy(sessionSummary.pass_count, sessionSummary.fail_count)} suffix="%" />
                    </div>
                    <div className="text-xs uppercase tracking-widest text-primary/70 mt-1">Accuracy</div>
                </div>
            </div>

            {/* Missed Puzzles — teach, not just count */}
            {hasMissed && (
                <section className="mb-6 border-y border-primary/10 py-5" aria-labelledby="missed-puzzles-heading">
                    <h3 id="missed-puzzles-heading" className="text-lg font-serif text-primary mb-1">
                        {missedPuzzles.length === 1 ? 'Missed puzzle' : `Missed puzzles (${missedPuzzles.length})`}
                    </h3>
                    <p className="text-sm text-primary/70 mb-3">Review what to learn from this session.</p>
                    <ul className="divide-y divide-primary/10" aria-label="Missed puzzles">
                        {missedPuzzles.map(mp => (
                            <li
                                key={mp.puzzle_id}
                                className="flex flex-col items-stretch gap-1 py-3 sm:flex-row sm:items-center sm:gap-3"
                            >
                                <div className="min-w-0 flex-1">
                                    <span className="block text-sm font-serif text-primary whitespace-normal break-words">
                                        {mp.display_name}
                                    </span>
                                    {mp.cause_label ? (
                                        <span className="block text-xs text-primary/70 mt-1 whitespace-normal break-words">
                                            {mp.cause_label}
                                        </span>
                                    ) : (
                                        <span className="block text-xs text-primary/70 mt-1 italic">
                                            Cause not yet diagnosed
                                        </span>
                                    )}
                                </div>
                                {/* min-h/min-w 11 (44px): WCAG 2.5.5 touch-target contract.
                                    The flex wrapper expands the interactive area without
                                    visually bloating the row — the text stays text-xs. */}
                                <Link
                                    to={`/library/${mp.puzzle_id}?from=session`}
                                    className="km-review-link self-start sm:self-auto shrink-0 inline-flex items-center justify-center min-h-11 min-w-11 text-xs font-serif text-primary/70 underline underline-offset-2 hover:text-primary transition-colors km-focus-visible"
                                    aria-label={`Review ${mp.display_name}`}
                                >
                                    Review
                                </Link>
                            </li>
                        ))}
                    </ul>
                </section>
            )}

            {/* Supporting details stay available without competing with the result. */}
            <section className="mb-6 border-t border-primary/10 pt-4" aria-labelledby="session-details-heading">
                <h3 id="session-details-heading" className="text-sm font-serif text-primary mb-3">Session details</h3>
                <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
                    <div>
                        <dt className="text-primary/70">Best streak</dt>
                        <dd className="font-serif text-lg text-primary"><AnimatedNumber value={sessionSummary.best_streak} /></dd>
                    </div>
                    <div>
                        <dt className="text-primary/70">Hints used</dt>
                        <dd className="font-serif text-lg text-primary"><AnimatedNumber value={sessionSummary.hints_used} /></dd>
                    </div>
                    <div>
                        <dt className="text-primary/70">Total time</dt>
                        <dd className="font-serif text-primary">{completionTime}</dd>
                    </div>
                    {sessionSummary.completed_at && (
                        <div>
                            <dt className="text-primary/70">Completed</dt>
                            <dd className="font-serif text-primary">{new Date(sessionSummary.completed_at).toLocaleString(LOCALE)}</dd>
                        </div>
                    )}
                    {sessionSummary.session_type && sessionSummary.session_type !== 'standard' && (
                        <div className="col-span-2">
                            <dt className="text-primary/70">Session type</dt>
                            <dd className="font-serif text-primary capitalize">
                                {sessionSummary.session_type.replace('_', ' ')}
                                {sessionSummary.target_accuracy && ` (${sessionSummary.target_accuracy}% accuracy)`}
                                {sessionSummary.target_time_minutes && ` (${sessionSummary.target_time_minutes} minutes)`}
                            </dd>
                        </div>
                    )}
                </dl>

                {earnedAchievements.length > 0 && (
                    <div className="mt-4 border-t border-primary/10 pt-4">
                        <h4 className="text-sm font-serif text-primary mb-2">Achievements earned</h4>
                        <div className="flex flex-wrap gap-2">
                            {earnedAchievements.map(achievement => (
                                <div
                                    key={achievement.id}
                                    className="flex items-center bg-primary/10 border border-primary/20 rounded-full px-3 py-1"
                                    title={achievement.description}
                                >
                                    <span className="text-lg mr-2">{achievement.icon}</span>
                                    <span className="text-sm font-serif text-primary">{achievement.name}</span>
                                </div>
                            ))}
                        </div>
                    </div>
                )}
            </section>

            {/* Closeout actions: Back to Dashboard is the primary ritual close;
                Start New Session is secondary — do not trap the user in a forced ritual. */}
            <Link
                to="/dashboard"
                className="w-full block text-center px-6 py-3 bg-primary text-bg-primary rounded-sm font-serif transition-opacity hover:opacity-90 km-focus-visible"
            >
                Back to Dashboard
            </Link>
            <button
                type="button"
                onClick={onStartNewSession}
                className="w-full mt-3 px-6 py-3 border border-primary/20 text-primary rounded-sm font-serif transition-all hover:bg-primary hover:text-bg-primary hover:border-transparent km-focus-visible cursor-pointer">
                Start New Session
            </button>
        </section>
    );
}
