import { type SessionSummary } from '../api';

interface RecentSessionsCardProps {
    sessions: SessionSummary[];
}

const calculateAccuracy = (passCount: number, failCount: number): number => {
    const total = passCount + failCount;
    return total > 0 ? Math.round((passCount / total) * 100) : 0;
};

export function RecentSessionsCard({ sessions }: RecentSessionsCardProps) {
    if (sessions.length === 0) {
        return null;
    }

    return (
        <section className="bg-primary/5 border border-primary/10 rounded-sm p-6 backdrop-blur-sm">
            <h3 className="text-lg font-serif text-primary mb-4">Recent Sessions</h3>
            <div className="space-y-2">
                {sessions.map((session) => (
                    <div key={session.session_id} className="flex justify-between items-center p-3 bg-primary/5 rounded-sm text-sm">
                        <div className="flex gap-4">
                            <span className="text-green-600">{session.pass_count}P</span>
                            <span className="text-red-500">{session.fail_count}F</span>
                            <span className="text-primary/60">
                                {calculateAccuracy(session.pass_count, session.fail_count)}%
                            </span>
                            {session.best_streak > 0 && (
                                <span className="text-primary/80">🔥{session.best_streak}</span>
                            )}
                        </div>
                        <div className="flex gap-2">
                            {session.session_type && session.session_type !== 'standard' && (
                                <span className="text-primary/40 text-xs capitalize">
                                    {session.session_type.replace('_', ' ')}
                                </span>
                            )}
                            <span className="text-primary/40 text-xs">
                                {new Date(session.created_at).toLocaleDateString()}
                            </span>
                        </div>
                    </div>
                ))}
            </div>
        </section>
    );
}
