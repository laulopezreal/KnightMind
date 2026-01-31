## Task: Fix “All Done” + Generation Banner
- Date: 2026-01-31
- Goal: Enable “All Done” whenever puzzles exist and the final puzzle is ready; avoid stale generation failures.
- Changes:
  - Tightened final-puzzle/attempt state booleans and gated the button on `finishButtonDisabled` so generation status no longer controls enablement.
  - Added `handleAdvancePuzzle` to centralize review/advance logic.
  - Hid job/error cards whenever puzzles are present to prevent lingering “Generation Failed” banners.
- Testing: not run (per request).
