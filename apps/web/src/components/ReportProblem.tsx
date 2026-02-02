import { useState } from 'react';
import { useLocation } from 'react-router-dom';
import { useChessUsername } from '../context/ChessUsernameContext';
import { submitReport } from '../api/reports';
import type { ReportRequest } from '../api/reports';

type Category = ReportRequest['category'];
type Status = 'idle' | 'submitting' | 'success' | 'error';

const CATEGORIES: { value: Category; label: string }[] = [
    { value: 'bug', label: 'Bug' },
    { value: 'feature', label: 'Feature idea' },
    { value: 'feedback', label: 'General feedback' },
];

function AntIcon({ className }: { className?: string }) {
    return (
        <svg
            viewBox="0 0 100 100"
            className={className}
            fill="currentColor"
            aria-hidden="true"
        >
            {/* Head */}
            <ellipse cx="50" cy="22" rx="14" ry="12" />
            {/* Eyes */}
            <circle cx="44" cy="19" r="3" className="fill-[var(--bg-primary)]" />
            <circle cx="56" cy="19" r="3" className="fill-[var(--bg-primary)]" />
            <circle cx="45" cy="18" r="1.2" />
            <circle cx="57" cy="18" r="1.2" />
            {/* Antennae */}
            <path d="M42 12 Q36 2 28 4" strokeWidth="2.5" stroke="currentColor" fill="none" strokeLinecap="round" />
            <circle cx="27" cy="4" r="2.5" />
            <path d="M58 12 Q64 2 72 4" strokeWidth="2.5" stroke="currentColor" fill="none" strokeLinecap="round" />
            <circle cx="73" cy="4" r="2.5" />
            {/* Smile */}
            <path d="M44 26 Q50 31 56 26" strokeWidth="2" stroke="var(--bg-primary)" fill="none" strokeLinecap="round" />
            {/* Thorax */}
            <ellipse cx="50" cy="42" rx="11" ry="10" />
            {/* Abdomen */}
            <ellipse cx="50" cy="66" rx="18" ry="18" />
            {/* Abdomen stripes */}
            <path d="M34 60 Q50 56 66 60" strokeWidth="2" stroke="var(--bg-primary)" fill="none" opacity="0.3" />
            <path d="M33 68 Q50 64 67 68" strokeWidth="2" stroke="var(--bg-primary)" fill="none" opacity="0.3" />
            <path d="M35 76 Q50 72 65 76" strokeWidth="2" stroke="var(--bg-primary)" fill="none" opacity="0.3" />
            {/* Legs - left */}
            <path d="M40 38 Q28 34 20 28" strokeWidth="2.5" stroke="currentColor" fill="none" strokeLinecap="round" />
            <path d="M40 44 Q24 44 16 42" strokeWidth="2.5" stroke="currentColor" fill="none" strokeLinecap="round" />
            <path d="M42 50 Q28 56 18 58" strokeWidth="2.5" stroke="currentColor" fill="none" strokeLinecap="round" />
            {/* Legs - right */}
            <path d="M60 38 Q72 34 80 28" strokeWidth="2.5" stroke="currentColor" fill="none" strokeLinecap="round" />
            <path d="M60 44 Q76 44 84 42" strokeWidth="2.5" stroke="currentColor" fill="none" strokeLinecap="round" />
            <path d="M58 50 Q72 56 82 58" strokeWidth="2.5" stroke="currentColor" fill="none" strokeLinecap="round" />
        </svg>
    );
}

export function ReportProblem() {
    const [isOpen, setIsOpen] = useState(false);
    const [category, setCategory] = useState<Category>('bug');
    const [description, setDescription] = useState('');
    const [status, setStatus] = useState<Status>('idle');
    const location = useLocation();
    const { username } = useChessUsername();

    const resetForm = () => {
        setCategory('bug');
        setDescription('');
        setStatus('idle');
    };

    const handleClose = () => {
        setIsOpen(false);
        // Reset after close animation
        setTimeout(resetForm, 200);
    };

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (description.trim().length < 10) return;

        setStatus('submitting');
        try {
            await submitReport({
                category,
                description: description.trim(),
                page: location.pathname,
                username: username || undefined,
            });
            setStatus('success');
            setTimeout(handleClose, 1500);
        } catch {
            setStatus('error');
        }
    };

    return (
        <>
            {/* Floating ant button */}
            <button
                onClick={() => setIsOpen(!isOpen)}
                className="fixed bottom-6 right-6 z-40 p-2 rounded-full transition-all duration-500 opacity-40 hover:opacity-100 km-focus-visible"
                aria-label="Report a problem"
                title="Report a problem"
            >
                <AntIcon className="w-7 h-7" />
            </button>

            {/* Popover form */}
            {isOpen && (
                <div className="fixed bottom-16 right-6 z-50 animate-teedin" style={{ animationDuration: '0.4s' }}>
                    <div className="bg-[var(--bg-primary)] border border-[var(--border-primary)]/20 rounded-sm shadow-lg w-80 p-6">
                        <div className="flex items-center justify-between mb-4">
                            <div className="flex items-center gap-2">
                                <AntIcon className="w-5 h-5 opacity-60" />
                                <h3 className="font-serif text-lg tracking-wide">Report a problem</h3>
                            </div>
                            <button
                                onClick={handleClose}
                                className="opacity-40 hover:opacity-100 transition-opacity duration-300 km-focus-visible rounded-sm p-1"
                                aria-label="Close"
                            >
                                <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4" aria-hidden="true">
                                    <path d="M6 6l8 8M14 6l-8 8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" fill="none" />
                                </svg>
                            </button>
                        </div>

                        {status === 'success' ? (
                            <p className="text-sm font-sans opacity-70 py-4 text-center">
                                Thank you for your report.
                            </p>
                        ) : (
                            <form onSubmit={handleSubmit} className="space-y-4">
                                {/* Category selector */}
                                <div className="flex gap-2 font-sans">
                                    {CATEGORIES.map((cat) => (
                                        <button
                                            key={cat.value}
                                            type="button"
                                            onClick={() => setCategory(cat.value)}
                                            className={`text-xs px-3 py-1.5 rounded-sm border transition-all duration-300 km-focus-visible ${
                                                category === cat.value
                                                    ? 'border-[var(--border-primary)]/40 opacity-100 font-medium'
                                                    : 'border-transparent opacity-50 hover:opacity-80'
                                            }`}
                                        >
                                            {cat.label}
                                        </button>
                                    ))}
                                </div>

                                {/* Description */}
                                <textarea
                                    value={description}
                                    onChange={(e) => setDescription(e.target.value)}
                                    placeholder="Describe what happened..."
                                    rows={4}
                                    maxLength={2000}
                                    className="w-full bg-transparent border border-[var(--border-primary)]/15 rounded-sm px-3 py-2 text-sm font-sans placeholder:opacity-30 focus:outline-none focus:border-[var(--border-primary)]/40 transition-colors duration-300 resize-none"
                                    autoFocus
                                />

                                {/* Error message */}
                                {status === 'error' && (
                                    <p className="text-sm font-sans opacity-60">
                                        Something went wrong. Please try again.
                                    </p>
                                )}

                                {/* Submit */}
                                <div className="flex justify-end">
                                    <button
                                        type="submit"
                                        disabled={description.trim().length < 10 || status === 'submitting'}
                                        className={`font-serif text-sm tracking-wider px-5 py-2 rounded-sm border border-[var(--border-primary)]/20 transition-all duration-300 km-focus-visible ${
                                            description.trim().length < 10 || status === 'submitting'
                                                ? 'opacity-30 cursor-not-allowed'
                                                : 'opacity-80 hover:opacity-100 km-interactive'
                                        }`}
                                    >
                                        {status === 'submitting' ? 'Sending...' : 'Send'}
                                    </button>
                                </div>
                            </form>
                        )}
                    </div>
                </div>
            )}
        </>
    );
}
