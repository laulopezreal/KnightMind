import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AnimatedNumber } from './AnimatedNumber';

afterEach(() => vi.unstubAllGlobals());

describe('AnimatedNumber', () => {
  it('settles on the exact final value (with suffix)', async () => {
    render(<AnimatedNumber value={80} suffix="%" duration={80} />);
    expect(await screen.findByText('80%')).toBeInTheDocument();
  });

  it('renders 0 immediately without animating', () => {
    render(<AnimatedNumber value={0} />);
    expect(screen.getByText('0')).toBeInTheDocument();
  });

  it('renders the final value immediately when the user prefers reduced motion', () => {
    vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({
      matches: true,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));

    render(<AnimatedNumber value={42} />);
    // No count-up frames: the very first paint is the final number.
    expect(screen.getByText('42')).toBeInTheDocument();
  });
});
