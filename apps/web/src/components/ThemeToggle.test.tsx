import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ThemeToggle from './ThemeToggle';

const mockToggleTheme = vi.fn();
let mockTheme = 'night';

vi.mock('../context/ThemeContext', () => ({
  useTheme: () => ({ theme: mockTheme, toggleTheme: mockToggleTheme }),
}));

describe('ThemeToggle', () => {
  const user = userEvent.setup();

  beforeEach(() => {
    vi.resetAllMocks();
    mockTheme = 'night';
  });

  it('should render a toggle switch', () => {
    render(<ThemeToggle />);

    const toggle = screen.getByRole('switch');
    expect(toggle).toBeInTheDocument();
  });

  it('should expose an accessible name and checked state', () => {
    render(<ThemeToggle />);

    const toggle = screen.getByRole('switch');
    expect(toggle).toHaveAttribute('aria-label', 'Night theme');
    // night theme is active, so the switch reads as "on"
    expect(toggle).toHaveAttribute('aria-checked', 'true');
  });

  it('should reflect day theme as unchecked', () => {
    mockTheme = 'day';
    render(<ThemeToggle />);

    const toggle = screen.getByRole('switch');
    expect(toggle).toHaveAttribute('aria-checked', 'false');
  });

  it('should call toggleTheme on click', async () => {
    render(<ThemeToggle />);

    const toggle = screen.getByRole('switch');
    await user.click(toggle);

    expect(mockToggleTheme).toHaveBeenCalledTimes(1);
  });

  it('should be keyboard accessible with Enter key', async () => {
    render(<ThemeToggle />);

    const toggle = screen.getByRole('switch');
    toggle.focus();
    await user.keyboard('{Enter}');

    expect(mockToggleTheme).toHaveBeenCalledTimes(1);
  });

  it('should be keyboard accessible with Space key', async () => {
    render(<ThemeToggle />);

    const toggle = screen.getByRole('switch');
    toggle.focus();
    await user.keyboard(' ');

    expect(mockToggleTheme).toHaveBeenCalledTimes(1);
  });

  it('should have tabIndex 0 for keyboard focus', () => {
    render(<ThemeToggle />);

    const toggle = screen.getByRole('switch');
    expect(toggle).toHaveAttribute('tabindex', '0');
  });

  it('keeps a 44px minimum hit area without transform scaling', () => {
    render(<ThemeToggle />);

    const toggle = screen.getByRole('switch');
    expect(toggle).toHaveClass('h-11');
    expect(toggle).not.toHaveClass('scale-85');
  });

  it('should position knob based on theme', () => {
    const { container } = render(<ThemeToggle />);

    // In night mode, knob should be on the right
    const knob = container.querySelector('.rounded-full.bg-toggle-knob');
    expect(knob).toBeInTheDocument();
  });
});
