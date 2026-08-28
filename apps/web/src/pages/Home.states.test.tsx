import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import Home from './Home';

// Every state must keep the page's level-one heading. Home used to early-return
// its skeleton in place of the whole page, which took the <h1> with it: axe
// flagged `page-has-heading-one`, and a screen-reader user navigating by
// headings had no way to tell which page was loading.
//
// getByRole is the load-bearing probe — it ignores aria-hidden subtrees, so the
// skeleton's placeholder blocks cannot satisfy it, only a real heading outside
// the aria-hidden wrapper.

let mockUsername = 'alice';

vi.mock('react-router-dom', () => ({
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => <a href={to}>{children}</a>,
  useNavigate: () => vi.fn(),
}));

vi.mock('../context/ChessUsernameContext', () => ({
  useChessUsername: () => ({ username: mockUsername, setUsername: vi.fn(), setEditorOpen: vi.fn() }),
}));

const mockGetUserStatus = vi.fn();
const mockGetImportStatus = vi.fn();

vi.mock('../api', () => ({
  importChessComGames: vi.fn(),
  getImportStatus: (...a: unknown[]) => mockGetImportStatus(...a),
  validateChessComUser: vi.fn(),
  getUserStatus: (...a: unknown[]) => mockGetUserStatus(...a),
  ApiError: class extends Error { detail?: string },
}));

vi.mock('../api/users', async () => {
  const barrel = await vi.importMock<typeof import('../api')>('../api');
  return {
    importChessComGames: barrel.importChessComGames,
    getImportStatus: barrel.getImportStatus,
    validateChessComUser: barrel.validateChessComUser,
    getUserStatus: barrel.getUserStatus,
  };
});

vi.mock('../api/core', () => ({ ApiError: class extends Error { detail?: string } }));

vi.mock('../api/puzzles', () => ({ generatePuzzles: vi.fn() }));
vi.mock('../hooks/useJobPolling', () => ({ useJobPolling: () => ({ job: null, isPolling: false }) }));
vi.mock('../components/Modal', () => ({ Modal: () => null }));
vi.mock('../components/JobStatusCard', () => ({ JobStatusCard: () => null }));
vi.mock('../components/LoadingSpinner', () => ({ LoadingSpinner: () => <div data-testid="spinner" /> }));

describe('Home data states', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUsername = 'alice';
  });

  it('keeps the page h1 while loading', () => {
    // Neither request settles, so allSettled never resolves and the page stays
    // in its loading state for the duration of the assertion.
    mockGetUserStatus.mockReturnValue(new Promise(() => {}));
    mockGetImportStatus.mockReturnValue(new Promise(() => {}));

    render(<Home />);

    expect(screen.getByRole('status')).toHaveTextContent(/loading your chess data/i);
    expect(screen.getByRole('heading', { level: 1, name: 'KnightMind' })).toBeInTheDocument();
  });

  it('keeps the page h1 in the error state', async () => {
    // Home only surfaces a page-level error when BOTH requests fail.
    mockGetUserStatus.mockRejectedValue(new Error('status boom'));
    mockGetImportStatus.mockRejectedValue(new Error('import boom'));

    render(<Home />);

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(/couldn't load your data/i),
    );
    expect(screen.getByRole('heading', { level: 1, name: 'KnightMind' })).toBeInTheDocument();
  });

  // The loaded state's h1 is asserted in Home.test.tsx ("should render the page
  // title as the level-one heading"), rather than a third time here.
});
