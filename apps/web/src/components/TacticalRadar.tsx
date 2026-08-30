import { useEffect, useMemo, useState } from 'react';
import { Radar, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, ResponsiveContainer } from 'recharts';
import { type MotifPerformance } from '../api/users';
import { formatMotifName } from '../utils/motif';

interface TacticalRadarProps {
    motifs: MotifPerformance[];
    onMotifClick: (motif: string) => void;
}

export function TacticalRadar({ motifs, onMotifClick }: TacticalRadarProps) {
    const [isDesktop, setIsDesktop] = useState(() =>
        typeof window !== 'undefined' &&
        typeof window.matchMedia === 'function' &&
        window.matchMedia('(min-width: 768px)').matches
    );

    useEffect(() => {
        if (typeof window.matchMedia !== 'function') return;

        const mediaQuery = window.matchMedia('(min-width: 768px)');
        const handleChange = (event: MediaQueryListEvent) => setIsDesktop(event.matches);

        mediaQuery.addEventListener('change', handleChange);
        return () => mediaQuery.removeEventListener('change', handleChange);
    }, []);

    // Transform data for recharts
    const radarData = useMemo(() =>
        motifs.map(m => ({
            motif: formatMotifName(m.name),
            accuracy: m.accuracy * 100, // Convert to percentage
            fullMark: 100
        })),
        [motifs]
    );

    // Find weakest motif. Only consider motifs with enough attempts for a
    // reliable accuracy — a single unlucky attempt is not a "weakest area".
    // Fall back to all motifs if none yet clear the reliability bar.
    const weakest = useMemo(() => {
        const reliable = motifs.filter(m => !m.insufficient_data);
        if (reliable.length === 0) return null;
        return reliable.reduce((min, m) =>
            m.accuracy < min.accuracy ? m : min
        );
    }, [motifs]);

    // Check if all motifs are mastered (>85% accuracy)
    const allMastered = useMemo(() => {
        if (motifs.length === 0) return false;
        return motifs.every(m => m.accuracy >= 0.85);
    }, [motifs]);

    // Empty state: No motifs at all
    if (motifs.length === 0) {
        return (
            <section className="bg-primary/10 rounded-sm p-8 shadow-lg shadow-primary/5" aria-labelledby="tactical-radar-heading">
                <h2 id="tactical-radar-heading" className="text-2xl font-serif text-primary mb-2 text-center">
                    🎯 Tactical Vision
                </h2>
                <p className="text-primary/70 text-center mb-6">
                    Your chess pattern mastery
                </p>
                <div className="text-center py-12">
                    <p className="text-primary/70 font-sans">
                        No motif data yet. Complete your first training session to start tracking your tactical patterns!
                    </p>
                </div>
            </section>
        );
    }

    // Empty state: Not enough motifs for radar
    if (motifs.length < 3) {
        return (
            <section className="bg-primary/10 rounded-sm p-8 shadow-lg shadow-primary/5" aria-labelledby="tactical-radar-heading">
                <h2 id="tactical-radar-heading" className="text-2xl font-serif text-primary mb-2 text-center">
                    🎯 Tactical Vision
                </h2>
                <p className="text-primary/70 text-center mb-6">
                    Your chess pattern mastery
                </p>
                <div className="text-center py-12">
                    <p className="text-primary/70 font-sans">
                        Complete more puzzles to unlock your tactical radar.
                        At least 3 different motifs are needed for meaningful visualization.
                    </p>
                </div>
            </section>
        );
    }

    return (
        <section className="bg-primary/10 rounded-sm p-8 shadow-lg shadow-primary/5" aria-labelledby="tactical-radar-heading">
            <h2 id="tactical-radar-heading" className="text-2xl font-serif text-primary mb-2 text-center">
                🎯 Tactical Vision
            </h2>
            <p className="text-primary/70 text-center mb-6" id="tactical-radar-desc">
                Your chess pattern mastery
            </p>

            <div
                role="img"
                aria-label={`Radar chart showing accuracy across different tactical motifs: ${motifs.map(m => `${m.name} ${Math.round(m.accuracy * 100)}%`).join(', ')}`}
            >
                <ResponsiveContainer width="100%" height={isDesktop ? 384 : 256}>
                    <RadarChart data={radarData}>
                        <PolarGrid stroke="var(--border-primary)" strokeOpacity={0.2} />
                        <PolarAngleAxis
                            dataKey="motif"
                            tick={{ fill: 'var(--text-primary)', fontSize: 12 }}
                        />
                        <PolarRadiusAxis
                            angle={90}
                            domain={[0, 100]}
                            tick={{ fill: 'var(--text-primary)', opacity: 0.4 }}
                        />
                        <Radar
                            name="Accuracy"
                            dataKey="accuracy"
                            stroke="var(--text-primary)"
                            fill="var(--text-primary)"
                            fillOpacity={0.3}
                        />
                    </RadarChart>
                </ResponsiveContainer>
            </div>

            {/* Celebration state: All motifs mastered */}
            {allMastered && (
                <div className="mt-6 text-center bg-green-500/10 border border-green-500/30 rounded-sm p-6 animate-teedin">
                    <div className="flex items-center justify-center mb-3">
                        <div className="flex-shrink-0 h-10 w-10 rounded-full bg-green-500 flex items-center justify-center mr-3">
                            <svg className="h-6 w-6 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                            </svg>
                        </div>
                        <p className="text-2xl font-serif text-positive">
                            All Motifs Mastered!
                        </p>
                    </div>
                    <p className="text-primary/70 text-sm font-sans">
                        Congratulations! You've achieved 85%+ accuracy on all tactical patterns.
                        Keep training to maintain your mastery!
                    </p>
                </div>
            )}

            {/* Weakest area: Show only if not all mastered */}
            {!allMastered && weakest && (
                <div className="mt-6 text-center">
                    <p className="text-primary/70 text-sm mb-2">
                        Your weakest area:
                    </p>
                    <p className="text-xl font-serif text-negative mb-4">
                        {formatMotifName(weakest.name)} ({Math.round(weakest.accuracy * 100)}%)
                    </p>
                    <button
                        type="button"
                        onClick={() => onMotifClick(weakest.name)}
                        disabled={weakest.total_puzzles === 0}
                        aria-label={`Practice ${formatMotifName(weakest.name)} tactical patterns`}
                        aria-disabled={weakest.total_puzzles === 0}
                        title={
                            weakest.total_puzzles === 0
                                ? 'No puzzles available for this motif yet'
                                : `Practice ${formatMotifName(weakest.name)} to improve your weakest area`
                        }
                        className={`px-6 py-3 bg-primary text-bg-primary rounded-sm font-serif transition-opacity km-focus-visible ${
                            weakest.total_puzzles === 0 ? 'km-interactive-disabled' : 'hover:opacity-90 cursor-pointer'
                        }`}
                    >
                        Practice {formatMotifName(weakest.name)} Now
                    </button>
                </div>
            )}
        </section>
    );
}
