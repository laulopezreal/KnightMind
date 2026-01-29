import { Link, useLocation } from 'react-router-dom';

const NavItem = ({ to, label, isActive }: { to: string; label: string; isActive: boolean }) => (
    <Link
        to={to}
        className={`
      block py-2 text-lg tracking-wide transition-all duration-500 ease-in-out
      hover:opacity-70 hover:translate-x-1
      ${isActive ? 'opacity-100 font-medium' : 'opacity-50 font-light'}
    `}
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
                <Link to="/" className="block mb-20">
                    <div className="w-8 h-8 rounded-full bg-current opacity-80" />
                </Link>

                {/* Navigation */}
                <nav className="space-y-6 font-sans">
                    <NavItem to="/" label="Home" isActive={location.pathname === '/'} />
                    <NavItem to="/openings" label="Openings" isActive={location.pathname === '/openings'} />
                    <NavItem to="/engine" label="Engine" isActive={location.pathname === '/engine'} />
                    <NavItem to="/puzzles" label="Puzzles" isActive={location.pathname === '/puzzles'} />
                </nav>
            </div>

            <div className="text-xs font-serif opacity-30 tracking-widest">
                KNIGHTMIND
            </div>
        </aside>
    );
}
