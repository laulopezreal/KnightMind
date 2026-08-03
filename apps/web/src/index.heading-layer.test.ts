/// <reference types="node" />
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

// The base heading rule must stay inside `@layer base`. Unlayered CSS beats
// layered CSS regardless of specificity, so while this rule sat outside a layer
// it silently defeated Tailwind's `font-sans`/`font-normal` (which land in
// `@layer utilities`) on every heading in the app — `<h2 class="font-sans">`
// rendered Cormorant 500 and the class list gave no hint.
//
// Assert on the source: the failure is invisible to jsdom (no real cascade) and
// to component tests (the classes are present either way), so this file is the
// only place the regression can actually be caught. Read the file directly
// rather than importing it — the Tailwind Vite plugin rewrites the CSS entry, so
// a `?raw` import can come back transformed. cwd is apps/web under vitest.
const css = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8');

/** Extent of the first `@layer <name> {` block, via brace matching. */
function layerBlock(name: string): { start: number; end: number } | null {
    const open = css.search(new RegExp(`@layer\\s+${name}\\s*\\{`));
    if (open === -1) return null;
    let depth = 0;
    for (let i = css.indexOf('{', open); i < css.length; i++) {
        if (css[i] === '{') depth++;
        else if (css[i] === '}' && --depth === 0) return { start: open, end: i };
    }
    return null;
}

describe('base heading typography', () => {
    const headingRule = /h1\s*,\s*h2\s*,\s*h3\s*,\s*h4\s*,\s*h5\s*,\s*h6\s*\{[^}]*\}/;

    it('still declares the serif heading default', () => {
        const match = css.match(headingRule);
        expect(match).not.toBeNull();
        expect(match![0]).toMatch(/font-family:\s*var\(--font-serif\)/);
    });

    it('keeps the h1..h6 rule inside @layer base so font utilities can override it', () => {
        const base = layerBlock('base');
        expect(base, 'index.css should declare an @layer base block').not.toBeNull();

        const at = css.search(headingRule);
        expect(at).toBeGreaterThan(-1);
        // Inside the block, not merely after it.
        expect(at).toBeGreaterThan(base!.start);
        expect(at).toBeLessThan(base!.end);
    });
});
