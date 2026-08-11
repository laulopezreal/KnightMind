import { describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { useAsyncData } from './useAsyncData';

/** A promise you resolve by hand, so response ORDER can be controlled exactly. */
function deferred<T>() {
    let resolve!: (value: T) => void;
    let reject!: (err: unknown) => void;
    const promise = new Promise<T>((res, rej) => {
        resolve = res;
        reject = rej;
    });
    return { promise, resolve, reject };
}

describe('useAsyncData', () => {
    it('loads, then exposes the data and clears loading', async () => {
        const { result } = renderHook(() => useAsyncData(async () => 'value', []));

        expect(result.current.loading).toBe(true);
        await waitFor(() => expect(result.current.loading).toBe(false));
        expect(result.current.data).toBe('value');
        expect(result.current.error).toBeNull();
    });

    it('surfaces an Error message, and keeps data null', async () => {
        const { result } = renderHook(() =>
            useAsyncData(async () => {
                throw new Error('the server said no');
            }, []),
        );

        await waitFor(() => expect(result.current.error).toBe('the server said no'));
        expect(result.current.data).toBeNull();
        expect(result.current.loading).toBe(false);
    });

    it('falls back to errorMessage when the thrown value is not an Error', async () => {
        const { result } = renderHook(() =>
            useAsyncData(
                async () => {
                    throw 'a bare string';
                },
                [],
                { errorMessage: 'could not load' },
            ),
        );

        await waitFor(() => expect(result.current.error).toBe('could not load'));
    });

    it('does not fetch when disabled, and settles instead of hanging', async () => {
        const fetcher = vi.fn(async () => 'value');
        const { result } = renderHook(() => useAsyncData(fetcher, [], { enabled: false }));

        await waitFor(() => expect(result.current.loading).toBe(false));
        expect(fetcher).not.toHaveBeenCalled();
        expect(result.current.data).toBeNull();
    });

    it('refetches when deps change', async () => {
        const fetcher = vi.fn(async () => 'x');
        const { result, rerender } = renderHook(({ id }) => useAsyncData(fetcher, [id]), {
            initialProps: { id: 1 },
        });

        await waitFor(() => expect(result.current.loading).toBe(false));
        expect(fetcher).toHaveBeenCalledTimes(1);

        rerender({ id: 2 });
        await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    });

    it('does NOT refetch when only the fetcher identity changes', async () => {
        // The trap this hook exists to avoid: callers pass inline arrows, so a
        // fetcher dependency would refetch every render, forever.
        let calls = 0;
        const { result, rerender } = renderHook(() =>
            useAsyncData(async () => {
                calls += 1;
                return calls;
            }, []),
        );

        await waitFor(() => expect(result.current.loading).toBe(false));
        rerender();
        rerender();
        await waitFor(() => expect(result.current.data).toBe(1));
        expect(calls).toBe(1);
    });

    it('ignores a superseded response that resolves LAST', async () => {
        // The race the eight un-guarded pages had: the username changes, and the
        // slower request for the OLD username resolves after the new one.
        const first = deferred<string>();
        const second = deferred<string>();
        const fetcher = vi
            .fn<(signal: AbortSignal) => Promise<string>>()
            .mockReturnValueOnce(first.promise)
            .mockReturnValueOnce(second.promise);

        const { result, rerender } = renderHook(({ user }) => useAsyncData(fetcher, [user]), {
            initialProps: { user: 'alice' },
        });

        rerender({ user: 'bob' });
        await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));

        // bob resolves first, then alice's slower response arrives.
        await act(async () => {
            second.resolve('bob-data');
        });
        await act(async () => {
            first.resolve('alice-data');
        });

        expect(result.current.data).toBe('bob-data');
    });

    it('ignores a superseded FAILURE, so a dead old request cannot show an error', async () => {
        const first = deferred<string>();
        const second = deferred<string>();
        const fetcher = vi
            .fn<(signal: AbortSignal) => Promise<string>>()
            .mockReturnValueOnce(first.promise)
            .mockReturnValueOnce(second.promise);

        const { result, rerender } = renderHook(({ user }) => useAsyncData(fetcher, [user]), {
            initialProps: { user: 'alice' },
        });
        rerender({ user: 'bob' });
        await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));

        await act(async () => {
            second.resolve('bob-data');
        });
        await act(async () => {
            first.reject(new Error('alice request failed'));
        });

        expect(result.current.error).toBeNull();
        expect(result.current.data).toBe('bob-data');
    });

    it('keeps showing the old data while refreshing, and never re-raises the spinner', async () => {
        // A background refresh must not blank the page it is refreshing.
        const first = deferred<string>();
        const second = deferred<string>();
        const fetcher = vi
            .fn<(signal: AbortSignal) => Promise<string>>()
            .mockReturnValueOnce(first.promise)
            .mockReturnValueOnce(second.promise);

        const { result } = renderHook(() => useAsyncData(fetcher, []));
        await act(async () => {
            first.resolve('original');
        });
        await waitFor(() => expect(result.current.data).toBe('original'));

        act(() => result.current.reload());
        await waitFor(() => expect(result.current.refreshing).toBe(true));
        expect(result.current.loading).toBe(false);
        expect(result.current.data).toBe('original');

        await act(async () => {
            second.resolve('updated');
        });
        await waitFor(() => expect(result.current.data).toBe('updated'));
        expect(result.current.refreshing).toBe(false);
    });

    it('passes an AbortSignal that is aborted when a newer request starts', async () => {
        const signals: AbortSignal[] = [];
        const fetcher = vi.fn(async (signal: AbortSignal) => {
            signals.push(signal);
            return 'v';
        });

        const { rerender } = renderHook(({ id }) => useAsyncData(fetcher, [id]), {
            initialProps: { id: 1 },
        });
        await waitFor(() => expect(signals).toHaveLength(1));
        rerender({ id: 2 });
        await waitFor(() => expect(signals).toHaveLength(2));

        expect(signals[0].aborted).toBe(true);
        expect(signals[1].aborted).toBe(false);
    });

    it('aborts the in-flight request on unmount', async () => {
        // This replaces a test that asserted "no console.error after unmount".
        // React 18 removed the setState-after-unmount warning, so that assertion
        // passed even with the staleness guard deleted entirely -- it could not
        // fail. Aborting the signal is the property that is actually observable.
        let captured: AbortSignal | undefined;
        const pending = deferred<string>();
        const { unmount } = renderHook(() =>
            useAsyncData((signal) => {
                captured = signal;
                return pending.promise;
            }, []),
        );

        await waitFor(() => expect(captured).toBeDefined());
        expect(captured!.aborted).toBe(false);
        unmount();
        expect(captured!.aborted).toBe(true);
    });

    it('discards an in-flight result when `enabled` flips to false', async () => {
        // Disabling begins no new request, so isStale() stays false unless the
        // hook explicitly invalidates. Without that, a user disconnecting their
        // account mid-load gets the old username's data rendered as their own.
        const pending = deferred<string>();
        const fetcher = vi.fn(() => pending.promise);
        const { result, rerender } = renderHook(
            ({ on }) => useAsyncData(fetcher, ['user'], { enabled: on }),
            { initialProps: { on: true } },
        );
        await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));

        rerender({ on: false });
        await act(async () => {
            pending.resolve('data-for-a-disconnected-account');
        });

        expect(result.current.data).toBeNull();
        expect(result.current.loading).toBe(false);
    });


    it('exposes the thrown value, not only its message', async () => {
        // Pages that must distinguish failure KINDS -- Openings tells a 404
        // ("nothing imported yet") from any other error -- would otherwise have
        // to compare message text, which breaks the first time wording changes.
        class NotFound extends Error {
            statusCode = 404;
        }
        const fetcher = vi.fn(() => Promise.reject(new NotFound('no games')));
        const { result } = renderHook(() => useAsyncData(fetcher, ['u']));

        await waitFor(() => expect(result.current.error).toBe('no games'));
        expect(result.current.errorCause).toBeInstanceOf(NotFound);
        expect((result.current.errorCause as NotFound).statusCode).toBe(404);
    });

    it('clears the error on the next attempt by default', async () => {
        const fetcher = vi
            .fn()
            .mockRejectedValueOnce(new Error('boom'))
            .mockImplementationOnce(() => new Promise(() => {}));
        const { result } = renderHook(() => useAsyncData(fetcher, ['u']));
        await waitFor(() => expect(result.current.error).toBe('boom'));

        act(() => result.current.reload());
        // The second attempt never settles; the slot is empty because it STARTED.
        await waitFor(() => expect(result.current.error).toBeNull());
        expect(result.current.errorCause).toBeNull();
    });

    it("with clearErrorOn 'success', keeps the message until something succeeds", async () => {
        // A polled fetch against a down API must not blank its banner on every
        // tick. The message survives the next attempt, and only a success
        // clears it.
        const fetcher = vi
            .fn()
            .mockRejectedValueOnce(new Error('API unreachable'))
            .mockImplementationOnce(() => new Promise(() => {}));
        const { result } = renderHook(() =>
            useAsyncData(fetcher, ['u'], { clearErrorOn: 'success' }),
        );
        await waitFor(() => expect(result.current.error).toBe('API unreachable'));

        act(() => result.current.reload());
        await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
        // Still showing, because nothing has succeeded.
        expect(result.current.error).toBe('API unreachable');
    });

    it("with clearErrorOn 'success', a success does clear it", async () => {
        const fetcher = vi
            .fn()
            .mockRejectedValueOnce(new Error('API unreachable'))
            .mockResolvedValueOnce('recovered');
        const { result } = renderHook(() =>
            useAsyncData(fetcher, ['u'], { clearErrorOn: 'success' }),
        );
        await waitFor(() => expect(result.current.error).toBe('API unreachable'));

        act(() => result.current.reload());
        await waitFor(() => expect(result.current.data).toBe('recovered'));
        expect(result.current.error).toBeNull();
        expect(result.current.errorCause).toBeNull();
    });
});
