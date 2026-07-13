import { useState, type ReactNode } from 'react';
import Sidebar from './Sidebar';
import UsernameDisplay from './UsernameDisplay';
import ThemeToggle from './ThemeToggle';
import { ReportProblem } from './ReportProblem';
import { useChessUsername } from '../context/ChessUsernameContext';

interface LayoutProps {
    children: ReactNode;
}

export default function Layout({ children }: LayoutProps) {
    const [mobileNavOpen, setMobileNavOpen] = useState(false);
    const { username } = useChessUsername();
    const showUsernameDisplay = Boolean(username);

    return (
        <div className="min-h-screen font-serif selection:bg-chess-brown-700 selection:text-chess-cream-100">
            <Sidebar mobileOpen={mobileNavOpen} onMobileClose={() => setMobileNavOpen(false)} />

            <header aria-label="Site header" className="fixed top-0 left-0 right-0 z-30 flex md:hidden items-center justify-between px-5 py-4 bg-primary/95 backdrop-blur-sm border-b border-primary/10">
                <button
                    type="button"
                    onClick={() => setMobileNavOpen(true)}
                    aria-label="Open navigation menu"
                    aria-expanded={mobileNavOpen}
                    aria-controls="primary-sidebar"
                    className="h-11 w-11 flex items-center justify-center rounded-sm border border-primary/20 km-interactive km-focus-visible"
                >
                    <span className="text-xl leading-none" aria-hidden="true">☰</span>
                </button>
                <span className="text-sm font-sans tracking-[0.2em] opacity-70">KNIGHTMIND</span>
                <ThemeToggle />
            </header>

            {/* Desktop site chrome (username + theme). A <header> landmark so these
                controls aren't flagged as content outside any landmark. The mobile
                <header> above is display:none at md+, so only one banner is ever
                exposed; distinct aria-labels keep them unambiguous even if that CSS
                ever failed. */}
            <header aria-label="Account and preferences" className="absolute top-8 right-8 z-50 hidden md:flex items-center gap-4">
                {showUsernameDisplay && <UsernameDisplay />}
                <ThemeToggle />
            </header>

            <main className="ml-0 md:ml-64 min-h-screen p-5 pt-24 md:p-20 flex flex-col max-w-5xl mx-auto opacity-0 animate-teedin">
                <div className="flex-1 w-full">
                    {children}
                </div>
            </main>

            <ReportProblem />
        </div>
    );
}
