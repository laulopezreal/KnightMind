/**
 * Format a date/time into a human-readable relative time string.
 * Handles both past dates ("5m ago") and future dates ("5m").
 *
 * @param isoString - ISO 8601 date string
 * @returns Relative time string (e.g., "5m ago", "2h ago", "3d ago", "5m", "2h")
 */
export const formatRelativeTime = (isoString: string | null): string => {
    if (!isoString) return 'N/A';

    const date = new Date(isoString);
    if (Number.isNaN(date.getTime())) {
        return 'Unknown';
    }

    const deltaMs = date.getTime() - Date.now();
    const isPast = deltaMs < 0;
    const absDeltaMs = Math.abs(deltaMs);

    const minutes = Math.floor(absDeltaMs / 60000);

    if (minutes < 1) {
        return isPast ? 'Just now' : 'soon';
    }

    let timeString;
    if (minutes < 60) {
        timeString = `${minutes}m`;
    } else {
        const hours = Math.floor(minutes / 60);
        if (hours < 24) {
            timeString = `${hours}h`;
        } else {
            const days = Math.floor(hours / 24);
            if (days < 7) {
                timeString = `${days}d`;
            } else {
                return date.toLocaleDateString();
            }
        }
    }

    return isPast ? `${timeString} ago` : timeString;
};
