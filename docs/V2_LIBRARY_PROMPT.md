# V2 Library Implementation Prompt

You are implementing 5 features for the Library puzzle exploration surface in KnightMind, a personal chess intelligence platform. This document contains everything you need: current codebase state, feature specs, code patterns, and verification steps.

---

## Project Overview

**KnightMind** lets users import their chess.com games, auto-generate tactical puzzles from their mistakes, and train with spaced repetition.

**Tech stack:**
- Backend: FastAPI (Python), SQLAlchemy ORM, SQLite/Postgres
- Frontend: React + Vite + TypeScript, Tailwind CSS
- Tests: pytest (backend), vitest + @testing-library/react (frontend)

**Two puzzle surfaces exist:**
- **Train (`/puzzles`)** — session-based, system-picked puzzles, streaks, achievements
- **Library (`/library`)** — self-directed browsing, user-picked puzzles, no session wrapper

Library V1 and V1.1 are already shipped. You are implementing V2.

**Rules:**
- All PRs target the `dev` branch
- Follow `apps/web/DESIGN_GUIDE.md` for all UI
- Small incremental commits
- Don't refactor unrelated code

---

## Current Codebase State (V1.1 — already built)

### Backend

#### Database Models (`services/api/models.py`)

```python
class PuzzleStats(Base):
    __tablename__ = "puzzle_stats"
    puzzle_id: Mapped[str]       # FK to puzzles.id, primary key
    username: Mapped[str]        # indexed
    title: Mapped[str]           # nullable
    primary_motif: Mapped[str]   # nullable, single string (not array)
    attempts: Mapped[int]        # default 0
    pass_count: Mapped[int]      # default 0
    fail_count: Mapped[int]      # default 0
    last_reviewed_at: Mapped[datetime]  # nullable
    last_result: Mapped[str]     # nullable
    next_due_at: Mapped[datetime]       # nullable
    interval_days: Mapped[int]   # nullable
    ease_factor: Mapped[float]   # default 2.0
    # NOTE: No `saved` column exists yet — Feature 2 adds this
```

```python
class Puzzle(Base):
    __tablename__ = "puzzles"
    id: Mapped[str]              # primary key
    username: Mapped[str]        # indexed
    source_game_id: Mapped[str]  # FK to games.game_id
    ply: Mapped[int]
    fen: Mapped[str]
    side_to_move: Mapped[str]
    played_move_uci: Mapped[str]
    best_move_uci: Mapped[str]
    eval_before: Mapped[float]
    eval_after: Mapped[float]
    swing: Mapped[float]         # eval swing in pawns — used for difficulty
    created_at: Mapped[datetime]
```

#### Pydantic Response Models (`services/api/main.py:373-406`)

```python
class PuzzleListItem(BaseModel):
    id: str
    title: str | None
    primary_motif: str | None
    difficulty: str              # "easy" | "medium" | "hard" (derived from swing)
    swing: float
    fen: str
    side_to_move: str
    best_move_uci: str
    status: str                  # "new" | "due" | "learning" | "mastered"
    attempts: int
    pass_count: int
    fail_count: int
    last_reviewed_at: datetime | None
    last_result: str | None
    next_due_at: datetime | None
    created_at: datetime | None

class PuzzleCorpusStats(BaseModel):
    total: int
    due: int
    new: int
    learning: int
    mastered: int

class PuzzleListResponse(BaseModel):
    puzzles: list[PuzzleListItem]
    total: int
    limit: int
    offset: int
    available_motifs: list[str]
    stats: PuzzleCorpusStats
```

#### Key Helper Functions (`services/api/main.py:768-788`)

```python
def _compute_puzzle_status(stats: PuzzleStats | None, now: datetime) -> str:
    if stats is None or stats.attempts == 0:
        return "new"
    if stats.next_due_at is not None:
        due = stats.next_due_at
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if due <= now:
            return "due"
    if stats.attempts > 0 and stats.pass_count / stats.attempts >= 0.8 and stats.attempts >= 3:
        return "mastered"
    return "learning"

def _swing_to_difficulty(swing: float) -> str:
    if swing < 2.0:
        return "easy"
    if swing < 5.0:
        return "medium"
    return "hard"

def _difficulty_to_swing_range(difficulty: str) -> tuple[float, float | None]:
    """Return (min_swing, max_swing) for a difficulty bucket. max is None for 'hard'."""
    if difficulty == "easy":
        return (0.0, 2.0)
    if difficulty == "medium":
        return (2.0, 5.0)
    return (5.0, None)  # hard
```

#### List Puzzles Endpoint (`services/api/main.py:791-956`)

`GET /puzzles/list` — accepts these query params:
- `username` (required)
- `q` — search by title or puzzle ID
- `status` — filter: new, due, learning, mastered
- `motif` — filter by primary_motif (comma-separated OR)
- `difficulty` — filter: easy, medium, hard
- `sort` — due_soonest (default), last_attempted, most_failed, difficulty_asc, difficulty_desc, newest
- `limit` (1-100, default 50), `offset` (default 0)

The endpoint:
1. Loads all puzzles with LEFT JOIN on puzzle_stats
2. Computes corpus stats BEFORE filtering (unfiltered totals)
3. Applies search, status, motif, difficulty filters
4. Sorts
5. Paginates
6. Returns `PuzzleListResponse` with `stats` field

#### Review Endpoint (`services/api/main.py:959`)

`POST /puzzles/{puzzle_id}/review` — body:
```python
class ReviewRequest(BaseModel):
    username: str
    result: PuzzleResult  # "pass" | "fail"
    time_spent_ms: int | None = None
    session_id: str | None = None  # optional — omit for library mode
```

Returns `ReviewPuzzleResponse` with `next_due_at`, `interval_days`, `ease_factor`, `feedback`, `puzzle_info`, `stats`.

#### Motif Performance Endpoint (`services/api/motifs.py` + `services/api/main.py:227-230`)

**Already built.** `GET /users/{username}/motifs/performance`

```python
class MotifPerformance(BaseModel):
    name: str
    total_puzzles: int
    passed: int
    accuracy: float          # 0.0 to 1.0
    rank: MotifRank          # "needs_work" | "learning" | "mastered"

class MotifPerformanceResponse(BaseModel):
    motifs: list[MotifPerformance]
    weakest_motifs: list[str]      # bottom 2 with "needs_work" rank
    total_motifs_practiced: int
```

Implementation queries `puzzle_stats` grouped by `primary_motif`, computes accuracy = passed/attempts, ranks: <70% needs_work, 70-85% learning, >85% mastered.

### Frontend

#### API Types and Functions (`apps/web/src/api/puzzles.ts`)

```typescript
export type PuzzleStatus = 'new' | 'due' | 'learning' | 'mastered';
export type PuzzleDifficulty = 'easy' | 'medium' | 'hard';
export type PuzzleSort = 'due_soonest' | 'last_attempted' | 'most_failed'
    | 'difficulty_asc' | 'difficulty_desc' | 'newest';

export interface LibraryPuzzle {
    id: string;
    title: string | null;
    primary_motif: string | null;
    difficulty: PuzzleDifficulty;
    swing: number;
    fen: string;
    side_to_move: string;
    best_move_uci: string;
    status: PuzzleStatus;
    attempts: number;
    pass_count: number;
    fail_count: number;
    last_reviewed_at: string | null;
    last_result: string | null;
    next_due_at: string | null;
    created_at: string | null;
}

export interface LibraryCorpusStats {
    total: number; due: number; new: number; learning: number; mastered: number;
}

export interface LibraryListResponse {
    puzzles: LibraryPuzzle[];
    total: number;
    limit: number;
    offset: number;
    available_motifs: string[];
    stats: LibraryCorpusStats;
}

export interface LibraryListParams {
    username: string;
    q?: string;
    status?: PuzzleStatus;
    motif?: string;
    difficulty?: PuzzleDifficulty;
    sort?: PuzzleSort;
    limit?: number;
    offset?: number;
}

export async function getLibraryPuzzles(params: LibraryListParams): Promise<LibraryListResponse>
export async function reviewPuzzle(
    puzzleId: string, username: string, result: 'pass' | 'fail',
    timeSpentMs?: number, sessionId?: string
): Promise<ReviewPuzzleResponse>
```

The `request` helper is in `apps/web/src/api/core.ts` — standard fetch wrapper.

#### Library List Page (`apps/web/src/pages/Library.tsx`)

- Corpus stats header (5 colored cards: Total, Due, New, Learning, Mastered)
- Search input with 300ms debounce
- Filter dropdowns: Status, Difficulty, Motif (dynamic), Sort
- Puzzle rows as `<Link to="/library/{id}">` cards
- Offset pagination (Previous/Next buttons)
- Training nudge at bottom ("X puzzles due — Start Training")
- State: `search`, `statusFilter`, `difficultyFilter`, `motifFilter`, `sort`, `offset`, `puzzles`, `total`, `availableMotifs`, `corpusStats`

#### Library Puzzle Detail Page (`apps/web/src/pages/LibraryPuzzle.tsx`)

- Fetches single puzzle via `getLibraryPuzzles({ username, q: puzzleId, limit: 1 })`
- Chessboard with drag-and-drop piece movement
- UCI text input toggle ("Type Move Manually")
- Status machine: `solving` → `correct`/`incorrect`/`revealed`
- Solve timer via `useRef<number>` (starts on puzzle load)
- Records result via `reviewPuzzle()` with `time_spent_ms`
- Post-solve: "Recorded" confirmation, solve time, success rate, next review date
- "Back to Library" link throughout

#### Routes (`apps/web/src/App.tsx`)

```tsx
<Route path="/library" element={<Library />} />
<Route path="/library/:puzzleId" element={<LibraryPuzzle />} />
```

#### useClue Hook (`apps/web/src/hooks/useClue.ts`)

**Confirmed standalone — NO session dependencies.** Takes `bestMoveUci` and `fen` as params.

```typescript
export function useClue(bestMoveUci: string, fen: string): UseClueReturn {
    // 3-stage progressive hint:
    // Stage 0: No hint shown (label: "Clue")
    // Stage 1: Source square highlighted + piece name hint (label: "Reveal squares")
    // Stage 2: Both from+to squares highlighted (label: "Clue used")

    return {
        clueStage,           // 0 | 1 | 2
        squareStyles,        // Record<string, {backgroundColor: string}> for board highlights
        pieceHint,           // e.g. "Move the knight"
        advance,             // () => void — moves to next stage
        reset,               // () => void — back to stage 0
        isExhausted,         // true when stage === 2
        isDisabled,          // true when no bestMoveUci or stage === 2
        label,               // "Clue" | "Reveal squares" | "Clue used"
    };
}
```

Helper utilities in `apps/web/src/utils/puzzle-clue.ts`:
- `parseBestMoveUci(uci: string): { from, to, promotion? }` — splits UCI string
- `getPieceNameAtSquare(fen: string, square: string): string` — returns e.g. "Move the knight"

---

## Design System (`apps/web/DESIGN_GUIDE.md`)

**Aesthetic:** Minimal, Calm, Intellectual, Premium.

- **Fonts:** `font-serif` (Cormorant Garamond) for headings/action. `font-sans` (Inter) for UI/data. `font-mono` for move notation.
- **Colors:** `text-primary`, `bg-primary`, `chess-brown`, `chess-cream` tokens. Opacity variants: `text-primary/60`, `bg-primary/5`.
- **Cards:** `bg-primary/5 border border-primary/10 rounded-sm p-6 backdrop-blur-sm`
- **Primary button:** `px-6 py-2 bg-primary text-bg-primary rounded-sm font-serif`
- **Secondary button:** `px-6 py-2 border border-primary/20 text-primary rounded-sm font-serif`
- **Interactive elements:** use `km-interactive`, `km-focus-visible` utility classes
- **Inputs:** `bg-transparent border-b border-primary/20 py-2 text-primary font-serif`
- **Status colors:** green (success/mastered), orange (due), blue (new), yellow (learning), red (failed/error)
- **Spacing:** `space-y-6` or `space-y-8` for vertical rhythm
- **Loading:** `animate-pulse` on skeleton divs
- **Animations:** `animate-teedin` for page entrances

---

## V2 Features — Implementation Specs

### Feature 1: "Hard for Me" / Personal Difficulty Filter

**Goal:** Let users filter puzzles by their personal success rate, separate from the objective swing-based difficulty.

#### Backend Changes (`services/api/main.py`)

Add `user_difficulty` query param to `list_puzzles`:

```python
@app.get("/puzzles/list", response_model=PuzzleListResponse)
async def list_puzzles(
    # ... existing params ...
    user_difficulty: str = Query(None, description="Filter: struggling, challenging, confident"),
    # ...
):
```

Add filter logic after existing filters (around line 876, after difficulty filter):

```python
# 6b. User difficulty filter (personal success rate)
if user_difficulty:
    def _user_diff_match(it: dict) -> bool:
        s = it["stats"]
        if s is None or s.attempts == 0:
            return user_difficulty == "struggling"  # No attempts = struggling by default
        rate = s.pass_count / s.attempts
        if user_difficulty == "struggling":
            return rate < 0.5
        elif user_difficulty == "challenging":
            return 0.5 <= rate < 0.8
        elif user_difficulty == "confident":
            return rate >= 0.8
        return True
    items = [it for it in items if _user_diff_match(it)]
```

#### Frontend Changes

**`apps/web/src/api/puzzles.ts`** — Add to `LibraryListParams`:
```typescript
export type UserDifficulty = 'struggling' | 'challenging' | 'confident';

export interface LibraryListParams {
    // ... existing fields ...
    user_difficulty?: UserDifficulty;
}
```

Add to `getLibraryPuzzles` URL builder:
```typescript
if (params.user_difficulty) searchParams.append('user_difficulty', params.user_difficulty);
```

**`apps/web/src/pages/Library.tsx`** — Add dropdown to filter bar:

```typescript
// New type import
import { type UserDifficulty } from '../api/puzzles';

// New options constant (after DIFFICULTY_OPTIONS)
const USER_DIFFICULTY_OPTIONS: { value: UserDifficulty | ''; label: string }[] = [
    { value: '', label: 'All' },
    { value: 'struggling', label: 'Struggling (<50%)' },
    { value: 'challenging', label: 'Challenging (50-80%)' },
    { value: 'confident', label: 'Confident (>80%)' },
];

// New state
const [userDifficultyFilter, setUserDifficultyFilter] = useState<UserDifficulty | ''>('');

// Add to useEffect reset (line ~153-155)
// Add userDifficultyFilter to the dependency array

// Add to fetchPuzzles params
user_difficulty: userDifficultyFilter || undefined,

// Add dropdown to filter row (after Difficulty select)
<select
    value={userDifficultyFilter}
    onChange={(e) => setUserDifficultyFilter(e.target.value as UserDifficulty | '')}
    className="bg-bg-primary border border-primary/20 rounded-sm px-3 py-1.5 text-primary focus:outline-none focus:border-primary/60"
>
    {USER_DIFFICULTY_OPTIONS.map(o => (
        <option key={o.value} value={o.value}>{o.label}</option>
    ))}
</select>
```

#### Tests

**Backend (`services/api/test_library.py`):** Add `TestUserDifficultyFilter` class:
- `test_filter_struggling` — puzzle with 1 pass / 5 attempts (20%) should appear
- `test_filter_confident` — puzzle with 9 pass / 10 attempts (90%) should appear
- `test_filter_challenging` — puzzle with 3 pass / 5 attempts (60%) should appear
- `test_no_attempts_counts_as_struggling` — puzzle with 0 attempts appears under "struggling"

**Frontend (`apps/web/src/pages/Library.test.tsx`):** Add test for "My Difficulty" dropdown rendering.

---

### Feature 2: Saved Puzzles (Bookmarks)

**Goal:** Let users bookmark puzzles for later. Heart icon on cards and detail page. Filter by saved.

#### Backend Changes

**`services/api/models.py`** — Add `saved` column to PuzzleStats:
```python
class PuzzleStats(Base):
    # ... existing columns ...
    saved: Mapped[bool] = mapped_column(Boolean, default=False)
```

**Database migration:** This project uses SQLAlchemy with `Base.metadata.create_all()` for development (no Alembic). For production, manually add:
```sql
ALTER TABLE puzzle_stats ADD COLUMN saved BOOLEAN DEFAULT FALSE;
```

**`services/api/main.py`** — Add toggle endpoint:
```python
class SavePuzzleRequest(BaseModel):
    username: str

@app.patch("/puzzles/{puzzle_id}/save")
async def toggle_save_puzzle(puzzle_id: str, request: SavePuzzleRequest, db: Session = Depends(get_db)):
    username_lower = request.username.lower()
    stats = db.query(PuzzleStats).filter(
        PuzzleStats.puzzle_id == puzzle_id,
        PuzzleStats.username == username_lower,
    ).first()
    if not stats:
        raise HTTPException(status_code=404, detail="Puzzle not found in library")
    stats.saved = not stats.saved
    db.commit()
    db.refresh(stats)
    return {"saved": stats.saved}
```

Add `saved` filter to `list_puzzles`:
```python
async def list_puzzles(
    # ... existing params ...
    saved: bool = Query(None, description="Filter by saved status"),
    # ...
):
    # After other filters, before sort:
    if saved is not None:
        items = [it for it in items if (it["stats"] and it["stats"].saved) == saved]
```

Add `saved` field to `PuzzleListItem`:
```python
class PuzzleListItem(BaseModel):
    # ... existing fields ...
    saved: bool
```

And populate it in the response builder:
```python
saved=s.saved if s else False,
```

#### Frontend Changes

**`apps/web/src/api/puzzles.ts`:**
```typescript
// Add to LibraryPuzzle interface
saved: boolean;

// Add to LibraryListParams
saved?: boolean;

// Add URL param
if (params.saved !== undefined) searchParams.append('saved', params.saved.toString());

// New API function
export async function toggleSavePuzzle(puzzleId: string, username: string): Promise<{ saved: boolean }> {
    return await request<{ saved: boolean }>(`/puzzles/${puzzleId}/save`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username }),
    });
}
```

**`apps/web/src/pages/Library.tsx`** — Add heart icon to `PuzzleRow`:
```tsx
// In PuzzleRow component, add save button to the right side
<button
    type="button"
    onClick={(e) => {
        e.preventDefault();  // prevent Link navigation
        e.stopPropagation();
        onToggleSave(puzzle.id);
    }}
    className="flex-shrink-0 km-interactive km-focus-visible text-primary/30 hover:text-red-500 transition-colors"
    aria-label={puzzle.saved ? 'Unsave puzzle' : 'Save puzzle'}
>
    {puzzle.saved ? '♥' : '♡'}
</button>
```

Add a "Saved" filter option. Simplest approach: add to STATUS_OPTIONS:
```typescript
const STATUS_OPTIONS = [
    { value: '', label: 'All' },
    { value: 'due', label: 'Due' },
    { value: 'new', label: 'New' },
    { value: 'learning', label: 'Learning' },
    { value: 'mastered', label: 'Mastered' },
    { value: 'saved', label: 'Saved' },
];
```

Handle "saved" separately in fetchPuzzles — if statusFilter is "saved", pass `saved: true` instead of `status`.

**`apps/web/src/pages/LibraryPuzzle.tsx`** — Add heart button in metadata strip:
```tsx
// Add state
const [isSaved, setIsSaved] = useState(puzzle.saved);

// Add toggle handler
const handleToggleSave = async () => {
    if (!puzzle || !username) return;
    try {
        const res = await toggleSavePuzzle(puzzle.id, username);
        setIsSaved(res.saved);
    } catch (err) {
        console.error('Failed to toggle save:', err);
    }
};

// Add button in metadata section
<button
    type="button"
    onClick={handleToggleSave}
    className="px-3 py-1 bg-primary/5 rounded-sm border border-primary/10 km-interactive km-focus-visible transition-colors hover:border-red-500/30"
>
    {isSaved ? '♥ Saved' : '♡ Save'}
</button>
```

#### Tests

**Backend:** `TestSavedPuzzles` class:
- `test_toggle_save` — PATCH toggles saved from False to True
- `test_toggle_unsave` — second PATCH toggles back to False
- `test_filter_saved` — `?saved=true` returns only saved puzzles
- `test_save_nonexistent_puzzle` — returns 404

**Frontend:** Add save button interaction tests to both Library.test.tsx and LibraryPuzzle.test.tsx.

---

### Feature 3: Similar Puzzles on Detail Page

**Goal:** After solving, show 5 related puzzles below the board to encourage deeper exploration.

#### Backend Changes (`services/api/main.py`)

New endpoint:
```python
@app.get("/puzzles/{puzzle_id}/similar")
async def get_similar_puzzles(
    puzzle_id: str,
    username: str = Query(...),
    limit: int = Query(5, ge=1, le=10),
    db: Session = Depends(get_db),
):
    from services.api.models import Puzzle as PuzzleModel

    username_lower = username.lower()
    now = datetime.now(timezone.utc)

    # Find the source puzzle
    puzzle = db.query(PuzzleModel).filter(PuzzleModel.id == puzzle_id).first()
    if not puzzle:
        raise HTTPException(status_code=404, detail="Puzzle not found")

    # Find stats for motif
    stats = db.query(PuzzleStats).filter(
        PuzzleStats.puzzle_id == puzzle_id,
        PuzzleStats.username == username_lower,
    ).first()

    motif = stats.primary_motif if stats else None

    # Build similarity query: same user, different puzzle
    stmt = (
        select(PuzzleModel, PuzzleStats)
        .outerjoin(
            PuzzleStats,
            (PuzzleModel.id == PuzzleStats.puzzle_id) & (PuzzleStats.username == username_lower)
        )
        .where(PuzzleModel.username == username_lower)
        .where(PuzzleModel.id != puzzle_id)
    )

    # Prefer same motif, fall back to same difficulty bucket
    if motif:
        stmt = stmt.where(PuzzleStats.primary_motif == motif)

    rows = db.execute(stmt).all()

    # If motif match found too few, fall back to difficulty bucket
    if len(rows) < limit and motif:
        diff_bucket = _swing_to_difficulty(puzzle.swing)
        swing_min, swing_max = _difficulty_to_swing_range(diff_bucket)
        existing_ids = [r[0].id for r in rows]

        # Build fallback query that filters by difficulty in the DB
        # to avoid fetching all user puzzles into memory
        fallback_conditions = [
            PuzzleModel.username == username_lower,
            PuzzleModel.id != puzzle_id,
            PuzzleModel.id.notin_(existing_ids),
            PuzzleModel.swing >= swing_min,
            PuzzleStats.primary_motif != motif,  # Exclude primary motif puzzles
        ]
        if swing_max is not None:
            fallback_conditions.append(PuzzleModel.swing < swing_max)

        stmt_fallback = (
            select(PuzzleModel, PuzzleStats)
            .outerjoin(
                PuzzleStats,
                (PuzzleModel.id == PuzzleStats.puzzle_id) & (PuzzleStats.username == username_lower)
            )
            .where(*fallback_conditions)
            .limit(limit - len(rows))
        )
        fallback_rows = db.execute(stmt_fallback).all()
        rows.extend(fallback_rows)

    # Sort by swing proximity
    rows.sort(key=lambda r: abs(r[0].swing - puzzle.swing))
    rows = rows[:limit]

    # Build response
    result = []
    for p, s in rows:
        result.append(PuzzleListItem(
            id=p.id,
            title=s.title if s else None,
            primary_motif=s.primary_motif if s else None,
            difficulty=_swing_to_difficulty(p.swing),
            swing=p.swing,
            fen=p.fen,
            side_to_move=p.side_to_move,
            best_move_uci=p.best_move_uci,
            status=_compute_puzzle_status(s, now),
            attempts=s.attempts if s else 0,
            pass_count=s.pass_count if s else 0,
            fail_count=s.fail_count if s else 0,
            last_reviewed_at=s.last_reviewed_at if s else None,
            last_result=s.last_result if s else None,
            next_due_at=s.next_due_at if s else None,
            created_at=p.created_at,
            saved=s.saved if s else False,  # only if Feature 2 is done first
        ))

    return {"puzzles": result}
```

#### Frontend Changes

**`apps/web/src/api/puzzles.ts`:**
```typescript
export async function getSimilarPuzzles(
    puzzleId: string, username: string, limit: number = 5
): Promise<{ puzzles: LibraryPuzzle[] }> {
    const params = new URLSearchParams({ username, limit: limit.toString() });
    return await request<{ puzzles: LibraryPuzzle[] }>(`/puzzles/${puzzleId}/similar?${params}`);
}
```

**`apps/web/src/pages/LibraryPuzzle.tsx`** — Add below the board section (after the `</section>` for Board + Controls):

```tsx
// New state
const [similarPuzzles, setSimilarPuzzles] = useState<LibraryPuzzleType[]>([]);

// Fetch after puzzle loads (in fetchPuzzle or separate useEffect)
useEffect(() => {
    if (!puzzle || !username) return;
    getSimilarPuzzles(puzzle.id, username).then(res => {
        setSimilarPuzzles(res.puzzles);
    }).catch(() => {}); // silent fail — not critical
}, [puzzle, username]);

// Render section (only show after solving or if there are results)
{similarPuzzles.length > 0 && (status === 'correct' || status === 'revealed') && (
    <section className="space-y-4">
        <h2 className="text-xl font-serif text-primary">Similar Puzzles</h2>
        <div className="grid gap-2">
            {similarPuzzles.map(sp => (
                <Link
                    key={sp.id}
                    to={`/library/${sp.id}`}
                    className="block bg-primary/5 border border-primary/10 rounded-sm p-3 km-interactive transition-all hover:border-primary/30"
                >
                    <div className="flex items-center justify-between">
                        <span className="font-serif text-primary text-sm">{sp.title || sp.id.slice(0, 8)}</span>
                        <div className="flex gap-2 text-xs font-sans text-primary/50">
                            {sp.primary_motif && <span>{sp.primary_motif}</span>}
                            <span className="uppercase">{sp.difficulty}</span>
                        </div>
                    </div>
                </Link>
            ))}
        </div>
    </section>
)}
```

#### Tests

**Backend:** `TestSimilarPuzzles` class:
- `test_returns_similar_by_motif` — puzzles with same motif returned first
- `test_falls_back_to_difficulty` — when no motif match, returns same difficulty bucket
- `test_excludes_source_puzzle` — the queried puzzle is not in results
- `test_puzzle_not_found` — returns 404

---

### Feature 4: Motif Weakness Summary on Library Page

**Goal:** Show the user's 3 weakest motifs below the stats header. Clicking filters the library by that motif.

#### Backend Changes

None — the `GET /users/{username}/motifs/performance` endpoint already exists and returns everything needed.

#### Frontend Changes

**`apps/web/src/api/puzzles.ts`** — Add API function (if not already exported):
```typescript
export interface MotifPerformance {
    name: string;
    total_puzzles: number;
    passed: number;
    accuracy: number;
    rank: 'needs_work' | 'learning' | 'mastered';
}

export interface MotifPerformanceResponse {
    motifs: MotifPerformance[];
    weakest_motifs: string[];
    total_motifs_practiced: number;
}

export async function getMotifPerformance(username: string): Promise<MotifPerformanceResponse> {
    return await request<MotifPerformanceResponse>(`/users/${username}/motifs/performance`);
}
```

**`apps/web/src/pages/Library.tsx`** — Add weakness bar below stats header:

```tsx
// New state
const [motifPerformance, setMotifPerformance] = useState<MotifPerformanceResponse | null>(null);

// Fetch on mount (alongside puzzles)
useEffect(() => {
    if (!username) return;
    getMotifPerformance(username).then(setMotifPerformance).catch(() => {});
}, [username]);

// Render below corpus stats section, before Search + Filters
{motifPerformance && motifPerformance.weakest_motifs.length > 0 && (
    <section className="bg-red-500/5 border border-red-500/10 rounded-sm p-4">
        <p className="text-xs font-sans text-red-500/60 uppercase tracking-wider mb-2">
            Areas to Improve
        </p>
        <div className="flex flex-wrap gap-2">
            {motifPerformance.motifs
                .filter(m => m.rank === 'needs_work')
                .slice(0, 3)
                .map(m => (
                    <button
                        key={m.name}
                        type="button"
                        onClick={() => setMotifFilter(m.name)}
                        className="px-3 py-1.5 bg-red-500/10 border border-red-500/20 rounded-sm text-sm font-sans text-red-500/80 km-interactive km-focus-visible hover:bg-red-500/20 transition-colors"
                    >
                        {m.name} — {Math.round(m.accuracy * 100)}%
                    </button>
                ))}
        </div>
    </section>
)}
```

#### Tests

**Frontend (`Library.test.tsx`):** Mock `getMotifPerformance` and test:
- Weakness bar renders when there are needs_work motifs
- Clicking a motif button updates the filter
- Bar does not render when no weak motifs exist

---

### Feature 5: Hints on Detail Page

**Goal:** Add the 3-stage progressive clue system to the library puzzle detail page.

#### Backend Changes

Optionally add `hints_used` to `ReviewRequest`:
```python
class ReviewRequest(BaseModel):
    # ... existing fields ...
    hints_used: int | None = None
```

Store in `PuzzleReview` if desired (add column):
```python
class PuzzleReview(Base):
    # ... existing columns ...
    hints_used: Mapped[int] = mapped_column(Integer, nullable=True)
```

This is optional — the hint system works entirely client-side. Only add if you want analytics.

#### Frontend Changes (`apps/web/src/pages/LibraryPuzzle.tsx`)

Import and use the hook:
```tsx
import { useClue } from '../hooks/useClue';

// Inside the component, after puzzle is loaded:
const {
    clueStage,
    squareStyles,
    pieceHint,
    advance: advanceClue,
    reset: resetClue,
    isExhausted: clueExhausted,
    isDisabled: clueDisabled,
    label: clueLabel,
} = useClue(puzzle?.best_move_uci ?? '', puzzle?.fen ?? '');

// Reset clue when puzzle changes or on retry
// In handleMarkFailedRetry, add: resetClue();
```

Pass `squareStyles` to the Chessboard:
```tsx
<Chessboard
    options={{
        position: game.fen(),
        onPieceDrop: ({ sourceSquare, targetSquare }) =>
            targetSquare ? onPieceDrop(sourceSquare, targetSquare) : false,
        boardOrientation: puzzle.side_to_move === 'white' ? 'white' : 'black',
        darkSquareStyle: { backgroundColor: 'var(--color-chess-brown-700)' },
        lightSquareStyle: { backgroundColor: 'var(--color-chess-cream-300)' },
        customSquareStyles: squareStyles,  // Add this
    }}
/>
```

Add hint button and display in the sidebar controls (in the `status === 'solving'` section):
```tsx
{status === 'solving' && (
    <div className="space-y-4">
        {/* Hint button + hint display */}
        <div className="flex items-center gap-4">
            <button
                type="button"
                onClick={advanceClue}
                disabled={clueDisabled}
                className={`px-4 py-2 border border-primary/20 rounded-sm font-sans text-sm transition-all km-focus-visible ${
                    clueDisabled ? 'opacity-30 km-interactive-disabled' : 'km-interactive hover:bg-primary/10'
                }`}
            >
                {clueLabel}
            </button>
            {pieceHint && (
                <span className="text-sm font-sans text-primary/60 italic animate-teedin">
                    {pieceHint}
                </span>
            )}
        </div>

        {/* Existing Check Move / Reveal buttons */}
        <div className="grid grid-cols-2 gap-4">
            {/* ... existing buttons ... */}
        </div>
    </div>
)}
```

Also show hint in the `status === 'incorrect'` section if clue hasn't been exhausted.

#### Tests (`apps/web/src/pages/LibraryPuzzle.test.tsx`)

The `useClue` hook will need to be mocked since it depends on `chess.js` which is already mocked:

```typescript
vi.mock('../hooks/useClue', () => ({
    useClue: () => ({
        clueStage: 0,
        squareStyles: {},
        pieceHint: '',
        advance: vi.fn(),
        reset: vi.fn(),
        isExhausted: false,
        isDisabled: false,
        label: 'Clue',
    }),
}));
```

Test:
- Clue button renders in solving state
- Clue button is disabled when exhausted

---

## Implementation Order

1. **Feature 1: "Hard for me" filter** — smallest scope, no model changes
2. **Feature 2: Saved puzzles** — requires model column addition
3. **Feature 3: Similar puzzles** — new endpoint, depends on Feature 2's `saved` field
4. **Feature 4: Motif weakness summary** — frontend-only, reuses existing endpoint
5. **Feature 5: Hints** — integrate existing hook, most independent

Features 1 and 4 can be done in parallel. Feature 3 should come after Feature 2 (to include `saved` field in response).

---

## Backend Test Patterns

All backend tests use the same fixture pattern in `services/api/test_library.py`:

```python
@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)

@pytest.fixture
def client(db_session, monkeypatch):
    monkeypatch.setenv("KNIGHTMIND_WORKER_DISABLED", "true")
    monkeypatch.setenv("KNIGHTMIND_STORAGE_MODE", "database")
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()

# Helpers
def _create_game(db, game_id, username="testuser"): ...
def _create_puzzle(db, puzzle_id, username="testuser", swing=3.0, ...): ...
def _create_stats(db, puzzle_id, username="testuser", title=None, primary_motif=None,
                  attempts=0, pass_count=0, fail_count=0, ...): ...
```

Group tests in classes: `class TestFeatureName:`.

---

## Frontend Test Patterns

Tests in `apps/web/src/pages/Library.test.tsx` and `LibraryPuzzle.test.tsx`:

```typescript
// Module mocks
vi.mock('react-router-dom', () => ({
    useParams: () => ({ puzzleId: mockPuzzleId }),
    Link: ({ children, to, ...props }) => <a href={to} {...props}>{children}</a>,
}));
vi.mock('../context/ChessUsernameContext', () => ({
    useChessUsername: () => ({ username: mockUsername }),
}));
vi.mock('../api/puzzles', () => ({
    getLibraryPuzzles: (...args) => mockGetLibraryPuzzles(...args),
    reviewPuzzle: (...args) => mockReviewPuzzle(...args),
}));
vi.mock('react-chessboard', () => ({
    Chessboard: () => <div data-testid="chessboard">Chessboard</div>,
}));

// Standard mock response
const MOCK_STATS = { total: 10, due: 3, new: 2, learning: 4, mastered: 1 };
mockGetLibraryPuzzles.mockResolvedValue({
    puzzles: [MOCK_PUZZLE],
    total: 1, limit: 50, offset: 0,
    available_motifs: ['Fork'],
    stats: MOCK_STATS,
});

// Testing pattern
it('should do something', async () => {
    render(<Library />);
    await waitFor(() => {
        expect(screen.getByText('expected text')).toBeInTheDocument();
    });
});
```

**Known gotchas:**
- `getByText` fails when multiple elements match — use `getAllByText` and check `.length`
- Do NOT use `vi.useFakeTimers()` — it breaks `waitFor` polling. Use real timers.
- Always add `vi.useRealTimers()` in `afterEach` as safety net.
- Mock responses must include `stats` field.

---

## Verification Steps

After implementing each feature:

1. **Backend tests:** `cd /path/to/project && python -m pytest services/api/test_library.py -v`
2. **Frontend tests:** `cd apps/web && npx vitest run src/pages/Library.test.tsx src/pages/LibraryPuzzle.test.tsx`
3. **Lint:** `cd apps/web && npm run lint`
4. **Build:** `cd apps/web && npm run build`
5. **Manual verification:** Start dev servers, navigate to `/library`, test each feature interactively.

For the saved puzzles feature specifically, verify that `Base.metadata.create_all()` picks up the new column (it should for SQLite in dev).
