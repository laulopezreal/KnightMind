import { useState, useRef, useEffect } from 'react';
import { useChessUsername } from '../context/ChessUsernameContext';

export default function UsernameDisplay() {
    const { username, setUsername, isEditorOpen, setEditorOpen } = useChessUsername();
    // const [isOpen, setIsOpen] = useState(false); // Removed local state
    const [inputValue, setInputValue] = useState('');
    const [isValidating, setIsValidating] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        if (isEditorOpen) {
            setInputValue(username);
            setError(null);
            setTimeout(() => inputRef.current?.focus(), 100);
        }
    }, [isEditorOpen, username]);

    useEffect(() => {
        function handleClickOutside(event: MouseEvent) {
            if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
                setEditorOpen(false);
            }
        }
        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, []);

    const handleSave = async () => {
        const trimmed = inputValue.trim();
        if (!trimmed) {
            if (username) {
                // If clearing text, do we allow removing the username? 
                // The requirements imply "Single source of truth" and "Set it intentionally".
                // Let's assume empty string = remove.
                setUsername('');
                setEditorOpen(false);
                return;
            }
            return;
        }

        if (trimmed === username) {
            setEditorOpen(false);
            return;
        }

        setIsValidating(true);
        setError(null);

        try {
            // Simple validation check against Chess.com API
            const res = await fetch(`https://api.chess.com/pub/player/${trimmed}`);
            if (res.status === 404) {
                setError('User not found on Chess.com');
                setIsValidating(false);
                return;
            }
            if (!res.ok) {
                throw new Error('Validation failed');
            }

            setUsername(trimmed);
            setEditorOpen(false);
        } catch (err) {
            setError('Could not validate username');
        } finally {
            setIsValidating(false);
        }
    };

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter') handleSave();
        if (e.key === 'Escape') setEditorOpen(false);
    };

    return (
        <div ref={containerRef} className="relative font-sans">
            <button
                onClick={() => setEditorOpen(!isEditorOpen)}
                className={`
                    flex items-center gap-2 px-3 py-1.5 rounded-sm transition-all duration-300
                    ${username
                        ? 'text-primary/60 hover:text-primary'
                        : 'text-accent hover:text-accent/80 border border-accent/20 bg-accent/5'
                    }
                    ${isEditorOpen ? 'bg-primary/5 text-primary' : ''}
                `}
            >
                {username ? (
                    <>
                        <span className="opacity-50 text-xs uppercase tracking-wider">Chess.com</span>
                        <span className="font-medium truncate max-w-[100px] md:max-w-none">· {username}</span>
                    </>
                ) : (
                    <span className="text-sm font-medium">Set Chess.com username</span>
                )}
            </button>

            {isEditorOpen && (
                <div className="absolute top-full right-0 mt-2 w-72 bg-bg-primary border border-primary/20 shadow-xl rounded-sm p-4 z-50 animate-teedin">
                    <label className="block text-xs font-sans uppercase tracking-widest text-primary/40 mb-2">
                        Chess.com Username
                    </label>
                    <div className="flex gap-2">
                        <input
                            ref={inputRef}
                            type="text"
                            value={inputValue}
                            onChange={(e) => setInputValue(e.target.value)}
                            onKeyDown={handleKeyDown}
                            disabled={isValidating}
                            placeholder="username"
                            className="flex-1 bg-primary/5 border border-primary/10 px-3 py-2 text-primary focus:outline-none focus:border-primary/40 rounded-sm transition-colors"
                        />
                        <button
                            onClick={handleSave}
                            disabled={isValidating}
                            className="px-4 py-2 bg-primary text-bg-primary font-medium hover:opacity-90 rounded-sm disabled:opacity-50 transition-opacity"
                        >
                            {isValidating ? '...' : 'Save'}
                        </button>
                    </div>
                    {error && (
                        <p className="mt-2 text-xs text-red-500">{error}</p>
                    )}
                </div>
            )}
        </div>
    );
}
