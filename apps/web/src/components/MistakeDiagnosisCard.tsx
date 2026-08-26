import type { PuzzleDiagnosis } from '../api/puzzles';

interface MistakeDiagnosisCardProps {
    diagnosis: PuzzleDiagnosis | null;
    /** False while the puzzle is still being solved — see the note below. */
    revealed: boolean;
    loading?: boolean;
    savingConfirmation?: boolean;
    confirmationError?: string | null;
    onConfirm?: (cause: string) => void;
}

/**
 * Explains why this mistake probably happened, from the evidence behind it.
 *
 * Two rules shape this component:
 *
 * 1. **It never appears before the puzzle is resolved.** The evidence names the
 *    solution ("Best move: Qxd5", the squares it attacks, how long the winning
 *    line is), so rendering it mid-solve would hand over the answer. The card is
 *    post-mortem content and the caller gates it on `revealed`.
 *
 * The prose (`explanation`, `training_recommendation`) is present only on rows the
 * AI stage enriched. A rules-only row renders the cause and its evidence and nothing
 * else — which is a complete diagnosis, not a degraded one, so there is no placeholder
 * or "unavailable" treatment for its absence.
 *
 * 2. **Every state is a real answer, never an error.** A missing diagnosis is
 *    "not analysed yet", not a failure; an unsupported one says so plainly
 *    rather than inventing the least-bad cause. There is deliberately no
 *    confidence percentage: the underlying rule strength is an ordering prior,
 *    not a calibrated probability, and the API does not expose it.
 */
export function MistakeDiagnosisCard({
    diagnosis,
    revealed,
    loading = false,
    savingConfirmation = false,
    confirmationError = null,
    onConfirm,
}: MistakeDiagnosisCardProps) {
    // Solution-bearing content stays hidden until the puzzle is done with.
    if (!revealed) return null;

    if (loading) {
        return (
            <Shell>
                <p className="font-sans text-sm text-primary/70" role="status" aria-live="polite">
                    Loading diagnosis…
                </p>
            </Shell>
        );
    }

    if (!diagnosis) return null;

    if (diagnosis.state === 'withheld') {
        // The gate is shut for this puzzle. Without this branch the state fell
        // through to the 'ready' render and produced a framed card with an
        // empty heading -- deterministically, on the wrong-answer path, and
        // never refetched for that page visit.
        return (
            <Shell>
                <p className="font-sans text-sm text-primary/70">
                    Attempt this puzzle to see why the move was a mistake.
                </p>
            </Shell>
        );
    }

    if (diagnosis.state === 'pending') {
        return (
            <Shell>
                <p className="font-sans text-sm text-primary/70">
                    This mistake hasn’t been analysed yet.
                </p>
            </Shell>
        );
    }

    if (diagnosis.state === 'unavailable') {
        return (
            <Shell>
                <p className="font-sans text-sm text-primary/70">
                    This position can’t be analysed.
                </p>
            </Shell>
        );
    }

    if (diagnosis.state === 'unclear') {
        return (
            <Shell>
                <p className="font-sans text-sm text-primary/70">
                    No clear cause stands out for this one. The tactic is recorded, but the
                    evidence doesn’t point to a single habit worth training.
                </p>
            </Shell>
        );
    }

    const {
        primary_cause,
        primary_cause_label,
        secondary_cause_labels,
        evidence,
        phase,
        cause_options = [],
    } = diagnosis;
    const alternativeCauses = cause_options.filter((option) => option.value !== primary_cause);
    const canConfirm = !!onConfirm && !!primary_cause && cause_options.length > 0;

    return (
        <Shell>
            <div className="space-y-4">
                <div>
                    <p className="font-serif text-2xl text-primary">{primary_cause_label}</p>
                    {diagnosis.user_confirmed_cause && (
                        <p className="font-sans text-xs text-primary/70 mt-1">Your label</p>
                    )}
                </div>

                {diagnosis.explanation && (
                    <p className="font-serif text-lg text-primary/90 leading-relaxed">
                        {diagnosis.explanation}
                    </p>
                )}

                {diagnosis.training_recommendation && (
                    <div className="border-l-2 border-primary/30 pl-4">
                        <h3 className="font-sans font-normal text-xs uppercase tracking-widest text-primary/70 mb-1">
                            Next time
                        </h3>
                        <p className="font-sans text-sm text-primary/80">
                            {diagnosis.training_recommendation}
                        </p>
                    </div>
                )}

                {canConfirm && (
                    <div className="border-t border-primary/10 pt-3 space-y-3">
                        <div className="flex flex-wrap items-center gap-2">
                            <button
                                type="button"
                                onClick={() => onConfirm(primary_cause)}
                                disabled={savingConfirmation}
                                className="px-3 py-2 bg-primary text-bg-primary rounded-sm font-serif text-sm transition-colors km-focus-visible"
                            >
                                {savingConfirmation ? 'Saving…' : 'This fits'}
                            </button>
                            {alternativeCauses.length > 0 && (
                                <details className="font-sans text-sm text-primary/70">
                                    <summary className="cursor-pointer km-focus-visible">Choose a different cause</summary>
                                    <div className="mt-2 flex flex-wrap gap-2">
                                        {alternativeCauses.map((option) => (
                                            <button
                                                key={option.value}
                                                type="button"
                                                onClick={() => onConfirm(option.value)}
                                                disabled={savingConfirmation}
                                                className="px-2 py-1 border border-primary/20 text-primary rounded-sm font-sans font-normal text-xs km-focus-visible"
                                            >
                                                {option.label}
                                            </button>
                                        ))}
                                    </div>
                                </details>
                            )}
                        </div>
                        {confirmationError && (
                            <p className="font-sans text-sm text-negative" role="alert">
                                {confirmationError}
                            </p>
                        )}
                    </div>
                )}

                {secondary_cause_labels.length > 0 && (
                    <div className="flex flex-wrap gap-2">
                        {secondary_cause_labels.map((label) => (
                            <span
                                key={label}
                                className="font-sans text-xs text-primary/70 border border-primary/20 rounded-sm px-2 py-1"
                            >
                                {label}
                            </span>
                        ))}
                    </div>
                )}

                {evidence.length > 0 && (
                    <div>
                        <h3 className="font-sans font-normal text-xs uppercase tracking-widest text-primary/70 mb-2">
                            {diagnosis.explanation ? 'Evidence' : 'Why'}
                        </h3>
                        <dl className="space-y-1">
                            {evidence.map((item) => (
                                <div key={item.id} className="flex gap-2 text-sm">
                                    <dt className="font-sans text-primary/70 shrink-0">
                                        {item.label}:
                                    </dt>
                                    <dd className="font-mono text-primary/80 break-words">
                                        {item.value}
                                    </dd>
                                </div>
                            ))}
                        </dl>
                    </div>
                )}

                {diagnosis.evidence_withheld && (
                    <p className="font-sans text-sm text-primary/70">
                        Solve this puzzle to see the evidence behind the diagnosis.
                    </p>
                )}

                {phase && (
                    <p className="font-sans text-xs text-primary/70">
                        {/* Labelled, not a bare capitalized word: an orphaned
                            "Opening" at the card's foot read as debris. */}
                        Game phase: <span className="capitalize">{phase}</span>
                    </p>
                )}
            </div>
        </Shell>
    );
}

function Shell({ children }: { children: React.ReactNode }) {
    return (
        <section
            aria-labelledby="mistake-diagnosis-heading"
            className="bg-primary/5 border border-primary/10 rounded-sm p-6 backdrop-blur-sm animate-teedin"
        >
            <h2
                id="mistake-diagnosis-heading"
                className="font-sans font-normal text-xs uppercase tracking-widest text-primary/70 mb-3"
            >
                Mistake diagnosis
            </h2>
            {children}
        </section>
    );
}
