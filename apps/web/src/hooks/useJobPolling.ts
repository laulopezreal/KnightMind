import { useState, useEffect, useRef } from 'react';
import { getJobStatus, type JobStatusResponse } from '../api';

interface JobPollingOptions {
    pollInterval?: number;
    enabled?: boolean;
    maxRetries?: number;
    /** Wall-clock cap on total polling time; fires onError when exceeded. */
    timeoutMs?: number;
    onSuccess?: (job: JobStatusResponse) => void;
    onError?: (error: Error) => void;
}

const DEFAULT_MAX_RETRIES = 30;
// Generous wall-clock cap: puzzle generation (Stockfish analysis) normally
// finishes well under a minute, so 2 minutes signals a genuinely stuck job.
const DEFAULT_TIMEOUT_MS = 120_000;

export function useJobPolling(jobId: string | null, options: JobPollingOptions = {}) {
    const {
        pollInterval = 1000,
        enabled = true,
        maxRetries = DEFAULT_MAX_RETRIES,
        timeoutMs = DEFAULT_TIMEOUT_MS,
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
        let isMounted = true;
        let timedOut = false;
        let currentBackoff = pollInterval;
        let retryCount = 0;

        // Wall-clock cap: maxRetries only bounds consecutive network errors,
        // so a job stuck in queued/running would otherwise be polled forever.
        const wallClockTimerId = setTimeout(() => {
            timedOut = true;
            clearTimeout(timeoutId);
            setJob(null);
            const err = new Error(
                `Puzzle generation timed out after ${Math.round(timeoutMs / 1000)} seconds. ` +
                'The job may still be running on the server - please try again in a few minutes.'
            );
            callbacksRef.current.onError?.(err);
        }, timeoutMs);

        const poll = async () => {
            try {
                const status = await getJobStatus(jobId);

                if (!isMounted || timedOut) return;

                setJob(status);

                if (status.status === 'succeeded') {
                    clearTimeout(wallClockTimerId);
                    callbacksRef.current.onSuccess?.(status);
                } else if (status.status === 'failed' || status.status === 'canceled') {
                    clearTimeout(wallClockTimerId);
                    if (status.status === 'failed') {
                        const err = new Error(status.error || status.message || 'Job failed');
                        callbacksRef.current.onError?.(err);
                    }
                } else {
                    // Running/Queued - continue polling
                    currentBackoff = pollInterval; // Reset backoff
                    retryCount = 0; // Reset retry count on successful poll
                    timeoutId = setTimeout(poll, pollInterval);
                }
            } catch (error) {
                if (!isMounted || timedOut) return;

                retryCount++;

                if (retryCount >= maxRetries) {
                    clearTimeout(wallClockTimerId);
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
            clearTimeout(wallClockTimerId);
        };
    }, [jobId, enabled, pollInterval, maxRetries, timeoutMs]);

    // Derive isPolling from job state
    const isPolling = !!jobId && !!enabled && !!job && (job.status === 'queued' || job.status === 'running');

    return { job, isPolling };
}
