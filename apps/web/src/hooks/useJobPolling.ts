import { useState, useEffect, useRef } from 'react';
import { getJobStatus, type JobStatusResponse } from '../api';

interface JobPollingOptions {
    pollInterval?: number;
    enabled?: boolean;
    maxRetries?: number;
    /**
     * Max time a RUNNING job may go WITHOUT forward progress before the client
     * suspects a stall. The deadline resets every time the job advances (a
     * change in status, progress, message, updated_at, or the per-ply
     * heartbeat_at lease), so a steadily progressing job never stalls no
     * matter how long the total run takes. A `queued` job is never stalled: it
     * has no progress signal while it waits behind the worker, so the countdown
     * only begins once the job is RUNNING.
     *
     * A fired stall timer is NOT a verdict on its own: the client stops
     * polling, re-checks the server once after a short cooldown, and only
     * surfaces the stall after a bounded set of unchanged re-checks. A
     * backgrounded/frozen tab can stop observing a healthy long job, and the
     * server remains the source of truth for job state.
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

// After the stall window elapses the client pauses polling and re-checks the
// server once (spaced by this cooldown) before deciding anything. The server is
// the source of truth: a backgrounded/frozen tab can miss a healthy job's
// progress, so a single unchanged signature is not proof the worker is stuck.
const STALL_RECHECK_COOLDOWN_MS = 3_000;
// Bounded number of unchanged re-checks before the stall is surfaced. A
// genuinely frozen job still surfaces; a transient observation gap recovers.
const MAX_STALL_RECHECKS = 3;

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
        let stallRecheckId: ReturnType<typeof setTimeout>;
        let isMounted = true;
        let stalled = false;
        let currentBackoff = pollInterval;
        let retryCount = 0;
        let lastProgressSignature: string | null = null;
        let stallRecheckCount = 0;

        const stopPolling = () => {
            clearTimeout(timeoutId);
            clearTimeout(stallTimerId);
            clearTimeout(stallRecheckId);
        };

        // Forward-progress signature: any change counts as the job advancing.
        // `heartbeat_at` is the key addition - the backend bumps it on a per-ply
        // heartbeat DURING a single long game (while `updated_at` is pinned
        // across those heartbeats), so a game that outlasts the stall window no
        // longer false-fails.
        const buildSignature = (status: JobStatusResponse) =>
            [
                status.status,
                status.progress ?? '',
                status.message ?? '',
                status.updated_at ?? '',
                status.heartbeat_at ?? ''
            ].join('|');

        const schedulePoll = (delay: number = pollInterval) => {
            clearTimeout(timeoutId);
            timeoutId = setTimeout(poll, delay);
        };

        // Only here does the client admit the job may be stuck. The copy is
        // honest: it does not assert the job failed, because the server may
        // still be running it. Consumers (e.g. Home) read `isStall` to avoid
        // framing this as a definitive "failed:" error.
        const surfaceStallError = () => {
            clearTimeout(stallTimerId);
            clearTimeout(stallRecheckId);
            setJob(null);
            const err = new Error(
                'Puzzle generation seems stuck. Check back in a minute; the job may still be running on the server.'
            ) as Error & { isStall?: boolean };
            err.isStall = true;
            callbacksRef.current.onError?.(err);
        };

        // Hand control back to the normal poll loop after a transient gap.
        // `freshStallWindow` starts a new countdown when the re-check proved the
        // job advanced while we were not observing it.
        const resumePolling = (freshStallWindow: boolean) => {
            stalled = false;
            stallRecheckCount = 0;
            if (freshStallWindow) {
                armStallTimer();
            }
            schedulePoll();
        };

        // One re-fetch of GET /jobs/{id} after the stall timer fired. The
        // server decides: succeeded/failed/canceled are terminal, an advanced
        // running signature means the gap was transient, and only a bounded
        // run of unchanged running signatures surfaces the stall.
        const recheckAfterStall = async () => {
            if (!isMounted) return;
            try {
                const status = await getJobStatus(jobId);
                if (!isMounted) return;

                setJob(status);

                if (status.status === 'succeeded') {
                    stopPolling();
                    callbacksRef.current.onSuccess?.(status);
                    return;
                }
                if (status.status === 'failed') {
                    stopPolling();
                    callbacksRef.current.onError?.(new Error(status.error || status.message || 'Job failed'));
                    return;
                }
                if (status.status === 'canceled') {
                    // Mirror the normal loop: a canceled job fires no callback.
                    stopPolling();
                    return;
                }
                if (status.status === 'queued') {
                    // A re-queued job is waiting, not stuck: hand control back to
                    // the normal loop, which never stalls a queued job.
                    resumePolling(false);
                    return;
                }

                // Still running: compare against the signature seen before the stall.
                const signature = buildSignature(status);
                if (signature !== lastProgressSignature) {
                    // The job advanced while we were not observing it. The gap
                    // was transient: resume normal polling with a fresh window.
                    lastProgressSignature = signature;
                    resumePolling(true);
                    return;
                }

                // Still running with an unchanged signature. This is a suspected
                // stall, not a verdict: keep a bounded number of spaced
                // re-checks before surfacing, so one observation can't condemn a
                // job that is simply between progress writes.
                stallRecheckCount += 1;
                if (stallRecheckCount < MAX_STALL_RECHECKS) {
                    stallRecheckId = setTimeout(recheckAfterStall, STALL_RECHECK_COOLDOWN_MS);
                } else {
                    surfaceStallError();
                }
            } catch (error) {
                // The re-check itself could not reach the server. Do not
                // fabricate a failure: spend the remaining re-check budget,
                // then surface the honest stall copy.
                console.error('Job stall re-check failed:', error);
                stallRecheckCount += 1;
                if (stallRecheckCount < MAX_STALL_RECHECKS) {
                    stallRecheckId = setTimeout(recheckAfterStall, STALL_RECHECK_COOLDOWN_MS);
                } else {
                    surfaceStallError();
                }
            }
        };

        // Progress-stall detector: the countdown (re)starts every time a RUNNING
        // job advances, so a job that keeps moving never errors regardless of
        // how long it runs. When it fires, the client does NOT declare a
        // terminal failure: it pauses polling for a short cooldown, then
        // re-checks the server to learn the real state (see recheckAfterStall).
        const armStallTimer = () => {
            clearTimeout(stallTimerId);
            stallTimerId = setTimeout(() => {
                if (!isMounted) return;
                stalled = true;
                clearTimeout(timeoutId);
                stallRecheckCount = 0;
                stallRecheckId = setTimeout(recheckAfterStall, STALL_RECHECK_COOLDOWN_MS);
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
                    stopPolling();
                    callbacksRef.current.onSuccess?.(status);
                } else if (status.status === 'failed' || status.status === 'canceled') {
                    stopPolling();
                    if (status.status === 'failed') {
                        const err = new Error(status.error || status.message || 'Job failed');
                        callbacksRef.current.onError?.(err);
                    }
                } else {
                    // Running/Queued - continue polling.
                    const signature = buildSignature(status);
                    const advanced = signature !== lastProgressSignature;
                    lastProgressSignature = signature;

                    if (status.status === 'running') {
                        // Only a RUNNING job can stall. Arm on the first RUNNING
                        // poll (baseline starts fresh at the queued->running
                        // transition) and re-arm on every subsequent advance; a
                        // truly frozen RUNNING job never re-arms and triggers the
                        // recovery path once the window elapses.
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
                    stopPolling();
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
            clearTimeout(stallRecheckId);
        };
    }, [jobId, enabled, pollInterval, maxRetries, stallTimeoutMs]);

    // Derive isPolling from job state
    const isPolling = !!jobId && !!enabled && !!job && (job.status === 'queued' || job.status === 'running');

    return { job, isPolling };
}
