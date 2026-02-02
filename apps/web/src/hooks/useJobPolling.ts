import { useState, useEffect, useRef } from 'react';
import { getJobStatus, type JobStatusResponse } from '../api';

interface JobPollingOptions {
    pollInterval?: number;
    enabled?: boolean;
    maxRetries?: number;
    onSuccess?: (job: JobStatusResponse) => void;
    onError?: (error: Error) => void;
}

const DEFAULT_MAX_RETRIES = 30;

export function useJobPolling(jobId: string | null, options: JobPollingOptions = {}) {
    const {
        pollInterval = 1000,
        enabled = true,
        maxRetries = DEFAULT_MAX_RETRIES,
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
        let currentBackoff = pollInterval;
        let retryCount = 0;

        const poll = async () => {
            try {
                const status = await getJobStatus(jobId);

                if (!isMounted) return;

                setJob(status);

                if (status.status === 'succeeded') {
                    callbacksRef.current.onSuccess?.(status);
                } else if (status.status === 'failed' || status.status === 'canceled') {
                    if (status.status === 'failed') {
                        const err = new Error(status.message || 'Job failed');
                        callbacksRef.current.onError?.(err);
                    }
                } else {
                    // Running/Queued - continue polling
                    currentBackoff = pollInterval; // Reset backoff
                    retryCount = 0; // Reset retry count on successful poll
                    timeoutId = setTimeout(poll, pollInterval);
                }
            } catch (error) {
                if (!isMounted) return;

                retryCount++;

                if (retryCount >= maxRetries) {
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
        };
    }, [jobId, enabled, pollInterval, maxRetries]);

    // Derive isPolling from job state
    const isPolling = !!jobId && !!enabled && !!job && (job.status === 'queued' || job.status === 'running');

    return { job, isPolling };
}
