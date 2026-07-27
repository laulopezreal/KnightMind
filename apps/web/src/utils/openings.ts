/**
 * Colour for a chess *score* percentage — (wins + ½ draws) / games — which is
 * what the API's `win_rate` field actually carries. It is not the share of
 * games won: a line drawn every time scores 50%, having won none of them.
 * Never label this "win rate" in the UI.
 */
export function getScoreColor(score: number): string {
  if (score >= 60) return '#059669';
  if (score >= 50) return '#10B981';
  if (score >= 45) return '#84CC16';
  if (score >= 40) return '#EAB308';
  if (score >= 30) return '#F97316';
  return '#EF4444';
}
