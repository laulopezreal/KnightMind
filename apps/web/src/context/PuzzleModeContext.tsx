import { createContext, useContext, type ReactNode } from 'react';
import { useLocalStorage } from '../hooks/useLocalStorage';

type SessionType = 'standard' | 'timed' | 'accuracy_goal';

interface PuzzleModeContextType {
    sessionType: SessionType;
    setSessionType: (type: SessionType) => void;
    targetAccuracy: number;
    setTargetAccuracy: (n: number) => void;
    targetTimeMinutes: number;
    setTargetTimeMinutes: (n: number) => void;
}

const PuzzleModeContext = createContext<PuzzleModeContextType | undefined>(undefined);

export function PuzzleModeProvider({ children }: { children: ReactNode }) {
    // Use custom useLocalStorage hook to reduce boilerplate
    const [sessionType, setSessionType] = useLocalStorage<SessionType>(
        'knightmind:puzzle_mode',
        'standard',
    );

    const [targetAccuracy, setTargetAccuracy] = useLocalStorage<number>(
        'knightmind:target_accuracy',
        80,
        (value) => parseInt(value, 10)
    );

    const [targetTimeMinutes, setTargetTimeMinutes] = useLocalStorage<number>(
        'knightmind:target_time_minutes',
        10,
        (value) => parseInt(value, 10)
    );

    return (
        <PuzzleModeContext.Provider
            value={{ sessionType, setSessionType, targetAccuracy, setTargetAccuracy, targetTimeMinutes, setTargetTimeMinutes }}
        >
            {children}
        </PuzzleModeContext.Provider>
    );
}

// eslint-disable-next-line react-refresh/only-export-components
export function usePuzzleMode() {
    const context = useContext(PuzzleModeContext);
    if (context === undefined) {
        throw new Error('usePuzzleMode must be used within PuzzleModeProvider');
    }
    return context;
}
