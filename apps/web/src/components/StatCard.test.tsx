import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { StatCard } from './StatCard';

describe('StatCard', () => {
  it('renders label, value, and sub', () => {
    render(<StatCard label="Net Change" value="+18" sub="1500 → 1518" />);
    expect(screen.getByText('Net Change')).toBeInTheDocument();
    expect(screen.getByText('+18')).toBeInTheDocument();
    expect(screen.getByText('1500 → 1518')).toBeInTheDocument();
  });

  it('colours the value positive when highlighted positive', () => {
    render(<StatCard label="x" value="+18" highlight positive />);
    expect(screen.getByText('+18')).toHaveClass('text-positive');
  });

  it('colours the value negative when highlighted non-positive', () => {
    render(<StatCard label="x" value="-9" highlight />);
    expect(screen.getByText('-9')).toHaveClass('text-negative');
  });

  it('renders the footer slot', () => {
    render(<StatCard label="x" value="1" footer={<span>footer-content</span>} />);
    expect(screen.getByText('footer-content')).toBeInTheDocument();
  });
});
