import { useTheme } from '../context/ThemeContext';

export default function ThemeToggle() {
    const { theme, toggleTheme } = useTheme();
    const isNight = theme === 'night';

    return (
        <div
            role="switch"
            aria-checked={isNight}
            aria-label="Night theme"
            title={isNight ? 'Switch to day theme' : 'Switch to night theme'}
            tabIndex={0}
            onClick={toggleTheme}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleTheme(); } }}
            className="w-28 h-11 flex justify-center items-center select-none km-interactive km-focus-visible rounded-full"
        >
            <div className="relative h-10 w-28 rounded-full border-2 border-primary flex items-center overflow-hidden bg-toggle-bg transition-slow">
                {/* Track icons signal this is a day/night theme switch. Colored with
                    text-toggle-knob (the knob's own token) so they contrast the
                    now-opaque track in both themes; the knob overlaps the active
                    side, leaving the opposite icon visible. */}
                <span aria-hidden="true" className="absolute left-[9px] text-xs text-toggle-knob select-none">☀</span>
                <span aria-hidden="true" className="absolute right-[9px] text-xs text-toggle-knob select-none">☾</span>
                {/* The "Yoke" (Moving Circle) */}
                <div
                    className={`
            h-6 w-7 rounded-full bg-toggle-knob
            absolute top-[6px] z-10
            transition-all duration-300 ease-in-out
            ${isNight ? 'left-[calc(100%-38px)]' : 'left-[6px]'}
          `}
                />
            </div>
        </div>
    );
}
