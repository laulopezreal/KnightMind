type SessionType = 'standard' | 'timed' | 'accuracy_goal';

const modeLabelMap: Record<SessionType, string> = {
    standard: 'Standard',
    timed: 'Timed',
    accuracy_goal: 'Accuracy Goal',
};

const screenReaderModeLabelMap: Record<SessionType, string> = {
    standard: 'Standard training mode',
    timed: 'Timed training mode',
    accuracy_goal: 'Accuracy Goal training mode',
};

export function getModeLabels(sessionType: SessionType) {
    return {
        selectedModeLabel: modeLabelMap[sessionType],
        screenReaderModeLabel: screenReaderModeLabelMap[sessionType],
    };
}

export function getSessionDetailsA11yCopy(showSessionDetails: boolean, screenReaderModeLabel: string) {
    return {
        toggleLabel: showSessionDetails ? 'Hide session details, expanded' : 'Show session details, collapsed',
        helperText: showSessionDetails
            ? 'Advanced session stats are visible. Activate to collapse details.'
            : 'Advanced session stats are hidden. Activate to expand details.',
        liveStatus: showSessionDetails
            ? `Session details expanded for ${screenReaderModeLabel}.`
            : `Session details collapsed for ${screenReaderModeLabel}.`,
    } as const;
}

export function getPuzzleActionA11yCopy(activeSessionId: string | null, hintsUsed: number) {
    return {
        checkMoveLabel: 'Check entered move',
        hintLabel: activeSessionId
            ? `Use hint ${Math.min(hintsUsed + 1, 3)} of 3`
            : 'Show clue for current puzzle',
        revealLabel: 'Reveal best move solution',
        showSolutionLabel: 'Show full solution and board continuation',
    } as const;
}
