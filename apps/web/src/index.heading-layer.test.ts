/// <reference types="node" />
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { resolve, join } from 'node:path';

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
const cssRaw = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8');

// Comments are stripped before any offset math so that prose mentioning
// `@layer base` (the explanatory comment above the rule does) can never be
// mistaken for the block itself.
const css = cssRaw.replace(/\/\*[\s\S]*?\*\//g, (m) => ' '.repeat(m.length));

/** Extents of every `@layer <name> {` block, via brace matching. */
function layerBlocks(name: string): Array<{ start: number; end: number }> {
    const re = new RegExp(`@layer\\s+${name}\\s*\\{`, 'g');
    const out: Array<{ start: number; end: number }> = [];
    for (let m = re.exec(css); m; m = re.exec(css)) {
        let depth = 0;
        for (let i = css.indexOf('{', m.index); i < css.length; i++) {
            if (css[i] === '{') depth++;
            else if (css[i] === '}' && --depth === 0) {
                out.push({ start: m.index, end: i });
                break;
            }
        }
    }
    return out;
}

describe('base heading typography', () => {
    const headingRule = /h1\s*,\s*h2\s*,\s*h3\s*,\s*h4\s*,\s*h5\s*,\s*h6\s*\{[^}]*\}/;

    it('still declares the serif heading default', () => {
        const match = css.match(headingRule);
        expect(match).not.toBeNull();
        expect(match![0]).toMatch(/font-family:\s*var\(--font-serif\)/);
    });

    it('keeps the h1..h6 rule inside @layer base so font utilities can override it', () => {
        const blocks = layerBlocks('base');
        expect(blocks.length, 'index.css should declare an @layer base block').toBeGreaterThan(0);

        const at = css.search(headingRule);
        expect(at).toBeGreaterThan(-1);
        // Inside *any* base block — a second, unrelated `@layer base` elsewhere
        // in the file is valid CSS and must not fail this test.
        expect(blocks.some((b) => at > b.start && at < b.end)).toBe(true);
    });
});

// --- The other half of the fix -------------------------------------------
//
// Layering alone does not give a sans heading weight 400: Tailwind's `font-sans`
// emits only `font-family`, so the base rule's `font-weight: 500` survives and
// the heading renders Inter 500. Every heading that opts into sans must
// therefore also state a weight, so the choice is visible rather than inherited
// by accident. Swept across the tree so a NEW heading with the old mistake is
// caught too, not just the six that were fixed.
function tsxFiles(dir: string, acc: string[] = []): string[] {
    for (const entry of readdirSync(dir)) {
        const p = join(dir, entry);
        if (statSync(p).isDirectory()) tsxFiles(p, acc);
        else if (entry.endsWith('.tsx') && !entry.includes('.test.')) acc.push(p);
    }
    return acc;
}

describe('sans headings state their weight', () => {
    it('every heading using font-sans also sets an explicit font weight', () => {
        const offenders: string[] = [];
        const tag = /<(h[1-6])\b([^>]*?)>/gs;

        for (const file of tsxFiles(resolve(process.cwd(), 'src'))) {
            const src = readFileSync(file, 'utf8');
            for (let m = tag.exec(src); m; m = tag.exec(src)) {
                const attrs = m[2];
                // `.km-heading-sans` sets family and weight together, so it is fine alone.
                if (!/\bfont-sans\b/.test(attrs) || /\bkm-heading-sans\b/.test(attrs)) continue;
                if (/\bfont-(thin|extralight|light|normal|medium|semibold|bold|extrabold|black)\b/.test(attrs)) continue;
                const line = src.slice(0, m.index).split('\n').length;
                offenders.push(`${file.replace(process.cwd() + '/', '')}:${line} <${m[1]}>`);
            }
        }

        expect(
            offenders,
            'font-sans alone leaves these headings at the base weight 500 — add font-normal (or another explicit weight)',
        ).toEqual([]);
    });
});
