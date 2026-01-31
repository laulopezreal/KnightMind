import { type JobStatusResponse } from '../api';

interface JobStatusCardProps {
    status: JobStatusResponse['status'] | null;
    progress?: number;
    message?: string;
    error?: string;
    onCancel?: () => void;
}

export function JobStatusCard({ status, progress = 0, message, error, onCancel }: JobStatusCardProps) {
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

            {message && (
                <p className={`font-sans text-sm ${isError ? 'text-red-500/80' : 'text-primary/60'}`}>
                    {error || message}
                </p>
            )}

            {isProcessing && (
                <>
                    <div className="w-full bg-primary/10 rounded-full h-1.5 overflow-hidden">
                        <div
                            className="bg-primary h-full transition-all duration-500 ease-out"
                            style={{ width: `${Math.max(5, progress)}%` }} // Minimum 5% visibility
                        />
                    </div>
                    {onCancel && (
                        <button
                            onClick={onCancel}
                            className="px-4 py-2 text-sm border border-red-500/30 text-red-500 hover:bg-red-500/10 rounded-sm font-serif transition-all"
                        >
                            Cancel
                        </button>
                    )}
                </>
            )}

            {isSuccess && (
                <div className="flex items-center gap-2 text-green-600 font-serif text-sm">
                    <span>✓</span>
                    <span>Ready to solve!</span>
                </div>
            )}
        </div>
    );
}
