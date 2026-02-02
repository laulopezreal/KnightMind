import { useTheme } from '../context/ThemeContext';

export default function ThemeToggle() {
    const { theme, toggleTheme } = useTheme();
    const isNight = theme === 'night';

    return (
        <div
            role="button"
            tabIndex={0}
            onClick={toggleTheme}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleTheme(); } }}
            className="w-[130px] h-[50px] flex justify-center items-center scale-85 origin-center select-none km-interactive km-focus-visible rounded-full"
        >
            <div className="relative h-10 w-28 rounded-full border-2 border-primary flex items-center overflow-hidden bg-toggle-bg transition-slow">
                {/* The "Yoke" (Moving Circle) */}
                <div
                    className={`
            h-6 w-7 rounded-full bg-toggle-knob
            absolute top-[6px]
            transition-all duration-300 ease-in-out
            ${isNight ? 'left-[calc(100%-38px)]' : 'left-[6px]'}
          `}
                />
            </div>
        </div>
    );
}
