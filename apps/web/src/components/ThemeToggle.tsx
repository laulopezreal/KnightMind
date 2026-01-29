import { useState, useEffect } from 'react';

export default function ThemeToggle() {
    const [isNight, setIsNight] = useState(true);

    useEffect(() => {
        document.body.className = isNight ? 'night' : 'day';
    }, [isNight]);

    const toggleTheme = () => setIsNight(!isNight);

    return (
        <div
            className={`
        w-[130px] h-[50px] flex justify-center items-center scale-85 origin-center cursor-pointer select-none
        transition-transform duration-300 hover:scale-95
      `}
            onClick={toggleTheme}
        >
            <div className={`
        relative h-10 w-28 rounded-full border-2 border-primary
        flex items-center overflow-hidden
        ${isNight ? 'bg-chess-brown-700' : 'bg-chess-cream-200'}
        transition-slow
      `}>
                {/* The "Yoke" (Moving Circle) */}
                <div
                    className={`
            h-6 w-7 rounded-full bg-toggle-knob
            absolute top-[6px]
            transition-all duration-[3000ms] ease-in-out
            ${isNight ? 'left-[calc(100%-38px)] bg-chess-cream-100' : 'left-[6px] bg-chess-brown-900'}
          `}
                />
            </div>
        </div>
    );
}
