import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render } from '@testing-library/react';
import Dashboard from './Dashboard';

const mockNavigate = vi.fn();
let mockUsername = 'testplayer';

vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}));

vi.mock('../context/ChessUsernameContext', () => ({
  useChessUsername: () => ({ username: mockUsername }),
}));

vi.mock('../api/users', () => ({
  getDashboardSummary: vi.fn().mockRejectedValue(new Error('Not loaded')),
  getTrickyPuzzles: vi.fn().mockRejectedValue(new Error('Not loaded')),
}));

vi.mock('../api/sessions', () => ({
  getRecentSessions: vi.fn().mockRejectedValue(new Error('Not loaded')),
}));

vi.mock('../components/HeroTrainCard', () => ({
  HeroTrainCard: () => <div data-testid="hero-card">HeroTrainCard</div>,
}));

vi.mock('../components/RecentlyTrickyCard', () => ({
  RecentlyTrickyCard: () => <div data-testid="tricky-card">RecentlyTrickyCard</div>,
}));

vi.mock('../components/MomentumCard', () => ({
  MomentumCard: () => <div data-testid="momentum-card">MomentumCard</div>,
}));

vi.mock('../components/StreakCard', () => ({
  StreakCard: () => <div data-testid="streak-card">StreakCard</div>,
}));

vi.mock('../components/RecentSessionsCard', () => ({
  RecentSessionsCard: () => <div data-testid="sessions-card">RecentSessionsCard</div>,
}));

describe('Dashboard', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    mockUsername = 'testplayer';
  });

  it('should redirect to home when no username', () => {
    mockUsername = '';
    render(<Dashboard />);

    expect(mockNavigate).toHaveBeenCalledWith('/');
  });

  it('should render when username is set', () => {
    render(<Dashboard />);

    // Should not redirect
    expect(mockNavigate).not.toHaveBeenCalled();
  });
});
