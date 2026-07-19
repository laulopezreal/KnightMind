import { describe, it, expect } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useLatestRequest } from './useLatestRequest';

describe('useLatestRequest', () => {
    it('marks an earlier request stale once a newer one begins, and aborts it', () => {
        const { result } = renderHook(() => useLatestRequest());

        const first = result.current.begin();
        expect(first.isStale()).toBe(false);
        expect(first.signal.aborted).toBe(false);

        const second = result.current.begin();
        // Starting a newer request supersedes and aborts the first.
        expect(first.isStale()).toBe(true);
        expect(first.signal.aborted).toBe(true);
        expect(second.isStale()).toBe(false);
        expect(second.signal.aborted).toBe(false);
    });

    it('aborts the in-flight request on unmount', () => {
        const { result, unmount } = renderHook(() => useLatestRequest());
        const token = result.current.begin();
        expect(token.signal.aborted).toBe(false);
        unmount();
        expect(token.signal.aborted).toBe(true);
    });
});
