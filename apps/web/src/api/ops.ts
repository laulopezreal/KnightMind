import { request } from './core';
import { TAB_CLIENT_ID } from './clientId';

export interface HealthResponse {
    ok: boolean;
    db: string;
    worker: string;
    stockfish: string;
    version: {
        sha: string;
        built_at: string;
    };
}

export interface RecentJob {
    id: string;
    type: string;
    username: string;
    status: 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled';
    progress_current: number;
    progress_total: number;
    message?: string;
    error_message?: string;
    created_at: string;
    updated_at: string;
    result_json?: {
        generated?: number;
        cache_hits?: number;
        cache_misses?: number;
        [key: string]: unknown;
    };
}

export interface JobStatusResponse {
    job_id: string;
    status: 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled';
    message?: string;
    progress?: number;
    /**
     * Status-write timestamp: advances on per-game progress writes and status
     * transitions. Treated as forward progress by the polling stall detector.
     */
    updated_at?: string;
    /**
     * Liveness lease bumped by the worker's per-ply heartbeat DURING a single
     * long game. This is what lets a game that outlasts the stall window keep
     * the job alive client-side (updated_at is pinned across those heartbeats).
     */
    heartbeat_at?: string;
    result?: unknown;
    error?: string;
    /** Which browser tab last polled this job (set by the server from X-Client-Id). */
    client_id?: string | null;
    /** When that tab last sent X-Client-Id to GET /jobs/{id}. */
    client_last_seen_at?: string | null;
    /** When the tab's stall detector reported a stall via POST /jobs/{id}/stall-report. */
    stall_reported_at?: string | null;
}

export interface OpsStatusResponse {
    now: string;
    active_job: RecentJob | null;
    recent_jobs: RecentJob[];
    /**
     * Null whenever the worker runs as its own service, which is the deployed
     * configuration — recovery is in-process, so this API has no basis for
     * claiming a count. Typed non-nullable until now while the server had
     * already started sending null; every read site used `?.`, so nothing
     * crashed and nothing said the type was wrong.
     */
    last_recovery: {
        recovered_count: number;
        last_recovery_at: string | null;
    } | null;
    /**
     * The rate limiter fails OPEN, so a broken one is indistinguishable from a
     * working one by observing traffic. This counter is the only signal, which
     * is why it is surfaced rather than left to curl.
     */
    rate_limit_failures: number;
    rate_limit_last_error: string | null;
    metrics: {
        last_24h: {
            jobs_succeeded: number;
            jobs_failed: number;
            avg_duration_ms: number;
            cache_hits: number;
            cache_misses: number;
        };
    };
}

export interface StorageReportEntry {
    missing_games_count: number;
    missing_puzzles_count: number;
    missing_games_sample: string[];
    missing_puzzles_sample: string[];
}

export interface StorageReportResponse {
    user_count: number;
    report: Record<string, StorageReportEntry>;
}

/** Fetches API health and dependency status (DB, worker, Stockfish). */
export async function getHealth(): Promise<HealthResponse> {
    return await request<HealthResponse>('/ops/health');
}

/** Fetches ops dashboard data: active job, recent jobs, last recovery, 24h metrics. */
export async function getOpsStatus(): Promise<OpsStatusResponse> {
    return await request<OpsStatusResponse>('/ops/status');
}

/** Fetches storage parity report (filesystem vs DB). Optional username filter. */
export async function getStorageReport(username?: string): Promise<StorageReportResponse> {
    const params = new URLSearchParams();
    if (username) {
        params.append('username', username);
    }
    const suffix = params.toString();
    return await request<StorageReportResponse>(`/ops/storage/report${suffix ? `?${suffix}` : ''}`);
}

/** Fetches current status of a background job. Sends X-Client-Id so the server
 *  can record which tab is observing and when (client-observability). */
export async function getJobStatus(jobId: string): Promise<JobStatusResponse> {
    return await request<JobStatusResponse>(`/jobs/${jobId}`, {
        headers: { 'X-Client-Id': TAB_CLIENT_ID },
    });
}

/** Cancels a queued or running job. */
export async function cancelJob(jobId: string): Promise<JobStatusResponse> {
    return await request<JobStatusResponse>(`/jobs/${jobId}/cancel`, {
        method: 'POST',
    });
}

/** Fire-and-forget: tells the server that this tab's stall detector fired.
 *  Pure observability — does not change job lifecycle. Errors are intentionally
 *  swallowed by the caller (useJobPolling) so this never affects the UI. */
export async function reportJobStall(jobId: string): Promise<JobStatusResponse> {
    return await request<JobStatusResponse>(`/jobs/${jobId}/stall-report`, {
        method: 'POST',
        headers: { 'X-Client-Id': TAB_CLIENT_ID },
    });
}
