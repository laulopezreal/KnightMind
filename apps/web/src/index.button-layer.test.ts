/// <reference types="node" />
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { resolve, join } from 'node:path';

// Sibling of index.heading-layer.test.ts, same trap one element down. The base
// `button` rule must stay inside `@layer base`: Tailwind v4 emits utilities into
// `@layer utilities`, and unlayered CSS beats layered CSS regardless of
// specificity, so while this rule sat outside a layer every font utility on
// every `<button>` in the app was a silent no-op.
//
// The button rule declares three properties, so it shadowed three families of
// utility rather than one — `font-sans` (family), `font-light`/`font-normal`
// (weight) and `tracking-*` (letter-spacing). Measured on /engine before the
// fix: buttons asking for `font-sans tracking-widest` rendered Cormorant
// Garamond at 0.6px, while the `<label>` beside them carrying an identical
// class list rendered Inter at 1.2px.
//
// Assert on the source. The failure is invisible to jsdom (no real cascade) and
// to component tests (the classes are present either way), so this file is the
// only place the regression can be caught. Read the file directly rather than
// importing it — the Tailwind Vite plugin rewrites the CSS entry, so a `?raw`
// import can come back transformed. cwd is apps/web under vitest.
const cssRaw = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8');

// Strip comments before any offset math: the prose above the rule in index.css
// mentions `@layer base`, and must not be mistaken for the block itself.
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

/** The base `button { ... }` rule — the bare element selector, not `.foo button`. */
const buttonRule = /(^|[};{])\s*button\s*\{[^}]*\}/m;

describe('base button typography', () => {
    it('still declares the serif button defaults', () => {
        const match = css.match(buttonRule);
        expect(match).not.toBeNull();
        expect(match![0]).toMatch(/font-family:\s*var\(--font-serif\)/);
        expect(match![0]).toMatch(/font-weight:\s*500/);
        expect(match![0]).toMatch(/letter-spacing:\s*0\.05em/);
    });

    it('keeps the button rule inside @layer base so font utilities can override it', () => {
        const blocks = layerBlocks('base');
        expect(blocks.length, 'index.css should declare an @layer base block').toBeGreaterThan(0);

        const at = css.search(buttonRule);
        expect(at).toBeGreaterThan(-1);
        // Inside *any* base block — a second, unrelated `@layer base` elsewhere
        // in the file is valid CSS and must not fail this test.
        expect(blocks.some((b) => at > b.start && at < b.end)).toBe(true);
    });
});

// --- The other half of the fix -------------------------------------------
//
// Layering alone does not give a sans button weight 400: Tailwind's `font-sans`
// emits only `font-family`, so the base rule's `font-weight: 500` survives and
// the button renders Inter 500 — heavier than the `<label>` or `<p>` it sits
// beside. Every button that opts into sans must therefore also state a weight,
// so the choice is visible rather than inherited by accident. Swept across the
// tree so a NEW button with the old mistake is caught too.
function tsxFiles(dir: string, acc: string[] = []): string[] {
    for (const entry of readdirSync(dir)) {
        const p = join(dir, entry);
        if (statSync(p).isDirectory()) tsxFiles(p, acc);
        else if (entry.endsWith('.tsx') && !entry.includes('.test.')) acc.push(p);
    }
    return acc;
}

/**
 * Blank out comments, preserving length and newlines so byte offsets (and so
 * reported line numbers) stay valid. JSX permits comments in attribute
 * position, and one containing a `>` — a prose mention of `<nav>` is enough —
 * otherwise ends the tag scan early and hides the rest of the class list. The
 * `[^:]` guard keeps `https://` in an href from being read as a comment.
 */
function stripComments(src: string): string {
    return src
        .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
        .replace(/([^:])\/\/[^\n]*/g, (m, keep) => keep + ' '.repeat(m.length - 1));
}

/**
 * Opening `<button ...>` tags. Scanned by brace depth rather than matched with
 * `[^>]*?>`, because these tags routinely contain `onClick={() => ...}` and a
 * naive matcher stops at the `>` of the arrow, truncating the class list.
 */
function buttonTags(srcRaw: string): Array<{ attrs: string; index: number }> {
    const src = stripComments(srcRaw);
    const out: Array<{ attrs: string; index: number }> = [];
    for (let m = /<button\b/g.exec(src); m; ) {
        const start = m.index;
        let depth = 0;
        let i = start;
        for (; i < src.length; i++) {
            const c = src[i];
            if (c === '{') depth++;
            else if (c === '}') depth--;
            else if (c === '>' && depth === 0) break;
        }
        out.push({ attrs: src.slice(start, i), index: start });
        const re = /<button\b/g;
        re.lastIndex = i;
        m = re.exec(src);
    }
    return out;
}

const EXPLICIT_WEIGHT =
    /\bfont-(thin|extralight|light|normal|medium|semibold|bold|extrabold|black)\b/;

describe('sans buttons state their weight', () => {
    it('every button using font-sans also sets an explicit font weight', () => {
        const offenders: string[] = [];

        for (const file of tsxFiles(resolve(process.cwd(), 'src'))) {
            const src = readFileSync(file, 'utf8');
            for (const { attrs, index } of buttonTags(src)) {
                if (!/\bfont-sans\b/.test(attrs)) continue;
                if (EXPLICIT_WEIGHT.test(attrs)) continue;
                const line = src.slice(0, index).split('\n').length;
                offenders.push(`${file.replace(process.cwd() + '/', '')}:${line}`);
            }
        }

        expect(
            offenders,
            'font-sans alone leaves these buttons at the base weight 500 — add font-normal (or another explicit weight)',
        ).toEqual([]);
    });
});
