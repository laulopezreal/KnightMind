import { useState, useEffect, useRef } from 'react';
import { getJobStatus, type JobStatusResponse } from '../api';

interface JobPollingOptions {
    pollInterval?: number;
    enabled?: boolean;
    maxRetries?: number;
    /**
     * Max time WITHOUT forward progress before the job is treated as stalled and
     * onError fires. The deadline resets every time the job advances (a change in
     * status, progress, message, or updated_at), so a steadily progressing job
     * never errors no matter how long the total run takes.
     */
    stallTimeoutMs?: number;
    onSuccess?: (job: JobStatusResponse) => void;
    onError?: (error: Error) => void;
}

const DEFAULT_MAX_RETRIES = 30;
// Puzzle generation runs Stockfish over ~30 games and legitimately takes several
// minutes, writing per-game progress the whole way (~10s/game on average). So we
// do NOT cap total duration; we only fail a job that has made no forward progress
// within this window, which signals a genuinely stuck worker. 90s gives a healthy
// margin over the per-game cadence; the backend's own crash-recovery watchdog is
// far more lenient (it reclaims a job only after 15 min without a heartbeat), so
// raise this via the stallTimeoutMs option if you want to tolerate slower games.
const DEFAULT_STALL_TIMEOUT_MS = 90_000;

export function useJobPolling(jobId: string | null, options: JobPollingOptions = {}) {
    const {
        pollInterval = 1000,
        enabled = true,
        maxRetries = DEFAULT_MAX_RETRIES,
        stallTimeoutMs = DEFAULT_STALL_TIMEOUT_MS,
        onSuccess,
        onError
    } = options;

    const [job, setJob] = useState<JobStatusResponse | null>(null);

    // Store callbacks in refs to avoid effect re-runs
    const callbacksRef = useRef({ onSuccess, onError });
    useEffect(() => {
        callbacksRef.current = { onSuccess, onError };
    }, [onSuccess, onError]);

    useEffect(() => {
        if (!jobId || !enabled) {
            return;
        }

        let timeoutId: ReturnType<typeof setTimeout>;
        let stallTimerId: ReturnType<typeof setTimeout>;
        let isMounted = true;
        let stalled = false;
        let currentBackoff = pollInterval;
        let retryCount = 0;
        let lastProgressSignature: string | null = null;

        // Progress-stall detector: fire onError only if the job has made no
        // forward progress within stallTimeoutMs. armStallTimer() (re)starts the
        // countdown; it is reset every time the job advances, so a job that keeps
        // moving never errors regardless of how long it runs.
        const armStallTimer = () => {
            clearTimeout(stallTimerId);
            stallTimerId = setTimeout(() => {
                stalled = true;
                clearTimeout(timeoutId);
                setJob(null);
                const err = new Error(
                    `Puzzle generation has not made progress for ${Math.round(stallTimeoutMs / 1000)} seconds. ` +
                    'The job may still be running on the server - please try again in a few minutes.'
                );
                callbacksRef.current.onError?.(err);
            }, stallTimeoutMs);
        };

        // Arm immediately so a job that never advances (stuck queued, no first
        // progress write) still errors once the stall window elapses.
        armStallTimer();

        const poll = async () => {
            try {
                const status = await getJobStatus(jobId);

                if (!isMounted || stalled) return;

                setJob(status);

                if (status.status === 'succeeded') {
                    clearTimeout(stallTimerId);
                    callbacksRef.current.onSuccess?.(status);
                } else if (status.status === 'failed' || status.status === 'canceled') {
                    clearTimeout(stallTimerId);
                    if (status.status === 'failed') {
                        const err = new Error(status.error || status.message || 'Job failed');
                        callbacksRef.current.onError?.(err);
                    }
                } else {
                    // Running/Queued - continue polling. Reset the stall deadline
                    // whenever the job advances: any change in status, progress,
                    // message, or updated_at (when the backend sends it) counts as
                    // forward progress.
                    const signature = [
                        status.status,
                        status.progress ?? '',
                        status.message ?? '',
                        status.updated_at ?? ''
                    ].join('|');
                    if (signature !== lastProgressSignature) {
                        lastProgressSignature = signature;
                        armStallTimer();
                    }

                    currentBackoff = pollInterval; // Reset backoff
                    retryCount = 0; // Reset retry count on successful poll
                    timeoutId = setTimeout(poll, pollInterval);
                }
            } catch (error) {
                if (!isMounted || stalled) return;

                retryCount++;

                if (retryCount >= maxRetries) {
                    clearTimeout(stallTimerId);
                    const err = new Error(`Job polling failed after ${maxRetries} retries`);
                    callbacksRef.current.onError?.(err);
                    return;
                }

                // Log error for debugging
                console.error('Job polling request failed, retrying with backoff:', error);

                // Exponential backoff
                currentBackoff = Math.min(currentBackoff * 2, 10000);
                timeoutId = setTimeout(poll, currentBackoff);
            }
        };

        poll();

        return () => {
            isMounted = false;
            clearTimeout(timeoutId);
            clearTimeout(stallTimerId);
        };
    }, [jobId, enabled, pollInterval, maxRetries, stallTimeoutMs]);

    // Derive isPolling from job state
    const isPolling = !!jobId && !!enabled && !!job && (job.status === 'queued' || job.status === 'running');

    return { job, isPolling };
}
