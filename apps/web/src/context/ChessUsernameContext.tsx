import { createContext, useContext, useState, useEffect, type ReactNode } from 'react';

interface ChessUsernameContextType {
    username: string;
    setUsername: (username: string) => void;
    isLoading: boolean;
    isEditorOpen: boolean;
    setEditorOpen: (isOpen: boolean) => void;
}

const ChessUsernameContext = createContext<ChessUsernameContextType | undefined>(undefined);

export function ChessUsernameProvider({ children }: { children: ReactNode }) {
    const [username, setUsernameState] = useState('');
    const [isLoading, setIsLoading] = useState(true);
    const [isEditorOpen, setEditorOpen] = useState(false);

    useEffect(() => {
        const stored = localStorage.getItem('knightmind:chesscom_username');
        if (stored) {
            setUsernameState(stored);
        }
        setIsLoading(false);
    }, []);

    const setUsername = (newUsername: string) => {
        if (newUsername) {
            localStorage.setItem('knightmind:chesscom_username', newUsername);
        } else {
            localStorage.removeItem('knightmind:chesscom_username');
        }
        setUsernameState(newUsername);
    };

    return (
        <ChessUsernameContext.Provider value={{ username, setUsername, isLoading, isEditorOpen, setEditorOpen }}>
            {children}
        </ChessUsernameContext.Provider>
    );
}


export function useChessUsername() {
    const context = useContext(ChessUsernameContext);
    if (context === undefined) {
        throw new Error('useChessUsername must be used within a ChessUsernameProvider');
    }
    return context;
}
