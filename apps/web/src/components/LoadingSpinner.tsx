interface LoadingSpinnerProps {
  size?: 'sm' | 'md' | 'lg';
  label?: string;
}

/**
 * Reusable loading spinner component with accessibility support
 * Follows design system with size variants and proper ARIA labels
 */
export function LoadingSpinner({ size = 'md', label }: LoadingSpinnerProps) {
  const sizeClasses = {
    sm: 'h-4 w-4 border-2',
    md: 'h-12 w-12 border-4',
    lg: 'h-16 w-16 border-4'
  };

  return (
    <div className="flex flex-col items-center justify-center gap-2">
      <div
        className={`${sizeClasses[size]} border-primary/20 border-t-primary rounded-full animate-spin`}
        role="status"
        aria-label={label || 'Loading...'}
      />
      {label && <span className="sr-only">{label}</span>}
    </div>
  );
}
