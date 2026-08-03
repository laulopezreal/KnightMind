import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import {
    DataStateEmpty,
    DataStateError,
    DataStateOffline,
    DataStatePartial,
    DataStateStale,
} from './DataState';

describe('DataStateError', () => {
    it('breaks long unbroken words so server text cannot escape the panel', () => {
        // The message can be backend text. Measured in a browser at 375px, an
        // exception string containing a long URL painted ~38px outside this
        // panel and left the whole page scrolling sideways.
        render(
            <DataStateError
                message="HTTPSConnectionPool(host='api.chess.com',port=443):/pub/player/hikaru/games/2026/07?include_archived=true"
                onRetry={() => {}}
                retryLabel="Retry"
                ariaLabel="Retry"
            />,
        );
        const message = screen.getByRole('alert').querySelector('p');
        expect(message).toHaveClass('break-words');
    });
});

describe('DataStateEmpty', () => {
    const renderEmpty = (onAction = vi.fn()) =>
        render(
            <DataStateEmpty
                title="No games imported yet"
                description="Import your Chess.com games to chart the openings you play."
                actionLabel="Import games"
                onAction={onAction}
            />,
        );

    it('exposes the title as a level-2 heading, not just styled text', () => {
        renderEmpty();

        // Every call site sits directly under a PageHeader <h1>, so h2 is the
        // right level and nothing is skipped. As a <p> this title was missing
        // from the heading map entirely.
        expect(
            screen.getByRole('heading', { level: 2, name: 'No games imported yet' }),
        ).toBeInTheDocument();
    });

    it('leaves the description as body text', () => {
        renderEmpty();

        // Only the title is a heading — promoting the explanation too would
        // clutter the heading map with a full sentence.
        expect(screen.getAllByRole('heading')).toHaveLength(1);
        expect(screen.getByText(/Import your Chess\.com games/)).toBeInTheDocument();
    });

    it('invokes the action handler', () => {
        const onAction = vi.fn();
        renderEmpty(onAction);

        fireEvent.click(screen.getByRole('button', { name: 'Import games' }));

        expect(onAction).toHaveBeenCalledTimes(1);
    });
});

describe('DataStateOffline', () => {
    it('announces the offline state with an alert role and a non-colour text cue', () => {
        render(<DataStateOffline />);
        const alert = screen.getByRole('alert');
        expect(alert).toHaveTextContent(/offline/i);
        // The state is conveyed by a text label ("Offline"), not colour alone.
        expect(alert).toHaveTextContent(/⚠ Offline/);
    });

    it('invokes the retry handler', () => {
        const onRetry = vi.fn();
        render(<DataStateOffline onRetry={onRetry} retryLabel="Try again" />);
        fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
        expect(onRetry).toHaveBeenCalledTimes(1);
    });
});

describe('DataStatePartial', () => {
    it('renders successful children plus a non-blocking status banner', () => {
        render(
            <DataStatePartial message="Insights are unavailable.">
                <p>Primary content</p>
            </DataStatePartial>,
        );
        expect(screen.getByText('Primary content')).toBeInTheDocument();
        const status = screen.getByRole('status');
        expect(status).toHaveTextContent(/Partial data/);
        expect(status).toHaveTextContent(/Insights are unavailable/);
    });

    it('shows and wires a retry affordance when provided', () => {
        const onRetry = vi.fn();
        render(
            <DataStatePartial message="x" onRetry={onRetry} retryLabel="Reload">
                <span>ok</span>
            </DataStatePartial>,
        );
        fireEvent.click(screen.getByRole('button', { name: 'Reload' }));
        expect(onRetry).toHaveBeenCalledTimes(1);
    });
});

describe('DataStateStale', () => {
    it('shows older data with a status role and a text staleness cue', () => {
        render(
            <DataStateStale message="Last updated a while ago.">
                <p>Cached content</p>
            </DataStateStale>,
        );
        expect(screen.getByText('Cached content')).toBeInTheDocument();
        const status = screen.getByRole('status');
        expect(status).toHaveTextContent(/Showing older data/);
    });
});
