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
    title: 'Missed the fork',
    primary_motif: 'Fork',
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

    it('survives a sibling with no title or motif', () => {
        renderCard({
            puzzles: [{ ...sibling, title: null, primary_motif: null, fail_count: 0 }],
        });
        expect(screen.getByText('Untitled position')).toBeInTheDocument();
    });

    it('does not render a reason line when the server sent none', () => {
        renderCard({ puzzles: [sibling] });
        expect(screen.queryByText(/same mistake/i)).not.toBeInTheDocument();
    });
});
