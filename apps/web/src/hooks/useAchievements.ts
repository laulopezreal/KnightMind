import { useCallback, useEffect, useState } from 'react';

export interface Achievement {
    id: string;
    name: string;
    description: string;
    icon: string;
    earned: boolean;
    earnedAt?: Date;
}

export const ACHIEVEMENTS: Achievement[] = [
    { id: 'first_session', name: 'First Steps', description: 'Complete your first training session', icon: '👣', earned: false },
    { id: 'streak_5', name: 'Hot Streak', description: 'Achieve a 5 puzzle streak', icon: '🔥', earned: false },
    { id: 'streak_10', name: 'Blazing Streak', description: 'Achieve a 10 puzzle streak', icon: '🧨', earned: false },
    { id: 'accuracy_90', name: 'Sharp Shooter', description: 'Achieve 90% accuracy in a session', icon: '🎯', earned: false },
    { id: 'speed_demon', name: 'Speed Demon', description: 'Solve a puzzle in under 10 seconds', icon: '⚡', earned: false },
    { id: 'perfect_session', name: 'Flawless Victory', description: 'Complete a session with 100% accuracy', icon: '🏆', earned: false },
];

const calculateAccuracy = (passCount: number, failCount: number): number => {
    const total = passCount + failCount;
    return total > 0 ? Math.round((passCount / total) * 100) : 0;
};

export interface CheckAchievementsParams {
    streak: number;
    currentPuzzleTime: number;
}

export interface CheckSessionAchievementsParams {
    passCount: number;
    failCount: number;
}

export function useAchievements(username: string) {
    const [achievements, setAchievements] = useState<Achievement[]>(() =>
        ACHIEVEMENTS.map(a => ({ ...a })),
    );

    // Load from localStorage on username change
    useEffect(() => {
        if (!username) return;
        const saved = localStorage.getItem(`knightmind:achievements:${username}`);
        if (saved) {
            try {
                const parsed = JSON.parse(saved);
                const merged = ACHIEVEMENTS.map(def => {
                    const s = parsed.find((a: Achievement) => a.id === def.id);
                    if (s) {
                        return {
                            ...def,
                            ...s,
                            earnedAt: s.earnedAt ? new Date(s.earnedAt) : undefined,
                        };
                    }
                    return { ...def };
                });
                // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional: sync from localStorage on username change
                setAchievements(merged);
            } catch (e) {
                console.error('Failed to parse saved achievements', e);
            }
        } else {
            setAchievements(ACHIEVEMENTS.map(a => ({ ...a })));
        }
    }, [username]);

    // Save to localStorage when any are earned
    useEffect(() => {
        if (username && achievements.some(a => a.earned)) {
            localStorage.setItem(
                `knightmind:achievements:${username}`,
                JSON.stringify(achievements),
            );
        }
    }, [achievements, username]);

    /**
     * Check puzzle-level achievements (streak, speed).
     * Uses functional setState so the callback is fully stable.
     */
    const checkAchievements = useCallback(
        ({ streak, currentPuzzleTime }: CheckAchievementsParams) => {
            setAchievements(prev => {
                let changed = false;
                const now = new Date();
                const updated = prev.map(a => {
                    if (a.earned) return a;
                    if (a.id === 'streak_5' && streak >= 5) {
                        changed = true;
                        return { ...a, earned: true, earnedAt: now };
                    }
                    if (a.id === 'streak_10' && streak >= 10) {
                        changed = true;
                        return { ...a, earned: true, earnedAt: now };
                    }
                    if (a.id === 'speed_demon' && currentPuzzleTime < 10) {
                        changed = true;
                        return { ...a, earned: true, earnedAt: now };
                    }
                    return a;
                });
                return changed ? updated : prev;
            });
        },
        [],
    );

    /**
     * Check session-level achievements (first session, accuracy milestones).
     * Uses functional setState so the callback is fully stable.
     */
    const checkSessionAchievements = useCallback(
        ({ passCount, failCount }: CheckSessionAchievementsParams) => {
            setAchievements(prev => {
                let changed = false;
                const now = new Date();
                const accuracy = calculateAccuracy(passCount, failCount);
                const total = passCount + failCount;
                const updated = prev.map(a => {
                    if (a.earned) return a;
                    if (a.id === 'first_session') {
                        changed = true;
                        return { ...a, earned: true, earnedAt: now };
                    }
                    if (a.id === 'accuracy_90' && accuracy >= 90 && total > 0) {
                        changed = true;
                        return { ...a, earned: true, earnedAt: now };
                    }
                    if (a.id === 'perfect_session' && accuracy === 100 && total > 0) {
                        changed = true;
                        return { ...a, earned: true, earnedAt: now };
                    }
                    return a;
                });
                return changed ? updated : prev;
            });
        },
        [],
    );

    return { achievements, checkAchievements, checkSessionAchievements };
}
