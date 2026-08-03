import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { getOpeningPractice, type OpeningPractice } from '../api/users';

interface PractiseOpeningLinkProps {
    username: string;
    /** The tree node's full opening name, e.g. "Sicilian Defense: Najdorf Variation". */
    openingName: string | null;
}

/**
 * From "I lose 62% here" to actually drilling it.
 *
 * The natural response to a bad line is practice, not analysis — but the link
 * has to be honest about what it can serve. Diagnoses carry both the full line
 * and its family, so the server reports how many exist at each granularity and
 * which one is worth offering; this renders that verdict rather than deciding
 * it.
 *
 * Three states, and the middle one is the point:
 *
 * - **line** — enough puzzles from this exact line. "Practise this line (7)".
 * - **family** — the line is too thin to drill on its own, so the offer widens
 *   and *says so*. "Only 2 in this line — practise Sicilian Defense (101)".
 *   Silently serving 101 Sicilians under a Najdorf label is the mislabelling
 *   this design exists to avoid.
 * - **none** — nothing to practise. Renders nothing: a link to an empty list is
 *   worse than no link.
 *
 * The family is never derived here. It comes from the server, which splits the
 * name with the same rule the extraction used — a second copy in TypeScript is
 * how the two ends drift the first time that rule changes.
 */
export function PractiseOpeningLink({ username, openingName }: PractiseOpeningLinkProps) {
    const [practice, setPractice] = useState<OpeningPractice | null>(null);

    useEffect(() => {
        if (!username || !openingName) return;
        let cancelled = false;
        getOpeningPractice(username, openingName)
            .then((result) => {
                if (!cancelled) setPractice(result);
            })
            // Supplementary: an unnamed line or a failed lookup simply offers
            // no practice, it does not break the panel.
            .catch(() => {
                if (!cancelled) setPractice(null);
            });

        return () => {
            cancelled = true;
        };
    }, [username, openingName]);

    // Matched against the *currently* selected line rather than cleared in the
    // effect: a synchronous setState there triggers cascading renders, and this
    // also stops a previous line's verdict rendering for a moment after the
    // selection changes.
    if (!practice || practice.opening_name !== openingName) return null;
    if (practice.scope === 'none') return null;

    const isLine = practice.scope === 'line';
    // Into Train, not the library: the ask was to drill the line, and browsing
    // a filtered list is a different thing. The session biases toward these
    // puzzles rather than filtering to them, so a line with nothing due gives
    // an ordinary session instead of a dead end — same contract as focus_cause.
    const target = isLine ? practice.opening_name : practice.opening_family;
    const scope = isLine ? 'line' : 'family';
    const href =
        `/puzzles?focus_opening=${encodeURIComponent(target)}` +
        `&focus_opening_scope=${scope}`;
    const count = isLine ? practice.line_count : practice.family_count;

    return (
        // `relative flex` + an absolutely-positioned note keeps this element
        // exactly one button tall. Letting the note grow the wrapper made it the
        // tallest item in an `items-center` row, which centred it and left this
        // button 10px above "Analyse in Engine"; `flex` then removes the
        // inline-block baseline gap that kept a 2px offset after that.
        <div className="relative flex">
            <Link
                to={href}
                className="px-4 py-2 border border-primary/20 text-primary rounded-sm font-serif text-sm km-interactive km-focus-visible transition-all"
            >
                {isLine
                    ? `Practise this line (${count}) →`
                    : `Practise ${practice.opening_family} (${count}) →`}
            </Link>
            {!isLine && (
                // Naming the shortfall is what keeps the widened offer honest.
                <span className="absolute top-full left-0 mt-1 whitespace-nowrap font-sans text-xs text-primary/70">
                    Only {practice.line_count} puzzle
                    {practice.line_count === 1 ? '' : 's'} from this exact line.
                </span>
            )}
        </div>
    );
}
