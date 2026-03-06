import { Link, useLocation } from 'react-router-dom';
import { usePuzzleMode } from '../context/PuzzleModeContext';

const NavItem = ({ to, label, isActive }: { to: string; label: string; isActive: boolean }) => (
    <Link
        to={to}
        className={`block py-2 text-lg tracking-wide transition-all duration-500 ease-in-out km-interactive km-focus-visible rounded-sm px-1 -mx-1 ${isActive ? 'opacity-100 font-medium' : 'opacity-50 font-light'}`}
        aria-current={isActive ? 'page' : undefined}
    >
        {label}
    </Link>
);

const SubNavItem = ({
    label,
    isActive,
    onClick,
    badge,
    tooltip
}: {
    label: string;
    isActive: boolean;
    onClick: () => void;
    badge?: string;
    tooltip?: string;
}) => (
    <button
        onClick={onClick}
        title={tooltip}
        className={`block py-1 text-sm transition-all duration-300 km-focus-visible rounded-sm px-2 -mx-1 text-left outline-none ${
            isActive
                ? 'opacity-100 font-medium km-interactive'
                : 'opacity-50 font-light km-interactive hover:opacity-80'
        }`}
    >
        {label}
        {badge && <span className="text-xs text-primary/40 ml-1 uppercase tracking-wider">{badge}</span>}
    </button>
);

export default function Sidebar() {
    const location = useLocation();
    const { sessionType, setSessionType } = usePuzzleMode();
    const isPuzzlesRoute = location.pathname.startsWith('/puzzles');

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
                    <NavItem to="/library" label="Library" isActive={location.pathname.startsWith('/library')} />

                    {/* Train (formerly Puzzles) with sub-items */}
                    <div>
                        <NavItem to="/puzzles" label="Train (Puzzles)" isActive={location.pathname === '/puzzles'} />
                        {isPuzzlesRoute && (
                            <div className="ml-6 mt-2 space-y-1 border-l border-primary/10 pl-2">
                                <SubNavItem
                                    label="Standard"
                                    isActive={sessionType === 'standard'}
                                    onClick={() => setSessionType('standard')}
                                    tooltip="Classic puzzle solving - fully available now"
                                />
                                <SubNavItem
                                    label="Timed"
                                    badge="BETA"
                                    isActive={sessionType === 'timed'}
                                    onClick={() => setSessionType('timed')}
                                    tooltip="Coming soon: Race against the clock"
                                />
                                <SubNavItem
                                    label="Accuracy Goal"
                                    badge="BETA"
                                    isActive={sessionType === 'accuracy_goal'}
                                    onClick={() => setSessionType('accuracy_goal')}
                                    tooltip="Coming soon: Focus on precision"
                                />
                            </div>
                        )}
                    </div>

                    <NavItem to="/insights" label="Insights" isActive={location.pathname === '/insights'} />
                    <NavItem to="/rating-insights" label="Ratings" isActive={location.pathname === '/rating-insights'} />
                    <NavItem to="/how-it-works" label="How it Works" isActive={location.pathname === '/how-it-works'} />
                    <NavItem to="/ops" label="Ops" isActive={location.pathname === '/ops'} />
                </nav>
            </div>

            <div className="text-xs font-serif opacity-30 tracking-widest">
                KNIGHTMIND
            </div>
        </aside>
    );
}
