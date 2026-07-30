import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { TopMistakeCausesCard } from './TopMistakeCausesCard';
import type { MistakeCause, MistakeCausesResponse } from '../api/users';

function cause(overrides: Partial<MistakeCause> = {}): MistakeCause {
    return {
        cause: 'loose_piece_awareness',
        label: 'Loose piece awareness',
        mistakes: 8,
        dominant_phase: 'middlegame',
        verified_attempts: 10,
        verified_puzzles: 4,
        accuracy: 0.4,
        insufficient_data: false,
        is_unclassified: false,
        ...overrides,
    };
}

function data(overrides: Partial<MistakeCausesResponse> = {}): MistakeCausesResponse {
    return {
        username: 'testplayer',
        causes: [cause()],
        total_diagnosed: 8,
        pending: 0,
        min_for_ranking: 4,
        ...overrides,
    };
}

function show(payload: MistakeCausesResponse) {
    return render(
        <MemoryRouter>
            <TopMistakeCausesCard data={payload} />
        </MemoryRouter>
    );
}

describe('TopMistakeCausesCard', () => {
    it('names the cause with its count, share, phase and retry rate', () => {
        show(data());
        expect(screen.getByText('Loose piece awareness')).toBeInTheDocument();
        expect(screen.getByText(/8 mistakes · 100%/)).toBeInTheDocument();
        expect(screen.getByText(/mostly middlegame/)).toBeInTheDocument();
        expect(screen.getByText(/40% solved when retried/)).toBeInTheDocument();
    });

    it('links each ranked cause to filtered practice', () => {
        show(data());
        expect(screen.getByRole('link', { name: /practise this/i })).toHaveAttribute(
            'href',
            '/library?cause=loose_piece_awareness'
        );
    });

    describe('a count is not a tendency', () => {
        it('shows a thin cause without ranking or recommending it', () => {
            // Below the threshold, one bad afternoon looks identical to a habit.
            show(
                data({
                    causes: [cause({ mistakes: 2, insufficient_data: true })],
                    total_diagnosed: 2,
                })
            );
            expect(
                screen.getByText(/seen too few times to call a pattern/i)
            ).toBeInTheDocument();
            expect(screen.getByText(/Loose piece awareness \(2\)/)).toBeInTheDocument();
            expect(
                screen.queryByRole('link', { name: /practise this/i })
            ).not.toBeInTheDocument();
        });

        it('says so plainly when nothing has reached the threshold', () => {
            show(
                data({
                    causes: [cause({ mistakes: 1, insufficient_data: true })],
                    total_diagnosed: 1,
                })
            );
            expect(screen.getByText(/nothing here is a pattern/i)).toBeInTheDocument();
        });

        it('ranks only causes above the threshold', () => {
            show(
                data({
                    causes: [
                        cause({ mistakes: 6 }),
                        cause({
                            cause: 'king_safety_blindness',
                            label: 'King safety blindness',
                            mistakes: 1,
                            insufficient_data: true,
                        }),
                    ],
                    total_diagnosed: 7,
                })
            );
            const list = screen.getByRole('list');
            expect(within(list).getAllByRole('listitem')).toHaveLength(1);
        });
    });

    describe('accuracy honesty', () => {
        it('renders a missing rate as "not enough attempts", never as 0%', () => {
            show(
                data({
                    causes: [
                        cause({ accuracy: null, verified_attempts: 2, verified_puzzles: 2 }),
                    ],
                })
            );
            expect(screen.getByText(/not enough attempts yet/i)).toBeInTheDocument();
            expect(screen.queryByText(/0% solved/)).not.toBeInTheDocument();
        });

        it('says so specifically when the attempts are all on one puzzle', () => {
            // "not enough attempts" would read as a bug to someone who has
            // tried the same position ten times.
            show(
                data({
                    causes: [
                        cause({ accuracy: null, verified_attempts: 10, verified_puzzles: 1 }),
                    ],
                })
            );
            expect(screen.getByText(/only one puzzle tried so far/i)).toBeInTheDocument();
            expect(screen.queryByText(/not enough attempts/i)).not.toBeInTheDocument();
        });

        it('renders a genuine zero rate as 0%', () => {
            // Distinct from the case above: this one is measured.
            show(data({ causes: [cause({ accuracy: 0, verified_attempts: 9 })] }));
            expect(screen.getByText(/0% solved when retried/)).toBeInTheDocument();
        });

        it('omits the phase when none dominates', () => {
            show(data({ causes: [cause({ dominant_phase: null })] }));
            expect(screen.queryByText(/mostly/i)).not.toBeInTheDocument();
        });
    });

    describe('unclassified', () => {
        it('is reported as coverage, not as something to train', () => {
            show(
                data({
                    causes: [
                        cause({ mistakes: 6 }),
                        cause({
                            cause: 'unclassified',
                            label: 'Cause unclear',
                            mistakes: 3,
                            is_unclassified: true,
                            insufficient_data: true,
                        }),
                    ],
                    total_diagnosed: 9,
                })
            );
            expect(screen.getByText(/3 mistakes with no clear cause/i)).toBeInTheDocument();
            // Never offered as practice, and never listed among the thin causes
            // as if it were a habit.
            expect(screen.getAllByRole('link', { name: /practise this/i })).toHaveLength(1);
            expect(screen.queryByText(/Cause unclear \(3\)/)).not.toBeInTheDocument();
        });
    });

    describe('empty and pending states', () => {
        it('distinguishes "not analysed yet" from "no mistakes"', () => {
            show(data({ causes: [], total_diagnosed: 0, pending: 12 }));
            expect(screen.getByText(/haven’t been analysed yet/i)).toBeInTheDocument();
        });

        it('says there is nothing yet when there is genuinely nothing', () => {
            show(data({ causes: [], total_diagnosed: 0, pending: 0 }));
            expect(screen.getByText(/no diagnosed mistakes yet/i)).toBeInTheDocument();
        });

        it('surfaces outstanding work beside a populated list', () => {
            // Otherwise a short list reads as complete when it is partial.
            show(data({ pending: 30 }));
            expect(screen.getByText(/30 still to analyse/i)).toBeInTheDocument();
        });

        it('does not mention outstanding work when there is none', () => {
            show(data({ pending: 0 }));
            expect(screen.queryByText(/still to analyse/i)).not.toBeInTheDocument();
        });
    });

    it('is a labelled landmark', () => {
        show(data());
        expect(
            screen.getByRole('region', { name: /why your mistakes happen/i })
        ).toBeInTheDocument();
    });
});
