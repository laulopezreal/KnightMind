import { LOCALE } from '../utils/locale';
import { useState, useEffect, useCallback } from 'react';
import { getHealth, getOpsStatus, getStorageReport, getUsers, ApiError, API_TARGET } from '../api';
import type { HealthResponse, OpsStatusResponse, RecentJob, StorageReportResponse } from '../api';
import { useChessUsername } from '../context/ChessUsernameContext';

export default function Ops() {
    const [health, setHealth] = useState<HealthResponse | null>(null);
    const [opsStatus, setOpsStatus] = useState<OpsStatusResponse | null>(null);
    const [storageReport, setStorageReport] = useState<StorageReportResponse | null>(null);
    const [storageLoading, setStorageLoading] = useState(false);
    const [storageError, setStorageError] = useState<string | null>(null);
    const [users, setUsers] = useState<string[]>([]);
    const [usersError, setUsersError] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const { username, setUsername } = useChessUsername();
    const [selectedUser, setSelectedUser] = useState(username);

    const getErrorMessage = (err: unknown, fallback: string) => {
        if (err instanceof ApiError) {
            return err.detail || err.message;
        }
        if (err instanceof Error) {
            return err.message;
        }
        return fallback;
    };

    const fetchData = useCallback(async () => {
        try {
            const [h, s] = await Promise.all([getHealth(), getOpsStatus()]);
            setHealth(h);
            setOpsStatus(s);
            setError(null);
        } catch (err) {
            console.error('Failed to fetch ops data:', err);
            const msg = getErrorMessage(err, 'Check if API is running and proxy is correctly configured.');
            setError(`Failed to load operational data: ${msg}`);
        } finally {
            setLoading(false);
        }
    }, []);

    const fetchUsers = useCallback(async () => {
        try {
            const list = await getUsers();
            setUsers(list);
            setUsersError(null);
        } catch (err) {
            console.error('Failed to fetch users:', err);
            setUsersError(getErrorMessage(err, 'Unable to load users.'));
        }
    }, []);

    const fetchStorageReport = useCallback(async (filterUser?: string) => {
        try {
            setStorageLoading(true);
            const report = await getStorageReport(filterUser);
            setStorageReport(report);
            setStorageError(null);
        } catch (err) {
            console.error('Failed to fetch storage report:', err);
            setStorageError(getErrorMessage(err, 'Unable to load report.'));
        } finally {
            setStorageLoading(false);
        }
    }, []);

    useEffect(() => {
        fetchData();
        const interval = setInterval(fetchData, 5000);
        fetchUsers();
        return () => clearInterval(interval);
    }, [fetchData, fetchUsers]);

    useEffect(() => {
        fetchStorageReport(selectedUser || undefined);
    }, [selectedUser, fetchStorageReport]);

    useEffect(() => {
        setSelectedUser(username);
    }, [username]);

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
    const backendIssue = !!error || (health && !health.ok);
    const backendIssueDetails = health ? [
        health.db !== 'ok' && 'Database',
        health.worker !== 'ok' && 'Worker',
        health.stockfish !== 'ok' && 'Stockfish',
    ].filter(Boolean).join(', ') : null;
    const reportEntries = storageReport ? Object.entries(storageReport.report ?? {}) : [];
    const canSetUser = selectedUser && selectedUser !== username;

    return (
        <div className="w-full font-sans text-primary/80 space-y-12 pb-20">
            <header>
                <h1 className="text-4xl font-serif font-medium mb-2 text-primary">Operational Board</h1>
                <p className="text-sm opacity-50 uppercase tracking-widest px-1">System Health & Telemetry</p>
            </header>

            {backendIssue && (
                <div className="bg-red-500/10 border border-red-500/20 text-negative p-6 rounded-sm font-sans text-sm flex flex-col gap-3">
                    <span className="font-bold uppercase tracking-widest text-[10px]">Backend Unavailable</span>
                    <span>
                        {error ? error : 'Backend health checks reported degraded services.'}
                    </span>
                    <div className="text-xs text-negative space-y-1">
                        <span className="text-[10px] uppercase tracking-widest block">Attempted endpoints</span>
                        <code className="block font-mono text-[11px]">{API_TARGET}/ops/health</code>
                        <code className="block font-mono text-[11px]">{API_TARGET}/ops/status</code>
                    </div>
                    {backendIssueDetails && (
                        <span className="text-[10px] uppercase tracking-widest opacity-70">
                            Affected: {backendIssueDetails}
                        </span>
                    )}
                    <button
                        type="button"
                        onClick={() => { setLoading(true); fetchData(); }}
                        className="km-interactive km-focus-visible w-fit mt-2 text-[10px] uppercase border border-red-500/30 px-3 py-1 rounded-sm transition-colors hover:bg-red-500/10"
                    >
                        Retry Connection
                    </button>
                </div>
            )}

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <section className="bg-primary/5 border border-primary/10 rounded-sm p-6 space-y-4 backdrop-blur-sm">
                    <div className="flex items-center justify-between">
                        <div>
                            <h2 className="font-serif text-xl text-primary">User Switcher</h2>
                            <p className="text-xs text-primary/70">Admin-only: quickly swap the active username.</p>
                        </div>
                        <button
                            type="button"
                            onClick={() => fetchUsers()}
                            className="text-[10px] uppercase tracking-widest border border-primary/20 px-3 py-1 rounded-sm km-interactive km-focus-visible"
                        >
                            Refresh
                        </button>
                    </div>
                    <div className="space-y-3">
                        <label className="text-[10px] uppercase tracking-widest text-primary/70">Active user</label>
                        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
                            <select
                                value={selectedUser}
                                onChange={(event) => setSelectedUser(event.target.value)}
                                className="w-full bg-transparent border border-primary/20 py-2 px-3 text-primary focus:outline-none focus:border-primary/60 transition-colors font-sans text-sm rounded-sm"
                            >
                                <option value="">Select a user</option>
                                {users.map(user => (
                                    <option key={user} value={user}>{user}</option>
                                ))}
                            </select>
                            <button
                                type="button"
                                onClick={() => {
                                    if (canSetUser) {
                                        setUsername(selectedUser);
                                    }
                                }}
                                disabled={!canSetUser}
                                className="px-4 py-2 bg-primary text-bg-primary rounded-sm font-serif transition-colors disabled:opacity-50 km-focus-visible"
                            >
                                Set Active
                            </button>
                        </div>
                        {usersError && <p className="text-xs text-negative">{usersError}</p>}
                        {!usersError && users.length === 0 && (
                            <p className="text-xs text-primary/70">No users found yet.</p>
                        )}
                    </div>
                </section>

                <section className="bg-primary/5 border border-primary/10 rounded-sm p-6 space-y-4 backdrop-blur-sm">
                    <div className="flex items-center justify-between">
                        <div>
                            <h2 className="font-serif text-xl text-primary">Data Integrity</h2>
                            <p className="text-xs text-primary/70">Storage parity between filesystem and database.</p>
                        </div>
                        <button
                            type="button"
                            onClick={() => fetchStorageReport(selectedUser || undefined)}
                            className="text-[10px] uppercase tracking-widest border border-primary/20 px-3 py-1 rounded-sm km-interactive km-focus-visible"
                        >
                            Refresh
                        </button>
                    </div>
                    <div className="text-xs text-primary/70">
                        Showing: {selectedUser ? selectedUser : 'All users'}
                    </div>
                    {storageLoading && (
                        <div className="text-xs text-primary/70 animate-pulse">Loading report...</div>
                    )}
                    {storageError && (
                        <div className="text-xs text-negative">{storageError}</div>
                    )}
                    {!storageLoading && !storageError && reportEntries.length === 0 && (
                        <div className="text-xs text-primary/70">No storage report data available.</div>
                    )}
                    {!storageLoading && !storageError && reportEntries.length > 0 && (
                        <div className="space-y-3">
                            {reportEntries.map(([user, report]) => (
                                <div key={user} className="border border-primary/10 rounded-sm p-3 text-xs">
                                    <div className="flex items-center justify-between">
                                        <span className="font-serif text-primary">{user}</span>
                                        <span className="text-[10px] uppercase tracking-widest text-primary/70">
                                            Missing {report.missing_games_count + report.missing_puzzles_count}
                                        </span>
                                    </div>
                                    <div className="mt-2 grid grid-cols-2 gap-4 text-primary/70">
                                        <div>
                                            Games: <span className="font-mono">{report.missing_games_count}</span>
                                        </div>
                                        <div>
                                            Puzzles: <span className="font-mono">{report.missing_puzzles_count}</span>
                                        </div>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </section>
            </div>

            {(opsStatus?.last_recovery?.recovered_count ?? 0) > 0 && (
                <div className="bg-amber-500/10 border border-amber-500/20 text-warning p-4 rounded-sm font-sans text-xs flex items-center justify-between">
                    <div className="flex items-center gap-3">
                        <span className="w-2 h-2 rounded-full bg-amber-500 animate-pulse" />
                        <span>
                            <span className="font-bold">Self-Healing:</span> {opsStatus?.last_recovery?.recovered_count} stuck job{(opsStatus?.last_recovery?.recovered_count ?? 0) > 1 ? 's' : ''} recovered after restart
                        </span>
                    </div>
                    <span className="opacity-40 text-[10px]">
                        {opsStatus?.last_recovery?.last_recovery_at != null && new Date(opsStatus.last_recovery.last_recovery_at).toLocaleTimeString(LOCALE)}
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
                    SHA: <span className="font-mono text-primary/70">{(health?.version?.sha ?? '').substring(0, 7) || 'unknown'}</span>
                </div>
                <div>
                    BUILT: <span className="text-primary/70">{health?.version?.built_at ? new Date(health.version.built_at).toLocaleString(LOCALE) : '-'}</span>
                </div>
                {opsStatus?.now && (
                    <div>
                        NOW: <span className="text-primary/70 text-[10px]">{new Date(opsStatus.now).toLocaleTimeString(LOCALE)}</span>
                    </div>
                )}
            </div>

            {/* Background process — disclosure (details/summary) */}
            {activeJob ? (
                <details className="group bg-primary/5 border border-primary/10 rounded-sm overflow-hidden backdrop-blur-sm">
                    <summary className="list-none flex items-center justify-between gap-4 px-5 py-4 cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 rounded-sm [&::-webkit-details-marker]:hidden [&::marker]:hidden">
                        <div className="flex items-center gap-4 min-w-0">
                            <span className="text-[10px] uppercase tracking-widest text-primary/70 font-sans shrink-0">Process</span>
                            <span className="font-serif text-lg font-medium text-primary truncate">{activeJob.type}</span>
                            <StatusBadge status={activeJob.status} />
                        </div>
                        <div className="flex items-center gap-3 shrink-0">
                            <span className="text-[11px] font-mono text-primary/70 tabular-nums">{calculateElapsed(activeJob.created_at)}</span>
                            <span className="inline-block w-0 h-0 border-l-[4px] border-r-[4px] border-t-[5px] border-l-transparent border-r-transparent border-t-primary/40 transition-transform duration-200 group-open:rotate-180" aria-hidden />
                        </div>
                    </summary>
                    <div className="border-t border-primary/10 px-5 pb-5 pt-4 space-y-5">
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3 text-sm">
                            <dl className="flex flex-col gap-0.5">
                                <dt className="text-[10px] uppercase tracking-widest text-primary/70 font-sans">Job ID</dt>
                                <dd className="font-mono text-primary/90 text-xs">{activeJob.id}</dd>
                            </dl>
                            <dl className="flex flex-col gap-0.5">
                                <dt className="text-[10px] uppercase tracking-widest text-primary/70 font-sans">User</dt>
                                <dd className="font-sans text-primary/90 text-xs">{activeJob.username}</dd>
                            </dl>
                            <dl className="flex flex-col gap-0.5">
                                <dt className="text-[10px] uppercase tracking-widest text-primary/70 font-sans">Created</dt>
                                <dd className="font-mono text-primary/90 text-xs">{formatTime(activeJob.created_at)}</dd>
                            </dl>
                            <dl className="flex flex-col gap-0.5">
                                <dt className="text-[10px] uppercase tracking-widest text-primary/70 font-sans">Updated</dt>
                                <dd className="font-mono text-primary/90 text-xs">{formatTime(activeJob.updated_at)}</dd>
                            </dl>
                        </div>
                        <div>
                            <span className="text-[10px] uppercase tracking-widest text-primary/70 font-sans block mb-1">Message</span>
                            <p className="text-sm font-serif italic text-primary/80">{activeJob.message || 'Processing…'}</p>
                        </div>
                        {(() => {
                            const pct = activeJob.progress_total > 0
                                ? Math.round(100 * activeJob.progress_current / activeJob.progress_total)
                                : activeJob.progress_current;
                            return (
                                <div className="space-y-2">
                                    <div className="flex justify-between text-xs">
                                        <span className="text-[10px] uppercase tracking-widest text-primary/70">Progress</span>
                                        <span className="font-sans font-medium text-primary tabular-nums">{pct}%</span>
                                    </div>
                                    <div className="h-2 w-full bg-primary/10 rounded-full overflow-hidden">
                                        <div className="h-full bg-primary/40 transition-[width] duration-300 ease-out" style={{ width: `${Math.min(100, pct)}%` }} />
                                    </div>
                                </div>
                            );
                        })()}
                        {activeJob.error_message && (
                            <div>
                                <span className="text-[10px] uppercase tracking-widest text-negative font-sans block mb-1">Error</span>
                                <p className="text-xs text-negative font-sans">{activeJob.error_message}</p>
                            </div>
                        )}
                    </div>
                </details>
            ) : (
                <details className="group bg-primary/5 border border-primary/10 rounded-sm overflow-hidden backdrop-blur-sm">
                    <summary className="list-none flex items-center justify-between gap-3 px-5 py-3 cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 rounded-sm [&::-webkit-details-marker]:hidden [&::marker]:hidden">
                        <span className="text-[10px] uppercase tracking-widest text-primary/70 font-sans">No background process</span>
                        <span className="inline-block w-0 h-0 border-l-[4px] border-r-[4px] border-t-[5px] border-l-transparent border-r-transparent border-t-primary/40 transition-transform duration-200 group-open:rotate-180" aria-hidden />
                    </summary>
                    <div className="border-t border-primary/10 px-5 pb-4 pt-3">
                        <p className="text-xs font-sans text-primary/70 leading-relaxed">
                            No jobs are running. Start puzzle generation from Puzzles or run a sync to queue work.
                        </p>
                    </div>
                </details>
            )}

            {/* Bottom Row: Metrics & Activity Table */}
            <div className="grid grid-cols-1 xl:grid-cols-4 gap-12 items-start">
                {/* 24h Metrics Panel */}
                <aside className="xl:col-span-1 min-w-[220px] bg-primary/[0.01] border border-primary/10 p-6 rounded-sm">
                    <h3 className="font-serif text-xl mb-6 opacity-90 border-b border-primary/10 pb-3">Operational Metrics</h3>
                    {metrics ? (
                        <div className="space-y-5">
                            <MetricItem label="Jobs Succeeded" value={metrics.jobs_succeeded} />
                            <MetricItem label="Jobs Failed" value={metrics.jobs_failed} trend={metrics.jobs_failed > 0 ? 'bad' : 'good'} />
                            <MetricItem label="Avg execution" value={formatDuration(metrics.avg_duration_ms ?? 0)} />
                            <MetricItem label="Cache hit rate" value={`${calculateCacheRate(metrics.cache_hits, metrics.cache_misses)}%`} />
                            <div className="pt-4 mt-4 border-t border-primary/10">
                                <MetricItem label="Hits / misses" value={`${metrics.cache_hits} / ${metrics.cache_misses}`} />
                            </div>
                        </div>
                    ) : (
                        <div className="text-[10px] opacity-30 uppercase tracking-widest text-center py-10 italic">No telemetry data</div>
                    )}
                </aside>

                {/* Activity Feed / Table - Improved to match mockup */}
                {/* div, not <main>: Layout already provides the page's single main
                    landmark; a nested one here would duplicate it. */}
                <div className="xl:col-span-3">
                    <div className="border border-primary/10 rounded-sm">
                        <div className="px-6 py-4 border-b border-primary/10 bg-primary/[0.02] flex justify-between items-center">
                            <h3 className="font-serif text-lg opacity-80">Execution History</h3>
                            <span className="text-[9px] uppercase tracking-widest opacity-30">Last 20 Runs</span>
                        </div>
                        <div className="overflow-x-auto">
                            <table className="w-full text-left text-[11px] border-collapse">
                                <thead>
                                    <tr className="border-b border-primary/10 opacity-50 font-serif uppercase tracking-widest">
                                        <th scope="col" className="px-6 py-4 font-medium border-r border-primary/5">Job ID</th>
                                        <th scope="col" className="px-6 py-4 font-medium border-r border-primary/5">User</th>
                                        <th scope="col" className="px-6 py-4 font-medium border-r border-primary/5">Process Type</th>
                                        <th scope="col" className="px-6 py-4 font-medium border-r border-primary/5">Status</th>
                                        <th scope="col" className="px-6 py-4 font-medium border-r border-primary/5">Timestamp</th>
                                        <th scope="col" className="px-6 py-4 font-medium text-right">Telemetry</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-primary/5">
                                    {recentJobs.map(job => (
                                        <tr key={job.id} className="hover:bg-primary/[0.01] transition-colors group">
                                            <td className="px-6 py-4 font-mono opacity-50 group-hover:opacity-100 border-r border-primary/5">
                                                #{job.id?.substring(0, 6) ?? '—'}
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
                                            <td className="px-6 py-4 border-r border-primary/5 whitespace-nowrap">
                                                <span className="font-serif text-primary/90">{formatTime(job.created_at)}</span>
                                                <span className="text-[10px] opacity-50 font-mono ml-1.5">· {calculateDuration(job.created_at, job.updated_at)}</span>
                                            </td>
                                            <td className="px-6 py-4 text-right">
                                                {job.result_json ? (
                                                    <span className="inline-block whitespace-nowrap font-sans text-primary/80">
                                                        {job.result_json.generated != null ? `${job.result_json.generated} res` : '—'}
                                                        <span className="text-[10px] opacity-50 font-mono ml-1.5">
                                                            {calculateCacheRate(job.result_json.cache_hits ?? 0, job.result_json.cache_misses ?? 0)}% hit
                                                        </span>
                                                    </span>
                                                ) : (
                                                    <span className="text-[10px] opacity-40">—</span>
                                                )}
                                                {job.error_message && (
                                                    <span className="text-negative block mt-1 leading-tight max-w-[180px] truncate text-left" title={job.error_message}>
                                                        {job.error_message}
                                                    </span>
                                                )}
                                            </td>
                                        </tr>
                                    ))}
                                    {recentJobs.length === 0 && (
                                        <tr>
                                            <td colSpan={6} className="px-6 py-20 text-center opacity-30 italic font-serif text-sm">
                                                No execution history found in current archive
                                            </td>
                                        </tr>
                                    )}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
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
            <div className={`text-2xl font-serif font-medium ${isUp ? 'text-primary' : 'text-negative'}`}>
                {value}
            </div>
            <div className={`text-[9px] uppercase tracking-widest mt-3 flex items-center gap-2 ${isUp ? 'opacity-30' : 'text-negative'}`}>
                <span className={`w-1.5 h-1.5 rounded-full ${isUp ? 'bg-green-500/40 animate-pulse' : 'bg-red-500'}`} />
                {isUp ? 'Operational' : 'Critical Issue'}
            </div>
        </div>
    );
}

function MetricItem({ label, value, trend }: { label: string, value: string | number, trend?: 'good' | 'bad' }) {
    return (
        <div className="flex flex-col gap-1">
            <span className="text-[10px] uppercase tracking-widest opacity-50 font-sans">{label}</span>
            <span className={`text-lg font-serif tabular-nums ${trend === 'bad' ? 'text-negative' : 'text-primary'}`}>
                {value}
            </span>
        </div>
    );
}

function StatusBadge({ status }: { status: RecentJob['status'] }) {
    const colors: Record<RecentJob['status'], string> = {
        succeeded: 'text-positive border-green-500/20',
        failed: 'text-negative border-red-500/20',
        running: 'text-warning border-amber-500/20',
        queued: 'text-status-new border-blue-500/20',
        canceled: 'text-primary/70 border-primary/10',
    };
    const style = colors[status] ?? 'text-primary/70 border-primary/10';

    return (
        <span className={`px-2 py-0.5 border text-[9px] uppercase tracking-widest font-sans font-bold rounded-sm ${style}`}>
            {status}
        </span>
    );
}

// Helpers
function formatTime(iso: string) {
    const date = new Date(iso);
    return date.toLocaleString(LOCALE, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
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
