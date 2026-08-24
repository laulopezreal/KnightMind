import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { SimilarWeaknessCard } from './SimilarWeaknessCard';
import { type SimilarPuzzlesResponse } from '../api/puzzles';

function renderCard(data: SimilarPuzzlesResponse | null, currentPuzzleId = 'p1') {
    return render(
        <MemoryRouter>
            <SimilarWeaknessCard data={data} currentPuzzleId={currentPuzzleId} />
        </MemoryRouter>
    );
}

const sibling = {
    id: 'p2',
    title: 'Missed the fork', display_name: 'Missed the fork',
    primary_motif: 'hanging_piece',
    difficulty: 'medium' as const,
    swing: 3.2,
    fen: '8/8/8/8/8/8/8/8 w - - 0 1',
    side_to_move: 'white',
    created_at: null,
    attempts: 2,
    fail_count: 2,
};

describe('SimilarWeaknessCard', () => {
    it('renders nothing when there are no siblings', () => {
        const { container } = renderCard({ puzzles: [] });
        expect(container).toBeEmptyDOMElement();
    });

    it('renders nothing when the fetch never resolved', () => {
        const { container } = renderCard(null);
        expect(container).toBeEmptyDOMElement();
    });

    it('never lists the puzzle being viewed', () => {
        const { container } = renderCard(
            { puzzles: [{ ...sibling, id: 'p1' }] },
            'p1'
        );
        expect(container).toBeEmptyDOMElement();
    });

    it('shows the shared reason and links each sibling', () => {
        renderCard({
            cause: 'calculation_stopped_early',
            cause_label: 'calculation stopped early',
            match: 'exact',
            reason: 'Same mistake — calculation stopped early — on a Fork in the middlegame.',
            puzzles: [sibling],
        });

        expect(
            screen.getByRole('heading', { name: /more like this weakness/i })
        ).toBeInTheDocument();
        expect(
            screen.getByText(/same mistake — calculation stopped early/i)
        ).toBeInTheDocument();
        expect(screen.getByRole('link', { name: /missed the fork/i })).toHaveAttribute(
            'href',
            '/library/p2'
        );
    });

    it('humanises the motif key rather than printing the slug', () => {
        // Production motifs are snake_case (hanging_piece, back_rank,
        // mate_threat). The fixture used 'Fork' — a value that occurs nowhere
        // in the data — so a raw slug could never have failed this test.
        renderCard({ puzzles: [sibling] });
        expect(screen.getByText(/Hanging Piece/)).toBeInTheDocument();
        expect(screen.queryByText(/hanging_piece/)).not.toBeInTheDocument();
    });

    it('renders provenance for a sibling with no nickname or motif', () => {
        // The card no longer carries its own 'Untitled position' fallback: the
        // server always sends a non-empty display_name, so a sibling with no
        // nickname shows where it came from instead of a placeholder. That is
        // also what the resolution gate relies on -- a withheld nickname must
        // degrade to provenance, not to filler.
        renderCard({
            puzzles: [{
                ...sibling,
                title: null,
                display_name: '12 Mar · move 18',
                primary_motif: null,
                fail_count: 0,
            }],
        });
        expect(screen.getByText('12 Mar · move 18')).toBeInTheDocument();
    });

    it('does not render a reason line when the server sent none', () => {
        renderCard({ puzzles: [sibling] });
        expect(screen.queryByText(/same mistake/i)).not.toBeInTheDocument();
    });
});
