import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { MistakeDiagnosisCard } from './MistakeDiagnosisCard';
import type { PuzzleDiagnosis } from '../api/puzzles';

function diagnosis(overrides: Partial<PuzzleDiagnosis> = {}): PuzzleDiagnosis {
    return {
        state: 'ready',
        puzzle_id: 'p1',
        primary_motif: 'hanging_queen',
        primary_cause: 'loose_piece_awareness',
        primary_cause_label: 'Loose piece awareness',
        secondary_causes: [],
        secondary_cause_labels: [],
        phase: 'middlegame',
        evidence: [
            { id: 'best.move', label: 'Best move', value: 'Qxd5 (forcing)' },
            { id: 'eval.swing', label: 'Evaluation swing (pawns)', value: '9.00' },
        ],
        evidence_withheld: false,
        explanation: null,
        training_recommendation: null,
        user_confirmed_cause: null,
        source: 'rules',
        diagnosed_at: '2026-07-27T00:00:00Z',
        ...overrides,
    };
}

describe('MistakeDiagnosisCard', () => {
    describe('before the puzzle is resolved', () => {
        it('renders nothing at all, so it cannot give away the answer', () => {
            // The evidence literally contains the solution move in SAN.
            const { container } = render(
                <MistakeDiagnosisCard diagnosis={diagnosis()} revealed={false} />
            );
            expect(container).toBeEmptyDOMElement();
        });

        it('stays hidden even while loading', () => {
            const { container } = render(
                <MistakeDiagnosisCard diagnosis={null} revealed={false} loading />
            );
            expect(container).toBeEmptyDOMElement();
        });
    });

    describe('once resolved', () => {
        it('names the cause and shows the evidence behind it', () => {
            render(<MistakeDiagnosisCard diagnosis={diagnosis()} revealed />);
            expect(screen.getByText('Loose piece awareness')).toBeInTheDocument();
            expect(screen.getByText('Qxd5 (forcing)')).toBeInTheDocument();
            expect(screen.getByText('Evaluation swing (pawns):')).toBeInTheDocument();
        });

        it('lists secondary causes as supporting context', () => {
            render(
                <MistakeDiagnosisCard
                    revealed
                    diagnosis={diagnosis({
                        secondary_causes: ['forcing_move_blindness'],
                        secondary_cause_labels: ['Forcing move blindness'],
                    })}
                />
            );
            expect(screen.getByText('Forcing move blindness')).toBeInTheDocument();
        });

        it('marks a cause the user corrected as their own', () => {
            render(
                <MistakeDiagnosisCard
                    revealed
                    diagnosis={diagnosis({
                        user_confirmed_cause: 'king_safety_blindness',
                        primary_cause_label: 'King safety blindness',
                    })}
                />
            );
            expect(screen.getByText('Your label')).toBeInTheDocument();
        });

        it('never renders a confidence percentage', () => {
            // Rule strength is an ordering prior, not a calibrated probability.
            const { container } = render(
                <MistakeDiagnosisCard diagnosis={diagnosis()} revealed />
            );
            expect(container.textContent).not.toMatch(/\d+%/);
            expect(container.textContent).not.toMatch(/confiden/i);
        });

        it('confirms the displayed cause once without expanding the taxonomy', async () => {
            const onConfirm = vi.fn();
            const user = userEvent.setup();
            render(
                <MistakeDiagnosisCard
                    diagnosis={diagnosis({
                        cause_options: [
                            { value: 'loose_piece_awareness', label: 'Loose piece awareness' },
                            { value: 'king_safety_blindness', label: 'King safety blindness' },
                        ],
                    })}
                    revealed
                    onConfirm={onConfirm}
                />
            );

            expect(screen.getByRole('button', { name: /this fits/i })).toBeInTheDocument();
            expect(screen.getByRole('group')).not.toHaveAttribute('open');
            await user.click(screen.getByRole('button', { name: /this fits/i }));
            expect(onConfirm).toHaveBeenCalledTimes(1);
            expect(onConfirm).toHaveBeenCalledWith('loose_piece_awareness');
        });

        it('suppresses duplicate writes while a confirmation is saving', async () => {
            const onConfirm = vi.fn();
            const user = userEvent.setup();
            render(
                <MistakeDiagnosisCard
                    diagnosis={diagnosis({
                        cause_options: [
                            { value: 'loose_piece_awareness', label: 'Loose piece awareness' },
                            { value: 'king_safety_blindness', label: 'King safety blindness' },
                        ],
                    })}
                    revealed
                    savingConfirmation
                    onConfirm={onConfirm}
                />
            );

            const fits = screen.getByRole('button', { name: /saving/i });
            expect(fits).toBeDisabled();
            await user.click(fits);
            expect(onConfirm).not.toHaveBeenCalled();
        });

        it('submits the selected server-supplied alternative cause', async () => {
            const onConfirm = vi.fn();
            const user = userEvent.setup();
            render(
                <MistakeDiagnosisCard
                    diagnosis={diagnosis({
                        cause_options: [
                            { value: 'loose_piece_awareness', label: 'Loose piece awareness' },
                            { value: 'king_safety_blindness', label: 'King safety blindness' },
                        ],
                    })}
                    revealed
                    onConfirm={onConfirm}
                />
            );

            await user.click(screen.getByText('Choose a different cause'));
            await user.click(screen.getByRole('button', { name: 'King safety blindness' }));
            expect(onConfirm).toHaveBeenCalledWith('king_safety_blindness');
        });

        it('keeps correction compact, keyboard-operable, and accessible at the mobile structure', async () => {
            const onConfirm = vi.fn();
            const user = userEvent.setup();
            const { container } = render(
                <div style={{ width: 390, minHeight: 844 }}>
                    <MistakeDiagnosisCard
                        revealed
                        onConfirm={onConfirm}
                        diagnosis={diagnosis({
                            cause_options: [
                                { value: 'loose_piece_awareness', label: 'Loose piece awareness' },
                                { value: 'king_safety_blindness', label: 'King safety blindness' },
                                { value: 'forcing_move_blindness', label: 'Forcing move blindness' },
                            ],
                        })}
                    />
                </div>
            );
            const fits = screen.getByRole('button', { name: 'This fits' });
            const disclosure = screen.getByText('Choose a different cause');
            const details = disclosure.closest('details');

            expect(details).not.toBeNull();
            expect(details).not.toHaveAttribute('open');
            // Native disclosure keeps descendants in the DOM, but not in its
            // visible disclosure state; `open` is the platform contract.
            expect(fits.compareDocumentPosition(disclosure) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

            // `summary` is the native disclosure control: it receives focus
            // directly, and browsers supply Enter/Space activation without a
            // custom keyboard handler. JSDOM does not implement that default
            // action, so exercise the focused native control with a click.
            disclosure.focus();
            expect(document.activeElement).toBe(disclosure);
            await user.click(disclosure);
            expect(details).toHaveAttribute('open');
            const alternative = screen.getByRole('button', { name: 'King safety blindness' });
            expect(alternative).toBeEnabled();
            expect(alternative.compareDocumentPosition(fits) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
            expect(container.querySelector('.flex.flex-wrap')).toBeInTheDocument();
        });

        it('shows confirmation failure without disabling the move-on action around it', () => {
            render(
                <MistakeDiagnosisCard
                    diagnosis={diagnosis({
                        cause_options: [{ value: 'loose_piece_awareness', label: 'Loose piece awareness' }],
                    })}
                    revealed
                    confirmationError="Couldn’t save your label. Try again."
                    onConfirm={vi.fn()}
                />
            );

            expect(screen.getByRole('alert')).toHaveTextContent(/couldn’t save your label/i);
            expect(screen.getByRole('button', { name: /this fits/i })).toBeEnabled();
        });
    });

    describe('honest states', () => {
        it('says a puzzle has not been analysed yet rather than showing nothing', () => {
            render(
                <MistakeDiagnosisCard diagnosis={diagnosis({ state: 'pending' })} revealed />
            );
            expect(screen.getByText(/hasn’t been analysed yet/i)).toBeInTheDocument();
        });

        it('admits when no clear cause stands out instead of guessing', () => {
            render(
                <MistakeDiagnosisCard
                    revealed
                    diagnosis={diagnosis({
                        state: 'unclear',
                        primary_cause: null,
                        primary_cause_label: null,
                    })}
                />
            );
            expect(screen.getByText(/no clear cause/i)).toBeInTheDocument();
            // and does not fall back to naming one anyway
            expect(screen.queryByText('Loose piece awareness')).not.toBeInTheDocument();
        });

        it('says a position cannot be analysed without leaking why', () => {
            render(
                <MistakeDiagnosisCard
                    diagnosis={diagnosis({ state: 'unavailable' })}
                    revealed
                />
            );
            expect(screen.getByText(/can’t be analysed/i)).toBeInTheDocument();
        });

        it('shows a loading message rather than an empty card', () => {
            render(<MistakeDiagnosisCard diagnosis={null} revealed loading />);
            expect(screen.getByRole('status')).toHaveTextContent(/loading diagnosis/i);
        });

        it('explains withheld evidence instead of rendering an empty section', () => {
            render(
                <MistakeDiagnosisCard
                    revealed
                    diagnosis={diagnosis({ evidence: [], evidence_withheld: true })}
                />
            );
            expect(screen.getByText(/solve this puzzle to see the evidence/i)).toBeInTheDocument();
        });
    });

    it('is a labelled landmark so the section is reachable by assistive tech', () => {
        render(<MistakeDiagnosisCard diagnosis={diagnosis()} revealed />);
        expect(
            screen.getByRole('region', { name: /mistake diagnosis/i })
        ).toBeInTheDocument();
    });

    describe('AI-enriched rows', () => {
        it('leads with the explanation and shows the recommendation', () => {
            render(
                <MistakeDiagnosisCard
                    revealed
                    diagnosis={diagnosis({
                        explanation: 'You left the queen takeable while chasing your own threat.',
                        training_recommendation: 'Scan for loose pieces before calculating.',
                    })}
                />
            );
            expect(screen.getByText(/left the queen takeable/i)).toBeInTheDocument();
            expect(screen.getByText('Next time')).toBeInTheDocument();
            expect(screen.getByText(/scan for loose pieces/i)).toBeInTheDocument();
        });

        it('demotes the evidence heading once prose carries the explanation', () => {
            const { rerender } = render(
                <MistakeDiagnosisCard diagnosis={diagnosis()} revealed />
            );
            expect(screen.getByText('Why')).toBeInTheDocument();

            rerender(
                <MistakeDiagnosisCard
                    revealed
                    diagnosis={diagnosis({ explanation: 'Because the queen was loose.' })}
                />
            );
            expect(screen.getByText('Evidence')).toBeInTheDocument();
            expect(screen.queryByText('Why')).not.toBeInTheDocument();
        });

        it('renders a rules-only row as complete, not degraded', () => {
            // No prose is the normal state for a rules-only diagnosis — it must
            // not read as something missing or broken.
            render(<MistakeDiagnosisCard diagnosis={diagnosis()} revealed />);
            expect(screen.getByText('Loose piece awareness')).toBeInTheDocument();
            expect(screen.queryByText('Next time')).not.toBeInTheDocument();
            expect(screen.queryByText(/unavailable|missing|pending/i)).not.toBeInTheDocument();
        });

        it('still renders no percentage when the model reported confidence', () => {
            const { container } = render(
                <MistakeDiagnosisCard
                    revealed
                    diagnosis={diagnosis({ explanation: 'Loose queen.' })}
                />
            );
            expect(container.textContent).not.toMatch(/\d+%/);
        });
    });

});
