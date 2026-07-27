import type { PuzzleDiagnosis } from '../api/puzzles';

interface MistakeDiagnosisCardProps {
    diagnosis: PuzzleDiagnosis | null;
    /** False while the puzzle is still being solved — see the note below. */
    revealed: boolean;
    loading?: boolean;
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
}: MistakeDiagnosisCardProps) {
    // Solution-bearing content stays hidden until the puzzle is done with.
    if (!revealed) return null;

    if (loading) {
        return (
            <Shell>
                <p className="font-sans text-sm text-primary/60" role="status" aria-live="polite">
                    Loading diagnosis…
                </p>
            </Shell>
        );
    }

    if (!diagnosis) return null;

    if (diagnosis.state === 'pending') {
        return (
            <Shell>
                <p className="font-sans text-sm text-primary/60">
                    This mistake hasn’t been analysed yet.
                </p>
            </Shell>
        );
    }

    if (diagnosis.state === 'unavailable') {
        return (
            <Shell>
                <p className="font-sans text-sm text-primary/60">
                    This position can’t be analysed.
                </p>
            </Shell>
        );
    }

    if (diagnosis.state === 'unclear') {
        return (
            <Shell>
                <p className="font-sans text-sm text-primary/60">
                    No clear cause stands out for this one. The tactic is recorded, but the
                    evidence doesn’t point to a single habit worth training.
                </p>
            </Shell>
        );
    }

    const { primary_cause_label, secondary_cause_labels, evidence, phase } = diagnosis;

    return (
        <Shell>
            <div className="space-y-4">
                <div>
                    <p className="font-serif text-2xl text-primary">{primary_cause_label}</p>
                    {diagnosis.user_confirmed_cause && (
                        <p className="font-sans text-xs text-primary/60 mt-1">Your label</p>
                    )}
                </div>

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
                        <h3 className="font-sans text-xs uppercase tracking-widest text-primary/60 mb-2">
                            Why
                        </h3>
                        <dl className="space-y-1">
                            {evidence.map((item) => (
                                <div key={item.id} className="flex gap-2 text-sm">
                                    <dt className="font-sans text-primary/60 shrink-0">
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
                    <p className="font-sans text-sm text-primary/60">
                        Solve this puzzle to see the evidence behind the diagnosis.
                    </p>
                )}

                {phase && (
                    <p className="font-sans text-xs text-primary/50 capitalize">{phase}</p>
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
                className="font-sans text-xs uppercase tracking-widest text-primary/60 mb-3"
            >
                Mistake diagnosis
            </h2>
            {children}
        </section>
    );
}
