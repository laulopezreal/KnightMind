import type { TodaysFocus, TodaysFocusResponse } from '../api/users';

/**
 * Resolve URL intent against the server's current, user-scoped focus plan.
 * An absent or mismatched cause is ordinary Standard training, never a label
 * or a session payload value.
 */
export function resolveValidatedNormalFocus(
    requestedCause: string | null,
    response: TodaysFocusResponse,
    expectedUsername?: string,
): TodaysFocus | null {
    if (expectedUsername && response.username !== expectedUsername) return null;
    if (!requestedCause || response.focus?.cause !== requestedCause) return null;
    return response.focus;
}