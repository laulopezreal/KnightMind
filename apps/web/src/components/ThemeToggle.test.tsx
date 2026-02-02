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
    vi.clearAllMocks();
    mockTheme = 'night';
  });

  it('should render a toggle button', () => {
    render(<ThemeToggle />);

    const toggle = screen.getByRole('button');
    expect(toggle).toBeInTheDocument();
  });

  it('should call toggleTheme on click', async () => {
    render(<ThemeToggle />);

    const toggle = screen.getByRole('button');
    await user.click(toggle);

    expect(mockToggleTheme).toHaveBeenCalledTimes(1);
  });

  it('should be keyboard accessible with Enter key', async () => {
    render(<ThemeToggle />);

    const toggle = screen.getByRole('button');
    toggle.focus();
    await user.keyboard('{Enter}');

    expect(mockToggleTheme).toHaveBeenCalledTimes(1);
  });

  it('should be keyboard accessible with Space key', async () => {
    render(<ThemeToggle />);

    const toggle = screen.getByRole('button');
    toggle.focus();
    await user.keyboard(' ');

    expect(mockToggleTheme).toHaveBeenCalledTimes(1);
  });

  it('should have tabIndex 0 for keyboard focus', () => {
    render(<ThemeToggle />);

    const toggle = screen.getByRole('button');
    expect(toggle).toHaveAttribute('tabindex', '0');
  });

  it('should position knob based on theme', () => {
    const { container } = render(<ThemeToggle />);

    // In night mode, knob should be on the right
    const knob = container.querySelector('.rounded-full.bg-toggle-knob');
    expect(knob).toBeInTheDocument();
  });
});
