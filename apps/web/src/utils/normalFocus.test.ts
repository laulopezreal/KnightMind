import { describe, expect, it } from 'vitest';
import { resolveValidatedNormalFocus } from './normalFocus';

const focus = {
    cause: 'loose_piece_awareness',
    name: 'Loose Piece Syndrome',
    description: 'Scan loose pieces.',
    mistakes: 9,
    recent_mistakes: 4,
    accuracy: 0.4,
    priority: 12,
    rationale: '9 diagnosed mistakes.',
};

describe('resolveValidatedNormalFocus', () => {
    it('accepts the current server focus for the requested cause', () => {
        expect(resolveValidatedNormalFocus(focus.cause, {
            username: 'alice', focus, below_threshold: 0, pending: 0,
        })).toEqual(focus);
    });

    it('falls back when the server focus changed or disappeared', () => {
        expect(resolveValidatedNormalFocus(focus.cause, {
            username: 'alice', focus: { ...focus, cause: 'king_safety_blindness' },
            below_threshold: 0, pending: 0,
        })).toBeNull();
        expect(resolveValidatedNormalFocus(focus.cause, {
            username: 'alice', focus: null, below_threshold: 0, pending: 0,
        })).toBeNull();
    });

    it('does not treat an arbitrary URL value as a focus', () => {
        expect(resolveValidatedNormalFocus('arbitrary', {
            username: 'alice', focus, below_threshold: 0, pending: 0,
        })).toBeNull();
    });

    it('rejects a focus response belonging to another user', () => {
        expect(resolveValidatedNormalFocus(focus.cause, {
            username: 'mallory', focus, below_threshold: 0, pending: 0,
        }, 'alice')).toBeNull();
    });
});