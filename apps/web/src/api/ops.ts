import { request } from './core';

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
    result?: unknown;
}

export interface OpsStatusResponse {
    now: string;
    active_job: RecentJob | null;
    recent_jobs: RecentJob[];
    last_recovery: {
        recovered_count: number;
        last_recovery_at: string | null;
    };
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

export async function getHealth(): Promise<HealthResponse> {
    return await request<HealthResponse>('/ops/health');
}

export async function getOpsStatus(): Promise<OpsStatusResponse> {
    return await request<OpsStatusResponse>('/ops/status');
}

export async function getJobStatus(jobId: string): Promise<JobStatusResponse> {
    return await request<JobStatusResponse>(`/jobs/${jobId}`);
}

export async function cancelJob(jobId: string): Promise<JobStatusResponse> {
    return await request<JobStatusResponse>(`/jobs/${jobId}/cancel`, {
        method: 'POST',
    });
}
