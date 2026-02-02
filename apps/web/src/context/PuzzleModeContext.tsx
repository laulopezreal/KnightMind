import { createContext, useContext, useState, type ReactNode } from 'react';

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
    // Initialize from localStorage (same pattern as ChessUsernameContext)
    const [sessionType, setSessionTypeState] = useState<SessionType>(() => {
        if (typeof window !== 'undefined') {
            const saved = localStorage.getItem('knightmind:puzzle_mode');
            return (saved as SessionType) || 'standard';
        }
        return 'standard';
    });

    const [targetAccuracy, setTargetAccuracyState] = useState(() => {
        if (typeof window !== 'undefined') {
            const saved = localStorage.getItem('knightmind:target_accuracy');
            return saved ? parseInt(saved, 10) : 80;
        }
        return 80;
    });

    const [targetTimeMinutes, setTargetTimeMinutesState] = useState(() => {
        if (typeof window !== 'undefined') {
            const saved = localStorage.getItem('knightmind:target_time_minutes');
            return saved ? parseInt(saved, 10) : 10;
        }
        return 10;
    });

    // Persist to localStorage (same pattern as ChessUsernameContext)
    const setSessionType = (type: SessionType) => {
        localStorage.setItem('knightmind:puzzle_mode', type);
        setSessionTypeState(type);
    };

    const setTargetAccuracy = (n: number) => {
        localStorage.setItem('knightmind:target_accuracy', n.toString());
        setTargetAccuracyState(n);
    };

    const setTargetTimeMinutes = (n: number) => {
        localStorage.setItem('knightmind:target_time_minutes', n.toString());
        setTargetTimeMinutesState(n);
    };

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
