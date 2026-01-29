import type { ReactNode } from 'react';
import Sidebar from './Sidebar';
import ThemeToggle from './ThemeToggle';

interface LayoutProps {
    children: ReactNode;
}

export default function Layout({ children }: LayoutProps) {
    return (
        <div className="min-h-screen font-serif selection:bg-chess-brown-700 selection:text-chess-cream-100">
            <Sidebar />

            <div className="absolute top-8 right-8 z-50">
                <ThemeToggle />
            </div>

            <main className="ml-24 md:ml-64 min-h-screen p-8 md:p-20 flex flex-col max-w-5xl mx-auto opacity-0 animate-teedin">
                <div className="flex-1 w-full mt-20 md:mt-0">
                    {children}
                </div>
            </main>
        </div>
    );
}
