/**
 * Per-tab client identifier for job polling observability.
 *
 * Generated once at module load (i.e. once per browser tab lifetime) and
 * stable across every subsequent poll. Changing this between polls would
 * make server-side `client_last_seen_at` gaps meaningless, because the
 * server would see a new tab each time.
 *
 * Uses `crypto.randomUUID()` when available (all modern browsers + secure
 * contexts). Falls back to a Math.random hex string for non-secure contexts
 * (e.g. plain http local dev) — collision probability is negligible for a
 * short-lived per-tab diagnostic id.
 */
export const TAB_CLIENT_ID: string = (() => {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
        return crypto.randomUUID();
    }
    // Fallback: 16 hex bytes (128 bits), same length as a UUID without dashes.
    const bytes = new Uint8Array(16);
    if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
        crypto.getRandomValues(bytes);
    } else {
        for (let i = 0; i < 16; i++) {
            bytes[i] = Math.floor(Math.random() * 256);
        }
    }
    return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
})();
