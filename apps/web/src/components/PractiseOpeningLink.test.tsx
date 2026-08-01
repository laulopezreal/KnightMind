import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { PractiseOpeningLink } from './PractiseOpeningLink';

const mockGetOpeningPractice = vi.fn();
vi.mock('../api/users', () => ({
    getOpeningPractice: (...a: unknown[]) => mockGetOpeningPractice(...a),
}));

// Fresh object per call: a shared object lets React skip the state update and
// hides re-render bugs.
function practice(overrides: Record<string, unknown> = {}) {
    return {
        username: 'alice',
        opening_name: 'Sicilian Defense: Najdorf Variation',
        opening_family: 'Sicilian Defense',
        line_count: 7,
        family_count: 101,
        scope: 'line',
        ...overrides,
    };
}

function show(openingName: string | null = 'Sicilian Defense: Najdorf Variation') {
    return render(
        <MemoryRouter>
            <PractiseOpeningLink username="alice" openingName={openingName} />
        </MemoryRouter>
    );
}

beforeEach(() => vi.clearAllMocks());

describe('PractiseOpeningLink', () => {
    it('offers the exact line when it has enough puzzles', async () => {
        mockGetOpeningPractice.mockImplementation(() => Promise.resolve(practice()));
        show();

        const link = await screen.findByRole('link', { name: /practise this line \(7\)/i });
        expect(link).toHaveAttribute(
            'href',
            '/library?opening_line=Sicilian%20Defense%3A%20Najdorf%20Variation'
        );
    });

    it('widens to the family and says so when the line is thin', async () => {
        // The honesty requirement: serving 101 Sicilians under a Najdorf label
        // is the mislabelling this whole design exists to avoid.
        mockGetOpeningPractice.mockImplementation(() =>
            Promise.resolve(practice({ scope: 'family', line_count: 2 }))
        );
        show();

        const link = await screen.findByRole('link', {
            name: /practise Sicilian Defense \(101\)/i,
        });
        expect(link).toHaveAttribute('href', '/library?opening=Sicilian%20Defense');
        expect(
            screen.getByText(/only 2 puzzles from this exact line/i)
        ).toBeInTheDocument();
    });

    it('never calls a widened offer "this line"', async () => {
        mockGetOpeningPractice.mockImplementation(() =>
            Promise.resolve(practice({ scope: 'family', line_count: 1 }))
        );
        show();

        await screen.findByRole('link', { name: /practise Sicilian Defense/i });
        expect(screen.queryByText(/practise this line/i)).not.toBeInTheDocument();
    });

    it('uses the singular for a single line puzzle', async () => {
        mockGetOpeningPractice.mockImplementation(() =>
            Promise.resolve(practice({ scope: 'family', line_count: 1 }))
        );
        show();
        expect(
            await screen.findByText(/only 1 puzzle from this exact line/i)
        ).toBeInTheDocument();
    });

    it('renders nothing when there is nothing to practise', async () => {
        // A link to an empty list is worse than no link.
        mockGetOpeningPractice.mockImplementation(() =>
            Promise.resolve(practice({ scope: 'none', line_count: 0, family_count: 0 }))
        );
        const { container } = show();
        await waitFor(() => expect(mockGetOpeningPractice).toHaveBeenCalled());
        expect(container).toBeEmptyDOMElement();
    });

    it('renders nothing for an unnamed node without calling the API', async () => {
        const { container } = show(null);
        expect(container).toBeEmptyDOMElement();
        expect(mockGetOpeningPractice).not.toHaveBeenCalled();
    });

    it('sends the full line, letting the server derive the family', async () => {
        // If this ever sends the family, the split has been re-implemented on
        // the client and the two ends can drift.
        mockGetOpeningPractice.mockImplementation(() => Promise.resolve(practice()));
        show();
        await waitFor(() => expect(mockGetOpeningPractice).toHaveBeenCalled());
        expect(mockGetOpeningPractice).toHaveBeenCalledWith(
            'alice',
            'Sicilian Defense: Najdorf Variation'
        );
    });

    it('drops the previous line’s verdict as soon as the selection changes', async () => {
        // While the new lookup is in flight the old answer is wrong, not merely
        // stale: it would offer "practise this line (7)" for a line the user is
        // no longer looking at.
        mockGetOpeningPractice.mockImplementation(() => Promise.resolve(practice()));
        const { rerender } = show();
        await screen.findByRole('link', { name: /practise this line \(7\)/i });

        mockGetOpeningPractice.mockImplementation(() => new Promise(() => {}));
        rerender(
            <MemoryRouter>
                <PractiseOpeningLink
                    username="alice"
                    openingName="French Defense: Advance Variation"
                />
            </MemoryRouter>
        );

        expect(
            screen.queryByRole('link', { name: /practise this line \(7\)/i })
        ).not.toBeInTheDocument();
    });

    it('survives the lookup failing', async () => {
        mockGetOpeningPractice.mockImplementation(() => Promise.reject(new Error('x')));
        const { container } = show();
        await waitFor(() => expect(mockGetOpeningPractice).toHaveBeenCalled());
        expect(container).toBeEmptyDOMElement();
    });

    it('re-queries when the selected line changes', async () => {
        // Fresh JSON per call, so a second selection genuinely re-renders.
        mockGetOpeningPractice.mockImplementation(() => Promise.resolve(practice()));
        const { rerender } = show();
        await screen.findByRole('link', { name: /practise this line/i });

        mockGetOpeningPractice.mockImplementation(() =>
            Promise.resolve(
                practice({
                    opening_name: 'French Defense: Advance Variation',
                    opening_family: 'French Defense',
                    line_count: 4,
                })
            )
        );
        rerender(
            <MemoryRouter>
                <PractiseOpeningLink
                    username="alice"
                    openingName="French Defense: Advance Variation"
                />
            </MemoryRouter>
        );

        await waitFor(() =>
            expect(
                screen.getByRole('link', { name: /practise this line \(4\)/i })
            ).toHaveAttribute(
                'href',
                '/library?opening_line=French%20Defense%3A%20Advance%20Variation'
            )
        );
    });
});
