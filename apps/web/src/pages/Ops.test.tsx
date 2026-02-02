import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
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
});
