import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PuzzleModeProvider, usePuzzleMode } from './PuzzleModeContext';
import { setupMockLocalStorage } from '../test/helpers';

function TestConsumer() {
  const { sessionType } = usePuzzleMode();
  return (
    <div>
      <span data-testid="session-type">{sessionType}</span>
    </div>
  );
}

describe('PuzzleModeContext', () => {
  beforeEach(() => {
    setupMockLocalStorage();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('provides Standard as the only available mode', () => {
    render(
      <PuzzleModeProvider>
        <TestConsumer />
      </PuzzleModeProvider>
    );

    expect(screen.getByTestId('session-type')).toHaveTextContent('standard');
  });

  it.each([
    ['legacy timed mode', JSON.stringify('timed')],
    ['legacy accuracy mode', JSON.stringify('accuracy_goal')],
    ['unknown mode', JSON.stringify('blitz')],
    ['JSON null', 'null'],
    ['null-like string', JSON.stringify('null')],
    ['malformed value', '{not-json'],
  ])('normalizes %s to a usable Standard flow', (_label, storedValue) => {
    localStorage.setItem('knightmind:puzzle_mode', storedValue);

    render(
      <PuzzleModeProvider>
        <TestConsumer />
      </PuzzleModeProvider>
    );

    expect(screen.getByTestId('session-type')).toHaveTextContent('standard');
  });

  it('should throw when usePuzzleMode is used outside provider', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});

    expect(() => render(<TestConsumer />)).toThrow(
      'usePuzzleMode must be used within PuzzleModeProvider'
    );
  });
});
