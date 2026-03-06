import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import Layout from './Layout';

vi.mock('./Sidebar', () => ({
  default: () => <aside data-testid="sidebar">Sidebar</aside>,
}));

vi.mock('./UsernameDisplay', () => ({
  default: () => <div data-testid="username-display">UsernameDisplay</div>,
}));

vi.mock('./ThemeToggle', () => ({
  default: () => <div data-testid="theme-toggle">ThemeToggle</div>,
}));

vi.mock('./ReportProblem', () => ({
  ReportProblem: () => <div data-testid="report-problem">ReportProblem</div>,
}));

describe('Layout', () => {
  it('should render children', () => {
    render(
      <Layout>
        <div>Page content</div>
      </Layout>
    );

    expect(screen.getByText('Page content')).toBeInTheDocument();
  });

  it('should render sidebar', () => {
    render(
      <Layout>
        <div>Content</div>
      </Layout>
    );

    expect(screen.getByTestId('sidebar')).toBeInTheDocument();
  });

  it('should render username display', () => {
    render(
      <Layout>
        <div>Content</div>
      </Layout>
    );

    expect(screen.getByTestId('username-display')).toBeInTheDocument();
  });

  it('should render theme toggle', () => {
    render(
      <Layout>
        <div>Content</div>
      </Layout>
    );

    expect(screen.getAllByTestId('theme-toggle').length).toBeGreaterThan(0);
  });

  it('should render report problem button', () => {
    render(
      <Layout>
        <div>Content</div>
      </Layout>
    );

    expect(screen.getByTestId('report-problem')).toBeInTheDocument();
  });

  it('should render main content area', () => {
    render(
      <Layout>
        <div>Content</div>
      </Layout>
    );

    const main = screen.getByRole('main');
    expect(main).toBeInTheDocument();
  });
});
