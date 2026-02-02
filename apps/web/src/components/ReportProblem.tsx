const ISSUES_URL = 'https://github.com/laulopezreal/KnightMind/issues';

export function ReportProblem() {
    return (
        <a
            href={ISSUES_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="fixed bottom-6 right-6 z-40 p-2 rounded-full transition-all duration-500 opacity-40 hover:opacity-100 km-focus-visible"
            aria-label="Report a problem"
            title="Report a problem"
        >
            <svg
                viewBox="0 0 24 24"
                className="w-5 h-5"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
            >
                {/* Antennae */}
                <path d="M8 6.5 L6 2" />
                <circle cx="5.5" cy="1.5" r="1" fill="currentColor" stroke="none" />
                <path d="M16 6.5 L18 2" />
                <circle cx="18.5" cy="1.5" r="1" fill="currentColor" stroke="none" />
                {/* Head */}
                <circle cx="12" cy="8" r="2.5" />
                {/* Body */}
                <ellipse cx="12" cy="14.5" rx="4" ry="3.5" />
                {/* Abdomen */}
                <ellipse cx="12" cy="20" rx="3.5" ry="3" />
                {/* Legs - left */}
                <path d="M8.5 12 L4 9.5" />
                <path d="M8 14.5 L3 14.5" />
                <path d="M8.5 17 L4 19.5" />
                {/* Legs - right */}
                <path d="M15.5 12 L20 9.5" />
                <path d="M16 14.5 L21 14.5" />
                <path d="M15.5 17 L20 19.5" />
            </svg>
        </a>
    );
}
