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
                className="w-6 h-6"
                fill="currentColor"
                aria-hidden="true"
            >
                {/* GitHub-style bug/ant icon */}
                {/* Antennae */}
                <path d="M8 2a1 1 0 0 1 .707.293l2 2a1 1 0 0 1-1.414 1.414L8 4.414 6.707 5.707A1 1 0 0 1 5.293 4.293l2-2A1 1 0 0 1 8 2z" opacity="0" />
                <path d="M4.5 7.5A1.5 1.5 0 0 1 6 6h1.5L9 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" fill="none" />
                <path d="M19.5 7.5A1.5 1.5 0 0 0 18 6h-1.5L15 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" fill="none" />
                {/* Head */}
                <circle cx="12" cy="8" r="2.5" />
                {/* Body */}
                <ellipse cx="12" cy="15" rx="4.5" ry="6" />
                {/* Body segment line */}
                <line x1="7.5" y1="14" x2="16.5" y2="14" stroke="var(--bg-primary, white)" strokeWidth="1" />
                <line x1="7.5" y1="17" x2="16.5" y2="17" stroke="var(--bg-primary, white)" strokeWidth="1" />
                {/* Legs - left */}
                <path d="M7.5 12.5 L4 10.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" fill="none" />
                <path d="M7.5 15 L3.5 15" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" fill="none" />
                <path d="M7.5 17.5 L4 19.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" fill="none" />
                {/* Legs - right */}
                <path d="M16.5 12.5 L20 10.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" fill="none" />
                <path d="M16.5 15 L20.5 15" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" fill="none" />
                <path d="M16.5 17.5 L20 19.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" fill="none" />
            </svg>
        </a>
    );
}
