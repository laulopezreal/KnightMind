/**
 * The technical cause of a caught render error, for the person who can act on
 * it — which is the developer, not the user.
 *
 * `error.message` is whatever the engine produced ("Cannot read properties of
 * undefined (reading 'color')"). It tells a user nothing they can use, and it
 * describes the app's internals to whoever is looking at the screen. That is
 * fine on a single-user instance and stops being fine the moment
 * KNIGHTMIND_REQUIRE_AUTH puts other people on it.
 *
 * So: dev builds get it behind a closed disclosure, production builds don't
 * paint it at all. Nothing is lost either way — `ErrorBoundary.componentDidCatch`
 * console.errors the full error and component stack in every build, and the
 * fallbacks' "Report this" link carries the message into the issue.
 */
export function ErrorDetails({ error }: { error: Error | null }) {
  if (!import.meta.env.DEV || !error?.message) return null;

  return (
    <details className="mt-6 text-left">
      <summary className="text-primary/70 font-sans text-xs cursor-pointer km-focus-visible">
        Technical details
      </summary>
      {/* break-words: engine messages carry long unbroken tokens (URLs, module
          paths) that otherwise paint outside the panel. */}
      <p className="mt-2 font-mono text-xs text-primary/70 break-words">{error.message}</p>
    </details>
  );
}
