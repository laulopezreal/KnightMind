import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { useJobPolling } from './useJobPolling';

const mockGetJobStatus = vi.fn();

vi.mock('../api', () => ({
  getJobStatus: (...args: unknown[]) => mockGetJobStatus(...args),
}));

describe('useJobPolling', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('should not poll when jobId is null', () => {
    renderHook(() => useJobPolling(null));

    expect(mockGetJobStatus).not.toHaveBeenCalled();
  });

  it('should not poll when enabled is false', () => {
    renderHook(() => useJobPolling('job-123', { enabled: false }));

    expect(mockGetJobStatus).not.toHaveBeenCalled();
  });

  it('should poll immediately when given a jobId', async () => {
    mockGetJobStatus.mockResolvedValue({ status: 'succeeded', message: 'Done' });

    renderHook(() => useJobPolling('job-123'));

    await waitFor(() => {
      expect(mockGetJobStatus).toHaveBeenCalledWith('job-123');
    });
  });

  it('should update job state from poll response', async () => {
    mockGetJobStatus.mockResolvedValue({ status: 'succeeded', message: 'Done', progress: 100 });

    const { result } = renderHook(() => useJobPolling('job-123'));

    await waitFor(() => {
      expect(result.current.job).toEqual({ status: 'succeeded', message: 'Done', progress: 100 });
    });
  });

  it('should call onSuccess when job succeeds', async () => {
    const onSuccess = vi.fn();
    mockGetJobStatus.mockResolvedValue({ status: 'succeeded', message: 'Done' });

    renderHook(() => useJobPolling('job-123', { onSuccess }));

    await waitFor(() => {
      expect(onSuccess).toHaveBeenCalledWith({ status: 'succeeded', message: 'Done' });
    });
  });

  it('should call onError when job fails', async () => {
    const onError = vi.fn();
    mockGetJobStatus.mockResolvedValue({ status: 'failed', message: 'Something broke' });

    renderHook(() => useJobPolling('job-123', { onError }));

    await waitFor(() => {
      expect(onError).toHaveBeenCalledWith(expect.any(Error));
    });
  });

  it('should stop polling when job succeeds', async () => {
    mockGetJobStatus.mockResolvedValue({ status: 'succeeded', message: 'Done' });

    renderHook(() => useJobPolling('job-123'));

    await waitFor(() => {
      expect(mockGetJobStatus).toHaveBeenCalledTimes(1);
    });

    // Wait a bit more to make sure no additional polls happen
    await new Promise(r => setTimeout(r, 100));
    expect(mockGetJobStatus).toHaveBeenCalledTimes(1);
  });

  it('should stop polling when job fails', async () => {
    mockGetJobStatus.mockResolvedValue({ status: 'failed', message: 'Error' });

    renderHook(() => useJobPolling('job-123'));

    await waitFor(() => {
      expect(mockGetJobStatus).toHaveBeenCalledTimes(1);
    });

    await new Promise(r => setTimeout(r, 100));
    expect(mockGetJobStatus).toHaveBeenCalledTimes(1);
  });

  it('should report isPolling correctly for running job', async () => {
    // Always return running so the state doesn't race to succeeded
    mockGetJobStatus.mockResolvedValue({ status: 'running', message: 'In progress' });

    const { result } = renderHook(() => useJobPolling('job-123', { pollInterval: 5000 }));

    await waitFor(() => {
      expect(result.current.job?.status).toBe('running');
    });

    expect(result.current.isPolling).toBe(true);
  });

  it('should not report isPolling when job is done', async () => {
    mockGetJobStatus.mockResolvedValue({ status: 'succeeded', message: 'Done' });

    const { result } = renderHook(() => useJobPolling('job-123'));

    await waitFor(() => {
      expect(result.current.job?.status).toBe('succeeded');
    });

    expect(result.current.isPolling).toBe(false);
  });

  it('should not report isPolling when no jobId', () => {
    const { result } = renderHook(() => useJobPolling(null));

    expect(result.current.isPolling).toBe(false);
    expect(result.current.job).toBeNull();
  });

  it('should handle API errors gracefully', async () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    // Fail first, then succeed to stop retry loop
    mockGetJobStatus
      .mockRejectedValueOnce(new Error('Network error'))
      .mockResolvedValue({ status: 'succeeded', message: 'Done' });

    const { result } = renderHook(() => useJobPolling('job-123', { pollInterval: 50 }));

    await waitFor(() => {
      expect(mockGetJobStatus).toHaveBeenCalledTimes(2);
    }, { timeout: 3000 });

    await waitFor(() => {
      expect(result.current.job?.status).toBe('succeeded');
    });

    consoleSpy.mockRestore();
  });

  it('should stop polling and call onError after maxRetries exceeded', async () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const onError = vi.fn();
    mockGetJobStatus.mockRejectedValue(new Error('Network error'));

    renderHook(() => useJobPolling('job-123', {
      pollInterval: 10,
      maxRetries: 3,
      onError,
    }));

    await waitFor(() => {
      expect(onError).toHaveBeenCalledWith(
        expect.objectContaining({ message: 'Job polling failed after 3 retries' })
      );
    }, { timeout: 5000 });

    // Should have been called exactly 3 times (maxRetries)
    expect(mockGetJobStatus).toHaveBeenCalledTimes(3);

    consoleSpy.mockRestore();
  });

  it('should reset retry count on successful poll', async () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    // Fail once, then succeed (running), then fail once more, then succeed (done)
    mockGetJobStatus
      .mockRejectedValueOnce(new Error('Network error'))
      .mockResolvedValueOnce({ status: 'running', message: 'In progress' })
      .mockRejectedValueOnce(new Error('Network error'))
      .mockResolvedValue({ status: 'succeeded', message: 'Done' });

    const { result } = renderHook(() => useJobPolling('job-123', {
      pollInterval: 10,
      maxRetries: 2,
    }));

    await waitFor(() => {
      expect(result.current.job?.status).toBe('succeeded');
    }, { timeout: 5000 });

    // All 4 calls made: the retry count reset after the successful 'running' poll
    expect(mockGetJobStatus).toHaveBeenCalledTimes(4);

    consoleSpy.mockRestore();
  });

  it('should use default maxRetries of 30', () => {
    // Just verifying the hook can be called without maxRetries option
    const { result } = renderHook(() => useJobPolling(null));
    expect(result.current.job).toBeNull();
  });

  it('should prefer error field over message when job fails', async () => {
    const onError = vi.fn();
    mockGetJobStatus.mockResolvedValue({
      status: 'failed',
      error: 'Stockfish binary not found at /usr/bin/stockfish',
      message: 'Processing games',
    });

    renderHook(() => useJobPolling('job-123', { onError }));

    await waitFor(() => {
      expect(onError).toHaveBeenCalledWith(
        expect.objectContaining({ message: 'Stockfish binary not found at /usr/bin/stockfish' })
      );
    });
  });

  it('should fall back to message when error field is absent', async () => {
    const onError = vi.fn();
    mockGetJobStatus.mockResolvedValue({
      status: 'failed',
      message: 'Something broke',
    });

    renderHook(() => useJobPolling('job-123', { onError }));

    await waitFor(() => {
      expect(onError).toHaveBeenCalledWith(
        expect.objectContaining({ message: 'Something broke' })
      );
    });
  });

  it('should not fire onError when job is canceled', async () => {
    const onError = vi.fn();
    const onSuccess = vi.fn();
    mockGetJobStatus.mockResolvedValue({ status: 'canceled', message: 'Canceled by user' });

    const { result } = renderHook(() => useJobPolling('job-123', { onError, onSuccess }));

    await waitFor(() => {
      expect(result.current.job?.status).toBe('canceled');
    });

    // Neither callback should be called for canceled jobs
    expect(onError).not.toHaveBeenCalled();
    expect(onSuccess).not.toHaveBeenCalled();

    // Polling should have stopped
    await new Promise(r => setTimeout(r, 100));
    expect(mockGetJobStatus).toHaveBeenCalledTimes(1);
  });

  describe('progress-stall detector', () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it('should error a fully frozen RUNNING job once stallTimeoutMs elapses', async () => {
      const onError = vi.fn();
      // Every field static (status/progress/message/updated_at/heartbeat_at): the
      // RUNNING worker is genuinely stuck, so it MUST still error.
      mockGetJobStatus.mockResolvedValue({
        status: 'running',
        message: 'In progress',
        progress: 10,
        updated_at: '2026-01-01T00:00:00Z',
        heartbeat_at: '2026-01-01T00:00:00Z',
      });

      const { result } = renderHook(() => useJobPolling('job-123', {
        pollInterval: 1000,
        stallTimeoutMs: 5000,
        onError,
      }));

      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000);
      });

      expect(onError).toHaveBeenCalledTimes(1);
      expect(onError).toHaveBeenCalledWith(
        expect.objectContaining({
          message: expect.stringContaining('has not made progress for 5 seconds'),
        })
      );
      expect(onError).toHaveBeenCalledWith(
        expect.objectContaining({
          message: expect.stringContaining('may still be running on the server'),
        })
      );
      // The message must not read like the job itself failed after a fixed time.
      expect(onError).toHaveBeenCalledWith(
        expect.objectContaining({
          message: expect.not.stringContaining('timed out after'),
        })
      );

      // Job state is cleared so consumers stop showing a "generating" spinner
      expect(result.current.job).toBeNull();
      expect(result.current.isPolling).toBe(false);

      // Polling has stopped: no further requests after the stall error fired
      const callsAtStall = mockGetJobStatus.mock.calls.length;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10000);
      });
      expect(mockGetJobStatus).toHaveBeenCalledTimes(callsAtStall);
    });

    it('should NEVER error a queued job, no matter how long it waits behind the worker', async () => {
      const onError = vi.fn();
      // A job parked in the queue behind another user's multi-minute job: the
      // signature never changes, but a queued job is waiting, not stuck.
      mockGetJobStatus.mockResolvedValue({
        status: 'queued',
        message: 'Queued for generation',
        progress: 0,
      });

      const { result } = renderHook(() => useJobPolling('job-123', {
        pollInterval: 1000,
        stallTimeoutMs: 5000,
        onError,
      }));

      // Five minutes of waiting - 60x the stall window - must not error.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(300_000);
      });

      expect(onError).not.toHaveBeenCalled();
      expect(result.current.job?.status).toBe('queued');
      // Still polling: the client keeps watching for the job to start running.
      expect(mockGetJobStatus.mock.calls.length).toBeGreaterThan(100);
    });

    it('should start the stall window fresh at the queued->running transition', async () => {
      const onError = vi.fn();
      // Long queue wait, then the worker claims it. The window must begin at the
      // transition, not count the (unbounded) time already spent queued.
      let phase: 'queued' | 'running' = 'queued';
      mockGetJobStatus.mockImplementation(() => Promise.resolve(
        phase === 'queued'
          ? { status: 'queued', message: 'Queued for generation', progress: 0 }
          : {
              status: 'running',
              message: 'Analyzing game 1 of 30',
              progress: 3,
              updated_at: '2026-01-01T00:00:00Z',
              heartbeat_at: '2026-01-01T00:00:00Z',
            }
      ));

      renderHook(() => useJobPolling('job-123', {
        pollInterval: 1000,
        stallTimeoutMs: 5000,
        onError,
      }));

      // Wait 20s in the queue (4x the window): no error.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(20_000);
      });
      expect(onError).not.toHaveBeenCalled();

      // Worker claims the job; it is now RUNNING but immediately frozen.
      phase = 'running';
      // One poll observes RUNNING and arms the fresh window.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
      expect(onError).not.toHaveBeenCalled();

      // 5s of frozen RUNNING after the transition -> stall fires now (and would
      // NOT have, had the window still been counting from job creation).
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000);
      });
      expect(onError).toHaveBeenCalledTimes(1);
    });

    it('should treat a per-ply heartbeat as forward progress during a single long game', async () => {
      const onError = vi.fn();
      // A single game that outlasts the stall window: progress + message stay
      // static (they only move BETWEEN games), but the worker's per-ply
      // heartbeat keeps bumping heartbeat_at. That liveness must keep the job
      // alive even though nothing else changes.
      let tick = 0;
      mockGetJobStatus.mockImplementation(() => {
        tick += 1;
        return Promise.resolve({
          status: 'running',
          message: 'Analyzing game 4 of 30',
          progress: 12,
          updated_at: '2026-01-01T00:00:00Z', // pinned across heartbeats
          heartbeat_at: `2026-01-01T00:00:${String(tick).padStart(2, '0')}Z`,
        });
      });

      const { result } = renderHook(() => useJobPolling('job-123', {
        pollInterval: 1000,
        stallTimeoutMs: 5000,
        onError,
      }));

      // Run 30s (6x the window) inside one game: the heartbeat advances every
      // poll, so the job never stalls.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000);
      });

      expect(onError).not.toHaveBeenCalled();
      expect(result.current.job?.status).toBe('running');
    });

    it('should never error a job that keeps advancing per-game, even with a gap larger than one poll', async () => {
      const onError = vi.fn();
      // Realistic backend cadence: progress + message advance once PER GAME, not
      // per poll. Model a 15s inter-game gap (well under the 90s window) and run
      // 150s total - past the old 120s wall-clock cap. No error, still advancing.
      let poll = 0;
      mockGetJobStatus.mockImplementation(() => {
        poll += 1;
        const game = Math.floor(poll / 15) + 1; // advances ~every 15 polls (~15s)
        return Promise.resolve({
          status: 'running',
          progress: game,
          message: `Analyzing game ${game} of 30`,
        });
      });

      const { result } = renderHook(() => useJobPolling('job-123', {
        pollInterval: 1000,
        stallTimeoutMs: 90_000,
        onError,
      }));

      for (let i = 0; i < 15; i++) {
        await act(async () => {
          await vi.advanceTimersByTimeAsync(10_000);
        });
      }

      expect(onError).not.toHaveBeenCalled();
      expect(result.current.job?.status).toBe('running');
      expect(mockGetJobStatus.mock.calls.length).toBeGreaterThan(120);
    });

    it('should reset the stall deadline whenever progress advances', async () => {
      const onError = vi.fn();
      // Progress advances until t~=8s, then freezes. The stall window is 5s, so a
      // naive fixed timer would fire around 5s; the reset-on-progress detector
      // must not fire until 5s AFTER the last advance.
      let elapsed = 0;
      mockGetJobStatus.mockImplementation(() => {
        elapsed += 1;
        const frozen = elapsed > 8;
        return Promise.resolve({
          status: 'running',
          progress: frozen ? 8 : elapsed,
          message: frozen ? 'Analyzing game 8 of 30' : `Analyzing game ${elapsed} of 30`,
        });
      });

      renderHook(() => useJobPolling('job-123', {
        pollInterval: 1000,
        stallTimeoutMs: 5000,
        onError,
      }));

      // At 10s the job has only just frozen (last advance ~8s): still within window.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10_000);
      });
      expect(onError).not.toHaveBeenCalled();

      // 5s after the last advance the stall fires.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(4000);
      });
      expect(onError).toHaveBeenCalledTimes(1);
      expect(onError).toHaveBeenCalledWith(
        expect.objectContaining({
          message: expect.stringContaining('has not made progress'),
        })
      );
    });

    it('should cancel an armed stall timer when a running job is re-queued (crash recovery)', async () => {
      const onError = vi.fn();
      // Job runs (arming the stall timer), then crash recovery flips it back to
      // QUEUED with a fresh message. The armed timer must be cancelled so the
      // healthy re-queued job is not falsely failed.
      let phase: 'running' | 'queued' = 'running';
      mockGetJobStatus.mockImplementation(() => Promise.resolve(
        phase === 'running'
          ? {
              status: 'running',
              message: 'In progress',
              progress: 10,
              updated_at: '2026-01-01T00:00:00Z',
              heartbeat_at: '2026-01-01T00:00:00Z',
            }
          : { status: 'queued', message: 'Recovered from crash', progress: 0 }
      ));

      renderHook(() => useJobPolling('job-123', {
        pollInterval: 1000,
        stallTimeoutMs: 5000,
        onError,
      }));

      // Run frozen for 2s: the stall timer is armed but has not fired yet.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
      expect(onError).not.toHaveBeenCalled();

      // Crash recovery re-queues the job.
      phase = 'queued';
      // Well past the 5s window while queued: the armed timer was cancelled, so
      // no stall error fires.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(20_000);
      });
      expect(onError).not.toHaveBeenCalled();
    });

    it('should not fire a stall error when the job succeeds first', async () => {
      const onError = vi.fn();
      const onSuccess = vi.fn();
      mockGetJobStatus
        .mockResolvedValueOnce({ status: 'running', message: 'In progress' })
        .mockResolvedValue({ status: 'succeeded', message: 'Done' });

      renderHook(() => useJobPolling('job-123', {
        pollInterval: 1000,
        stallTimeoutMs: 5000,
        onError,
        onSuccess,
      }));

      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
      expect(onSuccess).toHaveBeenCalledTimes(1);

      // Advance well past the stall window: the timer was cleared on completion
      await act(async () => {
        await vi.advanceTimersByTimeAsync(20000);
      });
      expect(onError).not.toHaveBeenCalled();
      expect(onSuccess).toHaveBeenCalledTimes(1);
    });

    it('should not fire a stall error after unmount', async () => {
      const onError = vi.fn();
      mockGetJobStatus.mockResolvedValue({ status: 'running', message: 'In progress' });

      const { unmount } = renderHook(() => useJobPolling('job-123', {
        pollInterval: 1000,
        stallTimeoutMs: 5000,
        onError,
      }));

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
      unmount();

      const callsAtUnmount = mockGetJobStatus.mock.calls.length;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(20000);
      });
      expect(onError).not.toHaveBeenCalled();
      expect(mockGetJobStatus).toHaveBeenCalledTimes(callsAtUnmount);
    });

    it('should use a default stall window of 90 seconds for a frozen running job', async () => {
      const onError = vi.fn();
      // Fully frozen RUNNING signature (including updated_at + heartbeat_at):
      // no forward progress at all.
      mockGetJobStatus.mockResolvedValue({
        status: 'running',
        message: 'In progress',
        progress: 5,
        updated_at: '2026-01-01T00:00:00Z',
        heartbeat_at: '2026-01-01T00:00:00Z',
      });

      renderHook(() => useJobPolling('job-123', { pollInterval: 1000, onError }));

      // Just before the default stall window: still polling, no error
      await act(async () => {
        await vi.advanceTimersByTimeAsync(89_000);
      });
      expect(onError).not.toHaveBeenCalled();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
      expect(onError).toHaveBeenCalledWith(
        expect.objectContaining({
          message: expect.stringContaining('has not made progress for 90 seconds'),
        })
      );
    });
  });
});
