import { type JobStatusResponse } from '../api';

interface JobStatusCardProps {
    status: JobStatusResponse['status'] | null;
    progress?: number;
    message?: string;
    /** Faint expectation-setting line shown under the bar while processing (e.g. "~2-3 min"). */
    hint?: string;
    error?: string;
    onCancel?: () => void;
}

export function JobStatusCard({ status, progress = 0, message, hint, error, onCancel }: JobStatusCardProps) {
    if (!status) return null;

    const isProcessing = status === 'queued' || status === 'running';
    const isError = status === 'failed';
    const isSuccess = status === 'succeeded';

    return (
        <div className="bg-primary/5 border border-primary/10 rounded-sm p-6 backdrop-blur-sm space-y-4 animate-teedin">
            <div className="flex items-center justify-between">
                <h3 className="font-serif text-xl text-primary">
                    {isProcessing && 'Generating Puzzles...'}
                    {isSuccess && 'Generation Complete'}
                    {isError && 'Generation Failed'}
                </h3>
                {isProcessing && (
                    <div className="animate-spin h-5 w-5 border-2 border-primary/20 border-t-primary rounded-full" />
                )}
            </div>

            {(message || error) && (
                <p className={`font-sans text-sm ${isError ? 'text-negative' : 'text-primary/70'}`}>
                    {error || message}
                </p>
            )}

            {isProcessing && (
                <>
                    {/* Faint themeable track: bg-primary/10 never rendered (unregistered
                        token) and bg-current/10 collapses to solid currentColor in
                        Tailwind v4, so mix the runtime ink var inline. */}
                    <div
                        className="w-full rounded-full h-1.5 overflow-hidden"
                        style={{ backgroundColor: 'color-mix(in srgb, var(--text-primary) 12%, transparent)' }}
                    >
                        <div
                            className="bg-primary h-full transition-all duration-500 ease-out"
                            style={{ width: `${Math.max(5, progress)}%` }} // Minimum 5% visibility
                        />
                    </div>
                    {hint && (
                        <p className="font-sans text-xs text-primary/50">{hint}</p>
                    )}
                    {onCancel && (
                        <button
                            type="button"
                            onClick={onCancel}
                            className="km-interactive km-focus-visible px-4 py-2 text-sm border border-negative-soft text-negative rounded-sm font-serif transition-all hover:bg-negative-soft"
                        >
                            Cancel
                        </button>
                    )}
                </>
            )}

            {isSuccess && (
                <div className="flex items-center gap-2 text-positive font-serif text-sm">
                    <span>✓</span>
                    <span>Ready to solve!</span>
                </div>
            )}
        </div>
    );
}
