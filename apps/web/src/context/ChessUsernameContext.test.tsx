import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ChessUsernameProvider, useChessUsername } from './ChessUsernameContext';
import { setupMockLocalStorage } from '../test/helpers';

function TestConsumer() {
  const { username, setUsername, isLoading, isEditorOpen, setEditorOpen } = useChessUsername();
  return (
    <div>
      <span data-testid="username">{username}</span>
      <span data-testid="loading">{String(isLoading)}</span>
      <span data-testid="editor-open">{String(isEditorOpen)}</span>
      <button onClick={() => setUsername('testuser')}>Set Username</button>
      <button onClick={() => setUsername('')}>Clear Username</button>
      <button onClick={() => setEditorOpen(true)}>Open Editor</button>
      <button onClick={() => setEditorOpen(false)}>Close Editor</button>
    </div>
  );
}

describe('ChessUsernameContext', () => {
  const user = userEvent.setup();

  beforeEach(() => {
    setupMockLocalStorage();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('should provide empty username by default', () => {
    render(
      <ChessUsernameProvider>
        <TestConsumer />
      </ChessUsernameProvider>
    );

    expect(screen.getByTestId('username')).toHaveTextContent('');
  });

  it('should read stored username from localStorage', () => {
    localStorage.setItem('knightmind:chesscom_username', 'storeduser');

    render(
      <ChessUsernameProvider>
        <TestConsumer />
      </ChessUsernameProvider>
    );

    expect(screen.getByTestId('username')).toHaveTextContent('storeduser');
  });

  it('should set username and persist to localStorage', async () => {
    render(
      <ChessUsernameProvider>
        <TestConsumer />
      </ChessUsernameProvider>
    );

    await user.click(screen.getByText('Set Username'));

    expect(screen.getByTestId('username')).toHaveTextContent('testuser');
    expect(localStorage.getItem('knightmind:chesscom_username')).toBe('testuser');
  });

  it('should clear username and remove from localStorage', async () => {
    localStorage.setItem('knightmind:chesscom_username', 'existinguser');

    render(
      <ChessUsernameProvider>
        <TestConsumer />
      </ChessUsernameProvider>
    );

    await user.click(screen.getByText('Clear Username'));

    expect(screen.getByTestId('username')).toHaveTextContent('');
    expect(localStorage.getItem('knightmind:chesscom_username')).toBeNull();
  });

  it('should manage editor open state', async () => {
    render(
      <ChessUsernameProvider>
        <TestConsumer />
      </ChessUsernameProvider>
    );

    expect(screen.getByTestId('editor-open')).toHaveTextContent('false');

    await user.click(screen.getByText('Open Editor'));
    expect(screen.getByTestId('editor-open')).toHaveTextContent('true');

    await user.click(screen.getByText('Close Editor'));
    expect(screen.getByTestId('editor-open')).toHaveTextContent('false');
  });

  it('should not be loading by default', () => {
    render(
      <ChessUsernameProvider>
        <TestConsumer />
      </ChessUsernameProvider>
    );

    expect(screen.getByTestId('loading')).toHaveTextContent('false');
  });

  it('should throw when useChessUsername is used outside provider', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});

    expect(() => render(<TestConsumer />)).toThrow(
      'useChessUsername must be used within a ChessUsernameProvider'
    );
  });
});
