import { test, expect } from '@playwright/test';

const onePuzzle = {
  id: 'test-puzzle-1',
  username: 'testuser',
  source_game_id: 'game1',
  ply: 10,
  fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
  side_to_move: 'white',
  played_move_uci: 'e7e5',
  best_move_uci: 'e2e4',
  eval_before: 0,
  eval_after: 0.2,
  swing: 0.2,
  created_at: new Date().toISOString(),
  used_on: null,
};

test.describe('Clue button', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/sessions/start', async (route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ session_id: 'test-session', requested_n: 5 }) });
      } else {
        await route.continue();
      }
    });
    await page.route('**/api/puzzles/due*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          due_count: 1,
          returned_count: 1,
          now: new Date().toISOString(),
          puzzles: [onePuzzle],
        }),
      });
    });
  });

  test('shows Clue button when puzzle is solving, first clue shows piece hint, second clue reveals solution', async ({ page }) => {
    await page.goto('/puzzles');
    await expect(page.getByText('Daily Puzzles')).toBeVisible({ timeout: 10000 });

    await page.evaluate(() => localStorage.setItem('knightmind:chesscom_username', 'testuser'));
    await page.reload();
    await expect(page.getByRole('button', { name: 'Start Session' })).toBeVisible({ timeout: 5000 });

    await page.getByRole('button', { name: 'Start Session' }).click();
    await expect(page.getByText('Find the best move')).toBeVisible({ timeout: 10000 });

    const clueBtn = page.getByRole('button', { name: 'Clue' });
    await expect(clueBtn).toBeVisible();

    await clueBtn.click();
    await expect(page.getByText('Move the pawn')).toBeVisible({ timeout: 3000 });

    await clueBtn.click();
    await expect(page.getByText('Solution')).toBeVisible({ timeout: 3000 });
    await expect(page.getByText('e2e4')).toBeVisible();
  });

  test('Clue button is disabled when no best move', async ({ page }) => {
    await page.route('**/api/puzzles/due*', async (route) => {
      const puzzleNoBest = { ...onePuzzle, best_move_uci: '' };
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          due_count: 1,
          returned_count: 1,
          now: new Date().toISOString(),
          puzzles: [puzzleNoBest],
        }),
      });
    });

    await page.goto('/puzzles');
    await page.evaluate(() => localStorage.setItem('knightmind:chesscom_username', 'testuser'));
    await page.reload();
    await page.getByRole('button', { name: 'Start Session' }).click();
    await expect(page.getByText('Find the best move')).toBeVisible({ timeout: 10000 });

    const clueBtn = page.getByRole('button', { name: 'Clue' });
    await expect(clueBtn).toBeDisabled();
  });
});
