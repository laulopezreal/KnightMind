import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Sidebar from './Sidebar';

const mockSetSessionType = vi.fn();
const mockSessionType = 'standard';
let mockPathname = '/';

vi.mock('react-router-dom', () => ({
  Link: ({ children, to, ...props }: { children: React.ReactNode; to: string; [key: string]: unknown }) => (
    <a href={to} {...props}>{children}</a>
  ),
  useLocation: () => ({ pathname: mockPathname }),
}));

vi.mock('../context/PuzzleModeContext', () => ({
  usePuzzleMode: () => ({ sessionType: mockSessionType, setSessionType: mockSetSessionType }),
}));

describe('Sidebar', () => {
  const user = userEvent.setup();

  it('should render navigation links', () => {
    render(<Sidebar />);

    expect(screen.getByText('Home')).toBeInTheDocument();
    expect(screen.getByText('Dashboard')).toBeInTheDocument();
    expect(screen.getByText('Openings')).toBeInTheDocument();
    expect(screen.getByText('Engine')).toBeInTheDocument();
    expect(screen.getByText('Train')).toBeInTheDocument();
    expect(screen.getByText('Insights')).toBeInTheDocument();
    expect(screen.getByText('Ratings')).toBeInTheDocument();
    expect(screen.getByText('Ops')).toBeInTheDocument();
  });

  it('should mark active page with aria-current', () => {
    mockPathname = '/dashboard';
    render(<Sidebar />);

    const dashboardLink = screen.getByText('Dashboard');
    expect(dashboardLink).toHaveAttribute('aria-current', 'page');
  });

  it('should render primary navigation with aria label', () => {
    render(<Sidebar />);

    const nav = screen.getByRole('navigation', { name: /primary navigation/i });
    expect(nav).toBeInTheDocument();
  });

  it('should show puzzle sub-items when on puzzles route', () => {
    mockPathname = '/puzzles';
    render(<Sidebar />);

    expect(screen.getByText('Standard')).toBeInTheDocument();
    expect(screen.getByText('Timed')).toBeInTheDocument();
    expect(screen.getByText('Accuracy Goal')).toBeInTheDocument();
  });

  it('should not show puzzle sub-items on other routes', () => {
    mockPathname = '/dashboard';
    render(<Sidebar />);

    expect(screen.queryByText('Standard')).not.toBeInTheDocument();
    expect(screen.queryByText('Timed')).not.toBeInTheDocument();
  });

  it('should call setSessionType when sub-item is clicked', async () => {
    mockPathname = '/puzzles';
    render(<Sidebar />);

    await user.click(screen.getByText('Timed'));
    expect(mockSetSessionType).toHaveBeenCalledWith('timed');
  });

  it('should render KNIGHTMIND branding', () => {
    render(<Sidebar />);

    expect(screen.getByText('KNIGHTMIND')).toBeInTheDocument();
  });

  it('should render home link with aria-label', () => {
    render(<Sidebar />);

    const homeLink = screen.getByLabelText('KnightMind home');
    expect(homeLink).toBeInTheDocument();
  });
});
