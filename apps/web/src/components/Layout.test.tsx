import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import Layout from './Layout';

let mockUsername = '';

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

vi.mock('../context/ChessUsernameContext', () => ({
  useChessUsername: () => ({ username: mockUsername }),
}));

describe('Layout', () => {
  beforeEach(() => {
    mockUsername = '';
  });
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

  it('should not render username display before a username is set', () => {
    mockUsername = '';
    render(
      <Layout>
        <div>Content</div>
      </Layout>
    );

    expect(screen.queryByTestId('username-display')).not.toBeInTheDocument();
  });

  it('should render username display after a username is set', () => {
    mockUsername = 'testplayer';
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
