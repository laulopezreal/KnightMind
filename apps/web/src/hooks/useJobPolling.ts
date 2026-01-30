import { useState, useEffect, useRef, useCallback } from 'react';
import { getJobStatus, type JobStatusResponse } from '../api/client';

interface JobPollingOptions {
    pollInterval?: number;
    enabled?: boolean;
    onSuccess?: (job: JobStatusResponse) => void;
    onError?: (error: Error) => void;
}

export function useJobPolling(jobId: string | null, options: JobPollingOptions = {}) {
    const {
        pollInterval = 1000,
        enabled = true,
        onSuccess,
        onError
    } = options;

    const [job, setJob] = useState<JobStatusResponse | null>(null);
    const [isPolling, setIsPolling] = useState(false);
    const [error, setError] = useState<Error | null>(null);
    const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const isMounted = useRef(true);

    // Keep track of backoff
    const [backoff, setBackoff] = useState(pollInterval);

    const stopPolling = useCallback(() => {
        setIsPolling(false);
        if (timeoutRef.current) {
            clearTimeout(timeoutRef.current);
            timeoutRef.current = null;
        }
    }, []);

    const poll = useCallback(async () => {
        if (!jobId || !enabled) return;

        try {
            const status = await getJobStatus(jobId);

            if (isMounted.current) {
                setJob(status);
                // Reset backoff on successful call
                setBackoff(pollInterval);
            }

            if (status.status === 'succeeded') {
                stopPolling();
                onSuccess?.(status);
            } else if (status.status === 'failed' || status.status === 'canceled') {
                stopPolling();
                // Job-level error (logic fail, not network fail)
                if (status.status === 'failed') {
                    // Only treat as error hook callback if it's a "failure" state
                    onError?.(new Error(status.message || 'Job failed'));
                }
            } else {
                // Queued or running, continue polling
                if (isMounted.current) {
                    timeoutRef.current = setTimeout(poll, pollInterval);
                }
            }
        } catch (err) {

            if (isMounted.current) {
                // Keep the old job state (don't wipe it out on transient network error)
                // but maybe update error state
                // setError(errorObj); // Optional: do we want to show error immediately?

                // Exponential backoff
                const nextBackoff = Math.min(backoff * 2, 10000); // multiple of interval, cap at 10s
                setBackoff(nextBackoff);

                // Retry
                timeoutRef.current = setTimeout(poll, nextBackoff);
            }
        }
    }, [jobId, enabled, pollInterval, backoff, onSuccess, onError, stopPolling]);

    useEffect(() => {
        isMounted.current = true;

        if (jobId && enabled) {
            setIsPolling(true);
            setError(null);
            poll();
        } else {
            setIsPolling(false);
        }

        return () => {
            isMounted.current = false;
            if (timeoutRef.current) clearTimeout(timeoutRef.current);
        };
    }, [jobId, enabled]); // Intentionally exclude 'poll' to avoid loop if poll changes (useCallback handles deps)

    return { job, isPolling, error, stopPolling };
}
