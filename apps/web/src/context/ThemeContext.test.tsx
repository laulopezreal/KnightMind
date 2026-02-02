import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ThemeProvider, useTheme } from './ThemeContext';
import { setupMockLocalStorage } from '../test/helpers';

// Mock matchMedia for jsdom
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: query === '(prefers-color-scheme: dark)',
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

function TestConsumer() {
  const { theme, toggleTheme } = useTheme();
  return (
    <div>
      <span data-testid="theme">{theme}</span>
      <button onClick={toggleTheme}>Toggle</button>
    </div>
  );
}

describe('ThemeContext', () => {
  const user = userEvent.setup();

  beforeEach(() => {
    setupMockLocalStorage();
    document.body.className = '';
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('should provide default theme', () => {
    render(
      <ThemeProvider>
        <TestConsumer />
      </ThemeProvider>
    );

    // Default is based on system preference or 'night'
    const theme = screen.getByTestId('theme');
    expect(['night', 'day']).toContain(theme.textContent);
  });

  it('should read stored theme from localStorage', () => {
    localStorage.setItem('knightmind:theme', 'day');

    render(
      <ThemeProvider>
        <TestConsumer />
      </ThemeProvider>
    );

    expect(screen.getByTestId('theme')).toHaveTextContent('day');
  });

  it('should toggle theme', async () => {
    localStorage.setItem('knightmind:theme', 'night');

    render(
      <ThemeProvider>
        <TestConsumer />
      </ThemeProvider>
    );

    expect(screen.getByTestId('theme')).toHaveTextContent('night');

    await user.click(screen.getByText('Toggle'));

    expect(screen.getByTestId('theme')).toHaveTextContent('day');
  });

  it('should persist toggled theme to localStorage', async () => {
    localStorage.setItem('knightmind:theme', 'night');

    render(
      <ThemeProvider>
        <TestConsumer />
      </ThemeProvider>
    );

    await user.click(screen.getByText('Toggle'));

    expect(localStorage.getItem('knightmind:theme')).toBe('day');
  });

  it('should apply theme class to document.body', () => {
    localStorage.setItem('knightmind:theme', 'night');

    render(
      <ThemeProvider>
        <TestConsumer />
      </ThemeProvider>
    );

    expect(document.body.classList.contains('night')).toBe(true);
  });

  it('should not wipe other classes on body when toggling theme', async () => {
    localStorage.setItem('knightmind:theme', 'night');
    document.body.classList.add('extra-class');

    render(
      <ThemeProvider>
        <TestConsumer />
      </ThemeProvider>
    );

    expect(document.body.classList.contains('night')).toBe(true);
    expect(document.body.classList.contains('extra-class')).toBe(true);

    await user.click(screen.getByText('Toggle'));

    expect(document.body.classList.contains('day')).toBe(true);
    expect(document.body.classList.contains('night')).toBe(false);
    expect(document.body.classList.contains('extra-class')).toBe(true);
  });

  it('should throw when useTheme is used outside provider', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});

    expect(() => render(<TestConsumer />)).toThrow(
      'useTheme must be used within ThemeProvider'
    );
  });

  it('should ignore invalid stored theme values', () => {
    localStorage.setItem('knightmind:theme', 'invalid-theme');

    render(
      <ThemeProvider>
        <TestConsumer />
      </ThemeProvider>
    );

    // Should fall back to system preference
    const theme = screen.getByTestId('theme');
    expect(['night', 'day']).toContain(theme.textContent);
  });

  it('should toggle back and forth', async () => {
    localStorage.setItem('knightmind:theme', 'night');

    render(
      <ThemeProvider>
        <TestConsumer />
      </ThemeProvider>
    );

    await user.click(screen.getByText('Toggle'));
    expect(screen.getByTestId('theme')).toHaveTextContent('day');

    await user.click(screen.getByText('Toggle'));
    expect(screen.getByTestId('theme')).toHaveTextContent('night');
  });
});
