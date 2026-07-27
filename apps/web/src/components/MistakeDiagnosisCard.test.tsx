import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
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
});
