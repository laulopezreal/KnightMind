"""Per-user title uniqueness, and the thing it must never cost us.

A library that shows the same name twice cannot be navigated, so
``uq_puzzle_stats_username_title`` makes duplicates unrepresentable. The danger
is entirely on the other side of that: the write it constrains is the one that
saves a freshly generated puzzle during a game import, and two puzzles composing
the same position name is ordinary, not exceptional. If the constraint can turn
a repeated NAME into a lost PUZZLE, it has made things worse.

So these cover both directions — the guarantee holds, and no write path pays for
it with an error.
"""

import chess
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from services.api.models import Game, Puzzle, PuzzleStats
from services.api.puzzles import ai_naming, naming_pass
from services.api.puzzles.identity import backfill_puzzle_identity
from services.api.puzzles.title_registry import (
    is_duplicate_title,
    taken_titles,
    unique_title,
)
from services.api.storage.puzzle_repository import PuzzleRepository

# Two DIFFERENT positions whose winning move is a knight landing on f7, so both
# compose the same name ("King to g2"). This is the collision the
# generation path hits in the wild — the same tactic in two different games.
FORK_A = ("3q3k/8/8/4N3/8/8/8/6K1 w - - 0 1", "e5f7")
FORK_B = ("3q3k/8/5N2/8/8/8/8/6K1 w - - 0 1", "f6f7")

USER = "lauureal"
OTHER = "alfi3sr"


def _game(db, username, game_id):
    if db.get(Game, (game_id, username)) is None:
        db.add(
            Game(
                game_id=game_id,
                url="",
                username=username,
                white_username=username,
                black_username="",
                white_result="win",
                black_result="loss",
                time_control="600",
                end_time=0,
            )
        )
        db.commit()


def _save(repository, db, username, game_id, ply, position, **kwargs):
    fen, best = position
    _game(db, username, game_id)
    return repository.save_puzzle(
        username=username,
        source_game_id=game_id,
        ply=ply,
        fen=fen,
        side_to_move="white",
        played_move_uci="g1g2",
        best_move_uci=best,
        eval_before=0.0,
        eval_after=-3.0,
        swing=3.0,
        **kwargs,
    )


@pytest.fixture
def repository(db_session):
    return PuzzleRepository(db_session)


def _title(db, puzzle_id):
    return db.get(PuzzleStats, puzzle_id).title


class TestTheDatabaseEnforcesIt:
    """The guarantee has to live in the schema. Application code deduplicated
    within one run for months and the corpus still ended up with 103 puzzles
    called the same thing."""

    def test_a_duplicate_title_for_one_user_is_rejected(self, db_session, repository):
        _, first = _save(repository, db_session, USER, "g1", 10, FORK_A)
        _, second = _save(repository, db_session, USER, "g2", 12, FORK_B)

        # Forced past the application's own disambiguation, which is the point:
        # the guarantee must not depend on the code that normally upholds it.
        db_session.get(PuzzleStats, second).title = _title(db_session, first)
        with pytest.raises(IntegrityError) as raised:
            db_session.commit()
        db_session.rollback()

        # The same error save_puzzle classifies to decide whether to retry.
        assert is_duplicate_title(raised.value) is True

    def test_two_users_may_hold_the_same_title(self, db_session, repository):
        """Expected and fine: two people can reach the same fork. Making that
        collide would let one tenant's corpus rename another's."""
        _, mine = _save(repository, db_session, USER, "g1", 10, FORK_A)
        _, theirs = _save(repository, db_session, OTHER, "g2", 10, FORK_A)

        assert _title(db_session, mine) == _title(db_session, theirs)
        assert _title(db_session, mine) == "King to g2"

    def test_untitled_rows_do_not_collide(self, db_session, repository):
        """The index is not partial because it does not need to be: Postgres
        treats NULLs as distinct, so rows written before their name was computed
        stay unconstrained."""
        _, first = _save(repository, db_session, USER, "g1", 10, FORK_A)
        _, second = _save(repository, db_session, USER, "g2", 12, FORK_B)
        db_session.get(PuzzleStats, first).title = None
        db_session.get(PuzzleStats, second).title = None

        db_session.commit()  # must not raise


class TestSaveNeverFailsOverAName:
    """The hazard the constraint introduces. A cosmetic collision must not
    become a lost puzzle during a game import."""

    def test_two_puzzles_that_compose_the_same_name_both_survive(
        self, db_session, repository
    ):
        # Both are knight-to-f7 forks, so compose_position_name returns the same
        # string for each.
        new_a, id_a = _save(repository, db_session, USER, "g1", 10, FORK_A)
        new_b, id_b = _save(repository, db_session, USER, "g2", 24, FORK_B)

        assert new_a and new_b, "both puzzles must be created"
        assert id_a != id_b
        assert _title(db_session, id_a) != _title(db_session, id_b)
        assert _title(db_session, id_a) == "King to g2"

    def test_a_whole_import_of_identical_tactics_lands(self, db_session, repository):
        """Six of the same tactic in one import — the shape that produced 103
        copies of one name. Every puzzle is saved; every name is different."""
        ids = [
            _save(repository, db_session, USER, f"g{i}", 10 + i * 2, FORK_A)[1]
            for i in range(6)
        ]

        assert len(ids) == 6
        assert len({_title(db_session, i) for i in ids}) == 6

    def test_a_repeated_user_title_is_renamed_not_refused(self, db_session, repository):
        """The manual-save route passes the string the user typed. Reusing a
        name they already used is a mistake worth correcting, not a 500."""
        _, first = _save(
            repository, db_session, USER, "g1", 10, FORK_A, title="My Nemesis"
        )
        _, second = _save(
            repository, db_session, USER, "g2", 12, FORK_B, title="My Nemesis"
        )

        assert _title(db_session, first) == "My Nemesis"
        assert _title(db_session, second).startswith("My Nemesis")
        assert _title(db_session, second) != "My Nemesis"
        assert db_session.get(PuzzleStats, second).title_source == "user"

    def test_a_title_taken_between_the_read_and_the_write_is_retried(
        self, db_session, db_engine, repository
    ):
        """The race the pre-insert lookup cannot close.

        A second connection commits our chosen name after we read the free-name
        set and before our INSERT lands. The retry is what makes the constraint
        safe to add: without it this is a 500 in the middle of an import.
        """
        other = sessionmaker(bind=db_engine)()
        _game(db_session, USER, "g1")
        _game(db_session, USER, "g2")
        # A committed row this session cannot see coming: it is inserted by
        # another connection at the moment our save has already picked its name.
        stolen = {"done": False}
        original_commit = db_session.commit

        def steal_then_commit():
            if not stolen["done"]:
                stolen["done"] = True
                other.add(
                    Puzzle(
                        id="thief",
                        username=USER,
                        source_game_id="g2",
                        ply=99,
                        fen=FORK_A[0],
                        side_to_move="white",
                        played_move_uci="g1g2",
                        best_move_uci=FORK_A[1],
                        eval_before=0.0,
                        eval_after=-3.0,
                        swing=3.0,
                    )
                )
                other.add(
                    PuzzleStats(
                        puzzle_id="thief",
                        username=USER,
                        title="King to g2",
                        title_source="position",
                    )
                )
                other.commit()
            return original_commit()

        db_session.commit = steal_then_commit
        try:
            is_new, puzzle_id = _save(repository, db_session, USER, "g1", 10, FORK_A)
        finally:
            db_session.commit = original_commit
            other.close()

        assert stolen["done"], "the race never happened; the test proves nothing"
        assert is_new, "the puzzle must be saved despite losing its name"
        assert _title(db_session, puzzle_id) != "King to g2"

    def test_a_duplicate_puzzle_still_resolves_to_the_winner(
        self, db_session, repository
    ):
        """The pre-existing IntegrityError replay, unchanged: the natural
        duplicate key is checked BEFORE the title retry, so a second save of the
        same (user, game, ply) still returns the first puzzle's id rather than
        being renamed into a second row."""
        first_new, first_id = _save(repository, db_session, USER, "g1", 10, FORK_A)
        second_new, second_id = _save(repository, db_session, USER, "g1", 10, FORK_B)

        assert first_new is True
        assert second_new is False
        assert first_id == second_id
        assert db_session.query(PuzzleStats).count() == 1

    def test_an_unrelated_integrity_error_is_still_raised(self, db_session, repository):
        """The manual-save route depends on save_puzzle re-raising a violation
        it did not cause (the position index), so it can absorb it itself."""

        def raise_integrity_error():
            raise IntegrityError("commit failed", {}, Exception("synthetic failure"))

        _game(db_session, USER, "g1")
        db_session.commit = raise_integrity_error
        with pytest.raises(IntegrityError):
            _save(repository, db_session, USER, "g1", 10, FORK_A)


class TestTheNamingPassChecksTheDatabase:
    def _seed(self, db, puzzle_id, *, username=USER, title=None, source=None, ply=10):
        _game(db, username, f"game-{puzzle_id}")
        db.add(
            Puzzle(
                id=puzzle_id,
                username=username,
                source_game_id=f"game-{puzzle_id}",
                ply=ply,
                fen=FORK_A[0],
                side_to_move="white",
                played_move_uci="g1g2",
                best_move_uci=FORK_A[1],
                eval_before=0.0,
                eval_after=-3.0,
                swing=3.0,
            )
        )
        db.add(
            PuzzleStats(
                puzzle_id=puzzle_id,
                username=username,
                title=title,
                title_source=source,
                primary_motif="fork",
            )
        )
        db.commit()

    @pytest.fixture
    def naming_on(self, monkeypatch):
        monkeypatch.setenv("KNIGHTMIND_AI_NAMING", "1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def test_it_cannot_hand_out_a_name_already_in_the_database(
        self, db_session, monkeypatch, naming_on
    ):
        """The whole point. The pass used to dedup against its own run only, so
        a name written by an EARLIER run — or by a puzzle outside this run's
        scope — was free to be handed out again."""
        self._seed(db_session, "already", source="ai", title="Knight Went Wandering")
        self._seed(db_session, "p1", source="position", title="King to g2")
        monkeypatch.setattr(
            ai_naming,
            "name_puzzle",
            lambda facts, avoid=None: ai_naming.NameOutcome(
                ai_naming.ACCEPTED, name="Knight Went Wandering", model_version="test"
            ),
        )

        naming_pass.name_puzzles(db_session, username=USER)

        assert _title(db_session, "already") == "Knight Went Wandering"
        assert _title(db_session, "p1") != "Knight Went Wandering"

    def test_a_limited_pass_respects_the_rows_it_did_not_look_at(
        self, db_session, monkeypatch, naming_on
    ):
        """``--limit`` and the background job's batch of 25 both mean the pass
        sees a fraction of the library. The names it cannot see still count."""
        for pid in ("p1", "p2", "p3"):
            self._seed(db_session, pid, source="position")
        monkeypatch.setattr(
            ai_naming,
            "name_puzzle",
            lambda facts, avoid=None: ai_naming.NameOutcome(
                ai_naming.ACCEPTED, name="Knight Went Wandering", model_version="test"
            ),
        )

        naming_pass.name_puzzles(db_session, username=USER, limit=1)
        naming_pass.name_puzzles(db_session, username=USER, force=True)

        titles = {_title(db_session, p) for p in ("p1", "p2", "p3")}
        assert len(titles) == 3

    def test_a_re_run_does_not_rename_a_puzzle_away_from_itself(
        self, db_session, monkeypatch, naming_on
    ):
        """A row's own title is not a collision with itself; if it were, every
        pass would append another suffix to every puzzle."""
        self._seed(db_session, "p1", source="position", title="King to g2")
        monkeypatch.delenv("KNIGHTMIND_AI_NAMING")  # deterministic name only

        naming_pass.name_puzzles(db_session, username=USER)
        naming_pass.name_puzzles(db_session, username=USER)

        assert _title(db_session, "p1") == "King to g2"

    def test_another_users_title_is_not_a_collision(
        self, db_session, monkeypatch, naming_on
    ):
        """Uniqueness is per user, so a pass with no --username must not rename
        one tenant's puzzle because another tenant reached the same name."""
        self._seed(db_session, "mine", source="position")
        self._seed(db_session, "theirs", username=OTHER, source="position")
        monkeypatch.delenv("KNIGHTMIND_AI_NAMING")

        naming_pass.name_puzzles(db_session)

        assert _title(db_session, "mine") == _title(db_session, "theirs")

    def test_renamed_rows_do_not_swap_names_within_one_pass(
        self, db_session, monkeypatch, naming_on
    ):
        """Two UPDATEs in one transaction cannot trade titles: Postgres checks
        the unique index per statement, and the unit of work emits UPDATEs in
        primary-key order, so one of the two would abort the run at random."""
        self._seed(db_session, "aaa", source="position", title="King to g2")
        self._seed(db_session, "zzz", source="position", title="Taken Elsewhere")
        names = iter(["Something Else", "Taken Elsewhere"])
        monkeypatch.setattr(
            ai_naming,
            "name_puzzle",
            lambda facts, avoid=None: ai_naming.NameOutcome(
                ai_naming.ACCEPTED, name=next(names), model_version="test"
            ),
        )

        naming_pass.name_puzzles(db_session, username=USER, force=True)

        assert _title(db_session, "aaa") == "Something Else"
        # "Taken Elsewhere" was vacated by zzz in this same pass; it stays
        # reserved until the next one rather than being traded mid-transaction.
        assert _title(db_session, "zzz") != _title(db_session, "aaa")

    def test_a_concurrently_taken_title_rolls_back_instead_of_raising(
        self, db_session, db_engine, monkeypatch, naming_on
    ):
        """This module promises not to raise into its callers — one of which is
        a background job nobody is watching."""
        self._seed(db_session, "p1", source="position")
        monkeypatch.delenv("KNIGHTMIND_AI_NAMING")  # deterministic name only
        other = sessionmaker(bind=db_engine)()
        _game(db_session, USER, "game-shadow")
        original_commit = db_session.commit

        def steal_then_commit():
            # Committed on another connection AFTER the pass chose its names and
            # BEFORE it writes them — the window no pre-read can close.
            if db_session.query(Puzzle).filter_by(id="shadow").count() == 0:
                other.add(
                    Puzzle(
                        id="shadow",
                        username=USER,
                        source_game_id="game-shadow",
                        ply=10,
                        fen=FORK_A[0],
                        side_to_move="white",
                        played_move_uci="g1g2",
                        best_move_uci=FORK_A[1],
                        eval_before=0.0,
                        eval_after=-3.0,
                        swing=3.0,
                    )
                )
                other.add(
                    PuzzleStats(
                        puzzle_id="shadow",
                        username=USER,
                        title="King to g2",
                        title_source="position",
                    )
                )
                other.commit()
            return original_commit()

        db_session.commit = steal_then_commit
        try:
            summary = naming_pass.name_puzzles(db_session, username=USER)
        finally:
            db_session.commit = original_commit
            other.close()

        assert summary["outcomes"]["title_conflict"] == 1
        assert _title(db_session, "p1") is None


class TestTheOtherWriters:
    """Every path that writes a title has to pick a free one. Two of these run
    somewhere an IntegrityError is expensive: startup, and a review."""

    def test_the_startup_backfill_does_not_collide(self, db_session, repository):
        """It runs inside ``lifespan`` and commits once for every row it
        touched, so a duplicate name here does not fail a backfill — it fails
        the API's boot."""
        for i, position in enumerate((FORK_A, FORK_B)):
            _, puzzle_id = _save(
                repository, db_session, USER, f"g{i}", 10 + i * 2, position
            )
            db_session.get(PuzzleStats, puzzle_id).title = None
        db_session.commit()

        backfill_puzzle_identity(db_session)  # must not raise

        titles = [t for (t,) in db_session.query(PuzzleStats.title).all()]
        assert len(titles) == len(set(titles)) == 2

    def test_a_first_review_of_a_statless_puzzle_does_not_collide(
        self, db_session, repository
    ):
        """``update_puzzle_stats`` names from the seven-string motif table, so
        its second row for a motif is a guaranteed duplicate."""
        from services.api.storage.spaced_repetition import update_puzzle_stats

        ids = []
        for i, position in enumerate((FORK_A, FORK_B)):
            _, puzzle_id = _save(
                repository, db_session, USER, f"g{i}", 10 + i * 2, position
            )
            db_session.delete(db_session.get(PuzzleStats, puzzle_id))
            ids.append(puzzle_id)
        db_session.commit()

        for puzzle_id in ids:
            update_puzzle_stats(db_session, puzzle_id, USER, "pass")
        db_session.commit()  # must not raise

        assert _title(db_session, ids[0]) != _title(db_session, ids[1])


class TestTitleRegistry:
    def test_taken_titles_is_scoped_to_one_user(self, db_session, repository):
        _save(repository, db_session, USER, "g1", 10, FORK_A)
        _save(repository, db_session, OTHER, "g2", 10, FORK_A)

        assert taken_titles(db_session, USER) == {"King to g2"}

    def test_a_rows_own_title_can_be_excluded(self, db_session, repository):
        _, puzzle_id = _save(repository, db_session, USER, "g1", 10, FORK_A)

        assert taken_titles(db_session, USER, exclude_puzzle_id=puzzle_id) == set()

    def test_unique_title_leaves_a_free_name_alone(self, db_session):
        assert (
            unique_title(db_session, USER, "Nobody Has This", 12) == "Nobody Has This"
        )

    def test_a_non_title_integrity_error_is_not_claimed(self):
        """The classifier decides whether save_puzzle retries or re-raises, so a
        false positive would swallow a real constraint failure."""
        error = IntegrityError("insert", {}, Exception("null value in column"))

        assert is_duplicate_title(error) is False


def test_the_fork_fixtures_really_do_compose_the_same_name():
    """Guards the premise of half this file: if FORK_A and FORK_B ever stopped
    colliding, the collision tests would pass without testing anything."""
    from services.api.puzzles.position_names import PositionFacts, compose_position_name

    # Both fixtures are seeded with the same PLAYED move, which is what the
    # composer names from now — it used to name from the engine's move, which
    # put the answer square in every title.
    names = {
        compose_position_name(
            PositionFacts(fen=fen, played_move_uci="g1g2", primary_motif="fork")
        )
        for fen, _best in (FORK_A, FORK_B)
    }

    assert names == {"King to g2"}
    # And they are genuinely different positions, not the same one twice.
    assert chess.Board(FORK_A[0]).fen() != chess.Board(FORK_B[0]).fen()
