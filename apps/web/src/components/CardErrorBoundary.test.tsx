import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { useEffect } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CardErrorBoundary } from './CardErrorBoundary';

// The message a real crash produced (#321: an unrecognised confidence level
// reaching `badge.color`), so the exposure assertions below are about the
// string a user would actually have seen.
const RAW = "Cannot read properties of undefined (reading 'color')";

let shouldThrow = true;
let mountCount = 0;

function CrashyTile() {
  // Declared before the throw so hook order is stable; effects never run on a
  // render that throws, which is what makes this a mount counter.
  useEffect(() => {
    mountCount += 1;
  }, []);
  if (shouldThrow) throw new Error(RAW);
  return <p>Tile content</p>;
}

function Tiles() {
  return (
    <div>
      <CardErrorBoundary label="Momentum">
        <CrashyTile />
      </CardErrorBoundary>
      <CardErrorBoundary label="Consistency">
        <p>Consistency card</p>
      </CardErrorBoundary>
    </div>
  );
}

describe('CardErrorBoundary', () => {
  let user: ReturnType<typeof userEvent.setup>;

  beforeEach(() => {
    user = userEvent.setup();
    // React logs the caught error itself; keep the run readable.
    vi.spyOn(console, 'error').mockImplementation(() => {});
    shouldThrow = true;
    mountCount = 0;
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it('renders the tile when nothing throws', () => {
    shouldThrow = false;
    render(<Tiles />);

    expect(screen.getByText('Tile content')).toBeInTheDocument();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('drops only the tile that threw, keeping its siblings', () => {
    render(<Tiles />);

    const fallback = screen.getByRole('status');
    expect(fallback).toHaveTextContent('Momentum');
    expect(fallback).toHaveTextContent(/couldn’t be displayed/);
    expect(screen.getByText('Consistency card')).toBeInTheDocument();
  });

  it('names the tile in the retry control and remounts it on click', async () => {
    render(<Tiles />);
    expect(mountCount).toBe(0);

    shouldThrow = false;
    await user.click(screen.getByRole('button', { name: /try loading momentum again/i }));

    expect(screen.getByText('Tile content')).toBeInTheDocument();
    // A repaint would leave this at 0: React unmounts the subtree when the
    // boundary catches, so retry is a genuine fresh mount and refetch.
    expect(mountCount).toBe(1);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('holds the raw message behind a dev-only disclosure', () => {
    vi.stubEnv('DEV', true);
    render(<Tiles />);

    const details = screen.getByText(RAW).closest('details');
    expect(details).not.toBeNull();
    expect(details).not.toHaveAttribute('open');
  });

  it('does not paint the raw message in a production build', () => {
    vi.stubEnv('DEV', false);
    render(<Tiles />);

    expect(screen.queryByText(RAW)).not.toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent(/couldn’t be displayed/);
  });
});
