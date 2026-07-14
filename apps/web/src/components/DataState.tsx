interface DataStateLoadingProps {
  label: string;
  compact?: boolean;
}

export function DataStateLoading({ label, compact = false }: DataStateLoadingProps) {
  if (compact) {
    return (
      <div className="flex items-center gap-2 text-primary/50 font-sans text-xs" role="status" aria-live="polite">
        <div className="animate-spin h-4 w-4 border-2 border-primary/20 border-t-primary rounded-full" aria-hidden="true" />
        <span>{label}</span>
      </div>
    );
  }

  return (
    <div className="flex items-center justify-center min-h-[40vh]" role="status" aria-live="polite">
      <div className="animate-spin h-12 w-12 border-4 border-primary/20 border-t-primary rounded-full" aria-hidden="true" />
      <span className="sr-only">{label}</span>
    </div>
  );
}

interface DataStateErrorProps {
  message: string;
  onRetry: () => void;
  retryLabel: string;
  ariaLabel: string;
  compact?: boolean;
}

export function DataStateError({ message, onRetry, retryLabel, ariaLabel, compact = false }: DataStateErrorProps) {
  return (
    <div className={`${compact ? "text-left p-4" : "max-w-md mx-auto mt-24 text-center p-8"} bg-red-500/5 border border-red-500/20 rounded-sm`} role="alert" aria-live="assertive">
      <p className="text-red-500 mb-4">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="px-6 py-2 border border-primary/20 rounded-sm km-interactive km-focus-visible"
        aria-label={ariaLabel}
      >
        {retryLabel}
      </button>
    </div>
  );
}

interface DataStateEmptyProps {
  title: string;
  description: string;
  actionLabel: string;
  onAction: () => void;
}

export function DataStateEmpty({ title, description, actionLabel, onAction }: DataStateEmptyProps) {
  return (
    <div className="bg-primary/5 border border-primary/10 rounded-sm p-10 text-center">
      <p className="text-primary/60 font-sans text-lg mb-3">{title}</p>
      <p className="text-primary/40 font-sans text-sm mb-6">{description}</p>
      <button
        type="button"
        onClick={onAction}
        className="px-6 py-2 bg-cta text-cta-fg rounded-sm font-serif transition-opacity hover:opacity-90 cursor-pointer km-focus-visible"
      >
        {actionLabel}
      </button>
    </div>
  );
}
