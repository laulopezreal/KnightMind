import { useEffect, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { FocusTrap } from 'focus-trap-react';
import { usePuzzleMode } from '../context/PuzzleModeContext';

interface SidebarProps {
    mobileOpen?: boolean;
    onMobileClose?: () => void;
}

const NavItem = ({ to, label, isActive }: { to: string; label: string; isActive: boolean }) => (
    <Link
        to={to}
        // Inactive items keep the lighter weight for hierarchy but sit at
        // opacity-70 so they clear the 4.5:1 contrast minimum in both themes
        // (opacity-50 rendered ~3.4:1). Active items stay full-opacity + medium.
        className={`block py-2 text-lg tracking-wide transition-all duration-500 ease-in-out km-interactive km-focus-visible rounded-sm px-1 -mx-1 ${isActive ? 'opacity-100 font-medium' : 'opacity-70 font-light'}`}
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
        className={`flex items-center min-h-11 text-sm transition-all duration-300 km-focus-visible rounded-sm px-2 -mx-1 text-left outline-none ${
            isActive
                ? 'opacity-100 font-medium km-interactive'
                : 'opacity-70 font-light km-interactive hover:opacity-90'
        }`}
    >
        {label}
        {/* No /40 alpha: it compounded with the button's opacity to ~0.28 (fails
            AA). Full text-primary rides the button opacity (0.7 inactive / 1.0
            active), which clears 4.5:1 while the badge stays visually distinct via
            size + tracking. */}
        {badge && <span className="text-xs text-primary ml-1 uppercase tracking-wider">{badge}</span>}
    </button>
);

export default function Sidebar({ mobileOpen = false, onMobileClose }: SidebarProps) {
    const location = useLocation();
    const { sessionType, setSessionType } = usePuzzleMode();
    const isPuzzlesRoute = location.pathname.startsWith('/puzzles');
    const closeButtonRef = useRef<HTMLButtonElement>(null);

    // Track the mobile (drawer) breakpoint so we can mark the always-mounted aside
    // `inert` while it's the *closed* off-screen drawer — otherwise its nav links
    // stay in the tab order behind the page (phantom tab stops). On desktop the
    // aside is the real sidebar and must stay interactive.
    const [isMobileViewport, setIsMobileViewport] = useState(
        () => typeof window !== 'undefined' && typeof window.matchMedia === 'function'
            ? window.matchMedia('(max-width: 767px)').matches
            : false,
    );
    useEffect(() => {
        if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
        const mq = window.matchMedia('(max-width: 767px)');
        const onChange = (e: MediaQueryListEvent) => setIsMobileViewport(e.matches);
        mq.addEventListener('change', onChange);
        return () => mq.removeEventListener('change', onChange);
    }, []);

    // Lock body scroll while the mobile drawer is open so the page behind the
    // scrim doesn't scroll (matches Modal's behaviour).
    useEffect(() => {
        if (!mobileOpen) return;
        document.body.classList.add('overflow-hidden');
        return () => document.body.classList.remove('overflow-hidden');
    }, [mobileOpen]);

    // The aside is always mounted and only becomes the desktop sidebar via CSS
    // (`md:` = >=768px). If the drawer is open when the viewport grows to desktop
    // (rotate/resize), close it: otherwise the focus trap below would stay active
    // on the now-static sidebar and strand keyboard focus (the ✕ is md:hidden).
    useEffect(() => {
        if (!mobileOpen) return;
        if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
        const desktop = window.matchMedia('(min-width: 768px)');
        const onChange = (e: MediaQueryListEvent) => { if (e.matches) onMobileClose?.(); };
        desktop.addEventListener('change', onChange);
        if (desktop.matches) onMobileClose?.();
        return () => desktop.removeEventListener('change', onChange);
    }, [mobileOpen, onMobileClose]);

    const handleLinkClick = () => {
        onMobileClose?.();
    };

    // Close the mobile drawer on Escape. FocusTrap (below) handles moving focus
    // into the panel on open and restoring it to the hamburger on close; we keep
    // Escape here rather than via escapeDeactivates so the React `mobileOpen`
    // state and the trap stay in sync.
    useEffect(() => {
        if (!mobileOpen) return;
        const onKeyDown = (e: KeyboardEvent) => {
            if (e.key === 'Escape') onMobileClose?.();
        };
        document.addEventListener('keydown', onKeyDown);
        return () => document.removeEventListener('keydown', onKeyDown);
    }, [mobileOpen, onMobileClose]);

    return (
        <>
            {mobileOpen && (
                <button
                    type="button"
                    className="fixed inset-0 bg-black/40 backdrop-blur-sm z-40 md:hidden"
                    // Presentational scrim: the panel's ✕ button is the accessible
                    // close control, so keep this out of the a11y tree / tab order
                    // to avoid a duplicate "Close navigation menu" control.
                    aria-hidden="true"
                    tabIndex={-1}
                    onClick={onMobileClose}
                />
            )}

            <FocusTrap
                active={mobileOpen}
                focusTrapOptions={{
                    // Scrim click already closes via onMobileClose; allow it so the
                    // trap doesn't swallow the outside click.
                    allowOutsideClick: true,
                    escapeDeactivates: false,
                    initialFocus: () => closeButtonRef.current || undefined,
                    // On close, hand focus back to whatever opened the drawer.
                    returnFocusOnDeactivate: true,
                }}
            >
            <aside
                // Only the *closed* mobile drawer is inert: on desktop the aside is
                // the visible sidebar and must stay interactive.
                inert={!mobileOpen && isMobileViewport}
                className={`fixed left-0 top-0 h-full w-72 md:w-64 flex flex-col justify-between p-6 md:p-12 z-50 bg-primary border-r border-primary/10 transition-transform duration-300 md:translate-x-0 ${
                    mobileOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0'
                }`}
                aria-label="Sidebar"
            >
            <button
                ref={closeButtonRef}
                type="button"
                onClick={onMobileClose}
                aria-label="Close navigation menu"
                className="absolute top-4 right-4 h-11 w-11 flex items-center justify-center rounded-sm border border-primary/20 km-interactive km-focus-visible md:hidden"
            >
                <span className="text-xl leading-none" aria-hidden="true">✕</span>
            </button>

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
                </nav>
            </div>

            <div className="text-xs font-serif opacity-60 tracking-widest">
                KNIGHTMIND
            </div>
            </aside>
            </FocusTrap>
        </>
    );
}
