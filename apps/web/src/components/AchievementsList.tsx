import { LOCALE } from '../utils/locale';
import { type Achievement } from '../hooks/useAchievements';

interface AchievementsListProps {
    achievements: Achievement[];
}

export function AchievementsList({ achievements }: AchievementsListProps) {
    return (
        <section className="bg-primary/5 border border-primary/10 rounded-sm p-6 backdrop-blur-sm">
            <h3 className="text-lg font-serif text-primary mb-4">Achievements</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {achievements.map(achievement => (
                    <div
                        key={achievement.id}
                        className={`p-4 rounded-sm border ${achievement.earned
                            ? 'bg-green-500/10 border-green-500/30'
                            : 'bg-primary/5 border-primary/20'
                            }`}
                    >
                        <div className="flex items-center">
                            <span className="text-2xl mr-3">{achievement.icon}</span>
                            <div>
                                <h4 className={`font-serif ${achievement.earned ? 'text-positive' : 'text-primary'}`}>
                                    {achievement.name}
                                </h4>
                                <p className="text-xs text-primary/70 mt-1">{achievement.description}</p>
                                {achievement.earned && achievement.earnedAt && (
                                    <p className="text-xs text-positive mt-1">
                                        Earned: {achievement.earnedAt.toLocaleDateString(LOCALE)}
                                    </p>
                                )}
                            </div>
                        </div>
                    </div>
                ))}
            </div>
        </section>
    );
}
