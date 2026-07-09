import { Link, useLocation } from 'react-router-dom';
import { usePuzzleMode } from '../context/PuzzleModeContext';

interface SidebarProps {
    mobileOpen?: boolean;
    onMobileClose?: () => void;
}

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

export default function Sidebar({ mobileOpen = false, onMobileClose }: SidebarProps) {
    const location = useLocation();
    const { sessionType, setSessionType } = usePuzzleMode();
    const isPuzzlesRoute = location.pathname.startsWith('/puzzles');

    const handleLinkClick = () => {
        onMobileClose?.();
    };

    return (
        <>
            {mobileOpen && (
                <button
                    type="button"
                    className="fixed inset-0 bg-primary/30 backdrop-blur-sm z-40 md:hidden"
                    aria-label="Close navigation menu"
                    onClick={onMobileClose}
                />
            )}

            <aside
                className={`fixed left-0 top-0 h-full w-72 md:w-64 flex flex-col justify-between p-6 md:p-12 z-50 bg-primary border-r border-primary/10 transition-transform duration-300 md:translate-x-0 ${
                    mobileOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0'
                }`}
                aria-label="Sidebar"
            >
            <div>
                {/* Logo / Brand */}
                <Link to="/" onClick={handleLinkClick} className="block mb-12 md:mb-20 km-interactive km-focus-visible rounded-full w-8 h-8 inline-block" aria-label="KnightMind home">
                    <div className="w-8 h-8 rounded-full bg-current opacity-80" />
                </Link>

                {/* Navigation */}
                <nav className="space-y-6 font-sans" aria-label="Primary navigation">
                    <div onClick={handleLinkClick}><NavItem to="/" label="Home" isActive={location.pathname === '/'} /></div>
                    <div onClick={handleLinkClick}><NavItem to="/dashboard" label="Dashboard" isActive={location.pathname === '/dashboard'} /></div>
                    <div onClick={handleLinkClick}><NavItem to="/openings" label="Openings" isActive={location.pathname === '/openings'} /></div>
                    <div onClick={handleLinkClick}><NavItem to="/engine" label="Engine" isActive={location.pathname === '/engine'} /></div>
                    <div onClick={handleLinkClick}><NavItem to="/library" label="Library" isActive={location.pathname.startsWith('/library')} /></div>

                    {/* Train (formerly Puzzles) with sub-items */}
                    <div>
                        <div onClick={handleLinkClick}><NavItem to="/puzzles" label="Train" isActive={location.pathname === '/puzzles'} /></div>
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

                    <div onClick={handleLinkClick}><NavItem to="/insights" label="Insights" isActive={location.pathname === '/insights'} /></div>
                    <div onClick={handleLinkClick}><NavItem to="/rating-insights" label="Ratings" isActive={location.pathname === '/rating-insights'} /></div>
                    <div onClick={handleLinkClick}><NavItem to="/ops" label="Ops" isActive={location.pathname === '/ops'} /></div>
                </nav>
            </div>

            <div className="text-xs font-serif opacity-30 tracking-widest">
                KNIGHTMIND
            </div>
            </aside>
        </>
    );
}
