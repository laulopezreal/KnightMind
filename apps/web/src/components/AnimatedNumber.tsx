import { useEffect, useRef, useState } from 'react';

interface AnimatedNumberProps {
  value: number;
  /** Animation length in ms. */
  duration?: number;
  /** Rendered after the number, inside the same text node (e.g. "%"). */
  suffix?: string;
}

/**
 * Count-up number for result moments (session summary stats). Eases from 0 to
 * `value` once on mount; renders the final value immediately when the user
 * prefers reduced motion, when rAF is unavailable (jsdom), or when the value
 * is 0. Purely presentational — the DOM settles on the exact final value, so
 * tests can `findByText` the real number.
 */
export function AnimatedNumber({ value, duration = 700, suffix = '' }: AnimatedNumberProps) {
  const reduceMotion =
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const animatable =
    !reduceMotion && value > 0 && typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function';

  const [display, setDisplay] = useState(animatable ? 0 : value);
  const rafRef = useRef<number | null>(null);

  // Track prop changes in render (not via a sync effect setState): a new value
  // restarts from 0 when animatable, or snaps directly when not.
  const [prevValue, setPrevValue] = useState(value);
  if (value !== prevValue) {
    setPrevValue(value);
    setDisplay(animatable ? 0 : value);
  }

  useEffect(() => {
    if (!animatable) return;
    const start = performance.now();
    const tick = (now: number) => {
      // A frame timestamp can trail the `start` just read from performance.now()
      // — jsdom under load does exactly that. Unclamped, a negative t sends the
      // eased cubic far outside 0..1 (at t = -6 the multiplier is -342, which
      // rendered a session stat as "-27635%"). Clamping costs a lagging clock a
      // few frames parked at 0; it can never show a number the user's data
      // doesn't support.
      const t = Math.min(Math.max((now - start) / duration, 0), 1);
      const eased = 1 - Math.pow(1 - t, 3); // ease-out cubic
      setDisplay(Math.round(eased * value));
      rafRef.current = t < 1 ? requestAnimationFrame(tick) : null;
    };
    rafRef.current = requestAnimationFrame(tick);
    // Settle guarantee: rAF can be throttled, never fire (hidden tabs, headless
    // jsdom in CI), or report a clock that never reaches the end of the window.
    // A plain timer snaps to the exact final value shortly after the animation
    // window — and must STOP the frame loop to do it, because a t that never
    // reaches 1 keeps re-arming and would overwrite the settled value on the
    // very next frame.
    const settle = setTimeout(() => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
      setDisplay(value);
    }, duration + 80);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
      clearTimeout(settle);
    };
  }, [value, duration, animatable]);

  return <span>{display}{suffix}</span>;
}
