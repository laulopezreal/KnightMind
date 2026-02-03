export function getWinRateColor(winRate: number): string {
  if (winRate >= 60) return '#059669';
  if (winRate >= 50) return '#10B981';
  if (winRate >= 45) return '#84CC16';
  if (winRate >= 40) return '#EAB308';
  if (winRate >= 30) return '#F97316';
  return '#EF4444';
}
