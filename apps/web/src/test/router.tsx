import { useEffect } from 'react';
import { render, type RenderOptions, type RenderResult } from '@testing-library/react';
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom';
import type { ReactElement, ReactNode } from 'react';

/**
 * Live view of the router a page is rendered in. Updated after every render,
 * so an assertion sees the current URL rather than the one at mount.
 */
export interface RouterProbe {
  /** Query string including the leading `?`, or `''`. */
  search: string;
  /** Browser Back, for testing that a view can be stepped out of. */
  back: () => void;
}

/**
 * Render a page inside a real router, optionally at a given URL.
 *
 * Pages used to be tested against a hand-written `vi.mock` of
 * react-router-dom listing the two exports they happened to call. That breaks
 * the moment a page calls a third — and for state that lives in the query
 * string, a faked `useSearchParams` would only ever test the fake.
 */
export function renderAt(
  ui: ReactElement,
  url = '/',
  options?: Omit<RenderOptions, 'wrapper'>
): RenderResult & { router: RouterProbe } {
  // Held outside the component so the object a test captured keeps reflecting
  // the router, and so nothing is written to during render.
  const probe: RouterProbe = { search: '', back: () => {} };

  function Probe() {
    const { search } = useLocation();
    const navigate = useNavigate();
    useEffect(() => {
      probe.search = search;
      probe.back = () => navigate(-1);
    });
    return null;
  }

  const wrapper = ({ children }: { children: ReactNode }) => (
    <MemoryRouter initialEntries={[url]}>
      {children}
      <Probe />
    </MemoryRouter>
  );
  return { ...render(ui, { wrapper, ...options }), router: probe };
}
