import { useMemo } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import { type MotifTrend } from '../api/users';

interface MotifTrendsProps {
    trends: MotifTrend[];
    windowDays: number;
}

export function MotifTrends({ trends, windowDays }: MotifTrendsProps) {
    // Transform data for recharts - combine all motifs into single dataset
    const chartData = useMemo(() => {
        if (trends.length === 0) return [];

        // Get all unique dates across all motifs
        const allDates = new Set<string>();
        trends.forEach(trend => {
            trend.data_points.forEach(point => {
                allDates.add(point.date);
            });
        });

        // Sort dates
        const sortedDates = Array.from(allDates).sort();

        // Build data points with all motifs
        return sortedDates.map(date => {
            const dataPoint: Record<string, unknown> = { date };

            trends.forEach(trend => {
                const point = trend.data_points.find(p => p.date === date);
                if (point) {
                    dataPoint[trend.motif] = (point.accuracy * 100).toFixed(1);
                }
            });

            return dataPoint;
        });
    }, [trends]);

    // Generate colors for different motifs
    const motifColors = useMemo(() => {
        const colors = [
            'var(--text-primary)',
            '#10b981', // green
            '#3b82f6', // blue
            '#f59e0b', // amber
            '#8b5cf6', // purple
            '#ec4899', // pink
        ];

        return trends.reduce((acc, trend, index) => {
            acc[trend.motif] = colors[index % colors.length];
            return acc;
        }, {} as Record<string, string>);
    }, [trends]);

    if (trends.length === 0) {
        return (
            <section className="bg-primary/5 border border-primary/10 rounded-sm p-8">
                <h2 className="text-2xl font-serif text-primary mb-2 text-center">
                    📈 Progress Trends
                </h2>
                <p className="text-primary/60 text-center mb-6">
                    Track your improvement over time
                </p>
                <div className="text-center py-12">
                    <p className="text-primary/60 font-sans">
                        Complete more puzzles over time to see your progress trends.
                        At least {windowDays} days of training data is needed.
                    </p>
                </div>
            </section>
        );
    }

    return (
        <section className="bg-primary/5 border border-primary/10 rounded-sm p-8">
            <h2 className="text-2xl font-serif text-primary mb-2 text-center">
                📈 Progress Trends
            </h2>
            <p className="text-primary/60 text-center mb-6">
                Last {windowDays} days of training
            </p>

            <div className="h-80">
                <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={chartData}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--border-primary)" opacity={0.2} />
                        <XAxis
                            dataKey="date"
                            tick={{ fill: 'var(--text-primary)', fontSize: 11 }}
                            tickFormatter={(value) => {
                                const date = new Date(value);
                                return `${date.getMonth() + 1}/${date.getDate()}`;
                            }}
                        />
                        <YAxis
                            domain={[0, 100]}
                            tick={{ fill: 'var(--text-primary)', fontSize: 11 }}
                            label={{
                                value: 'Accuracy (%)',
                                angle: -90,
                                position: 'insideLeft',
                                style: { fill: 'var(--text-primary)', fontSize: 12 }
                            }}
                        />
                        <Tooltip
                            contentStyle={{
                                backgroundColor: 'var(--bg-primary)',
                                border: '1px solid var(--border-primary)',
                                borderRadius: '4px'
                            }}
                            labelStyle={{ color: 'var(--text-primary)' }}
                        />
                        <Legend
                            wrapperStyle={{
                                fontSize: '12px',
                                color: 'var(--text-primary)'
                            }}
                        />
                        {trends.map(trend => (
                            <Line
                                key={trend.motif}
                                type="monotone"
                                dataKey={trend.motif}
                                stroke={motifColors[trend.motif]}
                                strokeWidth={2}
                                dot={false}
                                activeDot={{ r: 4 }}
                            />
                        ))}
                    </LineChart>
                </ResponsiveContainer>
            </div>

            {/* Trend Summary */}
            <div className="mt-6 grid grid-cols-1 md:grid-cols-3 gap-4">
                {trends.slice(0, 3).map(trend => (
                    <div key={trend.motif} className="text-center">
                        <p className="text-sm text-primary/60 mb-1">{trend.motif}</p>
                        <p className={`text-lg font-serif ${
                            trend.trend === 'up' ? 'text-green-500' :
                            trend.trend === 'down' ? 'text-red-500' :
                            'text-primary/60'
                        }`}>
                            {trend.trend === 'up' ? '↗' : trend.trend === 'down' ? '↘' : '→'}
                            {' '}
                            {trend.change > 0 ? '+' : ''}{(trend.change * 100).toFixed(1)}%
                        </p>
                    </div>
                ))}
            </div>
        </section>
    );
}
