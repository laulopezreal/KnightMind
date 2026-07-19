import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { DataStateOffline, DataStatePartial, DataStateStale } from './DataState';

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
