import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import Openings from './Openings';

let mockUsername = 'testplayer';
const mockNavigate = vi.fn();

vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}));

vi.mock('../context/ChessUsernameContext', () => ({
  useChessUsername: () => ({ username: mockUsername }),
}));

const mockGetOpenings = vi.fn();

vi.mock('../api', () => ({
  getOpenings: (...args: unknown[]) => mockGetOpenings(...args),
  ApiError: class extends Error { detail?: string },
}));

// Mock d3 minimally — renderTree uses d3 heavily
vi.mock('d3', () => ({
  select: () => ({
    selectAll: () => ({ remove: vi.fn() }),
    attr: function () { return this; },
    append: function () { return this; },
  }),
  hierarchy: () => ({ descendants: () => [], links: () => [] }),
  tree: () => ({
    size: function () { return this; },
    separation: function () { return this; },
  }),
  linkHorizontal: () => ({
    x: function () { return this; },
    y: function () { return this; },
  }),
}));

describe('Openings', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    mockUsername = 'testplayer';
    mockGetOpenings.mockRejectedValue(new Error('Not loaded'));
  });

  it('should render page heading', () => {
    mockGetOpenings.mockReturnValue(new Promise(() => {}));
    render(<Openings />);

    expect(screen.getByText('Opening Explorer')).toBeInTheDocument();
  });

  it('should redirect when no username', () => {
    mockUsername = '';
    render(<Openings />);

    expect(mockNavigate).toHaveBeenCalledWith('/');
  });

  it('should show color filter options', () => {
    mockGetOpenings.mockReturnValue(new Promise(() => {}));
    render(<Openings />);

    expect(screen.getByDisplayValue('Both')).toBeInTheDocument();
  });

  it('should show Analyzing button while loading', () => {
    mockGetOpenings.mockReturnValue(new Promise(() => {}));
    render(<Openings />);

    // Auto-fetch triggers on mount, so button shows "Analyzing..."
    expect(screen.getByText('Analyzing...')).toBeInTheDocument();
  });

  it('should show loading state while fetching', () => {
    mockGetOpenings.mockReturnValue(new Promise(() => {}));
    render(<Openings />);

    expect(screen.getByText('Tracing paths...')).toBeInTheDocument();
  });

  it('should show error when fetch fails', async () => {
    mockGetOpenings.mockRejectedValue(new Error('Network error'));
    render(<Openings />);

    await waitFor(() => {
      expect(screen.getByText('Network error')).toBeInTheDocument();
    });
  });

  it('should show win rate legend', () => {
    mockGetOpenings.mockReturnValue(new Promise(() => {}));
    render(<Openings />);

    expect(screen.getByText('Win Rate:')).toBeInTheDocument();
  });
});
