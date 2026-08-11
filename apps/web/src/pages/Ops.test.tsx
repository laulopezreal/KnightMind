import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import Ops from './Ops';

let mockUsername = 'admin';
const mockSetUsername = vi.fn();

vi.mock('../context/ChessUsernameContext', () => ({
  useChessUsername: () => ({ username: mockUsername, setUsername: mockSetUsername }),
}));

const mockGetHealth = vi.fn();
const mockGetOpsStatus = vi.fn();
const mockGetStorageReport = vi.fn();
const mockGetUsers = vi.fn();

vi.mock('../api', () => ({
  getHealth: (...args: unknown[]) => mockGetHealth(...args),
  getOpsStatus: (...args: unknown[]) => mockGetOpsStatus(...args),
  getStorageReport: (...args: unknown[]) => mockGetStorageReport(...args),
  getUsers: (...args: unknown[]) => mockGetUsers(...args),
  cancelJob: vi.fn(),
  ApiError: class extends Error { detail?: string },
  API_TARGET: 'http://localhost:8000',
}));

const healthOk = {
  ok: true,
  db: 'ok',
  worker: 'ok',
  stockfish: 'ok',
  version: { sha: 'abc1234def', built_at: '2025-01-15T00:00:00Z' },
};

const opsOk = {
  active_job: null,
  recent_jobs: [],
  metrics: null,
  now: '2025-01-15T12:00:00Z',
};

describe('Ops', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockUsername = 'admin';
    mockGetHealth.mockResolvedValue(healthOk);
    mockGetOpsStatus.mockResolvedValue(opsOk);
    mockGetStorageReport.mockResolvedValue({ report: {} });
    mockGetUsers.mockResolvedValue(['admin', 'player2']);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('should show loading skeleton initially', () => {
    mockGetHealth.mockReturnValue(new Promise(() => {}));
    mockGetOpsStatus.mockReturnValue(new Promise(() => {}));
    mockGetStorageReport.mockReturnValue(new Promise(() => {}));
    mockGetUsers.mockReturnValue(new Promise(() => {}));

    render(<Ops />);

    // Skeleton has placeholder divs with animate-pulse
    expect(document.querySelector('.animate-pulse')).not.toBeNull();
  });

  it('should render page heading after load', async () => {
    render(<Ops />);

    await waitFor(() => {
      expect(screen.getByText('Operational Board')).toBeInTheDocument();
    });
  });

  it('should show health cards when healthy', async () => {
    render(<Ops />);

    await waitFor(() => {
      expect(screen.getByText('UP')).toBeInTheDocument();
      expect(screen.getByText('CONNECTED')).toBeInTheDocument();
      expect(screen.getByText('RUNNING')).toBeInTheDocument();
      expect(screen.getByText('AVAILABLE')).toBeInTheDocument();
    });
  });

  it('should show backend unavailable when health fails', async () => {
    mockGetHealth.mockRejectedValue(new Error('Connection refused'));
    mockGetOpsStatus.mockRejectedValue(new Error('Connection refused'));

    render(<Ops />);

    await waitFor(() => {
      expect(screen.getByText('Backend Unavailable')).toBeInTheDocument();
    });
  });

  it('should show no background process when no active job', async () => {
    render(<Ops />);

    await waitFor(() => {
      expect(screen.getByText('No background process')).toBeInTheDocument();
    });
  });

  it('should show user switcher', async () => {
    render(<Ops />);

    await waitFor(() => {
      expect(screen.getByText('User Switcher')).toBeInTheDocument();
    });
  });

  it('should show data integrity section', async () => {
    render(<Ops />);

    await waitFor(() => {
      expect(screen.getByText('Data Integrity')).toBeInTheDocument();
    });
  });

  it('should show empty execution history', async () => {
    render(<Ops />);

    await waitFor(() => {
      expect(screen.getByText(/No execution history found/)).toBeInTheDocument();
    });
  });

  it('should show degraded health indicators', async () => {
    mockGetHealth.mockResolvedValue({
      ok: false,
      db: 'error',
      worker: 'error',
      stockfish: 'error',
      version: { sha: '', built_at: '' },
    });

    render(<Ops />);

    await waitFor(() => {
      expect(screen.getByText('DOWN')).toBeInTheDocument();
      expect(screen.getByText('ERROR')).toBeInTheDocument();
    });
  });

  it('shows the newly selected user\'s storage report, not a slower earlier one', async () => {
    // The report is keyed on the selected user, and nothing used to stop an
    // earlier, slower response from landing after a later one -- putting one
    // user's row counts on screen under another user's name. Deliberately
    // resolves the FIRST request last.
    let releaseFirst: (v: unknown) => void = () => {};
    mockGetStorageReport
      .mockImplementationOnce(
        () => new Promise((resolve) => { releaseFirst = resolve; }),
      )
      .mockResolvedValueOnce({
        report: { player2: { missing_games_count: 42, missing_puzzles_count: 0 } },
      });

    render(<Ops />);
    await waitFor(() => expect(mockGetStorageReport).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'player2' } });
    await waitFor(() => expect(mockGetStorageReport).toHaveBeenCalledTimes(2));
    await screen.findAllByText(/42/);

    // The stale first response lands now. It must be ignored.
    releaseFirst({
      report: { admin: { missing_games_count: 999, missing_puzzles_count: 0 } },
    });
    // Give the stale response a chance to land before asserting it did not.
    await waitFor(() => expect(mockGetStorageReport).toHaveBeenCalledTimes(2));
    expect(screen.queryAllByText(/999/)).toHaveLength(0);
    expect(screen.queryAllByText(/42/).length).toBeGreaterThan(0);
  });

  it('keeps the outage banner up across a poll tick', async () => {
    // This page polls every five seconds. useAsyncData's default clears the
    // error slot when a fetch STARTS, which would blank the banner on every
    // tick and flash it back -- so the page opts into clearErrorOn: 'success'.
    // The second poll is left hanging deliberately: under the default the
    // banner would be gone and stay gone, so this fails without the opt-in.
    // Phase-flag rather than mockRejectedValueOnce: mounting fires more than one
    // request, so ordered one-shot mocks get consumed before the poll and the
    // hanging one lands on the initial load instead.
    mockGetHealth.mockRejectedValue(new Error('Connection refused'));
    mockGetOpsStatus.mockRejectedValue(new Error('Connection refused'));

    render(<Ops />);
    await waitFor(() =>
      expect(screen.getByText('Backend Unavailable')).toBeInTheDocument(),
    );

    // The poll now hangs. Under the default (clear-on-start) the banner would
    // be gone and STAY gone, because nothing settles to put it back.
    const before = mockGetHealth.mock.calls.length;
    mockGetHealth.mockImplementation(() => new Promise(() => {}));
    mockGetOpsStatus.mockImplementation(() => new Promise(() => {}));
    await act(async () => {
      vi.advanceTimersByTime(5000);
    });
    await waitFor(() =>
      expect(mockGetHealth.mock.calls.length).toBeGreaterThan(before),
    );

    expect(screen.getByText('Backend Unavailable')).toBeInTheDocument();
  });
});
