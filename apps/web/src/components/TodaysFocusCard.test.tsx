import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { TodaysFocusCard } from './TodaysFocusCard';
import type { TodaysFocus, TodaysFocusResponse } from '../api/users';

function focus(overrides: Partial<TodaysFocus> = {}): TodaysFocus {
    return {
        cause: 'loose_piece_awareness',
        name: 'Loose Piece Syndrome',
        description: 'You calculate your own threat first and skip the scan.',
        mistakes: 9,
        recent_mistakes: 4,
        accuracy: 0.4,
        priority: 12.5,
        rationale: '9 diagnosed mistakes; 4 of them recent; 40% solved when retried.',
        runner_up: null,
        trainable_now: 5,
        ...overrides,
    };
}

function data(overrides: Partial<TodaysFocusResponse> = {}): TodaysFocusResponse {
    return {
        username: 'testplayer',
        focus: focus(),
        below_threshold: 0,
        pending: 0,
        ...overrides,
    };
}

function show(payload: TodaysFocusResponse) {
    return render(
        <MemoryRouter>
            <TodaysFocusCard data={payload} />
        </MemoryRouter>
    );
}

describe('TodaysFocusCard', () => {
    it('names the habit and describes it', () => {
        show(data());
        expect(screen.getByText('Loose Piece Syndrome')).toBeInTheDocument();
        expect(screen.getByText(/skip the scan/)).toBeInTheDocument();
    });

    it('shows the evidence the recommendation rests on', () => {
        // A recommendation the user cannot check is one they can only take on
        // faith, which is not what this product offers.
        show(data());
        expect(screen.getByText(/9 diagnosed mistakes/)).toBeInTheDocument();
        expect(screen.getByText(/40% solved when retried/)).toBeInTheDocument();
    });

    it('says how many of the pattern are ready', () => {
        show(data());
        const action = screen.getByRole('link', { name: /5 ready/i });
        expect(action).toBeInTheDocument();
        expect(action).toHaveClass('min-h-11');
    });

    it('does not promise a session of exactly that size', () => {
        // A session is a fixed size and tops itself up from the rest of the due
        // queue, so "train 5 puzzles" would be a promise it does not keep.
        show(data());
        expect(screen.queryByText(/train 5 puzzles/i)).not.toBeInTheDocument();
    });

    it('offers no session when nothing of the pattern is due', () => {
        // Training early re-anchors intervals, so a button that served an
        // unrelated queue would be worse than no button.
        show(data({ focus: focus({ trainable_now: 0 }) }));
        expect(screen.queryByRole('link', { name: /train/i })).not.toBeInTheDocument();
        expect(screen.getByText(/nothing from this pattern is due/i)).toBeInTheDocument();
    });

    it('offers one truthful focus-practice action when no ordinary review is ready', () => {
        show(data({ focus: focus({ trainable_now: 0, practice_available: true, practice_candidate_count: 3 }) }));

        expect(screen.getByRole('link', { name: 'Practice this focus · 3 positions' }))
            .toHaveAttribute('href', '/puzzles?mode=focus_practice&focus_cause=loose_piece_awareness');
        expect(screen.queryByRole('link', { name: /train this pattern/i })).not.toBeInTheDocument();
        expect(screen.queryByText(/nothing from this pattern is due/i)).not.toBeInTheDocument();
    });

    it('still names the habit when nothing is due', () => {
        show(data({ focus: focus({ trainable_now: 0 }) }));
        expect(screen.getByText('Loose Piece Syndrome')).toBeInTheDocument();
    });

    it('opens a biased session rather than a filtered library', () => {
        // The distinction is the whole planner: focus_cause reorders what is
        // already due; it does not narrow or extend the queue.
        show(data());
        expect(screen.getByRole('link', { name: /train this pattern/i })).toHaveAttribute(
            'href',
            '/puzzles?focus_cause=loose_piece_awareness'
        );
    });

    it('names the runner-up when there is one', () => {
        show(data({ focus: focus({ runner_up: 'Back Rank Neglect' }) }));
        expect(screen.getByText(/After that: Back Rank Neglect/)).toBeInTheDocument();
    });

    it('says nothing about a runner-up when there is none', () => {
        show(data());
        expect(screen.queryByText(/After that/)).not.toBeInTheDocument();
    });

    describe('when there is no focus', () => {
        it('distinguishes "not a pattern yet" from "nothing analysed"', () => {
            show(data({ focus: null, below_threshold: 3, pending: 0 }));
            expect(screen.getByText(/no habit has recurred often enough/i))
                .toBeInTheDocument();
        });

        it('says analysis is still running when it is', () => {
            show(data({ focus: null, below_threshold: 0, pending: 20 }));
            expect(screen.getByText(/still analysing/i)).toBeInTheDocument();
        });

        it('never offers a session it cannot justify', () => {
            show(data({ focus: null, below_threshold: 3 }));
            expect(
                screen.queryByRole('link', { name: /train/i })
            ).not.toBeInTheDocument();
        });

        it('renders nothing at all for an untouched account', () => {
            // An empty shell reading "no focus" on a brand-new account is
            // noise, not information.
            const { container } = show(
                data({ focus: null, below_threshold: 0, pending: 0 })
            );
            expect(container).toBeEmptyDOMElement();
        });
    });

    it('never shows the raw priority number', () => {
        // It is not a probability and means nothing across users; printing it
        // would invite exactly the comparison it cannot support.
        show(data());
        expect(screen.queryByText(/12.5/)).not.toBeInTheDocument();
    });

    it('is a labelled landmark', () => {
        show(data());
        expect(
            screen.getByRole('region', { name: /today’s focus/i })
        ).toBeInTheDocument();
    });
});
