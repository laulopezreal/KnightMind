/**
 * Calculate accuracy as percentage from pass/fail counts
 * @param passCount Number of successful attempts
 * @param failCount Number of failed attempts
 * @returns Accuracy percentage rounded to nearest integer (0-100)
 */
export const calculateAccuracy = (passCount: number, failCount: number): number => {
  const total = passCount + failCount;
  return total > 0 ? Math.round((passCount / total) * 100) : 0;
};
