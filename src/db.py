# =============================================================================
# db.py
# SQLite Persistence Layer — NYP Women's Health Screening Simulation
# =============================================================================
#
# WHY A DATABASE?
# ─────────────────────────────────────────────────────────────────────────────
# A 30-year simulation with 15,000 cycling patients produces hundreds of
# thousands of patient records and millions of events. Keeping all of that
# in-memory throughout the run is wasteful. SQLite gives us:
#   • Zero-infrastructure persistence (built into Python's standard library).
#   • Efficient ad-hoc analysis via SQL after the run.
#   • Longitudinal queries: "show me all patients who were screened more than
#     once and eventually needed LEEP" — impossible from the metrics dict alone.
#   • Patient ID tracking across years.
#
# DESIGN: BATCH WRITES, NOT PER-ROW
# ─────────────────────────────────────────────────────────────────────────────
# Writing one row per event during the simulation hot loop would be
# prohibitively slow (SQLite commits are ~1ms each; 10M events = ~3 hours).
# Instead, exited patients are held in an in-memory flush buffer and written
# in bulk every DB_FLUSH_INTERVAL days. This gives ~100–1000× better throughput
# while still leaving the DB in a queryable state at any checkpoint.
#
# SCHEMA
# ─────────────────────────────────────────────────────────────────────────────
#   patients  — one row per patient: demographics, entry/exit, screening outcome
#   events    — full event log (day, event_string) for each patient
#
# Both tables use INSERT OR IGNORE so that re-flushing the same patient_id
# (e.g. during a crashed run) is idempotent.
# =============================================================================

import sqlite3
from typing import List, Optional

import config as cfg

# ── DDL statements ─────────────────────────────────────────────────────────────

_CREATE_PATIENTS = """
CREATE TABLE IF NOT EXISTS patients (
    patient_id           INTEGER PRIMARY KEY,
    age_at_entry         INTEGER,
    age_at_exit          INTEGER,
    race                 TEXT,
    insurance            TEXT,
    is_established       INTEGER,   -- 1 = established cycling patient; 0 = new entrant/drop-in
    simulation_entry_day INTEGER,   -- day the patient was first added to the pool
    exit_day             INTEGER,   -- day the patient left the system
    exit_reason          TEXT,      -- treated | mortality | lost_to_followup | ineligible
    visit_count          INTEGER,   -- total provider visits recorded during the simulation
    has_cervix           INTEGER,   -- 1 = yes
    smoker               INTEGER,   -- 1 = current smoker
    pack_years           REAL,
    cervical_result      TEXT,      -- last cervical screening result
    lung_result          TEXT,      -- last lung screening result
    colposcopy_result    TEXT,      -- last colposcopy result (if any)
    treatment_type       TEXT,      -- surveillance | leep | cone_biopsy (if any)
    last_cervical_screen_day  INTEGER,   -- simulation day of the patient's last cervical screening (-1 = never screened)
    last_lung_screen_day      INTEGER    -- simulation day of the patient's last lung screening (-1 = never screened)
);
"""

_CREATE_EVENTS = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id  INTEGER,
    day         INTEGER,
    event       TEXT,
    UNIQUE(patient_id, day, event),
    FOREIGN KEY(patient_id) REFERENCES patients(patient_id)
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_patients_exit_reason     ON patients(exit_reason);",
    "CREATE INDEX IF NOT EXISTS idx_patients_is_established  ON patients(is_established);",
    "CREATE INDEX IF NOT EXISTS idx_patients_race            ON patients(race);",
    "CREATE INDEX IF NOT EXISTS idx_events_patient_id        ON events(patient_id);",
    "CREATE INDEX IF NOT EXISTS idx_events_day               ON events(day);",
    "CREATE INDEX IF NOT EXISTS idx_patients_last_cervical_screen ON patients(last_cervical_screen_day);",
    "CREATE INDEX IF NOT EXISTS idx_patients_last_lung_screen     ON patients(last_lung_screen_day);",
]


# =============================================================================
# SimulationDB
# =============================================================================

class SimulationDB:
    """
    Lightweight SQLite persistence layer for the NYP screening simulation.

    Patients are held in-memory during simulation and batch-flushed to SQLite
    at regular intervals (cfg.DB_FLUSH_INTERVAL days). This design keeps the
    hot simulation loop fast while still providing a persistent, query-able
    store for post-run longitudinal analysis.

    Typical usage
    -------------
        db = SimulationDB()                    # opens / creates database
        db.flush_patients(exited_batch)        # batch write when interval hits
        history = db.get_patient_history(pid)  # trace one patient's full journey
        counts  = db.count_by_exit_reason()    # quick summary query
        db.close()                             # commit + close connection

    Both flush methods use INSERT OR IGNORE so re-running on an existing
    database (e.g. after a crash) does not produce duplicate rows.
    Idempotency for events requires the UNIQUE(patient_id, day, event)
    constraint defined in the schema.
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        Open (or create) the SQLite database at db_path.

        Enables WAL (Write-Ahead Logging) mode for better concurrent read
        performance — important when the simulation writes while a notebook
        is simultaneously running summary queries.
        """
        path = db_path if db_path is not None else cfg.DB_PATH
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row          # column-name access on rows
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")  # faster writes, still safe
        self._create_schema()

    # ── Schema ────────────────────────────────────────────────────────────────

    def _create_schema(self) -> None:
        """Create tables and indexes if they don't already exist."""
        cur = self.conn.cursor()
        cur.execute(_CREATE_PATIENTS)
        cur.execute(_CREATE_EVENTS)
        for stmt in _CREATE_INDEXES:
            cur.execute(stmt)
        self.conn.commit()

    # ── Writes ────────────────────────────────────────────────────────────────

    def flush_patients(self, patients: list) -> int:
        """
        Batch-insert a list of exited Patient objects into the patients table.

        Uses executemany for efficiency — one database transaction for the
        whole batch regardless of batch size. INSERT OR IGNORE means that
        re-flushing a patient already in the DB is a silent no-op.

        Parameters
        ----------
        patients : list[Patient]   Any Patient objects with exit_reason set.

        Returns
        -------
        int — number of rows actually inserted (0 for already-present patients).
        """
        if not patients:
            return 0

        rows = [
            (
                p.patient_id,
                p.age_at_entry,
                p.age,                       # current (post-aging) age = age at exit
                p.race,
                p.insurance,
                int(p.is_established),
                p.simulation_entry_day,
                p.exit_day,
                p.exit_reason,
                p.visit_count,
                int(p.has_cervix),
                int(p.smoker),
                p.pack_years,
                p.cervical_result,
                p.lung_result,
                p.colposcopy_result,
                p.treatment_type,
                p.last_cervical_screen_day,
                p.last_lung_screen_day,
            )
            for p in patients
        ]

        cur = self.conn.cursor()
        cur.executemany(
            """
            INSERT OR IGNORE INTO patients (
                patient_id, age_at_entry, age_at_exit, race, insurance,
                is_established, simulation_entry_day, exit_day, exit_reason,
                visit_count, has_cervix, smoker, pack_years,
                cervical_result, lung_result, colposcopy_result, treatment_type,
                last_cervical_screen_day, last_lung_screen_day
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )
        inserted = cur.rowcount
        self.conn.commit()
        return inserted

    def flush_events(self, patients: list) -> None:
        """
        Batch-insert the full event_log of a list of exited patients.

        Stores every (day, event_string) tuple from patient.event_log so the
        complete patient journey can be replayed via get_patient_history().
        Only call this AFTER flush_patients() so the foreign-key constraint
        is satisfied (though SQLite does not enforce FK by default).
        """
        if not patients:
            return

        rows = [
            (p.patient_id, day, event)
            for p in patients
            for day, event in p.event_log
        ]
        # INSERT OR IGNORE so that re-flushing the same patient (e.g. after a
        # crash) does not produce duplicate event rows.  Requires the UNIQUE
        # constraint on (patient_id, day, event) defined in the schema above.
        self.conn.executemany(
            "INSERT OR IGNORE INTO events (patient_id, day, event) VALUES (?,?,?)",
            rows,
        )
        self.conn.commit()

    # ── Reads ─────────────────────────────────────────────────────────────────

    def get_patient_history(self, patient_id: int) -> List[tuple]:
        """
        Return the full chronological event log for one patient.

        Returns a list of (day, event) tuples, sorted by day ascending.
        Returns an empty list if the patient has no events in the database.
        """
        cur = self.conn.execute(
            "SELECT day, event FROM events WHERE patient_id=? ORDER BY day ASC",
            (patient_id,),
        )
        return [(row["day"], row["event"]) for row in cur.fetchall()]

    def get_patient(self, patient_id: int) -> Optional[dict]:
        """Return the demographic + outcome row for one patient, or None."""
        cur = self.conn.execute(
            "SELECT * FROM patients WHERE patient_id=?", (patient_id,)
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def count_patients(self) -> int:
        """Return the total number of patient records flushed to the database."""
        return self.conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0]

    def count_by_exit_reason(self) -> dict:
        """
        Return a {exit_reason: count} dict for all flushed patients.

        Useful for quick post-run checks:
            mortality | treated | lost_to_followup | ineligible
        """
        rows = self.conn.execute(
            "SELECT exit_reason, COUNT(*) AS n FROM patients GROUP BY exit_reason"
        ).fetchall()
        return {row["exit_reason"]: row["n"] for row in rows}

    def count_established_vs_new(self) -> dict:
        """
        Return {'established': N, 'new_entrant': M} for flushed patients.

        Distinguishes the cycling stable-population patients from genuinely
        new drop-in entrants who replaced mortality exits.
        """
        rows = self.conn.execute(
            "SELECT is_established, COUNT(*) AS n FROM patients GROUP BY is_established"
        ).fetchall()
        result = {"established": 0, "new_entrant": 0}
        for row in rows:
            key = "established" if row["is_established"] else "new_entrant"
            result[key] = row["n"]
        return result

    def summary_stats(self) -> dict:
        """
        Return a quick summary dict suitable for printing or plotting.

        Includes: total patients, breakdown by exit reason, mean visit count,
        and mean age at exit.
        """
        total = self.count_patients()
        if total == 0:
            return {"total": 0}
        exits = self.count_by_exit_reason()
        rows  = self.conn.execute(
            "SELECT AVG(visit_count), AVG(age_at_exit) FROM patients"
        ).fetchone()
        return {
            "total_flushed":    total,
            "by_exit_reason":   exits,
            "mean_visit_count": round(rows[0] or 0, 2),
            "mean_age_at_exit": round(rows[1] or 0, 1),
            **self.count_established_vs_new(),
        }

    def query(self, sql: str, params: tuple = ()) -> list:
        """
        Execute an arbitrary read-only SQL query and return all rows as dicts.

        Intended for ad-hoc analysis in notebooks — do not use for writes.

        Example
        -------
            db.query(
                "SELECT race, COUNT(*) FROM patients WHERE treatment_type='leep'
                 GROUP BY race"
            )
        """
        cur = self.conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """
        Drop and recreate all tables. USE WITH CAUTION — deletes all data.
        Useful at the start of a fresh simulation run to avoid ID collisions
        with data from a previous run.
        """
        self.conn.execute("DROP TABLE IF EXISTS events;")
        self.conn.execute("DROP TABLE IF EXISTS patients;")
        self.conn.commit()
        self._create_schema()

    def close(self) -> None:
        """Commit any pending writes and close the database connection."""
        self.conn.commit()
        self.conn.close()
