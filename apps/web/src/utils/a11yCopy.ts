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

const HINT_RUNG_DESCRIPTIONS = [
    'name the piece to move',
    'highlight the destination square',
    'reveal the full solution',
] as const;

export function getPuzzleActionA11yCopy(clueStage: number) {
    const nextRung = clueStage + 1;
    const rungDescription = HINT_RUNG_DESCRIPTIONS[clueStage];
    return {
        checkMoveLabel: 'Check entered move',
        // The hint is a graduated ladder: each press escalates the help given.
        // Announcing the next rung (and what it reveals) keeps screen-reader
        // users on equal footing with the visual "Hint (n/3)" affordance.
        hintLabel: rungDescription
            ? `Hint ${nextRung} of 3: ${rungDescription}`
            : 'All hints revealed for this puzzle',
        revealLabel: 'Reveal best move solution',
        showSolutionLabel: 'Show full solution and board continuation',
    } as const;
}
