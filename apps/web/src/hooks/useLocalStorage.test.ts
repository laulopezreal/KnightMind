import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useLocalStorage } from './useLocalStorage';
import { setupMockLocalStorage } from '../test/helpers';

describe('useLocalStorage', () => {
  beforeEach(() => {
    setupMockLocalStorage();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('should return initial value when localStorage is empty', () => {
    const { result } = renderHook(() => useLocalStorage('test-key', 'default'));

    expect(result.current[0]).toBe('default');
  });

  it('should return stored value from localStorage', () => {
    localStorage.setItem('test-key', JSON.stringify('stored-value'));

    const { result } = renderHook(() => useLocalStorage('test-key', 'default'));

    expect(result.current[0]).toBe('stored-value');
  });

  it('should update localStorage when value is set', () => {
    const { result } = renderHook(() => useLocalStorage('test-key', 'default'));

    act(() => {
      result.current[1]('new-value');
    });

    expect(result.current[0]).toBe('new-value');
    expect(localStorage.getItem('test-key')).toBe(JSON.stringify('new-value'));
  });

  it('should handle object values', () => {
    const initial = { foo: 'bar' };
    const { result } = renderHook(() => useLocalStorage('test-key', initial));

    expect(result.current[0]).toEqual(initial);

    act(() => {
      result.current[1]({ foo: 'baz' });
    });

    expect(result.current[0]).toEqual({ foo: 'baz' });
    expect(JSON.parse(localStorage.getItem('test-key')!)).toEqual({ foo: 'baz' });
  });

  it('should handle number values', () => {
    const { result } = renderHook(() => useLocalStorage('test-key', 42));

    act(() => {
      result.current[1](100);
    });

    expect(result.current[0]).toBe(100);
    expect(JSON.parse(localStorage.getItem('test-key')!)).toBe(100);
  });

  it('should use custom parser when provided', () => {
    localStorage.setItem('test-key', '42');

    const { result } = renderHook(() =>
      useLocalStorage('test-key', 0, (value) => parseInt(value, 10))
    );

    expect(result.current[0]).toBe(42);
  });

  it('should JSON-stringify string values consistently', () => {
    const { result } = renderHook(() => useLocalStorage('test-key', 'default'));

    act(() => {
      result.current[1]('hello');
    });

    // String values are JSON.stringified like all other types
    expect(localStorage.getItem('test-key')).toBe(JSON.stringify('hello'));
  });

  it('should return initial value when localStorage getItem throws', () => {
    const mockLS = setupMockLocalStorage();
    (mockLS.getItem as ReturnType<typeof vi.fn>).mockImplementation(() => {
      throw new Error('Storage error');
    });

    const { result } = renderHook(() => useLocalStorage('test-key', 'fallback'));

    expect(result.current[0]).toBe('fallback');
  });

  it('should handle set errors gracefully', () => {
    const mockLS = setupMockLocalStorage();
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    const { result } = renderHook(() => useLocalStorage('test-key', 'default'));

    // Now make setItem throw for the setValue call
    (mockLS.setItem as ReturnType<typeof vi.fn>).mockImplementation(() => {
      throw new Error('Storage full');
    });

    act(() => {
      result.current[1]('new-value');
    });

    // State still updates even if localStorage fails
    expect(result.current[0]).toBe('new-value');
    expect(consoleSpy).toHaveBeenCalled();
    consoleSpy.mockRestore();
  });

  it('should return initial value for null localStorage item', () => {
    localStorage.setItem('other-key', 'value');

    const { result } = renderHook(() => useLocalStorage('missing-key', 'initial'));

    expect(result.current[0]).toBe('initial');
  });

  it('should maintain referential stability of setValue across renders', () => {
    const { result, rerender } = renderHook(() => useLocalStorage('test-key', 'default'));

    const firstSetValue = result.current[1];

    // Trigger a re-render by setting a new value
    act(() => {
      result.current[1]('updated');
    });

    const secondSetValue = result.current[1];
    expect(secondSetValue).toBe(firstSetValue);

    // Re-render the hook itself
    rerender();

    const thirdSetValue = result.current[1];
    expect(thirdSetValue).toBe(firstSetValue);
  });
});
