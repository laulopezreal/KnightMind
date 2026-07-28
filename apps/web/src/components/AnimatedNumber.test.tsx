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

  it('settles exactly even when the frame clock trails the animation start', async () => {
    // jsdom on a loaded machine hands rAF a timestamp BEHIND the performance.now()
    // read when the effect ran, and one that need never reach the end of the
    // window. Unclamped that drove the eased cubic to absurd values ("-27635%"),
    // and because t stayed < 1 the loop re-armed forever and overwrote the
    // settle timer's correct value on the next frame — so the right number was
    // only ever a ~16ms flicker. Freeze the clock in the past to pin both halves.
    let live = true;
    const frozen = performance.now() - 480;
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
      const id = setTimeout(() => { if (live) cb(frozen); }, 16);
      return id as unknown as number;
    });
    vi.stubGlobal('cancelAnimationFrame', (id: number) => clearTimeout(id));

    const { container } = render(<AnimatedNumber value={80} suffix="%" duration={80} />);
    try {
      // Assert what the user is LEFT looking at, well past the settle at
      // duration+80, rather than whether the right value flickered past.
      await new Promise((r) => setTimeout(r, 400));
      expect(container.textContent).toBe('80%');
    } finally {
      live = false;
    }
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
