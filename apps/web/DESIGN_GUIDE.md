# KnightMind Design Guide

All UI changes must follow this guide to maintain the "Chess Intelligence" aesthetic: **Minimal, Calm, Intellectual, Premium.**

## Core Rules

1.  **Do not introduce new UI libraries.** Use standard HTML/React + Tailwind classes defined below.
2.  **Do not add new fonts.** Use `font-serif` (Cormorant Garamond) for headings/action/personality and `font-sans` (Inter) for UI/data/body.
3.  **Do not use random colors.** Use semantic variables (`text-primary`, `bg-primary`) or `chess-brown`/`chess-cream` tokens.

## Components & Patterns

### Buttons

**Primary Action** (Generate, Save, Submit)
```tsx
<button className="px-6 py-2 bg-primary text-bg-primary hover:opacity-90 rounded-sm font-serif transition-colors disabled:opacity-50">
  Action
</button>
```

**Secondary Action** (Cancel, Load, Outline)
```tsx
<button className="px-6 py-2 border border-primary/20 text-primary hover:bg-primary hover:text-bg-primary hover:border-transparent rounded-sm font-serif transition-all disabled:opacity-50">
  Secondary
</button>
```

### Cards & Containers

**Standard Card** (Glassmorphism feel)
```tsx
<section className="bg-primary/5 border border-primary/10 rounded-sm p-6 backdrop-blur-sm">
  {/* Content */}
</section>
```

### Inputs

**Minimal Text Input**
```tsx
<input
  type="text"
  className="w-full bg-transparent border-b border-primary/20 py-2 text-primary placeholder-primary/30 focus:outline-none focus:border-primary/60 transition-colors font-serif text-xl"
/>
```

## Typography

-   **Headings**: `font-serif font-medium` (e.g., `text-4xl`)
-   **Body/UI**: `font-sans text-primary/70` (for labels, secondary text)
-   **Data/Move notation**: `font-mono`

### Ink opacity and contrast

Secondary text is dimmed with an **alpha colour** (`text-primary/70`), never with
`opacity-*`. Element opacity composites after paint, so contrast tooling measures
the underlying colour and the real ratio goes unchecked. (`disabled:opacity-*` on
inactive controls is fine — WCAG exempts them.)

`/70` is the **floor for text under 18px**, and the reason is the surfaces, not the
token. Cards tint themselves with `bg-primary/5`, and each tint layer pulls the
surface toward ink and erodes contrast by roughly 0.16. In the day theme
`text-primary/60` measures 4.65:1 on the bare body — and 4.49:1 on a `bg-primary/5`
panel, 4.33:1 on `bg-primary/10`. It fails AA everywhere it is actually used.
`/70` clears the bar on every surface in both themes (worst case 5.57:1).

| surface | day `/60` | day `/70` | night `/60` | night `/70` |
| --- | --- | --- | --- | --- |
| plain body | 4.65 | 6.52 | 6.55 | 8.54 |
| `bg-primary/5` | **4.49** | 6.21 | 6.19 | 7.93 |
| `bg-primary/10` | **4.33** | 5.89 | 5.68 | 7.16 |

So **measure in context**: a token checked against the body background passes and
still ships a failure. Larger text keeps its 3:1 bar and needs none of this, and
non-text marks (e.g. the flat-trend `Sparkline` stroke) are governed by 1.4.11's
3:1, so they stay at `/60` deliberately.

## Spacing

-   Use **consistent spacing**: `space-y-6` or `space-y-8` for vertical rhythm.
-   Use **containers**: `max-w-[600px] mx-auto` for focused content.

## States

-   **Loading**: Use `...` text or `animate-pulse` on a skeleton div.
-   **Error**: `text-error font-sans`
-   **Success**: `text-success font-serif`

## Tone

-   **Premium**: Avoid clutter. Less is more.
-   **Calm**: Use slow transitions (`transition-slow` where appropriate).
