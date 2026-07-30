import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { MistakePatternsCard } from './MistakePatternsCard';
import type { MistakePattern, MistakePatternsResponse } from '../api/users';

function pattern(overrides: Partial<MistakePattern> = {}): MistakePattern {
    return {
        cause: 'loose_piece_awareness',
        name: 'Loose Piece Syndrome',
        description: 'You calculate your own threat first and skip the scan.',
        mistakes: 8,
        recent_mistakes: 3,
        dominant_phase: 'middlegame',
        accuracy: 0.4,
        priority: 14.4,
        ...overrides,
    };
}

function data(overrides: Partial<MistakePatternsResponse> = {}): MistakePatternsResponse {
    return {
        username: 'testplayer',
        patterns: [pattern()],
        below_threshold: 0,
        pending: 0,
        ...overrides,
    };
}

function show(payload: MistakePatternsResponse) {
    return render(
        <MemoryRouter>
            <MistakePatternsCard data={payload} />
        </MemoryRouter>
    );
}

describe('MistakePatternsCard', () => {
    it('names the habit and says what to change', () => {
        show(data());
        expect(screen.getByText('Loose Piece Syndrome')).toBeInTheDocument();
        expect(screen.getByText(/skip the scan/i)).toBeInTheDocument();
        expect(screen.getByText('8 times')).toBeInTheDocument();
        expect(screen.getByText('3 in recent games')).toBeInTheDocument();
    });

    it('links to training filtered by the cause', () => {
        show(data());
        expect(screen.getByRole('link', { name: /train this pattern/i })).toHaveAttribute(
            'href',
            '/library?cause=loose_piece_awareness'
        );
    });

    it('omits the recency line when nothing is recent', () => {
        // A habit that stopped showing up should not claim it is current.
        show(data({ patterns: [pattern({ recent_mistakes: 0 })] }));
        expect(screen.queryByText(/in recent games/i)).not.toBeInTheDocument();
    });

    it('summarises causes that have not recurred without naming them', () => {
        show(data({ patterns: [], below_threshold: 3 }));
        expect(
            screen.getByText(/3 other causes seen too few times/i)
        ).toBeInTheDocument();
        expect(screen.queryByText(/syndrome/i)).not.toBeInTheDocument();
    });

    it('distinguishes still-analysing from no-habit-yet', () => {
        show(data({ patterns: [], pending: 12 }));
        expect(screen.getByText(/still analysing/i)).toBeInTheDocument();

        show(data({ patterns: [], pending: 0, below_threshold: 1 }));
        expect(screen.getByText(/no habit has recurred/i)).toBeInTheDocument();
    });

    it('renders nothing at all when there is nothing to say', () => {
        // An empty card on a fresh account is noise, not information.
        const { container } = show(
            data({ patterns: [], below_threshold: 0, pending: 0 })
        );
        expect(container).toBeEmptyDOMElement();
    });

    it('never renders the priority score', () => {
        // It orders one person's patterns against each other and means nothing
        // on its own — showing it would invite reading it as a severity.
        const { container } = show(data());
        expect(container.textContent).not.toContain('14.4');
    });

    it('is a labelled landmark', () => {
        show(data());
        expect(
            screen.getByRole('region', { name: /your patterns/i })
        ).toBeInTheDocument();
    });
});
