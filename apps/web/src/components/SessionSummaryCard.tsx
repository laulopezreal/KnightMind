import { type SessionSummary } from '../api';

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
    return (
        <section className="bg-primary/5 border border-green-500/30 rounded-sm p-8 backdrop-blur-sm animate-teedin">
            <div className="flex items-center mb-6">
                <div className="flex-shrink-0 h-8 w-8 rounded-full bg-green-500 flex items-center justify-center mr-3">
                    <svg className="h-5 w-5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                    </svg>
                </div>
                <h2 className="text-2xl font-serif text-primary">Session Successfully Recorded!</h2>
            </div>

            {sessionSummary.completed_at && (
                <div className="text-sm text-primary/60 mb-4">
                    Completed on {new Date(sessionSummary.completed_at).toLocaleString()}
                </div>
            )}

            <div className="grid grid-cols-2 md:grid-cols-4 gap-6 mb-6">
                <div className="text-center">
                    <div className="text-3xl font-serif text-green-600">{sessionSummary.pass_count}</div>
                    <div className="text-xs uppercase tracking-widest text-primary/40 mt-1">Passed</div>
                </div>
                <div className="text-center">
                    <div className="text-3xl font-serif text-red-500">{sessionSummary.fail_count}</div>
                    <div className="text-xs uppercase tracking-widest text-primary/40 mt-1">Failed</div>
                </div>
                <div className="text-center">
                    <div className="text-3xl font-serif text-primary">
                        {calculateAccuracy(sessionSummary.pass_count, sessionSummary.fail_count)}%
                    </div>
                    <div className="text-xs uppercase tracking-widest text-primary/40 mt-1">Accuracy</div>
                </div>
                <div className="text-center">
                    <div className="text-3xl font-serif text-primary">
                        {Math.floor(sessionSummary.total_time_ms / 60000)}m {Math.floor((sessionSummary.total_time_ms % 60000) / 1000)}s
                    </div>
                    <div className="text-xs uppercase tracking-widest text-primary/40 mt-1">Total Time</div>
                </div>
            </div>

            {/* Enhanced Session Stats */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-6 mb-6">
                <div className="text-center">
                    <div className="text-3xl font-serif text-primary">{sessionSummary.best_streak}</div>
                    <div className="text-xs uppercase tracking-widest text-primary/40 mt-1">Best Streak</div>
                </div>
                <div className="text-center">
                    <div className="text-3xl font-serif text-primary">{sessionSummary.hints_used}</div>
                    <div className="text-xs uppercase tracking-widest text-primary/40 mt-1">Hints Used</div>
                </div>
                {sessionSummary.session_type && sessionSummary.session_type !== 'standard' && (
                    <div className="text-center md:col-span-2">
                        <div className="text-xl font-serif text-primary capitalize">
                            {sessionSummary.session_type.replace('_', ' ')}
                            {sessionSummary.target_accuracy && ` (${sessionSummary.target_accuracy}% accuracy)`}
                            {sessionSummary.target_time_minutes && ` (${sessionSummary.target_time_minutes} minutes)`}
                        </div>
                        <div className="text-xs uppercase tracking-widest text-primary/40 mt-1">Session Type</div>
                    </div>
                )}
            </div>

            {/* Achievements Earned */}
            {achievements.filter(a => a.earned).length > 0 && (
                <div className="mb-6">
                    <h3 className="text-lg font-serif text-primary mb-3">Achievements Earned</h3>
                    <div className="flex flex-wrap gap-2">
                        {achievements.filter(a => a.earned).map(achievement => (
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

            <button
                type="button"
                onClick={onStartNewSession}
                className="w-full px-6 py-3 bg-primary text-bg-primary rounded-sm font-serif transition-opacity hover:opacity-90 cursor-pointer km-focus-visible">
                Start New Session
            </button>
        </section>
    );
}
