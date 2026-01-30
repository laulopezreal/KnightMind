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
-   **Body/UI**: `font-sans text-primary/60` (for labels, secondary text)
-   **Data/Move notation**: `font-mono`

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
