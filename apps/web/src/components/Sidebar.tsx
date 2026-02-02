import { Link, useLocation } from 'react-router-dom';

const NavItem = ({ to, label, isActive }: { to: string; label: string; isActive: boolean }) => (
    <Link
        to={to}
        className={`block py-2 text-lg tracking-wide transition-all duration-500 ease-in-out km-interactive km-focus-visible rounded-sm px-1 -mx-1 ${isActive ? 'opacity-100 font-medium' : 'opacity-50 font-light'}`}
        aria-current={isActive ? 'page' : undefined}
    >
        {label}
    </Link>
);

export default function Sidebar() {
    const location = useLocation();

    return (
        <aside className="fixed left-0 top-0 h-full w-24 md:w-64 flex flex-col justify-between p-8 md:p-12 z-50">
            <div>
                {/* Logo / Brand */}
                <Link to="/" className="block mb-20 km-interactive km-focus-visible rounded-full w-8 h-8 inline-block" aria-label="KnightMind home">
                    <div className="w-8 h-8 rounded-full bg-current opacity-80" />
                </Link>

                {/* Navigation */}
                <nav className="space-y-6 font-sans" aria-label="Primary navigation">
                    <NavItem to="/" label="Home" isActive={location.pathname === '/'} />
                    <NavItem to="/dashboard" label="Dashboard" isActive={location.pathname === '/dashboard'} />
                    <NavItem to="/openings" label="Openings" isActive={location.pathname === '/openings'} />
                    <NavItem to="/engine" label="Engine" isActive={location.pathname === '/engine'} />
                    <NavItem to="/puzzles" label="Puzzles" isActive={location.pathname === '/puzzles'} />
                    <NavItem to="/rating-insights" label="Ratings" isActive={location.pathname === '/rating-insights'} />
                    <NavItem to="/ops" label="Ops" isActive={location.pathname === '/ops'} />
                </nav>
            </div>

            <div className="text-xs font-serif opacity-30 tracking-widest">
                KNIGHTMIND
            </div>
        </aside>
    );
}
