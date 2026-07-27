import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
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

// Mock focus-trap-react to avoid focus-trap issues in jsdom (no layout, so
// tabbable() sees no visible nodes). Matches Modal.test.tsx.
vi.mock('focus-trap-react', () => ({
  FocusTrap: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

describe('Sidebar', () => {
  const user = userEvent.setup();

  it('should render navigation links', () => {
    render(<Sidebar />);

    expect(screen.getByText('Home')).toBeInTheDocument();
    expect(screen.getByText('Dashboard')).toBeInTheDocument();
    expect(screen.getByText('Openings')).toBeInTheDocument();
    expect(screen.getByText('Engine')).toBeInTheDocument();
    expect(screen.getByText('Library')).toBeInTheDocument();
    expect(screen.getByText('Train')).toBeInTheDocument();
    expect(screen.getByText('Insights')).toBeInTheDocument();
    expect(screen.getByText('Ratings')).toBeInTheDocument();
  });

  it('groups the nav into Training / Progress / Study with accessible names', () => {
    render(<Sidebar />);

    // Each cluster is a labelled group so AT users get the same structure
    // sighted users see.
    const training = screen.getByRole('group', { name: 'Training' });
    const progress = screen.getByRole('group', { name: 'Progress' });
    const study = screen.getByRole('group', { name: 'Study' });

    expect(training).toContainElement(screen.getByText('Train'));
    expect(training).toContainElement(screen.getByText('Library'));
    expect(progress).toContainElement(screen.getByText('Dashboard'));
    expect(progress).toContainElement(screen.getByText('Insights'));
    expect(progress).toContainElement(screen.getByText('Ratings'));
    expect(study).toContainElement(screen.getByText('Openings'));
    expect(study).toContainElement(screen.getByText('Engine'));
  });

  it('orders the nav as the loop: Home, then Training, Progress, Study', () => {
    render(<Sidebar />);

    const nav = screen.getByRole('navigation', { name: /primary navigation/i });
    const labels = [...nav.querySelectorAll('a')].map((a) => a.textContent);
    expect(labels).toEqual([
      'Home',
      'Train', 'Library',
      'Dashboard', 'Insights', 'Ratings',
      'Openings', 'Engine',
    ]);
  });

  it('should not render Ops link in desktop navigation', () => {
    render(<Sidebar />);
    expect(screen.queryByText('Ops')).not.toBeInTheDocument();
  });

  it('should not render Ops link in mobile navigation', () => {
    render(<Sidebar mobileOpen={true} />);
    expect(screen.queryByText('Ops')).not.toBeInTheDocument();
  });

  it('is a modal dialog when open on mobile, a complementary landmark otherwise', () => {
    const { rerender } = render(<Sidebar mobileOpen={false} />);
    // Closed / desktop: the persistent sidebar landmark, not a dialog.
    const panelClosed = document.getElementById('primary-sidebar');
    expect(panelClosed).toHaveAttribute('role', 'complementary');
    expect(panelClosed).not.toHaveAttribute('aria-modal');

    // Open drawer: exposes modal dialog semantics for assistive tech.
    rerender(<Sidebar mobileOpen={true} />);
    const panelOpen = document.getElementById('primary-sidebar');
    expect(panelOpen).toHaveAttribute('role', 'dialog');
    expect(panelOpen).toHaveAttribute('aria-modal', 'true');
  });

  it('exposes a single accessible close control that calls onMobileClose', async () => {
    const onClose = vi.fn();
    render(<Sidebar mobileOpen={true} onMobileClose={onClose} />);

    // getByRole (singular) throws on multiple matches, so this asserts there is
    // exactly ONE accessible "Close navigation menu" control — the panel ✕ — and
    // that the presentational scrim is not exposed as a duplicate button.
    const closeBtn = screen.getByRole('button', { name: /close navigation menu/i });
    expect(closeBtn).toHaveTextContent('✕');

    await user.click(closeBtn);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('closes the mobile drawer when Escape is pressed', async () => {
    const onClose = vi.fn();
    render(<Sidebar mobileOpen={true} onMobileClose={onClose} />);

    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('does not listen for Escape when the drawer is closed', async () => {
    const onClose = vi.fn();
    render(<Sidebar mobileOpen={false} onMobileClose={onClose} />);

    await user.keyboard('{Escape}');
    expect(onClose).not.toHaveBeenCalled();
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

  it('should give training mode sub-items a 44px touch target', () => {
    mockPathname = '/puzzles';
    render(<Sidebar />);

    const standardBtn = screen.getByRole('button', { name: /standard/i });
    expect(standardBtn).toHaveClass('min-h-11');
  });
});

/**
 * Viewport-driven behaviour: the `inert` guard and the grow-to-desktop
 * auto-close. Both hang off `matchMedia`, which jsdom does not implement — so
 * without the stub below the component takes its `typeof matchMedia !==
 * 'function'` bail-out and neither path was ever exercised.
 *
 * These are also the two behaviours that cannot be checked by driving a real
 * browser from the Claude Browser pane: it never fires `matchMedia` change
 * events on a viewport resize, and native Tab traversal is suppressed while the
 * document is hidden. Covering them here is the only place they get enforced.
 */
describe('Sidebar viewport behaviour', () => {
  type Listener = (e: { matches: boolean }) => void;
  let queries: Array<{ query: string; listeners: Set<Listener> }> = [];
  let isMobileWidth = true;

  const evaluate = (query: string) =>
    query.includes('max-width: 767px') ? isMobileWidth : !isMobileWidth;

  const installMatchMedia = () => {
    queries = [];
    window.matchMedia = ((query: string) => {
      const entry = { query, listeners: new Set<Listener>() };
      queries.push(entry);
      return {
        get matches() { return evaluate(query); },
        media: query,
        addEventListener: (_: string, cb: Listener) => entry.listeners.add(cb),
        removeEventListener: (_: string, cb: Listener) => entry.listeners.delete(cb),
        addListener: (cb: Listener) => entry.listeners.add(cb),
        removeListener: (cb: Listener) => entry.listeners.delete(cb),
        dispatchEvent: () => true,
        onchange: null,
      } as unknown as MediaQueryList;
    }) as typeof window.matchMedia;
  };

  /** Flip the viewport and deliver `change` to every live listener, as a browser would. */
  const setViewport = (mobile: boolean) => {
    isMobileWidth = mobile;
    act(() => {
      for (const q of queries) {
        for (const cb of q.listeners) cb({ matches: evaluate(q.query) });
      }
    });
  };

  beforeEach(() => {
    isMobileWidth = true;
    installMatchMedia();
  });

  afterEach(() => {
    // @ts-expect-error - restore jsdom's (absent) implementation
    delete window.matchMedia;
  });

  it('marks the closed drawer inert on mobile so its links are not phantom tab stops', () => {
    render(<Sidebar mobileOpen={false} />);
    expect(document.getElementById('primary-sidebar')).toHaveAttribute('inert');
  });

  it('never marks the sidebar inert on desktop, where it is the real navigation', () => {
    isMobileWidth = false;
    render(<Sidebar mobileOpen={false} />);
    expect(document.getElementById('primary-sidebar')).not.toHaveAttribute('inert');
  });

  it('leaves the open drawer interactive on mobile', () => {
    render(<Sidebar mobileOpen={true} />);
    expect(document.getElementById('primary-sidebar')).not.toHaveAttribute('inert');
  });

  it('closes the drawer when the viewport grows to desktop', () => {
    // Otherwise the focus trap stays active on the now-static sidebar and the
    // close button it would return focus to is md:hidden.
    const onMobileClose = vi.fn();
    render(<Sidebar mobileOpen={true} onMobileClose={onMobileClose} />);
    expect(onMobileClose).not.toHaveBeenCalled();

    setViewport(false);
    expect(onMobileClose).toHaveBeenCalled();
  });

  it('closes immediately when opened while already at desktop width', () => {
    isMobileWidth = false;
    const onMobileClose = vi.fn();
    render(<Sidebar mobileOpen={true} onMobileClose={onMobileClose} />);
    expect(onMobileClose).toHaveBeenCalled();
  });

  it('does not close on a viewport change while the drawer is shut', () => {
    const onMobileClose = vi.fn();
    render(<Sidebar mobileOpen={false} onMobileClose={onMobileClose} />);
    setViewport(false);
    expect(onMobileClose).not.toHaveBeenCalled();
  });
});
