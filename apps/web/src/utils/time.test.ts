import { describe, it, expect, vi, afterEach } from 'vitest';
import { formatRelativeTime } from './time';

describe('formatRelativeTime', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('should return "N/A" for null input', () => {
    expect(formatRelativeTime(null)).toBe('N/A');
  });

  it('should return "N/A" for empty string', () => {
    expect(formatRelativeTime('')).toBe('N/A');
  });

  it('should return "Unknown" for invalid date strings', () => {
    expect(formatRelativeTime('not-a-date')).toBe('Unknown');
    expect(formatRelativeTime('abc123')).toBe('Unknown');
  });

  it('should return "Just now" for very recent past dates', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2025-01-15T12:00:30Z'));

    // 30 seconds ago is past, but < 1 minute
    expect(formatRelativeTime('2025-01-15T12:00:00Z')).toBe('Just now');
    // 1 second ago
    expect(formatRelativeTime('2025-01-15T12:00:29Z')).toBe('Just now');
  });

  it('should return "soon" for very near future dates', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2025-01-15T12:00:00Z'));

    expect(formatRelativeTime('2025-01-15T12:00:30Z')).toBe('soon');
  });

  it('should format minutes ago', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2025-01-15T12:00:00Z'));

    expect(formatRelativeTime('2025-01-15T11:55:00Z')).toBe('5m ago');
    expect(formatRelativeTime('2025-01-15T11:30:00Z')).toBe('30m ago');
    expect(formatRelativeTime('2025-01-15T11:01:00Z')).toBe('59m ago');
  });

  it('should format hours ago', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2025-01-15T12:00:00Z'));

    expect(formatRelativeTime('2025-01-15T10:00:00Z')).toBe('2h ago');
    expect(formatRelativeTime('2025-01-14T13:00:00Z')).toBe('23h ago');
  });

  it('should format days ago', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2025-01-15T12:00:00Z'));

    expect(formatRelativeTime('2025-01-14T12:00:00Z')).toBe('1d ago');
    expect(formatRelativeTime('2025-01-09T12:00:00Z')).toBe('6d ago');
  });

  it('should return localized date for dates older than 7 days', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2025-01-15T12:00:00Z'));

    const result = formatRelativeTime('2025-01-01T12:00:00Z');
    // Should be a date string, not a relative time
    expect(result).not.toContain('ago');
    expect(result).not.toBe('N/A');
    expect(result).not.toBe('Unknown');
  });

  it('should format future times without "ago"', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2025-01-15T12:00:00Z'));

    expect(formatRelativeTime('2025-01-15T12:05:00Z')).toBe('5m');
    expect(formatRelativeTime('2025-01-15T14:00:00Z')).toBe('2h');
    expect(formatRelativeTime('2025-01-16T12:00:00Z')).toBe('1d');
  });
});
