import { vi } from 'vitest';

/**
 * Create a mock localStorage and install it via vi.stubGlobal.
 * Returns the mock for inspection/assertions. Call in beforeEach.
 */
export function setupMockLocalStorage(): Storage {
  const store: Record<string, string> = {};

  const mock: Storage = {
    getItem: vi.fn((key: string) => store[key] ?? null),
    setItem: vi.fn((key: string, value: string) => {
      store[key] = String(value);
    }),
    removeItem: vi.fn((key: string) => {
      delete store[key];
    }),
    clear: vi.fn(() => {
      Object.keys(store).forEach((key) => delete store[key]);
    }),
    key: vi.fn((index: number) => Object.keys(store)[index] ?? null),
    get length() {
      return Object.keys(store).length;
    },
  };

  vi.stubGlobal('localStorage', mock);
  return mock;
}
