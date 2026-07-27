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
      const t = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - t, 3); // ease-out cubic
      setDisplay(Math.round(eased * value));
      if (t < 1) rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    // Settle guarantee: rAF can be throttled or never fire (hidden tabs,
    // headless jsdom in CI). A plain timer snaps to the exact final value
    // shortly after the animation window no matter what rAF did.
    const settle = setTimeout(() => setDisplay(value), duration + 80);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
      clearTimeout(settle);
    };
  }, [value, duration, animatable]);

  return <span>{display}{suffix}</span>;
}
