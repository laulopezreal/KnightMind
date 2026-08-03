/// <reference types="node" />
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { resolve, join } from 'node:path';

// The disabled-control treatment lives in index.css. Assert on the source, for
// the same reason as index.reduced-motion.test.ts: jsdom does not run Tailwind,
// so no rendered component can observe the cascade this depends on. Read the
// file directly rather than importing it — the Tailwind Vite plugin rewrites the
// CSS entry, so a `?raw` import can come back transformed. cwd is apps/web.
const css = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8');

const disabledBlock = css.slice(
    css.indexOf('.km-interactive-disabled,'),
    css.indexOf('.km-toggle-option {'),
);

describe('disabled controls are styled by colour, not element opacity', () => {
    it('defines the disabled palette in both themes', () => {
        // A custom property resolves var() where it is *declared*. Declared only
        // in :root, --km-disabled-fg would freeze to the day ink and every
        // disabled control would stay brown in night mode.
        for (const token of ['--km-disabled-fg', '--km-disabled-bg', '--km-disabled-border']) {
            const declarations = css.match(new RegExp(`${token}:`, 'g')) ?? [];
            expect(declarations, `${token} must be declared in :root and body.night`).toHaveLength(2);
        }
        const night = css.slice(css.indexOf('body.night {'));
        expect(night).toContain('--km-disabled-fg:');
    });

    it('never dims a disabled control with element opacity', () => {
        expect(disabledBlock).not.toMatch(/opacity:\s*0?\.\d/);
        // Forces opacity back to 1 so a stray `hover:opacity-90` (which several
        // primary buttons carry unconditionally) cannot re-introduce post-paint
        // compositing on a disabled control.
        expect(disabledBlock).toMatch(/opacity:\s*1/);
    });

    it('keys off the real disabled state, not only the helper class', () => {
        expect(disabledBlock).toContain('button:disabled');
        expect(disabledBlock).toContain("[aria-disabled='true']");
        expect(disabledBlock).toContain('.km-interactive-disabled');
    });

    it('scopes the aria-disabled arm to native control elements', () => {
        // Bare `[aria-disabled='true']` matches ANY element.
        expect(disabledBlock).not.toMatch(/^\s*\[aria-disabled='true'\]\s*[,:{]/m);

        const arm = disabledBlock.match(/:is\(([^)]*)\)\[aria-disabled='true'\]/);
        expect(arm, 'the aria-disabled arm must be scoped with :is(...)').not.toBeNull();

        // Element names only — no role selectors. react-chessboard spreads BOTH
        // role="button" and aria-disabled onto the div wrapping every piece, so
        // a `[role='button']` arm would still paint all 32 pieces the moment a
        // board passed allowDragging={false}. Only the element list prevents it.
        expect(arm![1]).not.toContain('role=');
        expect(arm![1].split(',').map((s) => s.trim())).toEqual([
            'a',
            'button',
            'input',
            'select',
            'textarea',
        ]);
    });

    it('signals disabled without relying on colour alone', () => {
        expect(disabledBlock).toMatch(/cursor:\s*not-allowed/);
        expect(disabledBlock).toMatch(/outline:\s*1px dashed/);
        // Elevation goes with the fill; `shadow-lg shadow-primary/5` on the
        // Check Move button would otherwise keep reading as a raised CTA.
        expect(disabledBlock).toMatch(/box-shadow:\s*none/);
    });

    it('stays unlayered so it can beat the bg-primary utility on primary CTAs', () => {
        // Tailwind emits utilities into @layer utilities, and unlayered CSS beats
        // layered CSS regardless of specificity. Moving this rule into a layer
        // silently restores full-strength ink on every disabled CTA — the exact
        // trap documented on the h1..h6 rule, in reverse.
        //
        // Strip comments before counting braces: the rule's own comment says
        // "@layer utilities", which is enough to fool a text search for the
        // enclosing at-rule. Depth 0 == top level == unlayered.
        const bare = css.replace(/\/\*[\s\S]*?\*\//g, '');
        const start = bare.indexOf('.km-interactive-disabled,');
        expect(start).toBeGreaterThan(-1);
        const depth = [...bare.slice(0, start)].reduce(
            (d, ch) => d + (ch === '{' ? 1 : ch === '}' ? -1 : 0),
            0,
        );
        expect(depth).toBe(0);
    });

    it('does not give a disabled segmented-control option a filled surface', () => {
        // A fill means "selected" in a toggle group, which is the opposite of
        // unavailable. Guards the ratings "Since Session" toggle.
        const toggle = css.slice(css.indexOf('.km-toggle-option:disabled'));
        expect(toggle.slice(0, 260)).toMatch(/background-color:\s*transparent/);
    });
});

describe('no component re-introduces element opacity for disabled state', () => {
    const walk = (dir: string): string[] =>
        readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
            const p = join(dir, e.name);
            return e.isDirectory() ? walk(p) : p.endsWith('.tsx') ? [p] : [];
        });

    const sources = walk(resolve(process.cwd(), 'src'));

    it('finds no disabled:opacity-* utilities anywhere in src', () => {
        // Not merely redundant: such a utility is dead (the stylesheet forces
        // opacity: 1) while still reading as the thing that dims the control, so
        // the class list lies about what renders.
        const offenders = sources.filter((f) => /disabled:opacity-\d/.test(readFileSync(f, 'utf8')));
        expect(offenders).toEqual([]);
    });

    it('scanned a realistic number of component files', () => {
        // A zero-match audit over an empty file list is a false all-clear.
        expect(sources.length).toBeGreaterThan(30);
    });
});
