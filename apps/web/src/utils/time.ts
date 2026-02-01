/**
 * Format a date/time into a human-readable relative time string.
 *
 * @param isoString - ISO 8601 date string
 * @returns Relative time string (e.g., "5m ago", "2h ago", "3d ago")
 */
export const formatRelativeTime = (isoString: string | null): string => {
    if (!isoString) return 'N/A';

    const date = new Date(isoString);
    if (Number.isNaN(date.getTime())) {
        return 'Unknown';
    }

    const deltaMs = Date.now() - date.getTime();
    const minutes = Math.floor(deltaMs / 60000);

    if (minutes < 1) return 'Just now';
    if (minutes < 60) return `${minutes}m ago`;

    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ago`;

    const days = Math.floor(hours / 24);
    if (days < 7) return `${days}d ago`;

    return date.toLocaleDateString();
};
