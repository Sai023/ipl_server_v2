"""The weekly transitions are all-or-nothing, safe under concurrent triggers, previewable, reversible and right over a whole randomised season
(2026-11-20, Phase 1.5 of the SA20 readiness plan).

These are the properties that make "automate the rollover with redundancy" safe to switch on: several independent triggers may all fire (and may fire days
late, or die half way), and the outcome must still be one correct handover per week.

  * a failure part way through a week leaves nothing of that week behind, the weeks before it stay, and the retry just works (the failure mode that
    reproduced on scratch data in the Phase 0 review)
  * four triggers at the same instant apply each week exactly once
  * a dry run is the real run without the writes: same report, no trace
  * the last week can be undone (its rows, its status, its marks), is then held, and only a forced run applies it again
  * a randomised season (outages, saves while picking is open, injected failures) ends with the right XI for every member in every week, judged by a
    small independent oracle

Needs a scratch Postgres: set TEST_DATABASE_URL (a DIRECT Neon/Postgres URL, run with `py -3.14`); the module uses and drops its own schema.
ROLLOVER_REPLAY_SEEDS (default "0,1") picks the seeds of the whole-season replay."""
import os
import random
import sys
import threading
import unittest
from datetime import timedelta
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "helpers"))
from logic import rollover_plan as rp  # noqa: E402
import rollover_db as R  # noqa: E402
from rollover_db import at, row, team, weeks_of, SEASON, LOCKS, MEMBERS, TEAM_A, TEAM_B, TEAM_C  # noqa: E402

S = None
needs_db = unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "set TEST_DATABASE_URL to run the database tests")


def setUpModule():
    global S
    if os.environ.get("TEST_DATABASE_URL"):
        S = R.ScratchDb()


def tearDownModule():
    if S is not None:
        S.close()


class Base(unittest.TestCase):
    @property
    def db(self):
        return S.db

    def applied_weeks(self, rep):
        return [a["to_week"] for a in rep["applied"]]


@needs_db
class ATransitionIsAllOrNothing(Base):
    def test_a_failure_part_way_through_a_week_leaves_nothing_of_it_behind(self):
        import db_manager
        slug = S.clone()
        self.assertEqual(self.applied_weeks(self.db.reconcile_competition(slug, at(2))), [1, 2])
        S.sql(f"UPDATE user_selections SET nw_cap_id='BOOM' WHERE competition_id='{slug}' AND display_name='Moe' AND week_no=2")
        real = db_manager._pick_active_team

        def poisoned(nw_team, nw_cap, nw_vc, tw_team, tw_cap, tw_vc, jloads):
            if nw_cap == "BOOM":
                raise RuntimeError("boom")
            return real(nw_team, nw_cap, nw_vc, tw_team, tw_cap, tw_vc, jloads)

        before = S.snap(slug)
        with mock.patch.object(db_manager, "_pick_active_team", poisoned):
            dry = self.db.reconcile_competition(slug, at(3, hours=1), dry_run=True)
            self.assertEqual(dry["applied"], []); self.assertIn("boom", dry["error"])
            self.assertEqual(S.snap(slug), before, "a dry run that fails changes nothing either")
            rep = self.db.reconcile_competition(slug, at(3, hours=1))
        self.assertEqual(rep["applied"], []); self.assertIn("boom", rep["error"])
        self.assertEqual(S.snap(slug), before, "Buddy's week-3 row was written before Moe failed: it must be gone with the rest, and so must the ledger row")
        self.assertTrue(self.db.get_lock_state(slug, at(3, hours=1))["locked"], "while week 3 is stuck, picking stays locked (fail closed)")
        S.sql(f"UPDATE user_selections SET nw_cap_id='p5' WHERE competition_id='{slug}' AND display_name='Moe' AND week_no=2")
        retry = self.db.reconcile_competition(slug, at(3, hours=1))
        self.assertEqual(self.applied_weeks(retry), [3]); self.assertNotIn("error", retry)
        s = S.snap(slug)
        for name in MEMBERS:
            self.assertEqual(weeks_of(s, name), [1, 2, 3], name)

    def test_weeks_applied_before_a_failure_are_kept_and_the_failed_week_is_retried(self):
        slug = S.clone()
        real = self.db._apply_transition

        def dies_after_writing_week_3(con, slug_, comp, t, now, by, redo=False):
            out = real(con, slug_, comp, t, now, by, redo=redo)
            if t["to_week"] == 3:
                raise RuntimeError("the connection dropped at commit")          # everything of week 3 was written, then the transaction died
            return out

        now = at(3, hours=1)
        with mock.patch.object(self.db, "_apply_transition", dies_after_writing_week_3):
            rep = self.db.reconcile_competition(slug, now, triggered_by="flaky")
        self.assertEqual(self.applied_weeks(rep), [1, 2]); self.assertIn("connection dropped", rep["error"])
        s = S.snap(slug)
        for name in MEMBERS:
            self.assertEqual(weeks_of(s, name), [1, 2], name)
        self.assertEqual([e["to_week"] for e in s["log"]], [1, 2])
        self.assertEqual(s["meta"][f"_last_rollover:{slug}"], now.isoformat())
        st = self.db.get_lock_state(slug, now)
        self.assertTrue(st["locked"]); self.assertEqual(st["awaiting"], {"kind": "rollover", "to_week": 3})
        retry = self.db.reconcile_competition(slug, now)
        self.assertEqual(self.applied_weeks(retry), [3]); self.assertNotIn("error", retry)

    def test_a_week_that_is_already_in_the_ledger_is_never_written_again(self):
        slug = S.clone()
        before = S.snap(slug)
        with self.assertRaises(RuntimeError) as cm:
            with self.db._pool.connection() as con:
                con.execute("INSERT INTO rollover_log (competition_id,kind,to_week,lock_at_utc,applied_at) VALUES (%s,'rollover',2,%s,%s)",
                            (slug, rp.iso(LOCKS[2]), at(2).isoformat()))
                comp = con.execute("SELECT slug,status,max_weeks FROM competitions WHERE slug=%s", (slug,)).fetchone()
                self.db._apply_transition(con, slug, comp, {"kind": "rollover", "to_week": 2, "lock_at": LOCKS[2]}, at(2), "test")
        self.assertIn("already in the ledger", str(cm.exception))
        self.assertEqual(S.snap(slug), before, "the rows it wrote before noticing went with the rolled-back transaction")


@needs_db
class ConcurrentTriggers(Base):
    def test_four_triggers_at_the_same_instant_apply_each_week_exactly_once(self):
        slug = S.clone()
        now = at(3, hours=1)
        reps, errors = [None] * 4, []
        barrier = threading.Barrier(4)

        def go(i):
            try:
                barrier.wait(timeout=60)
                reps[i] = self.db.reconcile_competition(slug, now, triggered_by=f"t{i}")
            except Exception as e:                                    # noqa: BLE001
                errors.append(repr(e))

        threads = [threading.Thread(target=go, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=600)
        self.assertEqual(errors, [])
        self.assertTrue(all(r is not None for r in reps), "every trigger came back")
        for r in reps:
            self.assertNotIn("error", r)
        self.assertEqual(sorted(w for r in reps for w in self.applied_weeks(r)), [1, 2, 3], "each week was applied by exactly one of the four")
        s = S.snap(slug)
        self.assertEqual([e["to_week"] for e in s["log"]], [1, 2, 3])
        self.assertTrue({e["triggered_by"] for e in s["log"]} <= {"t0", "t1", "t2", "t3"})
        for name in MEMBERS:
            self.assertEqual(weeks_of(s, name), [1, 2, 3], name)


@needs_db
class ADryRunIsTheRealRunWithoutTheWrites(Base):
    def test_it_reports_exactly_what_the_real_run_then_does(self):
        slug = S.clone()
        before = S.snap(slug)
        now = at(2, hours=1)
        dry = self.db.reconcile_competition(slug, now, triggered_by="preview", dry_run=True)
        self.assertEqual(self.applied_weeks(dry), [1, 2]); self.assertTrue(dry["dry_run"])
        self.assertEqual(S.snap(slug), before, "a dry run leaves no trace at all: no rows, no ledger, no meta key, no status change")
        real = self.db.reconcile_competition(slug, now, triggered_by="preview")
        self.assertFalse(real["dry_run"]); self.assertEqual(real["applied"], dry["applied"])
        self.assertNotEqual(S.snap(slug), before)

    def test_a_forced_preview_names_the_week_it_would_apply(self):
        slug = S.clone()
        before = S.snap(slug)
        prev = self.db.reconcile_competition(slug, at(1, days=-5), force_next=True, dry_run=True, max_steps=1)
        self.assertEqual(self.applied_weeks(prev), [1]); self.assertEqual(S.snap(slug), before)


@needs_db
class UndoingATransition(Base):
    def test_undo_restores_everything_a_week_changed_then_holds_it_until_a_forced_run(self):
        slug = S.clone()
        self.db.reconcile_competition(slug, at(1))
        after_start = S.snap(slug)
        self.db.reconcile_competition(slug, at(2))
        after_two = S.snap(slug)
        res = self.db.undo_transition(slug, 2)
        self.assertEqual((res["undone_week"], res["kind"]), (2, "rollover"))
        s = S.snap(slug)
        self.assertEqual(s["rows"], after_start["rows"], "every row week 2 created is gone and every earlier row is as it was")
        self.assertEqual(s["status"], after_start["status"])
        self.assertEqual(s["meta"][f"_last_rollover:{slug}"], after_start["meta"][f"_last_rollover:{slug}"])
        self.assertNotEqual(s["meta"][f"_saved:{slug}"], after_two["meta"][f"_saved:{slug}"], "the state tag moved, so open pages refresh")
        log = {e["to_week"]: e for e in self.db.get_rollover_log(slug)}
        self.assertIsNone(log[1]["undone_at"]); self.assertIsNotNone(log[2]["undone_at"], "the ledger keeps the undone week, marked")

        rep = self.db.reconcile_competition(slug, at(2, hours=1))                         # a trigger must not quietly redo what an admin undid
        self.assertEqual(rep["applied"], []); self.assertIn("held", rep["skipped"])
        st = self.db.get_lock_state(slug, at(2, hours=1))
        self.assertTrue(st["locked"]); self.assertEqual(st["awaiting"]["to_week"], 2); self.assertEqual(st["held"], [2])
        self.assertEqual(S.snap(slug)["rows"], after_start["rows"])

        wrong = self.db.reconcile_competition(slug, at(2, hours=2), force_next=True, expect_week=3)
        self.assertEqual(wrong["applied"], []); self.assertIn("is 2, not 3", wrong["skipped"])
        redo = self.db.reconcile_competition(slug, at(2, hours=2), force_next=True, expect_week=2, triggered_by="admin")
        self.assertEqual(self.applied_weeks(redo), [2])
        again = {e["to_week"]: e for e in self.db.get_rollover_log(slug)}[2]
        self.assertIsNone(again["undone_at"]); self.assertTrue(again["detail"]["redo"]); self.assertEqual(again["triggered_by"], "admin")
        self.assertEqual(S.snap(slug)["rows"], after_two["rows"], "applied again, the same rows come back")
        self.assertFalse(self.db.get_lock_state(slug, at(2, hours=2))["locked"])

    def test_undo_is_refused_unless_it_is_the_latest_applied_unscored_week(self):
        slug = S.clone()
        with self.assertRaises(LookupError):
            self.db.undo_transition(slug, 1)                                              # nothing has run yet
        with self.assertRaises(LookupError):
            self.db.undo_transition("nope", 1)
        self.db.reconcile_competition(slug, at(2))
        with self.assertRaises(ValueError) as cm:
            self.db.undo_transition(slug, 1)
        self.assertIn("week 2 has run after it", str(cm.exception))
        S.sql(f"INSERT INTO matches (competition_id,id,week_no,title,status) VALUES ('{slug}','{slug}m2',2,'Week 2 game','completed');\n"
              f"INSERT INTO user_match_points (competition_id,display_name,week_no,match_id,pts) VALUES ('{slug}','Sai',2,'{slug}m2',7)")
        with self.assertRaises(ValueError) as cm:
            self.db.undo_transition(slug, 2)
        self.assertIn("points have already been worked out", str(cm.exception))
        self.assertEqual([e["to_week"] for e in self.db.get_rollover_log(slug) if e["undone_at"]], [])
        self.db.undo_transition(slug, 2, force=True)
        s = S.snap(slug)
        for name in MEMBERS:
            self.assertEqual(weeks_of(s, name), [1], name)
        with self.db._read() as con:
            left = con.execute("SELECT COUNT(*) AS n FROM user_match_points WHERE competition_id=%s AND week_no=2", (slug,)).fetchone()["n"]
        self.assertEqual(left, 0, "a forced undo leaves no stale points for the week it removed")
        with self.assertRaises(ValueError) as cm:
            self.db.undo_transition(slug, 2, force=True)
        self.assertIn("already undone", str(cm.exception))

    def test_undo_will_not_silently_throw_away_a_draft_saved_after_the_handover(self):
        slug = S.clone()
        self.db.reconcile_competition(slug, at(2))                                        # weeks 1 and 2
        self.db.save_next_week("Moe", TEAM_C, "p12", "p13", competition_id=slug)         # Moe drafts for week 3, into the week-2 row the handover created
        before = S.snap(slug)
        with self.assertRaises(ValueError) as cm:
            self.db.undo_transition(slug, 2)
        self.assertIn("Moe", str(cm.exception)); self.assertIn("saved a new draft", str(cm.exception)); self.assertNotIn("Sai", str(cm.exception))
        self.assertEqual(S.snap(slug), before, "refused means nothing was touched")
        self.db.undo_transition(slug, 2, force=True)                                      # the admin knows and accepts it
        for name in MEMBERS:
            self.assertEqual(weeks_of(S.snap(slug), name), [1], name)
        self.db.save_next_week("Buddy", TEAM_A, "p1", "p2", competition_id=slug)         # Buddy had no draft: the start made him an empty row; now he drafts into it
        with self.assertRaises(ValueError) as cm:
            self.db.undo_transition(slug, 1)
        self.assertIn("Buddy", str(cm.exception)); self.assertNotIn("Moe", str(cm.exception))
        self.assertEqual(self.db.get_rollover_log(slug)[0]["undone_at"], None)
        self.db.undo_transition(slug, 1, force=True)
        self.assertIsNotNone(self.db.get_rollover_log(slug)[0]["undone_at"])

    def test_undoing_the_start_puts_week_1_back_and_deactivates_the_competition(self):
        slug = S.clone(status="upcoming", auto=True)
        before = S.snap(slug)
        rep = self.db.reconcile_competition(slug, at(1))
        self.assertTrue(rep["applied"][0]["activated"]); self.assertEqual(S.snap(slug)["status"], "active")
        self.db.undo_transition(slug, 1)
        s = S.snap(slug)
        self.assertEqual(s["status"], "upcoming"); self.assertEqual(s["rows"], before["rows"], "Buddy's empty row is gone and Sai's and Moe's XI is empty again")
        self.assertNotIn(f"_last_rollover:{slug}", s["meta"])
        keep = lambda m: {k: v for k, v in m.items() if not k.startswith("_saved")}   # noqa: E731  (the state tag is meant to move)
        self.assertEqual(keep(s["meta"]), keep(before["meta"]))
        self.assertIsNotNone(s["log"][0]["undone_at"])
        rep = self.db.reconcile_competition(slug, at(1, hours=1))
        self.assertEqual(rep["applied"], []); self.assertIn("held", rep["skipped"])


@needs_db
class ReplayingASeasonOnTheDatabase(Base):
    """Time passes; members save drafts whenever picking is open; something wakes the system up at random moments (sometimes not for days); some weeks
    die after writing everything and before committing. A small oracle, written separately from the code under test, says which XI every member must
    have in every week: the draft saved before that week's lock, else last week's XI."""
    SEEDS = [int(x) for x in os.environ.get("ROLLOVER_REPLAY_SEEDS", "0,1").split(",") if x.strip() != ""]

    def test_random_outages_failures_and_saves_end_with_the_right_teams_every_week(self):
        for seed in self.SEEDS:
            with self.subTest(seed=seed):
                self.replay(seed)

    def replay(self, seed):
        rng = random.Random(seed)
        slug = S.clone(drafts=False)
        options = [(TEAM_A, "p1", "p2"), (TEAM_B, "p5", "p6"), (TEAM_C, "p12", "p13"), ([], None, None)]
        draft = {m: None for m in MEMBERS}              # what each member last saved (None: never saved anything)
        xi = {m: ([], None, None) for m in MEMBERS}     # the oracle's XI for the current week
        xi_at, applied, armed = {}, [], {"week": None}
        real = self.db._apply_transition

        def flaky(con, slug_, comp, t, now, by, redo=False):
            out = real(con, slug_, comp, t, now, by, redo=redo)
            if armed["week"] == t["to_week"]:
                armed["week"] = None
                raise RuntimeError("injected failure after writing week %d" % t["to_week"])
            return out

        def note(rep):                                  # the oracle follows what the database says it committed
            for a in rep["applied"]:
                w = a["to_week"]
                for m in MEMBERS:
                    d = draft[m]
                    new = d if (d and d[0]) else xi[m]
                    xi[m] = new
                    if w > 1:
                        draft[m] = new                  # a rollover pre-fills the next draft with this week's XI (the start leaves the draft as it was)
                    xi_at.setdefault(w, {})[m] = new
                applied.append(w)

        t, end, ticks = LOCKS[1] - timedelta(days=3), at(6, days=1), 0
        with mock.patch.object(self.db, "_apply_transition", flaky):
            while t < end and len(applied) < 6:
                t += timedelta(hours=rng.choice([3, 6, 12, 24, 48, 96]))
                if not rp.lock_state(SEASON, set(applied), t, "active", max_weeks=6)["locked"]:     # what the route does before it accepts a save
                    for m in MEMBERS:
                        if rng.random() < 0.4:
                            tm, cap, vc = rng.choice(options)
                            self.db.save_next_week(m, tm, cap, vc, competition_id=slug)
                            draft[m] = (tm, cap, vc)
                if rng.random() < 0.7:
                    nxt = rp.next_due(SEASON, set(applied), t, "active", max_weeks=6)
                    if nxt and armed["week"] is None and rng.random() < 0.3:
                        armed["week"] = min(6, nxt["to_week"] + rng.choice([0, 0, 1, 2]))
                    ticks += 1
                    note(self.db.reconcile_competition(slug, t, triggered_by=f"tick{ticks}"))
            armed["week"] = None
            note(self.db.reconcile_competition(slug, end, triggered_by="final"))

        s = S.snap(slug)
        self.assertEqual(applied, [1, 2, 3, 4, 5, 6], "every week applied once, in order")
        self.assertEqual([e["to_week"] for e in s["log"]], [1, 2, 3, 4, 5, 6])
        self.assertTrue(all(e["undone_at"] is None for e in s["log"]))
        self.assertEqual(len(s["rows"]), 18)
        for w in range(1, 7):
            for m in MEMBERS:
                r = row(s, m, w)
                self.assertIsNotNone(r, (seed, m, w))
                tm, cap, vc = xi_at[w][m]
                self.assertEqual((team(r), r["tw_cap_id"], r["tw_vc_id"]), (tm, cap, vc), f"seed {seed}: {m}'s XI for week {w}")
        st = self.db.get_lock_state(slug, end)
        self.assertTrue(st["locked"] and st["season_over"])


if __name__ == "__main__":
    unittest.main()
