import { useState } from 'react';
import { type SessionSummary } from '../api';
import { calculateAccuracy } from '../utils/accuracy';

interface RecentSessionsCardProps {
    sessions: SessionSummary[];
    collapsible?: boolean;
    defaultExpanded?: boolean;
}

export function RecentSessionsCard({
    sessions,
    collapsible = false,
    defaultExpanded = true
}: RecentSessionsCardProps) {
    const [isExpanded, setIsExpanded] = useState(defaultExpanded);

    if (sessions.length === 0) {
        return null;
    }

    return (
        <section
            className="bg-primary/5 border border-primary/5 rounded-sm p-6"
            aria-labelledby="recent-sessions-heading"
        >
            {/* Header with optional collapse button */}
            <div className="flex justify-between items-center mb-4">
                <h3 id="recent-sessions-heading" className="text-lg font-serif text-primary">
                    Recent sessions
                </h3>
                {collapsible && (
                    <button
                        type="button"
                        onClick={() => setIsExpanded(!isExpanded)}
                        className="text-primary/70 hover:text-primary transition-colors km-interactive km-focus-visible text-sm"
                        aria-expanded={isExpanded}
                        aria-controls="sessions-list"
                    >
                        {isExpanded ? 'Collapse' : 'Expand'}
                    </button>
                )}
            </div>

            {/* Collapsible content */}
            <div
                id="sessions-list"
                className={`transition-all duration-300 overflow-hidden ${
                    collapsible && !isExpanded ? 'max-h-0 opacity-0' : 'max-h-[1000px] opacity-100'
                }`}
                aria-hidden={collapsible && !isExpanded}
            >
                <div className="space-y-2" role="list">
                    {sessions.map((session) => {
                        const accuracy = calculateAccuracy(session.pass_count, session.fail_count);
                        const sessionDate = new Date(session.created_at).toLocaleDateString();
                        return (
                            <div
                                key={session.session_id}
                                className="flex justify-between items-center p-3 bg-primary/5 rounded-sm text-sm"
                                role="listitem"
                                aria-label={`Session from ${sessionDate}: ${session.pass_count} passed, ${session.fail_count} failed, ${accuracy}% accuracy${session.best_streak > 0 ? `, best streak ${session.best_streak}` : ''}`}
                            >
                                <div className="flex gap-4">
                                    <span className="text-primary/70" aria-label={`${session.pass_count} passed`}>{session.pass_count}P</span>
                                    <span className="text-primary/70" aria-label={`${session.fail_count} failed`}>{session.fail_count}F</span>
                                    <span className="text-primary/70">
                                        {accuracy}%
                                    </span>
                                    {session.best_streak > 0 && (
                                        <span className="text-primary/80" aria-label={`Best streak: ${session.best_streak}`}>🔥{session.best_streak}</span>
                                    )}
                                </div>
                                <div className="flex gap-2">
                                    {session.session_type && session.session_type !== 'standard' && (
                                        <span className="text-primary/70 text-xs capitalize">
                                            {session.session_type.replace('_', ' ')}
                                        </span>
                                    )}
                                    <span className="text-primary/70 text-xs">
                                        {sessionDate}
                                    </span>
                                </div>
                            </div>
                        );
                    })}
                </div>
            </div>
        </section>
    );
}
