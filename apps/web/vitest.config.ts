import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    globals: true,
    fileParallelism: false,
    // Vitest's 5s default is sized for unit tests, not for rendering a page
    // component into jsdom and driving it with userEvent. The heaviest tests
    // here take ~800ms on an idle machine, which sounds safe until a developer
    // box is running several suites at once: a 7x slowdown is enough to trip
    // the limit, and it showed up as a one-in-N "flaky" failure with no
    // assertion behind it. A hung test still fails, just 10s later.
    testTimeout: 15000,
  },
});