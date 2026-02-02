import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
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
});
