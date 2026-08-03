import { useState, useRef, useEffect } from 'react';
import { useChessUsername } from '../context/ChessUsernameContext';
import { validateChessComUser, ApiError } from '../api';

export default function UsernameDisplay() {
    const { username, setUsername, isEditorOpen, setEditorOpen } = useChessUsername();
    // const [isOpen, setIsOpen] = useState(false); // Removed local state
    const [inputValue, setInputValue] = useState('');
    const [isValidating, setIsValidating] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<HTMLInputElement>(null);
    const triggerRef = useRef<HTMLButtonElement>(null);

    const focusTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const disarmAutoFocus = () => {
        if (focusTimerRef.current !== null) clearTimeout(focusTimerRef.current);
        focusTimerRef.current = null;
    };

    // Close the editor and hand keyboard focus back to the trigger, so a
    // keyboard user isn't dropped to the top of the page after Escape/Save.
    const closeAndRestoreFocus = () => {
        // Disarm before moving focus, not in the effect cleanup. Cleanup only
        // runs once React commits the close, and anything that delays that
        // commit leaves the timer live — long enough to pull focus onto the
        // input we are dismissing, which the commit then unmounts, dropping
        // focus to <body> instead of the trigger.
        disarmAutoFocus();
        setEditorOpen(false);
        triggerRef.current?.focus();
    };

    useEffect(() => {
        if (!isEditorOpen) return;
        setInputValue(username);
        setError(null);
        // Deferred so the input exists and the open transition has begun.
        focusTimerRef.current = setTimeout(() => inputRef.current?.focus(), 100);
        return disarmAutoFocus;
    }, [isEditorOpen, username]);

    useEffect(() => {
        function handleClickOutside(event: MouseEvent) {
            if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
                setEditorOpen(false);
            }
        }
        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, [setEditorOpen]);

    const handleSave = async () => {
        const trimmed = inputValue.trim();
        if (!trimmed) {
            if (username) {
                setUsername('');
                closeAndRestoreFocus();
                return;
            }
            return;
        }

        if (trimmed === username) {
            closeAndRestoreFocus();
            return;
        }

        setIsValidating(true);
        setError(null);

        try {
            // Use the shared API helper to keep /api proxy usage consistent across the app.
            const data = await validateChessComUser(trimmed);

            if (!data.valid) {
                setError(data.error || 'User not found on Chess.com');
                return;
            }

            setUsername(data.username || trimmed);
            closeAndRestoreFocus();
        } catch (err) {
            if (err instanceof ApiError) {
                if (err.detail) console.error('[connect]', err.detail);
                setError(err.message || 'Could not validate username');
            } else {
                setError('Could not validate username');
            }
        } finally {
            setIsValidating(false);
        }
    };

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter') handleSave();
        if (e.key === 'Escape') closeAndRestoreFocus();
    };

    return (
        <div ref={containerRef} className="relative font-sans">
            <button
                ref={triggerRef}
                type="button"
                onClick={() => setEditorOpen(!isEditorOpen)}
                aria-haspopup="dialog"
                aria-expanded={isEditorOpen}
                className={`
                    km-interactive km-focus-visible flex items-center gap-2 px-3 py-1.5 min-h-11 rounded-sm transition-all duration-300
                    ${username
                        ? 'text-primary/70'
                        : 'text-accent border border-accent/20 bg-accent/5'
                    }
                    ${isEditorOpen ? 'bg-primary/5 text-primary' : ''}
                `}
            >
                {username ? (
                    <>
                        <span className="text-xs uppercase tracking-wider">Chess.com</span>
                        <span className="font-medium truncate max-w-[100px] md:max-w-none">· {username}</span>
                    </>
                ) : (
                    <span className="text-sm font-medium">Set Chess.com username</span>
                )}
            </button>

            {isEditorOpen && (
                <div className="absolute top-full right-0 mt-2 w-72 bg-bg-primary border border-primary/20 shadow-xl rounded-sm p-4 z-50 animate-teedin">
                    <label htmlFor="chesscom-username-input" className="block text-xs font-sans uppercase tracking-widest text-primary/70 mb-2">
                        Chess.com Username
                    </label>
                    <div className="flex gap-2">
                        <input
                            id="chesscom-username-input"
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
                            type="button"
                            onClick={handleSave}
                            disabled={isValidating}
                            className={`px-4 py-2 bg-primary text-bg-primary font-medium rounded-sm transition-opacity km-focus-visible ${isValidating ? 'km-interactive-disabled' : 'hover:opacity-90 cursor-pointer'}`}
                        >
                            {isValidating ? '...' : 'Save'}
                        </button>
                    </div>
                    {error && (
                        <p className="mt-2 text-xs text-negative">{error}</p>
                    )}
                </div>
            )}
        </div>
    );
}
