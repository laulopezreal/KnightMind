import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AchievementsList } from './AchievementsList';

const mockAchievements = [
  { id: '1', name: 'First Steps', description: 'Complete your first puzzle', icon: '🏆', earned: true, earnedAt: new Date('2025-01-01') },
  { id: '2', name: 'Streak Master', description: 'Get a 7-day streak', icon: '🔥', earned: false },
  { id: '3', name: 'Perfect Score', description: '100% accuracy in a session', icon: '💯', earned: true },
];

describe('AchievementsList', () => {
  it('should render achievements heading', () => {
    render(<AchievementsList achievements={mockAchievements} />);

    expect(screen.getByText('Achievements')).toBeInTheDocument();
  });

  it('should render all achievements', () => {
    render(<AchievementsList achievements={mockAchievements} />);

    expect(screen.getByText('First Steps')).toBeInTheDocument();
    expect(screen.getByText('Streak Master')).toBeInTheDocument();
    expect(screen.getByText('Perfect Score')).toBeInTheDocument();
  });

  it('should display achievement descriptions', () => {
    render(<AchievementsList achievements={mockAchievements} />);

    expect(screen.getByText('Complete your first puzzle')).toBeInTheDocument();
    expect(screen.getByText('Get a 7-day streak')).toBeInTheDocument();
  });

  it('should display earned date when available', () => {
    render(<AchievementsList achievements={mockAchievements} />);

    // First achievement has earnedAt
    expect(screen.getByText(/Earned:/)).toBeInTheDocument();
  });

  it('should render icons', () => {
    render(<AchievementsList achievements={mockAchievements} />);

    expect(screen.getByText('🏆')).toBeInTheDocument();
    expect(screen.getByText('🔥')).toBeInTheDocument();
    expect(screen.getByText('💯')).toBeInTheDocument();
  });

  it('should handle empty achievements list', () => {
    render(<AchievementsList achievements={[]} />);

    expect(screen.getByText('Achievements')).toBeInTheDocument();
  });
});
