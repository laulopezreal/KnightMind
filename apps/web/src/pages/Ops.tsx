import { useState, useEffect } from 'react';
import { getHealth, getOpsStatus, ApiError } from '../api/client';
import type { HealthResponse, OpsStatusResponse, RecentJob } from '../api/client';

export default function Ops() {
    const [health, setHealth] = useState<HealthResponse | null>(null);
    const [opsStatus, setOpsStatus] = useState<OpsStatusResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const fetchData = async () => {
        try {
            const [h, s] = await Promise.all([getHealth(), getOpsStatus()]);
            setHealth(h);
            setOpsStatus(s);
            setError(null);
        } catch (err) {
            console.error('Failed to fetch ops data:', err);
            let msg = 'Check if API is running and proxy is correctly configured.';

            if (err instanceof ApiError) {
                msg = err.detail || err.message;
            } else if (err instanceof Error) {
                msg = err.message;
            }

            setError(`Failed to load operational data: ${msg}`);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchData();
        const interval = setInterval(fetchData, 5000);
        return () => clearInterval(interval);
    }, []);

    if (loading && !opsStatus) {
        return (
            <div className="w-full animate-pulse space-y-8">
                <div className="h-10 w-64 bg-primary/10 rounded" />
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
                    {[1, 2, 3, 4].map(i => <div key={i} className="h-28 bg-primary/5 rounded border border-primary/10" />)}
                </div>
            </div>
        );
    }

    const activeJob = opsStatus?.active_job;
    const recentJobs = opsStatus?.recent_jobs || [];
    const metrics = opsStatus?.metrics?.last_24h;

    return (
        <div className="w-full font-sans text-primary/80 space-y-12 pb-20">
            <header>
                <h1 className="text-4xl font-serif font-medium mb-2 text-primary">Operational Board</h1>
                <p className="text-sm opacity-50 uppercase tracking-widest px-1">System Health & Telemetry</p>
            </header>

            {error && (
                <div className="bg-red-500/10 border border-red-500/20 text-red-500 p-6 rounded-sm font-sans text-sm flex flex-col gap-2">
                    <span className="font-bold uppercase tracking-widest text-[10px]">Error Detected</span>
                    <span>{error}</span>
                    <button
                        onClick={() => { setLoading(true); fetchData(); }}
                        className="w-fit mt-2 text-[10px] uppercase border border-red-500/30 px-3 py-1 hover:bg-red-500/10 transition-colors"
                    >
                        Retry Connection
                    </button>
                </div>
            )}

            {opsStatus?.last_recovery.recovered_count && opsStatus.last_recovery.recovered_count > 0 && (
                <div className="bg-amber-500/10 border border-amber-500/20 text-amber-500 p-4 rounded-sm font-sans text-xs flex items-center justify-between">
                    <div className="flex items-center gap-3">
                        <span className="w-2 h-2 rounded-full bg-amber-500 animate-pulse" />
                        <span>
                            <span className="font-bold">Self-Healing:</span> {opsStatus.last_recovery.recovered_count} stuck job{opsStatus.last_recovery.recovered_count > 1 ? 's' : ''} recovered after restart
                        </span>
                    </div>
                    <span className="opacity-40 text-[10px]">
                        {opsStatus.last_recovery.last_recovery_at && new Date(opsStatus.last_recovery.last_recovery_at).toLocaleTimeString()}
                    </span>
                </div>
            )}

            {/* Health Cards Grid - Fixed responsiveness */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
                <HealthCard label="API" status={health?.ok ? 'up' : 'down'} value={health?.ok ? 'UP' : 'DOWN'} />
                <HealthCard label="DB" status={health?.db === 'ok' ? 'up' : 'down'} value={health?.db === 'ok' ? 'CONNECTED' : 'ERROR'} />
                <HealthCard label="Worker" status={health?.worker === 'ok' ? 'up' : 'down'} value={health?.worker === 'ok' ? 'RUNNING' : 'OFFLINE'} />
                <HealthCard label="Stockfish" status={health?.stockfish === 'ok' ? 'up' : 'down'} value={health?.stockfish === 'ok' ? 'AVAILABLE' : 'MISSING'} />
            </div>

            {/* Version Info (Pinned to bottom or side, now more subtle) */}
            <div className="flex gap-8 text-[10px] opacity-40 uppercase tracking-tighter border-t border-primary/10 pt-4">
                <div>
                    SHA: <span className="font-mono text-primary/60">{health?.version?.sha.substring(0, 7) || 'unknown'}</span>
                </div>
                <div>
                    BUILT: <span className="text-primary/60">{health?.version?.built_at ? new Date(health.version.built_at).toLocaleString() : '-'}</span>
                </div>
                {opsStatus?.now && (
                    <div>
                        NOW: <span className="text-primary/60 text-[10px]">{new Date(opsStatus.now).toLocaleTimeString()}</span>
                    </div>
                )}
            </div>

            {/* Active Job Panel - More premium feel */}
            {activeJob ? (
                <section className="border border-primary/20 bg-primary/[0.02] rounded-sm p-8 relative">
                    <div className="absolute top-0 left-0 h-[2px] bg-amber-500/30 w-full" />
                    <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-6 mb-8">
                        <div>
                            <div className="text-[10px] uppercase tracking-[0.2em] text-amber-500 font-bold mb-2">Live Process</div>
                            <h2 className="text-2xl font-serif font-medium text-primary">{activeJob.type}</h2>
                            <p className="text-xs opacity-50 font-mono mt-1">
                                {activeJob.id} • {activeJob.username}
                            </p>
                        </div>
                        <div className="text-right flex flex-col items-end">
                            <span className="px-3 py-1 bg-amber-500/5 text-amber-500 text-[11px] uppercase tracking-widest border border-amber-500/20 rounded-sm font-sans font-bold">
                                {activeJob.status}
                            </span>
                            <div className="text-[10px] opacity-40 mt-2">
                                ELAPSED: {calculateElapsed(activeJob.created_at)}
                            </div>
                        </div>
                    </div>

                    <div className="space-y-3">
                        <div className="flex justify-between text-xs font-serif italic mb-1">
                            <span className="opacity-70">{activeJob.message || 'Processing...'}</span>
                            <span className="font-sans font-bold text-primary">{activeJob.progress_current}%</span>
                        </div>
                        <div className="h-1 w-full bg-primary/10 rounded-full overflow-hidden">
                            <div
                                className="h-full bg-primary/40 transition-all duration-1000 ease-out shadow-[0_0_10px_rgba(var(--text-primary-rgb),0.1)]"
                                style={{ width: `${activeJob.progress_current}%` }}
                            />
                        </div>
                    </div>
                </section>
            ) : (
                <div className="py-12 border border-dashed border-primary/10 text-center rounded-sm">
                    <p className="text-[10px] uppercase tracking-widest opacity-30 italic">No background processes active</p>
                </div>
            )}

            {/* Bottom Row: Metrics & Activity Table */}
            <div className="grid grid-cols-1 xl:grid-cols-4 gap-12 items-start">
                {/* 24h Metrics Panel */}
                <aside className="xl:col-span-1 bg-primary/[0.01] border border-primary/10 p-6 rounded-sm">
                    <h3 className="font-serif text-xl mb-6 opacity-90 border-b border-primary/10 pb-3">Operational Metrics</h3>
                    {metrics ? (
                        <div className="space-y-6">
                            <MetricItem label="Jobs Succeeded" value={metrics.jobs_succeeded} />
                            <MetricItem label="Jobs Failed" value={metrics.jobs_failed} trend={metrics.jobs_failed > 0 ? 'bad' : 'good'} />
                            <MetricItem label="Avg Execution" value={formatDuration(metrics.avg_duration_ms)} />
                            <MetricItem label="Neural Hit Rate" value={`${calculateCacheRate(metrics.cache_hits, metrics.cache_misses)}%`} />

                            <div className="pt-4 mt-4 border-t border-primary/5 text-center">
                                <span className="text-[9px] uppercase tracking-[0.3em] opacity-30">Network Efficiency</span>
                                <div className="text-[10px] opacity-40 mt-1 font-mono">
                                    {metrics.cache_hits}H / {metrics.cache_misses}M
                                </div>
                            </div>
                        </div>
                    ) : (
                        <div className="text-[10px] opacity-30 uppercase tracking-widest text-center py-10 italic">No telemetry data</div>
                    )}
                </aside>

                {/* Activity Feed / Table - Improved to match mockup */}
                <main className="xl:col-span-3">
                    <div className="border border-primary/10 rounded-sm">
                        <div className="px-6 py-4 border-b border-primary/10 bg-primary/[0.02] flex justify-between items-center">
                            <h3 className="font-serif text-lg opacity-80">Execution History</h3>
                            <span className="text-[9px] uppercase tracking-widest opacity-30">Last 20 Runs</span>
                        </div>
                        <div className="overflow-x-auto">
                            <table className="w-full text-left text-[11px] border-collapse">
                                <thead>
                                    <tr className="border-b border-primary/10 opacity-50 font-serif uppercase tracking-widest">
                                        <th className="px-6 py-4 font-medium border-r border-primary/5">Job ID</th>
                                        <th className="px-6 py-4 font-medium border-r border-primary/5">User</th>
                                        <th className="px-6 py-4 font-medium border-r border-primary/5">Process Type</th>
                                        <th className="px-6 py-4 font-medium border-r border-primary/5">Status</th>
                                        <th className="px-6 py-4 font-medium border-r border-primary/5">Timestamp</th>
                                        <th className="px-6 py-4 font-medium text-right">Telemetry</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-primary/5">
                                    {recentJobs.map(job => (
                                        <tr key={job.id} className="hover:bg-primary/[0.01] transition-colors group">
                                            <td className="px-6 py-4 font-mono opacity-50 group-hover:opacity-100 border-r border-primary/5">
                                                #{job.id.substring(0, 6)}
                                            </td>
                                            <td className="px-6 py-4 border-r border-primary/5 font-medium opacity-80">
                                                {job.username}
                                            </td>
                                            <td className="px-6 py-4 font-serif text-primary/90 border-r border-primary/5">
                                                {job.type}
                                            </td>
                                            <td className="px-6 py-4 border-r border-primary/5">
                                                <StatusBadge status={job.status} />
                                            </td>
                                            <td className="px-6 py-4 border-r border-primary/5">
                                                <div className="font-serif italic">{formatTime(job.created_at)}</div>
                                                <div className="text-[9px] opacity-30 tracking-tighter">{calculateDuration(job.created_at, job.updated_at)}</div>
                                            </td>
                                            <td className="px-6 py-4 text-right">
                                                {job.result_json ? (
                                                    <div className="flex flex-col items-end opacity-70">
                                                        <span className="font-serif italic">{job.result_json.generated} results</span>
                                                        <span className="text-[9px] opacity-40 font-mono tracking-tighter">
                                                            {calculateCacheRate(job.result_json.cache_hits ?? 0, job.result_json.cache_misses ?? 0)}% hit rate
                                                        </span>
                                                    </div>
                                                ) : (
                                                    <span className="text-[9px] opacity-30 uppercase tracking-widest">No results</span>
                                                )}
                                                {job.error_message && (
                                                    <span className="text-red-500/60 block mt-1 leading-tight max-w-[150px] truncate" title={job.error_message}>
                                                        {job.error_message}
                                                    </span>
                                                )}
                                            </td>
                                        </tr>
                                    ))}
                                    {recentJobs.length === 0 && (
                                        <tr>
                                            <td colSpan={5} className="px-6 py-20 text-center opacity-30 italic font-serif text-sm">
                                                No execution history found in current archive
                                            </td>
                                        </tr>
                                    )}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </main>
            </div>
        </div>
    );
}

function HealthCard({ label, status, value }: { label: string, status: 'up' | 'down', value: string }) {
    const isUp = status === 'up';
    return (
        <div className="border border-primary/10 rounded-sm p-6 relative group hover:border-primary/20 transition-all">
            <div className={`absolute left-0 top-6 bottom-6 w-[3px] rounded-r-full ${isUp ? 'bg-green-500/40' : 'bg-red-500/40'}`} />
            <div className="text-[9px] uppercase tracking-[0.3em] opacity-40 mb-3 font-bold">{label}</div>
            <div className={`text-2xl font-serif font-medium ${isUp ? 'text-primary' : 'text-red-500 opacity-80'}`}>
                {value}
            </div>
            <div className={`text-[9px] uppercase tracking-widest mt-3 flex items-center gap-2 ${isUp ? 'opacity-30' : 'text-red-500 opacity-50'}`}>
                <span className={`w-1.5 h-1.5 rounded-full ${isUp ? 'bg-green-500/40 animate-pulse' : 'bg-red-500'}`} />
                {isUp ? 'Operational' : 'Critical Issue'}
            </div>
        </div>
    );
}

function MetricItem({ label, value, trend }: { label: string, value: string | number, trend?: 'good' | 'bad' }) {
    return (
        <div className="flex justify-between items-end">
            <span className="text-[10px] uppercase tracking-widest opacity-40">{label}</span>
            <div className="flex flex-col items-end">
                <span className={`text-lg font-serif ${trend === 'bad' ? 'text-red-500 opacity-70' : 'text-primary'}`}>
                    {value}
                </span>
            </div>
        </div>
    );
}

function StatusBadge({ status }: { status: RecentJob['status'] }) {
    const colors: Record<RecentJob['status'], string> = {
        succeeded: 'text-green-500/70 border-green-500/20',
        failed: 'text-red-500/70 border-red-500/20',
        running: 'text-amber-500/70 border-amber-500/20',
        queued: 'text-blue-500/70 border-blue-500/20',
        canceled: 'text-primary/30 border-primary/10',
    };

    return (
        <span className={`px-2 py-0.5 border text-[9px] uppercase tracking-widest font-sans font-bold rounded-sm ${colors[status]}`}>
            {status}
        </span>
    );
}

// Helpers
function formatTime(iso: string) {
    const date = new Date(iso);
    return date.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function calculateDuration(start: string, end: string) {
    const s = new Date(start).getTime();
    const e = new Date(end).getTime();
    const diff = Math.max(0, e - s);
    return formatDuration(diff);
}

function formatDuration(ms: number) {
    if (ms < 1000) return `${ms}ms`;
    const sec = Math.floor(ms / 1000);
    if (sec < 60) return `${sec}s`;
    const min = Math.floor(sec / 60);
    const s = sec % 60;
    return `${min}m ${s}s`;
}

function calculateElapsed(start: string) {
    const s = new Date(start).getTime();
    const diff = Date.now() - s;
    return formatDuration(diff);
}

function calculateCacheRate(hits: number, misses: number) {
    const total = hits + misses;
    if (total === 0) return 0;
    return Math.round((hits / total) * 100);
}
