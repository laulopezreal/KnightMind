/// <reference types="node" />
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

// The global reduced-motion treatment lives in index.css (a11y dim 24). Assert
// on the source so a regression that drops the block is caught, independent of
// any component. Read the file directly (rather than importing it) because the
// Tailwind Vite plugin rewrites the CSS entry, so a `?raw` import can come back
// transformed/empty. cwd is apps/web under vitest.
const css = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8');

describe('global prefers-reduced-motion treatment', () => {
    it('declares a prefers-reduced-motion: reduce media query', () => {
        expect(css).toMatch(/@media\s*\(prefers-reduced-motion:\s*reduce\)/);
    });

    it('reduces transition and animation durations app-wide under reduced motion', () => {
        const block = css.slice(css.indexOf('prefers-reduced-motion'));
        // Applies to every element (universal selector) so Tailwind transition-*
        // utilities and the teedin/switchedin keyframes are all covered.
        expect(block).toMatch(/\*\s*,/);
        expect(block).toMatch(/transition-duration:\s*0\.01ms\s*!important/);
        expect(block).toMatch(/animation-duration:\s*0\.01ms\s*!important/);
    });
});
