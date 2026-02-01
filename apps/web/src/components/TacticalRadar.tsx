import { useMemo } from 'react';
import { Radar, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, ResponsiveContainer } from 'recharts';
import { type MotifPerformance } from '../api/users';

interface TacticalRadarProps {
    motifs: MotifPerformance[];
    onMotifClick: (motif: string) => void;
}

export function TacticalRadar({ motifs, onMotifClick }: TacticalRadarProps) {
    // Transform data for recharts
    const radarData = useMemo(() =>
        motifs.map(m => ({
            motif: m.name,
            accuracy: m.accuracy * 100, // Convert to percentage
            fullMark: 100
        })),
        [motifs]
    );

    // Find weakest motif
    const weakest = useMemo(() => {
        if (motifs.length === 0) return null;
        return motifs.reduce((min, m) =>
            m.accuracy < min.accuracy ? m : min
        );
    }, [motifs]);

    if (motifs.length < 3) {
        return (
            <section className="bg-primary/5 border border-primary/10 rounded-sm p-8">
                <h2 className="text-2xl font-serif text-primary mb-2 text-center">
                    🎯 Tactical Vision
                </h2>
                <p className="text-primary/60 text-center mb-6">
                    Your chess pattern mastery
                </p>
                <div className="text-center py-12">
                    <p className="text-primary/60 font-sans">
                        Complete more puzzles to unlock your tactical radar.
                        At least 3 different motifs are needed for meaningful visualization.
                    </p>
                </div>
            </section>
        );
    }

    return (
        <section className="bg-primary/5 border border-primary/10 rounded-sm p-8">
            <h2 className="text-2xl font-serif text-primary mb-2 text-center">
                🎯 Tactical Vision
            </h2>
            <p className="text-primary/60 text-center mb-6">
                Your chess pattern mastery
            </p>

            <div className="h-64 md:h-96">
                <ResponsiveContainer width="100%" height="100%">
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

            {weakest && (
                <div className="mt-6 text-center">
                    <p className="text-primary/60 text-sm mb-2">
                        Your weakest area:
                    </p>
                    <p className="text-xl font-serif text-red-500 mb-4">
                        {weakest.name} ({Math.round(weakest.accuracy * 100)}%)
                    </p>
                    <button
                        type="button"
                        onClick={() => onMotifClick(weakest.name)}
                        className="px-6 py-3 bg-primary text-bg-primary rounded-sm km-interactive km-focus-visible"
                    >
                        Practice {weakest.name} Now
                    </button>
                </div>
            )}
        </section>
    );
}
