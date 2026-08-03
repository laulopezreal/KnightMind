import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TimeControlOverview } from './TimeControlOverview';

const mockGetRatingHistory = vi.fn();
vi.mock('../api/ratings', () => ({
  getRatingHistory: (...a: unknown[]) => mockGetRatingHistory(...a),
}));

const hist = (ratings: number[]) =>
  ratings.map((r, i) => ({ rating: r, recorded_at: `2026-07-0${i + 1}T00:00:00Z` }));

describe('TimeControlOverview', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetRatingHistory.mockImplementation((_u: string, tc: string) => {
      if (tc === 'rapid') return Promise.resolve(hist([1500, 1510, 1524]));
      if (tc === 'blitz') return Promise.resolve(hist([1480, 1468]));
      return Promise.resolve([]); // bullet: no games
    });
  });

  it('shows latest rating and signed net movement per control', async () => {
    render(<TimeControlOverview username="alice" active="rapid" onSelect={vi.fn()} />);

    await waitFor(() => expect(screen.getByText('1524')).toBeInTheDocument());
    expect(screen.getByText('+24')).toBeInTheDocument();   // rapid up
    expect(screen.getByText('1468')).toBeInTheDocument();
    expect(screen.getByText('-12')).toBeInTheDocument();   // blitz down
    expect(screen.getByText('No games yet')).toBeInTheDocument(); // bullet
  });

  it('marks the active control and switches on tile click', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<TimeControlOverview username="alice" active="rapid" onSelect={onSelect} />);
    await waitFor(() => expect(screen.getByText('1524')).toBeInTheDocument());

    const rapid = screen.getByRole('button', { name: /rapid/i });
    expect(rapid).toHaveAttribute('aria-pressed', 'true');

    await user.click(screen.getByRole('button', { name: /blitz/i }));
    expect(onSelect).toHaveBeenCalledWith('blitz');
  });

  it('says Unavailable — not "No games yet" — when a control\'s fetch fails', async () => {
    mockGetRatingHistory.mockImplementation((_u: string, tc: string) =>
      tc === 'rapid' ? Promise.resolve(hist([1500, 1524])) : Promise.reject(new Error('boom'))
    );
    render(<TimeControlOverview username="alice" active="rapid" onSelect={vi.fn()} />);

    await waitFor(() => expect(screen.getByText('1524')).toBeInTheDocument());
    // An outage must not read as an empty account.
    expect(screen.getAllByText('Unavailable')).toHaveLength(2); // bullet + blitz failed
    expect(screen.queryByText('No games yet')).not.toBeInTheDocument();
  });

  it('renders a sparkline only when there are at least two snapshots', async () => {
    const { container } = render(<TimeControlOverview username="alice" active="rapid" onSelect={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('1524')).toBeInTheDocument());
    // rapid (3 pts) + blitz (2 pts) draw sparklines; bullet (0) does not.
    expect(container.querySelectorAll('svg polyline')).toHaveLength(2);
  });

  describe('accessible name', () => {
    it('separates the control, its rating and its movement', async () => {
      // Without an explicit label the name is built by concatenating the child
      // text nodes, which ran them together as "Bullet+661486".
      render(<TimeControlOverview username="alice" active="rapid" onSelect={vi.fn()} />);

      const rapid = await screen.findByRole('button', { name: /^Rapid: 1524/ });
      expect(rapid).toBeInTheDocument();
      expect(rapid.getAttribute('aria-label')).toMatch(/Rapid: 1524, \+24 across your last 3 snapshots/);
    });

    it('does not run the rating into the delta', async () => {
      render(<TimeControlOverview username="alice" active="rapid" onSelect={vi.fn()} />);
      const blitz = await screen.findByRole('button', { name: /^Blitz:/ });
      // "1468-12" would be the concatenated form.
      expect(blitz.getAttribute('aria-label')).not.toMatch(/1468-12/);
      expect(blitz.getAttribute('aria-label')).toMatch(/1468, -12/);
    });

    it('says so plainly when a control has no rating yet', async () => {
      render(<TimeControlOverview username="alice" active="rapid" onSelect={vi.fn()} />);
      const bullet = await screen.findByRole('button', { name: /Bullet: no rating recorded yet/ });
      expect(bullet).toBeInTheDocument();
    });

    it('omits the movement clause when there is no movement', async () => {
      mockGetRatingHistory.mockImplementation((_u: string, tc: string) =>
        tc === 'rapid' ? Promise.resolve(hist([1500, 1500])) : Promise.resolve([])
      );
      render(<TimeControlOverview username="alice" active="rapid" onSelect={vi.fn()} />);
      const rapid = await screen.findByRole('button', { name: /^Rapid: 1500$/ });
      expect(rapid).toBeInTheDocument();
    });
  });
});
