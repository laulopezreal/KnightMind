import { useState, useEffect, useRef } from 'react';
import { getJobStatus, type JobStatusResponse } from '../api';

interface JobPollingOptions {
    pollInterval?: number;
    enabled?: boolean;
    maxRetries?: number;
    /**
     * Max time a RUNNING job may go WITHOUT forward progress before it is treated
     * as stalled and onError fires. The deadline resets every time the job
     * advances (a change in status, progress, message, updated_at, or the per-ply
     * heartbeat_at lease), so a steadily progressing job never errors no matter
     * how long the total run takes. A `queued` job is never stalled: it has no
     * progress signal while it waits behind the worker, so the countdown only
     * begins once the job is RUNNING.
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

        // Progress-stall detector: fire onError only if a RUNNING job has made
        // no forward progress within stallTimeoutMs. armStallTimer() (re)starts
        // the countdown; it is reset every time the job advances, so a job that
        // keeps moving never errors regardless of how long it runs.
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

        // NB: the stall timer is NOT armed here. A job sitting in `queued` (e.g.
        // waiting behind another user's multi-minute job) has no progress signal
        // by design, so arming on creation would falsely fail a perfectly healthy
        // queued job. The countdown starts only once the job is RUNNING (below),
        // which also resets the baseline at the queued->running transition.

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
                    // Running/Queued - continue polling.
                    //
                    // Forward-progress signature: any change counts as the job
                    // advancing. `heartbeat_at` is the key addition - the backend
                    // bumps it on a per-ply heartbeat DURING a single long game
                    // (while `updated_at` is pinned across those heartbeats), so a
                    // game that outlasts the stall window no longer false-fails.
                    const signature = [
                        status.status,
                        status.progress ?? '',
                        status.message ?? '',
                        status.updated_at ?? '',
                        status.heartbeat_at ?? ''
                    ].join('|');
                    const advanced = signature !== lastProgressSignature;
                    lastProgressSignature = signature;

                    if (status.status === 'running') {
                        // Only a RUNNING job can stall. Arm on the first RUNNING
                        // poll (baseline starts fresh at the queued->running
                        // transition) and re-arm on every subsequent advance; a
                        // truly frozen RUNNING job never re-arms and errors once
                        // the window elapses.
                        if (advanced) {
                            armStallTimer();
                        }
                    } else {
                        // status === 'queued': waiting behind the worker, not
                        // stuck. Never stall a queued job, and cancel any pending
                        // countdown (e.g. crash recovery flips running -> queued).
                        clearTimeout(stallTimerId);
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
