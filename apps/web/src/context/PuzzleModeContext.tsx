import { createContext, useContext, type ReactNode } from 'react';

type SessionType = 'standard';

interface PuzzleModeContextType {
    sessionType: SessionType;
}

const PuzzleModeContext = createContext<PuzzleModeContextType | undefined>(undefined);

export function PuzzleModeProvider({ children }: { children: ReactNode }) {
    return (
        <PuzzleModeContext.Provider value={{ sessionType: 'standard' }}>
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
