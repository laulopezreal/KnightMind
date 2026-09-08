import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import LibraryPuzzle from '../pages/LibraryPuzzle';
import type { SessionSummary } from '../api/sessions';
import { WarmupSummary } from './WarmupSummary';

vi.mock('../context/ChessUsernameContext', () => ({
  useChessUsername: () => ({ username: 'testplayer' }),
}));

vi.mock('../api/puzzles', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/puzzles')>()),
  getLibraryPuzzle: vi.fn(() => new Promise(() => {})),
}));

const mockSessionSummary = {
  session_id: 'warmup-1',
  requested_n: 5,
  pass_count: 4,
  fail_count: 1,
  total_time_ms: 60000,
  current_streak: 4,
  best_streak: 4,
  hints_used: 0,
  created_at: '2025-01-15T11:59:00Z',
  completed_at: '2025-01-15T12:00:00Z',
};

describe('WarmupSummary', () => {
  const user = userEvent.setup();

  function renderSummary(summary: SessionSummary = mockSessionSummary, onContinue = vi.fn()) {
    const returnToken = WarmupSummary.createReturnToken('testplayer', summary);
    return render(
      <MemoryRouter>
        <WarmupSummary sessionSummary={summary} returnToken={returnToken} onContinue={onContinue} />
      </MemoryRouter>,
    );
  }

  it('should display warmup complete heading', () => {
    renderSummary();

    expect(screen.getByText(/Warmup complete/i)).toBeInTheDocument();
  });

  it('should display accuracy percentage', () => {
    renderSummary();

    // 4 pass, 1 fail = 80%
    expect(screen.getByText('80%')).toBeInTheDocument();
  });

  it('should display pass and fail counts', () => {
    renderSummary();

    expect(screen.getByText('4')).toBeInTheDocument();
    expect(screen.getByText('1')).toBeInTheDocument();
  });

  it('should show high retention feedback for >= 80%', () => {
    renderSummary();

    expect(screen.getByText(/Great retention/)).toBeInTheDocument();
  });

  it('should show moderate feedback for 60-79%', () => {
    const summary = { ...mockSessionSummary, pass_count: 3, fail_count: 2 };
    renderSummary(summary);

    expect(screen.getByText(/Some patterns need brushing up/)).toBeInTheDocument();
  });

  it('should show low retention feedback for < 60%', () => {
    const summary = { ...mockSessionSummary, pass_count: 1, fail_count: 4 };
    renderSummary(summary);

    expect(screen.getByText(/Time to rebuild/)).toBeInTheDocument();
  });

  it('renders Back to Dashboard as the sole primary closeout and calls onContinue once', async () => {
    const onContinue = vi.fn();
    renderSummary(mockSessionSummary, onContinue);

    const closeout = screen.getByRole('button', { name: 'Back to Dashboard' });
    expect(closeout).toHaveClass('bg-primary', 'text-bg-primary');
    expect(screen.queryByText('Continue to Dashboard')).not.toBeInTheDocument();
    expect(screen.getAllByRole('button')).toEqual([closeout]);
    await user.click(closeout);
    expect(onContinue).toHaveBeenCalledTimes(1);
  });

  it('should have accessible region', () => {
    renderSummary();

    const section = screen.getByRole('region', { name: /warmup/i });
    expect(section).toBeInTheDocument();
  });

  it('renders one missed puzzle with a truthful review link and optional cause', async () => {
    const onContinue = vi.fn();
    renderSummary({
      ...mockSessionSummary,
      missed_puzzles: [{
        puzzle_id: 'p-abc',
        display_name: '12 Mar · Sicilian · move 18',
        cause: 'king_safety_blindness',
        cause_label: 'King safety blindness',
      }],
    }, onContinue);

    expect(screen.getByRole('heading', { name: 'Missed puzzle' })).toBeInTheDocument();
    expect(screen.getByText('King safety blindness')).toBeInTheDocument();
    const reviewLink = screen.getByRole('link', { name: 'Review 12 Mar · Sicilian · move 18' });
    expect(reviewLink.getAttribute('href')).toMatch(/^\/library\/p-abc\?from=session&warmup_return=[^&]+$/);
    expect(reviewLink).toHaveClass('min-h-11', 'min-w-11', 'inline-flex', 'km-focus-visible');
    await user.click(reviewLink);
    expect(onContinue).not.toHaveBeenCalled();
  });

  it('renders multiple missed puzzle names without inventing missing cause text', () => {
    renderSummary({
      ...mockSessionSummary,
      missed_puzzles: [
        { puzzle_id: 'p-1', display_name: 'First learning moment', cause: null, cause_label: null },
        { puzzle_id: 'p-2', display_name: 'Second learning moment', cause: 'calculation', cause_label: 'Calculation depth' },
      ],
    });

    expect(screen.getByRole('heading', { name: 'Missed puzzles (2)' })).toBeInTheDocument();
    expect(screen.getByText('First learning moment')).toBeInTheDocument();
    expect(screen.getByText('Second learning moment')).toBeInTheDocument();
    expect(screen.getByText('Calculation depth')).toBeInTheDocument();
    expect(screen.getAllByRole('link')).toHaveLength(2);
  });

  it.each([
    ['unavailable', {}],
    ['throwing', { randomUUID: vi.fn(() => { throw new Error('UUID unavailable'); }) }],
  ])('fails closed when secure UUID generation is %s', (_label, unavailableCrypto) => {
    vi.stubGlobal('crypto', unavailableCrypto);
    try {
      const summary = {
        ...mockSessionSummary,
        session_id: `warmup-no-uuid-${_label}`,
        missed_puzzles: [{
          puzzle_id: 'p-no-token',
          display_name: 'Safe missed-puzzle detail',
          cause: 'calculation',
          cause_label: 'Calculation depth',
        }],
      };

      const token = WarmupSummary.createReturnToken('testplayer', summary);
      expect(token).toBeNull();
      expect(WarmupSummary.readReturnToken(token, 'testplayer')).toBeNull();

      render(
        <MemoryRouter>
          <WarmupSummary sessionSummary={summary} returnToken={token} onContinue={vi.fn()} />
        </MemoryRouter>,
      );

      expect(screen.getByRole('heading', { name: 'Missed puzzle' })).toBeInTheDocument();
      expect(screen.getByText('Safe missed-puzzle detail')).toBeInTheDocument();
      expect(screen.getByText('Calculation depth')).toBeInTheDocument();
      expect(screen.queryByRole('link')).not.toBeInTheDocument();
      expect(document.body.innerHTML).not.toContain('warmup_return=');
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('mints and registers the secure UUID happy path', () => {
    const secureToken = '123e4567-e89b-42d3-a456-426614174000';
    vi.stubGlobal('crypto', { randomUUID: vi.fn(() => secureToken) });
    try {
      const summary = {
        ...mockSessionSummary,
        session_id: 'warmup-secure-uuid',
        missed_puzzles: [{
          puzzle_id: 'p-secure',
          display_name: 'Secure return puzzle',
          cause: null,
          cause_label: null,
        }],
      };

      expect(WarmupSummary.createReturnToken('testplayer', summary)).toBe(secureToken);
      expect(WarmupSummary.readReturnToken(secureToken, 'testplayer')?.summary.session_id)
        .toBe('warmup-secure-uuid');
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it.each([
    ['absent', undefined],
    ['empty', []],
  ])('does not render missed-puzzle learning when data is %s', (_label, missedPuzzles) => {
    renderSummary({ ...mockSessionSummary, missed_puzzles: missedPuzzles });

    expect(screen.queryByRole('heading', { name: /missed puzzle/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });

  it('does not allocate a return capability without a reviewable missed puzzle', () => {
    expect(WarmupSummary.createReturnToken('testplayer', mockSessionSummary)).toBeNull();
    expect(WarmupSummary.createReturnToken('testplayer', {
      ...mockSessionSummary,
      missed_puzzles: [],
    })).toBeNull();
  });

  it('expires stale capabilities and deterministically evicts the oldest entries at capacity', () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date('2026-09-07T12:00:00Z'));
      const reviewable = {
        ...mockSessionSummary,
        missed_puzzles: [{
          puzzle_id: 'p-capacity',
          display_name: 'Capacity puzzle',
          cause: null,
          cause_label: null,
        }],
      };
      const expired = WarmupSummary.createReturnToken('testplayer', reviewable);
      expect(expired).not.toBeNull();

      vi.setSystemTime(new Date('2026-09-07T12:31:00Z'));
      const tokens = Array.from({ length: 129 }, (_, index) => WarmupSummary.createReturnToken('testplayer', {
        ...reviewable,
        session_id: `warmup-capacity-${index}`,
      }));

      expect(WarmupSummary.readReturnToken(expired, 'testplayer')).toBeNull();
      expect(WarmupSummary.readReturnToken(tokens[0]!, 'testplayer')).toBeNull();
      expect(WarmupSummary.readReturnToken(tokens[1]!, 'testplayer')).not.toBeNull();
      expect(WarmupSummary.readReturnToken(tokens[tokens.length - 1]!, 'testplayer')).not.toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it('revokes a registered capability when another user attempts to read it', () => {
    const token = WarmupSummary.createReturnToken('testplayer', {
      ...mockSessionSummary,
      session_id: 'cross-user-return',
      missed_puzzles: [{
        puzzle_id: 'p-cross-user',
        display_name: 'Cross-user puzzle',
        cause: null,
        cause_label: null,
      }],
    });

    expect(WarmupSummary.readReturnToken(token, 'other-player')).toBeNull();
    expect(WarmupSummary.readReturnToken(token, 'testplayer')).toBeNull();
  });

  it('keeps long missed-puzzle identity and cause text wrapping safely', () => {
    const longName = 'Championship preparation game · Sicilian Najdorf poisoned pawn · move 38';
    const longCause = 'Missed the long forcing sequence after overlooking the opponent’s back-rank threat';
    renderSummary({
      ...mockSessionSummary,
      missed_puzzles: [{ puzzle_id: 'p-long', display_name: longName, cause: 'calculation', cause_label: longCause }],
    });

    expect(screen.getByText(longName)).toHaveClass('whitespace-normal', 'break-words');
    expect(screen.getByText(longCause)).toHaveClass('whitespace-normal', 'break-words');
    expect(screen.getByText(longName).parentElement).toHaveClass('min-w-0', 'flex-1');
  });

  it('restores the same warmup summary through the real review route and consumes it on closeout', async () => {
    const lifecycleUser = userEvent.setup();

    function PuzzlesHarness() {
      const location = useLocation();
      const navigate = useNavigate();
      const isInitialCompletion = location.search === '?warmup=true';
      const returned = WarmupSummary.readReturnToken(
        new URLSearchParams(location.search).get('warmup_return'),
        'testplayer',
      );
      const summary: SessionSummary | undefined = isInitialCompletion ? {
        ...mockSessionSummary,
        missed_puzzles: [{
          puzzle_id: 'p-abc',
          display_name: '12 Mar · Sicilian · move 18',
          cause: 'king_safety_blindness',
          cause_label: 'King safety blindness',
        }],
      } : returned?.summary;
      const returnToken = returned?.token ?? (summary
        ? WarmupSummary.createReturnToken('testplayer', summary)
        : null);

      if (!summary) return <p>Training idle</p>;
      return (
        <WarmupSummary
          sessionSummary={summary}
          returnToken={returnToken}
          onContinue={() => {
            const lifecycle = WarmupSummary.readReturnToken(returnToken, 'testplayer');
            if (lifecycle) WarmupSummary.consumeReturnState(lifecycle);
            navigate('/dashboard', { replace: true });
          }}
        />
      );
    }

    function DashboardHarness() {
      const navigate = useNavigate();
      return <button type="button" onClick={() => navigate(-1)}>Browser Back</button>;
    }

    render(
      <MemoryRouter initialEntries={['/puzzles?warmup=true']}>
        <Routes>
          <Route path="/puzzles" element={<PuzzlesHarness />} />
          <Route path="/library/:puzzleId" element={<LibraryPuzzle />} />
          <Route path="/dashboard" element={<DashboardHarness />} />
        </Routes>
      </MemoryRouter>,
    );

    await lifecycleUser.click(screen.getByRole('link', { name: 'Review 12 Mar · Sicilian · move 18' }));
    await lifecycleUser.click(await screen.findByRole('link', { name: /back to session summary/i }));

    expect(screen.getByRole('heading', { name: 'Warmup complete' })).toBeInTheDocument();
    expect(screen.getByText('12 Mar · Sicilian · move 18')).toBeInTheDocument();
    expect(screen.getByText('King safety blindness')).toBeInTheDocument();

    await lifecycleUser.click(screen.getByRole('button', { name: 'Back to Dashboard' }));
    await lifecycleUser.click(screen.getByRole('button', { name: 'Browser Back' }));
    await lifecycleUser.click(await screen.findByRole('link', { name: /back to session summary/i }));

    expect(screen.getByText('Training idle')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Warmup complete' })).not.toBeInTheDocument();
  });

  it.each([
    ['absent', undefined, 'testplayer'],
    ['malformed', { warmupReturn: { version: 1 } }, 'testplayer'],
    ['different user', {
      warmupReturn: {
        version: 1,
        token: 'return-state-token',
        username: 'other-player',
        issuedAt: Date.now(),
        summary: mockSessionSummary,
      },
    }, 'testplayer'],
    ['stale', {
      warmupReturn: {
        version: 1,
        token: 'return-state-token',
        username: 'testplayer',
        issuedAt: Date.now() - 31 * 60 * 1000,
        summary: mockSessionSummary,
      },
    }, 'testplayer'],
  ])('rejects %s return state without throwing or exposing carried labels', (_label, state, username) => {
    expect(WarmupSummary.readReturnState(state, username)).toBeNull();
  });

  it('projects only completed summary fields and never carries puzzle positions or solutions', () => {
    const token = WarmupSummary.createReturnToken('testplayer', {
      ...mockSessionSummary,
      missed_puzzles: [{
        puzzle_id: 'p-safe',
        display_name: 'Safe learning label',
        cause: null,
        cause_label: null,
      }],
      puzzles: [{ fen: 'private-fen', best_move_uci: 'e2e4' }],
      selected_items: [{ puzzle_id: 'p-safe', position: 1, review_policy: 'normal_review' }],
    } as SessionSummary);

    const returned = WarmupSummary.readReturnToken(token, 'testplayer');
    expect(returned?.summary).toMatchObject({
      session_id: 'warmup-1',
      missed_puzzles: [{ display_name: 'Safe learning label' }],
    });
    expect(returned?.summary).not.toHaveProperty('puzzles');
    expect(returned?.summary).not.toHaveProperty('selected_items');
    expect(JSON.stringify(returned)).not.toContain('private-fen');
    expect(JSON.stringify(returned)).not.toContain('e2e4');
  });
});
