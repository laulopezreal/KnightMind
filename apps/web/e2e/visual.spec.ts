import { test, expect } from '@playwright/test';

test('Puzzles page visual regression', async ({ page }) => {
    page.on('console', msg => console.log(`[Browser]: ${msg.text()}`));
    page.on('pageerror', err => console.log(`[Browser Error]: ${err.message}`));

    await page.goto('/puzzles');

    // Wait for initial load
    await expect(page.getByText('Daily Puzzles')).toBeVisible({ timeout: 10000 });

    // Snapshot: Idle state
    await expect(page).toHaveScreenshot('puzzles-idle.png');
});
