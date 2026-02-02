import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PuzzleModeProvider, usePuzzleMode } from './PuzzleModeContext';
import { setupMockLocalStorage } from '../test/helpers';

function TestConsumer() {
  const { sessionType, setSessionType, targetAccuracy, setTargetAccuracy, targetTimeMinutes, setTargetTimeMinutes } = usePuzzleMode();
  return (
    <div>
      <span data-testid="session-type">{sessionType}</span>
      <span data-testid="target-accuracy">{targetAccuracy}</span>
      <span data-testid="target-time">{targetTimeMinutes}</span>
      <button onClick={() => setSessionType('timed')}>Set Timed</button>
      <button onClick={() => setSessionType('accuracy_goal')}>Set Accuracy Goal</button>
      <button onClick={() => setSessionType('standard')}>Set Standard</button>
      <button onClick={() => setTargetAccuracy(90)}>Set Accuracy 90</button>
      <button onClick={() => setTargetTimeMinutes(15)}>Set Time 15</button>
    </div>
  );
}

describe('PuzzleModeContext', () => {
  const user = userEvent.setup();

  beforeEach(() => {
    setupMockLocalStorage();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('should provide default values', () => {
    render(
      <PuzzleModeProvider>
        <TestConsumer />
      </PuzzleModeProvider>
    );

    expect(screen.getByTestId('session-type')).toHaveTextContent('standard');
    expect(screen.getByTestId('target-accuracy')).toHaveTextContent('80');
    expect(screen.getByTestId('target-time')).toHaveTextContent('10');
  });

  it('should read stored session type from localStorage', () => {
    localStorage.setItem('knightmind:puzzle_mode', JSON.stringify('timed'));

    render(
      <PuzzleModeProvider>
        <TestConsumer />
      </PuzzleModeProvider>
    );

    expect(screen.getByTestId('session-type')).toHaveTextContent('timed');
  });

  it('should update session type', async () => {
    render(
      <PuzzleModeProvider>
        <TestConsumer />
      </PuzzleModeProvider>
    );

    await user.click(screen.getByText('Set Timed'));
    expect(screen.getByTestId('session-type')).toHaveTextContent('timed');

    await user.click(screen.getByText('Set Accuracy Goal'));
    expect(screen.getByTestId('session-type')).toHaveTextContent('accuracy_goal');

    await user.click(screen.getByText('Set Standard'));
    expect(screen.getByTestId('session-type')).toHaveTextContent('standard');
  });

  it('should update target accuracy', async () => {
    render(
      <PuzzleModeProvider>
        <TestConsumer />
      </PuzzleModeProvider>
    );

    await user.click(screen.getByText('Set Accuracy 90'));
    expect(screen.getByTestId('target-accuracy')).toHaveTextContent('90');
  });

  it('should update target time', async () => {
    render(
      <PuzzleModeProvider>
        <TestConsumer />
      </PuzzleModeProvider>
    );

    await user.click(screen.getByText('Set Time 15'));
    expect(screen.getByTestId('target-time')).toHaveTextContent('15');
  });

  it('should read stored numeric values from localStorage', () => {
    localStorage.setItem('knightmind:target_accuracy', '95');
    localStorage.setItem('knightmind:target_time_minutes', '20');

    render(
      <PuzzleModeProvider>
        <TestConsumer />
      </PuzzleModeProvider>
    );

    expect(screen.getByTestId('target-accuracy')).toHaveTextContent('95');
    expect(screen.getByTestId('target-time')).toHaveTextContent('20');
  });

  it('should throw when usePuzzleMode is used outside provider', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});

    expect(() => render(<TestConsumer />)).toThrow(
      'usePuzzleMode must be used within PuzzleModeProvider'
    );
  });
});
