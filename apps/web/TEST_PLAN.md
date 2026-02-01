# Test Plan: Dashboard Features

## Overview
This document outlines comprehensive test cases for the dashboard improvements implemented in PR #66, including empty states, mastery celebration, refresh mechanism, and button disable logic.

## Test Infrastructure Setup (Prerequisites)

To execute these tests, install the following:

```bash
npm install --save-dev vitest @testing-library/react @testing-library/jest-dom @testing-library/user-event jsdom
```

Add to `package.json`:
```json
"scripts": {
  "test": "vitest",
  "test:ui": "vitest --ui",
  "test:coverage": "vitest --coverage"
}
```

Create `vitest.config.ts`:
```typescript
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    globals: true,
  },
});
```

---

## 1. TacticalRadar Component Tests

### 1.1 Empty State Tests

#### Test: Zero motifs (no data yet)
```typescript
describe('TacticalRadar - Empty States', () => {
  it('should display "No motif data yet" when motifs array is empty', () => {
    const onMotifClick = vi.fn();
    const { getByText } = render(
      <TacticalRadar motifs={[]} onMotifClick={onMotifClick} />
    );

    expect(getByText(/No motif data yet/i)).toBeInTheDocument();
    expect(getByText(/Complete your first training session/i)).toBeInTheDocument();
    expect(getByText('🎯 Tactical Vision')).toBeInTheDocument();
  });

  it('should not render radar chart when motifs array is empty', () => {
    const onMotifClick = vi.fn();
    const { container } = render(
      <TacticalRadar motifs={[]} onMotifClick={onMotifClick} />
    );

    const radarChart = container.querySelector('.recharts-wrapper');
    expect(radarChart).not.toBeInTheDocument();
  });
});
```

#### Test: 1-2 motifs (insufficient data)
```typescript
it('should display "at least 3 motifs needed" when fewer than 3 motifs', () => {
  const motifs: MotifPerformance[] = [
    { name: 'Fork', accuracy: 0.75, total_puzzles: 10, passed: 7, rank: 'learning' },
    { name: 'Pin', accuracy: 0.80, total_puzzles: 8, passed: 6, rank: 'learning' },
  ];

  const { getByText } = render(
    <TacticalRadar motifs={motifs} onMotifClick={vi.fn()} />
  );

  expect(getByText(/at least 3 different motifs/i)).toBeInTheDocument();
});
```

### 1.2 Celebration State Tests

#### Test: All motifs mastered (>85%)
```typescript
describe('TacticalRadar - Mastery Celebration', () => {
  it('should display celebration when all motifs have 85%+ accuracy', () => {
    const motifs: MotifPerformance[] = [
      { name: 'Fork', accuracy: 0.90, total_puzzles: 20, passed: 18, rank: 'mastered' },
      { name: 'Pin', accuracy: 0.87, total_puzzles: 15, passed: 13, rank: 'mastered' },
      { name: 'Skewer', accuracy: 0.92, total_puzzles: 25, passed: 23, rank: 'mastered' },
    ];

    const { getByText, container } = render(
      <TacticalRadar motifs={motifs} onMotifClick={vi.fn()} />
    );

    expect(getByText('All Motifs Mastered!')).toBeInTheDocument();
    expect(getByText(/85%\+ accuracy on all tactical patterns/i)).toBeInTheDocument();

    // Should have green success styling
    const celebration = container.querySelector('.bg-green-500\\/10');
    expect(celebration).toBeInTheDocument();

    // Should have checkmark icon
    const checkmark = container.querySelector('svg');
    expect(checkmark).toBeInTheDocument();
  });

  it('should NOT show weakest area when all motifs are mastered', () => {
    const motifs: MotifPerformance[] = [
      { name: 'Fork', accuracy: 0.90, total_puzzles: 20, passed: 18, rank: 'mastered' },
      { name: 'Pin', accuracy: 0.87, total_puzzles: 15, passed: 13, rank: 'mastered' },
      { name: 'Skewer', accuracy: 0.92, total_puzzles: 25, passed: 23, rank: 'mastered' },
    ];

    const { queryByText } = render(
      <TacticalRadar motifs={motifs} onMotifClick={vi.fn()} />
    );

    expect(queryByText(/Your weakest area/i)).not.toBeInTheDocument();
  });

  it('should apply entrance animation to celebration', () => {
    const motifs: MotifPerformance[] = [
      { name: 'Fork', accuracy: 0.90, total_puzzles: 20, passed: 18, rank: 'mastered' },
      { name: 'Pin', accuracy: 0.87, total_puzzles: 15, passed: 13, rank: 'mastered' },
      { name: 'Skewer', accuracy: 0.92, total_puzzles: 25, passed: 23, rank: 'mastered' },
    ];

    const { container } = render(
      <TacticalRadar motifs={motifs} onMotifClick={vi.fn()} />
    );

    const celebration = container.querySelector('.animate-teedin');
    expect(celebration).toBeInTheDocument();
  });
});
```

#### Test: Edge case - exactly 85%
```typescript
it('should show celebration when motif has exactly 85% accuracy', () => {
  const motifs: MotifPerformance[] = [
    { name: 'Fork', accuracy: 0.85, total_puzzles: 20, passed: 17, rank: 'mastered' },
    { name: 'Pin', accuracy: 0.90, total_puzzles: 15, passed: 13, rank: 'mastered' },
    { name: 'Skewer', accuracy: 0.92, total_puzzles: 25, passed: 23, rank: 'mastered' },
  ];

  const { getByText } = render(
    <TacticalRadar motifs={motifs} onMotifClick={vi.fn()} />
  );

  expect(getByText('All Motifs Mastered!')).toBeInTheDocument();
});
```

#### Test: Edge case - one motif below threshold
```typescript
it('should NOT show celebration when even one motif is below 85%', () => {
  const motifs: MotifPerformance[] = [
    { name: 'Fork', accuracy: 0.90, total_puzzles: 20, passed: 18, rank: 'mastered' },
    { name: 'Pin', accuracy: 0.84, total_puzzles: 15, passed: 12, rank: 'learning' }, // Below threshold
    { name: 'Skewer', accuracy: 0.92, total_puzzles: 25, passed: 23, rank: 'mastered' },
  ];

  const { queryByText, getByText } = render(
    <TacticalRadar motifs={motifs} onMotifClick={vi.fn()} />
  );

  expect(queryByText('All Motifs Mastered!')).not.toBeInTheDocument();
  expect(getByText(/Your weakest area/i)).toBeInTheDocument();
  expect(getByText('Pin')).toBeInTheDocument(); // Should show Pin as weakest
});
```

### 1.3 Practice Button Disable Tests

#### Test: Disable when no puzzles available
```typescript
describe('TacticalRadar - Practice Button', () => {
  it('should disable practice button when weakest motif has 0 puzzles', () => {
    const motifs: MotifPerformance[] = [
      { name: 'Fork', accuracy: 0.75, total_puzzles: 10, passed: 7, rank: 'learning' },
      { name: 'Pin', accuracy: 0.60, total_puzzles: 0, passed: 0, rank: 'needs_work' }, // No puzzles
      { name: 'Skewer', accuracy: 0.80, total_puzzles: 15, passed: 12, rank: 'learning' },
    ];

    const { getByRole } = render(
      <TacticalRadar motifs={motifs} onMotifClick={vi.fn()} />
    );

    const button = getByRole('button', { name: /Practice Pin Now/i });
    expect(button).toBeDisabled();
    expect(button).toHaveClass('km-interactive-disabled');
  });

  it('should display tooltip when button is disabled', () => {
    const motifs: MotifPerformance[] = [
      { name: 'Fork', accuracy: 0.75, total_puzzles: 10, passed: 7, rank: 'learning' },
      { name: 'Pin', accuracy: 0.60, total_puzzles: 0, passed: 0, rank: 'needs_work' },
      { name: 'Skewer', accuracy: 0.80, total_puzzles: 15, passed: 12, rank: 'learning' },
    ];

    const { getByRole } = render(
      <TacticalRadar motifs={motifs} onMotifClick={vi.fn()} />
    );

    const button = getByRole('button', { name: /Practice Pin Now/i });
    expect(button).toHaveAttribute('title', 'No puzzles available for this motif yet');
  });

  it('should enable button when weakest motif has puzzles', () => {
    const motifs: MotifPerformance[] = [
      { name: 'Fork', accuracy: 0.75, total_puzzles: 10, passed: 7, rank: 'learning' },
      { name: 'Pin', accuracy: 0.60, total_puzzles: 8, passed: 4, rank: 'needs_work' },
      { name: 'Skewer', accuracy: 0.80, total_puzzles: 15, passed: 12, rank: 'learning' },
    ];

    const { getByRole } = render(
      <TacticalRadar motifs={motifs} onMotifClick={vi.fn()} />
    );

    const button = getByRole('button', { name: /Practice Pin Now/i });
    expect(button).not.toBeDisabled();
    expect(button).toHaveClass('km-interactive');
  });

  it('should call onMotifClick when enabled button is clicked', async () => {
    const onMotifClick = vi.fn();
    const motifs: MotifPerformance[] = [
      { name: 'Fork', accuracy: 0.75, total_puzzles: 10, passed: 7, rank: 'learning' },
      { name: 'Pin', accuracy: 0.60, total_puzzles: 8, passed: 4, rank: 'needs_work' },
      { name: 'Skewer', accuracy: 0.80, total_puzzles: 15, passed: 12, rank: 'learning' },
    ];

    const { getByRole } = render(
      <TacticalRadar motifs={motifs} onMotifClick={onMotifClick} />
    );

    const button = getByRole('button', { name: /Practice Pin Now/i });
    await userEvent.click(button);

    expect(onMotifClick).toHaveBeenCalledWith('Pin');
    expect(onMotifClick).toHaveBeenCalledTimes(1);
  });

  it('should NOT call onMotifClick when disabled button is clicked', async () => {
    const onMotifClick = vi.fn();
    const motifs: MotifPerformance[] = [
      { name: 'Fork', accuracy: 0.75, total_puzzles: 10, passed: 7, rank: 'learning' },
      { name: 'Pin', accuracy: 0.60, total_puzzles: 0, passed: 0, rank: 'needs_work' },
      { name: 'Skewer', accuracy: 0.80, total_puzzles: 15, passed: 12, rank: 'learning' },
    ];

    const { getByRole } = render(
      <TacticalRadar motifs={motifs} onMotifClick={onMotifClick} />
    );

    const button = getByRole('button', { name: /Practice Pin Now/i });
    await userEvent.click(button);

    expect(onMotifClick).not.toHaveBeenCalled();
  });
});
```

### 1.4 Weakest Motif Calculation Tests

```typescript
describe('TacticalRadar - Weakest Motif Selection', () => {
  it('should identify motif with lowest accuracy as weakest', () => {
    const motifs: MotifPerformance[] = [
      { name: 'Fork', accuracy: 0.75, total_puzzles: 10, passed: 7, rank: 'learning' },
      { name: 'Pin', accuracy: 0.60, total_puzzles: 8, passed: 4, rank: 'needs_work' },
      { name: 'Skewer', accuracy: 0.80, total_puzzles: 15, passed: 12, rank: 'learning' },
    ];

    const { getByText } = render(
      <TacticalRadar motifs={motifs} onMotifClick={vi.fn()} />
    );

    expect(getByText('Pin (60%)')).toBeInTheDocument();
  });

  it('should handle tie in accuracy (deterministic selection)', () => {
    const motifs: MotifPerformance[] = [
      { name: 'Fork', accuracy: 0.60, total_puzzles: 10, passed: 6, rank: 'needs_work' },
      { name: 'Pin', accuracy: 0.60, total_puzzles: 8, passed: 4, rank: 'needs_work' },
      { name: 'Skewer', accuracy: 0.80, total_puzzles: 15, passed: 12, rank: 'learning' },
    ];

    const { getByText } = render(
      <TacticalRadar motifs={motifs} onMotifClick={vi.fn()} />
    );

    // Should pick first one encountered (Fork)
    expect(getByText('Fork (60%)')).toBeInTheDocument();
  });
});
```

---

## 2. Dashboard Component Tests

### 2.1 Refresh Mechanism Tests

#### Test: Manual refresh button
```typescript
describe('Dashboard - Refresh Mechanism', () => {
  it('should display refresh button in header', () => {
    const { getByRole } = render(<Dashboard />);

    const refreshButton = getByRole('button', { name: /Refresh/i });
    expect(refreshButton).toBeInTheDocument();
    expect(refreshButton).not.toBeDisabled();
  });

  it('should show loading state when refresh button is clicked', async () => {
    const { getByRole } = render(<Dashboard />);

    const refreshButton = getByRole('button', { name: /Refresh/i });
    await userEvent.click(refreshButton);

    expect(getByRole('button', { name: /Refreshing/i })).toBeInTheDocument();

    // Should show spinner
    const spinner = document.querySelector('.animate-spin');
    expect(spinner).toBeInTheDocument();
  });

  it('should disable refresh button while refreshing', async () => {
    const { getByRole } = render(<Dashboard />);

    const refreshButton = getByRole('button', { name: /Refresh/i });
    await userEvent.click(refreshButton);

    const refreshingButton = getByRole('button', { name: /Refreshing/i });
    expect(refreshingButton).toBeDisabled();
    expect(refreshingButton).toHaveClass('km-interactive-disabled');
  });

  it('should call all dashboard APIs when refresh is triggered', async () => {
    const getDashboardSummaryMock = vi.fn().mockResolvedValue({...});
    const getMotifPerformanceMock = vi.fn().mockResolvedValue({...});
    const getRecentSessionsMock = vi.fn().mockResolvedValue([]);
    const getMotifTrendsMock = vi.fn().mockResolvedValue({...});

    vi.mock('../api/users', () => ({
      getDashboardSummary: getDashboardSummaryMock,
      getMotifPerformance: getMotifPerformanceMock,
      getMotifTrends: getMotifTrendsMock,
    }));

    vi.mock('../api/sessions', () => ({
      getRecentSessions: getRecentSessionsMock,
    }));

    const { getByRole } = render(<Dashboard />);

    await waitFor(() => {
      expect(getDashboardSummaryMock).toHaveBeenCalledTimes(1); // Initial load
    });

    const refreshButton = getByRole('button', { name: /Refresh/i });
    await userEvent.click(refreshButton);

    await waitFor(() => {
      expect(getDashboardSummaryMock).toHaveBeenCalledTimes(2); // Refresh
      expect(getMotifPerformanceMock).toHaveBeenCalledTimes(2);
      expect(getRecentSessionsMock).toHaveBeenCalledTimes(2);
      expect(getMotifTrendsMock).toHaveBeenCalledTimes(2);
    });
  });

  it('should update dashboard data after refresh completes', async () => {
    const mockData1 = { ...dashboardSummary, training_streak_days: 5 };
    const mockData2 = { ...dashboardSummary, training_streak_days: 10 };

    const getDashboardSummaryMock = vi.fn()
      .mockResolvedValueOnce(mockData1)
      .mockResolvedValueOnce(mockData2);

    const { getByText, getByRole } = render(<Dashboard />);

    await waitFor(() => {
      expect(getByText('5-day streak')).toBeInTheDocument();
    });

    const refreshButton = getByRole('button', { name: /Refresh/i });
    await userEvent.click(refreshButton);

    await waitFor(() => {
      expect(getByText('10-day streak')).toBeInTheDocument();
    });
  });
});
```

#### Test: Auto-refresh on window focus
```typescript
describe('Dashboard - Auto-refresh on Focus', () => {
  it('should refresh data when window regains focus', async () => {
    const getDashboardSummaryMock = vi.fn().mockResolvedValue({...});

    render(<Dashboard />);

    await waitFor(() => {
      expect(getDashboardSummaryMock).toHaveBeenCalledTimes(1); // Initial load
    });

    // Simulate window losing and regaining focus
    act(() => {
      window.dispatchEvent(new Event('blur'));
    });

    act(() => {
      window.dispatchEvent(new Event('focus'));
    });

    await waitFor(() => {
      expect(getDashboardSummaryMock).toHaveBeenCalledTimes(2); // Auto-refresh
    });
  });

  it('should clean up focus listener on unmount', () => {
    const removeEventListenerSpy = vi.spyOn(window, 'removeEventListener');

    const { unmount } = render(<Dashboard />);

    unmount();

    expect(removeEventListenerSpy).toHaveBeenCalledWith('focus', expect.any(Function));
  });

  it('should not trigger refresh if window was already focused', async () => {
    const getDashboardSummaryMock = vi.fn().mockResolvedValue({...});

    render(<Dashboard />);

    await waitFor(() => {
      expect(getDashboardSummaryMock).toHaveBeenCalledTimes(1);
    });

    // Trigger focus without blur (already focused)
    act(() => {
      window.dispatchEvent(new Event('focus'));
    });

    // Should still be 1 (no additional call)
    await waitFor(() => {
      expect(getDashboardSummaryMock).toHaveBeenCalledTimes(1);
    });
  });
});
```

### 2.2 Error Handling Tests

```typescript
describe('Dashboard - Error Handling', () => {
  it('should display error message when API calls fail', async () => {
    vi.mock('../api/users', () => ({
      getDashboardSummary: vi.fn().mockRejectedValue(new Error('Network error')),
      getMotifPerformance: vi.fn().mockRejectedValue(new Error('Network error')),
      getMotifTrends: vi.fn().mockRejectedValue(new Error('Network error')),
    }));

    const { getByText } = render(<Dashboard />);

    await waitFor(() => {
      expect(getByText(/Network error/i)).toBeInTheDocument();
    });
  });

  it('should provide retry button when error occurs', async () => {
    vi.mock('../api/users', () => ({
      getDashboardSummary: vi.fn().mockRejectedValue(new Error('Network error')),
    }));

    const { getByRole } = render(<Dashboard />);

    await waitFor(() => {
      const retryButton = getByRole('button', { name: /Retry/i });
      expect(retryButton).toBeInTheDocument();
    });
  });

  it('should clear error message on successful refresh', async () => {
    const getDashboardSummaryMock = vi.fn()
      .mockRejectedValueOnce(new Error('Network error'))
      .mockResolvedValueOnce({...});

    const { getByRole, queryByText } = render(<Dashboard />);

    await waitFor(() => {
      expect(queryByText(/Network error/i)).toBeInTheDocument();
    });

    const refreshButton = getByRole('button', { name: /Refresh/i });
    await userEvent.click(refreshButton);

    await waitFor(() => {
      expect(queryByText(/Network error/i)).not.toBeInTheDocument();
    });
  });
});
```

### 2.3 Integration Tests

```typescript
describe('Dashboard - Integration Tests', () => {
  it('should handle user with no motif data', async () => {
    vi.mock('../api/users', () => ({
      getDashboardSummary: vi.fn().mockResolvedValue({ schedule: { due_now: 0 } }),
      getMotifPerformance: vi.fn().mockResolvedValue({ motifs: [] }),
      getMotifTrends: vi.fn().mockResolvedValue({ motif_trends: [] }),
    }));

    const { getByText } = render(<Dashboard />);

    await waitFor(() => {
      expect(getByText(/No motif data yet/i)).toBeInTheDocument();
    });
  });

  it('should show TacticalRadar with celebration when all motifs mastered', async () => {
    const mockMotifs = {
      motifs: [
        { name: 'Fork', accuracy: 0.90, total_puzzles: 20, passed: 18, rank: 'mastered' },
        { name: 'Pin', accuracy: 0.87, total_puzzles: 15, passed: 13, rank: 'mastered' },
        { name: 'Skewer', accuracy: 0.92, total_puzzles: 25, passed: 23, rank: 'mastered' },
      ],
    };

    vi.mock('../api/users', () => ({
      getMotifPerformance: vi.fn().mockResolvedValue(mockMotifs),
    }));

    const { getByText } = render(<Dashboard />);

    await waitFor(() => {
      expect(getByText('All Motifs Mastered!')).toBeInTheDocument();
    });
  });

  it('should navigate to puzzles page when Quick Session button is clicked', async () => {
    const mockNavigate = vi.fn();
    vi.mock('react-router-dom', () => ({
      useNavigate: () => mockNavigate,
    }));

    const mockDashboard = {
      schedule: { due_now: 5 },
    };

    vi.mock('../api/users', () => ({
      getDashboardSummary: vi.fn().mockResolvedValue(mockDashboard),
    }));

    const { getByRole } = render(<Dashboard />);

    await waitFor(() => {
      const quickSessionButton = getByRole('button', { name: /Quick Session/i });
      expect(quickSessionButton).not.toBeDisabled();
    });

    const quickSessionButton = getByRole('button', { name: /Quick Session/i });
    await userEvent.click(quickSessionButton);

    expect(mockNavigate).toHaveBeenCalledWith('/puzzles');
  });

  it('should disable Quick Session when no puzzles are due', async () => {
    const mockDashboard = {
      schedule: { due_now: 0 },
    };

    vi.mock('../api/users', () => ({
      getDashboardSummary: vi.fn().mockResolvedValue(mockDashboard),
    }));

    const { getByRole } = render(<Dashboard />);

    await waitFor(() => {
      const quickSessionButton = getByRole('button', { name: /Quick Session/i });
      expect(quickSessionButton).toBeDisabled();
    });
  });
});
```

---

## 3. Edge Case Tests

### 3.1 Boundary Values

```typescript
describe('Edge Cases - Boundary Values', () => {
  it('should handle 0% accuracy correctly', () => {
    const motifs: MotifPerformance[] = [
      { name: 'Fork', accuracy: 0.0, total_puzzles: 10, passed: 0, rank: 'needs_work' },
      { name: 'Pin', accuracy: 0.20, total_puzzles: 8, passed: 1, rank: 'needs_work' },
      { name: 'Skewer', accuracy: 0.80, total_puzzles: 15, passed: 12, rank: 'learning' },
    ];

    const { getByText } = render(
      <TacticalRadar motifs={motifs} onMotifClick={vi.fn()} />
    );

    expect(getByText('Fork (0%)')).toBeInTheDocument();
  });

  it('should handle 100% accuracy correctly', () => {
    const motifs: MotifPerformance[] = [
      { name: 'Fork', accuracy: 1.0, total_puzzles: 10, passed: 10, rank: 'mastered' },
      { name: 'Pin', accuracy: 1.0, total_puzzles: 8, passed: 8, rank: 'mastered' },
      { name: 'Skewer', accuracy: 1.0, total_puzzles: 15, passed: 15, rank: 'mastered' },
    ];

    const { getByText } = render(
      <TacticalRadar motifs={motifs} onMotifClick={vi.fn()} />
    );

    expect(getByText('All Motifs Mastered!')).toBeInTheDocument();
  });

  it('should handle exactly 3 motifs (minimum for radar)', () => {
    const motifs: MotifPerformance[] = [
      { name: 'Fork', accuracy: 0.75, total_puzzles: 10, passed: 7, rank: 'learning' },
      { name: 'Pin', accuracy: 0.60, total_puzzles: 8, passed: 4, rank: 'needs_work' },
      { name: 'Skewer', accuracy: 0.80, total_puzzles: 15, passed: 12, rank: 'learning' },
    ];

    const { container } = render(
      <TacticalRadar motifs={motifs} onMotifClick={vi.fn()} />
    );

    // Should render radar chart
    const radarChart = container.querySelector('.recharts-wrapper');
    expect(radarChart).toBeInTheDocument();
  });

  it('should handle very large number of motifs', () => {
    const motifs: MotifPerformance[] = Array.from({ length: 50 }, (_, i) => ({
      name: `Motif ${i + 1}`,
      accuracy: 0.5 + Math.random() * 0.4,
      total_puzzles: 10,
      passed: 5,
      rank: 'learning' as const,
    }));

    const { container } = render(
      <TacticalRadar motifs={motifs} onMotifClick={vi.fn()} />
    );

    // Should still render without crashing
    const radarChart = container.querySelector('.recharts-wrapper');
    expect(radarChart).toBeInTheDocument();
  });
});
```

### 3.2 Concurrent Operations

```typescript
describe('Edge Cases - Concurrent Operations', () => {
  it('should handle multiple rapid refresh clicks', async () => {
    const getDashboardSummaryMock = vi.fn().mockResolvedValue({...});

    const { getByRole } = render(<Dashboard />);

    await waitFor(() => {
      expect(getDashboardSummaryMock).toHaveBeenCalledTimes(1);
    });

    const refreshButton = getByRole('button', { name: /Refresh/i });

    // Click multiple times rapidly
    await userEvent.click(refreshButton);
    await userEvent.click(refreshButton);
    await userEvent.click(refreshButton);

    // Should only trigger one refresh (button disabled during refresh)
    await waitFor(() => {
      expect(getDashboardSummaryMock).toHaveBeenCalledTimes(2); // 1 initial + 1 refresh
    });
  });

  it('should handle focus events during active refresh', async () => {
    const getDashboardSummaryMock = vi.fn()
      .mockImplementation(() => new Promise(resolve => setTimeout(() => resolve({...}), 1000)));

    render(<Dashboard />);

    await waitFor(() => {
      expect(getDashboardSummaryMock).toHaveBeenCalledTimes(1);
    });

    // Start manual refresh
    const refreshButton = getByRole('button', { name: /Refresh/i });
    await userEvent.click(refreshButton);

    // Trigger focus event during refresh
    act(() => {
      window.dispatchEvent(new Event('focus'));
    });

    // Should not trigger another concurrent refresh
    await waitFor(() => {
      expect(getDashboardSummaryMock).toHaveBeenCalledTimes(2); // 1 initial + 1 refresh
    });
  });
});
```

### 3.3 Race Conditions

```typescript
describe('Edge Cases - Race Conditions', () => {
  it('should handle component unmount during data fetch', async () => {
    const getDashboardSummaryMock = vi.fn()
      .mockImplementation(() => new Promise(resolve => setTimeout(() => resolve({...}), 1000)));

    const { unmount } = render(<Dashboard />);

    // Unmount before data finishes loading
    unmount();

    // Should not cause errors or warnings
    await waitFor(() => {
      expect(getDashboardSummaryMock).toHaveBeenCalledTimes(1);
    });
  });

  it('should handle rapid username changes', async () => {
    const { rerender } = render(<Dashboard />);

    // Simulate rapid username changes
    act(() => {
      rerender(<ChessUsernameProvider value="user1"><Dashboard /></ChessUsernameProvider>);
    });

    act(() => {
      rerender(<ChessUsernameProvider value="user2"><Dashboard /></ChessUsernameProvider>);
    });

    act(() => {
      rerender(<ChessUsernameProvider value="user3"><Dashboard /></ChessUsernameProvider>);
    });

    // Should only load data for final username
    await waitFor(() => {
      expect(getDashboardSummaryMock).toHaveBeenLastCalledWith('user3');
    });
  });
});
```

---

## 4. Accessibility Tests

```typescript
describe('Accessibility', () => {
  it('should have proper ARIA labels on refresh button', () => {
    const { getByRole } = render(<Dashboard />);

    const refreshButton = getByRole('button', { name: /Refresh/i });
    expect(refreshButton).toHaveAttribute('title', 'Refresh dashboard data');
  });

  it('should have proper ARIA labels on disabled practice button', () => {
    const motifs: MotifPerformance[] = [
      { name: 'Fork', accuracy: 0.75, total_puzzles: 10, passed: 7, rank: 'learning' },
      { name: 'Pin', accuracy: 0.60, total_puzzles: 0, passed: 0, rank: 'needs_work' },
      { name: 'Skewer', accuracy: 0.80, total_puzzles: 15, passed: 12, rank: 'learning' },
    ];

    const { getByRole } = render(
      <TacticalRadar motifs={motifs} onMotifClick={vi.fn()} />
    );

    const button = getByRole('button', { name: /Practice Pin Now/i });
    expect(button).toHaveAttribute('title', 'No puzzles available for this motif yet');
  });

  it('should maintain keyboard focus after refresh', async () => {
    const { getByRole } = render(<Dashboard />);

    const refreshButton = getByRole('button', { name: /Refresh/i });
    refreshButton.focus();

    await userEvent.click(refreshButton);

    await waitFor(() => {
      expect(document.activeElement).toBe(refreshButton);
    });
  });
});
```

---

## 5. Performance Tests

```typescript
describe('Performance', () => {
  it('should not cause unnecessary re-renders on refresh', async () => {
    const renderSpy = vi.fn();

    function TestDashboard() {
      renderSpy();
      return <Dashboard />;
    }

    const { getByRole } = render(<TestDashboard />);

    const initialRenderCount = renderSpy.mock.calls.length;

    const refreshButton = getByRole('button', { name: /Refresh/i });
    await userEvent.click(refreshButton);

    await waitFor(() => {
      // Should only re-render for: click event, loading state, data update
      expect(renderSpy.mock.calls.length).toBeLessThanOrEqual(initialRenderCount + 3);
    });
  });

  it('should efficiently calculate weakest motif with large datasets', () => {
    const startTime = performance.now();

    const motifs: MotifPerformance[] = Array.from({ length: 1000 }, (_, i) => ({
      name: `Motif ${i + 1}`,
      accuracy: Math.random(),
      total_puzzles: 10,
      passed: 5,
      rank: 'learning' as const,
    }));

    render(<TacticalRadar motifs={motifs} onMotifClick={vi.fn()} />);

    const endTime = performance.now();
    const renderTime = endTime - startTime;

    // Should complete within reasonable time (< 100ms)
    expect(renderTime).toBeLessThan(100);
  });
});
```

---

## Summary

This test plan covers:

- **Empty States**: Zero motifs, insufficient motifs (1-2)
- **Celebration State**: All motifs >85%, edge cases (exactly 85%, one below)
- **Button Disable Logic**: No puzzles available, tooltips, click handlers
- **Refresh Mechanism**: Manual button, auto-refresh on focus, loading states
- **Error Handling**: API failures, retry logic, error clearing
- **Integration**: Full dashboard flow, navigation, conditional rendering
- **Edge Cases**: Boundary values (0%, 100%), concurrent operations, race conditions
- **Accessibility**: ARIA labels, keyboard focus, tooltips
- **Performance**: Render optimization, large datasets

All tests follow established patterns from existing codebase and prioritize user experience.

## Running Tests

Once Vitest is set up:

```bash
# Run all tests
npm test

# Run specific test file
npm test TacticalRadar.test.tsx

# Run tests in watch mode
npm test -- --watch

# Generate coverage report
npm run test:coverage
```
