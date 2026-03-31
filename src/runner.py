# =============================================================================
# runner.py
# SimulationRunner — day-by-day discrete event simulation.
# =============================================================================
#
# The clock is an integer day counter (0 … n_days-1).  Each tick processes
# one full day: new arrivals, provider queues, overflow routing, and any
# follow-up appointments that fall due.
#
# Queue logic
# -----------
# PCP / Gynecologist / Specialist
#   Capacity is split into outpatient slots (OUTPATIENT_FRACTION) and
#   drop-in slots.  Outpatients are scheduled in advance so their slots are
#   always guaranteed — the scheduler finds the next day with room rather
#   than ever overfilling.  Drop-ins fill whatever capacity remains after
#   outpatients are seated.  Drop-ins who cannot be seen today overflow
#   to tomorrow's drop-in queue for the same provider.
#
# ER
#   No outpatient slots — entirely drop-in.  Overflow patients either
#   retry the ER tomorrow (ER_OVERFLOW_RETRY_PROB) or are converted to a
#   scheduled outpatient appointment at PCP / Gyno / Specialist.
#
# Wait times
# ----------
# Wait time = day_seen - day_created.  For outpatients this equals the
# scheduling lead time.  For drop-ins it grows each time they overflow.
# These are recorded per resource in metrics["wait_times"].
#
# Follow-up scheduling
# --------------------
# After a screening event, follow-up appointments (colposcopy, LEEP, biopsy)
# are placed in a future-day follow-up queue.  On the due day they are
# processed before new arrivals, consuming procedure slots.
#
# Usage
# -----
#   sim = SimulationRunner(n_days=365, seed=42)
#   sim.run()
#   sim.summary()
#   sim.revenue_summary()
#   sim.plot_all()
# =============================================================================

import math
import random
from collections import defaultdict
from typing import Optional

import config as cfg
from patient import Patient
from population import sample_patient, generate_established_population, draw_mortality
from screening import (
    get_eligible_screenings,
    assign_screening_test,
    run_screening_step,
    days_until_eligible,
)
from followup import (
    route_cervical_result,
    run_colposcopy,
    run_treatment,
    run_lung_followup,
)
from metrics import (
    initialize_metrics,
    record_screening,
    record_exit,
    print_summary,
    print_revenue_summary,
    compute_revenue,
)
from db import SimulationDB

_NON_ER       = ["pcp", "gynecologist", "specialist"]
_ALL_PROVIDERS = _NON_ER + ["er"]


# =============================================================================
# Helpers
# =============================================================================

def _poisson(lam: float) -> int:
    """Normal approximation to Poisson(lam) for large lam (daily arrivals)."""
    return max(0, round(random.gauss(lam, math.sqrt(lam))))


def _outpatient_cap(provider: str) -> int:
    """Daily outpatient slot count for a provider."""
    total = cfg.PROVIDER_CAPACITY.get(provider, 0)
    frac  = cfg.OUTPATIENT_FRACTION.get(provider, 0.0)
    return int(total * frac)


def _dropin_cap(provider: str) -> int:
    """Daily drop-in slot count for a provider."""
    return cfg.PROVIDER_CAPACITY.get(provider, 0) - _outpatient_cap(provider)


# =============================================================================
# Patient Queue Manager
# =============================================================================

class PatientQueues:
    """
    Day-by-day patient queue manager for all providers.

    PATIENT TYPES
    ─────────────────────────────────────────────────────────────────────────────
    There are exactly two types — no inpatient modelling in this simulation:

      outpatient  — a patient with a confirmed scheduled appointment.
                    Guaranteed a slot: the scheduler never books beyond capacity.
                    Once scheduled they cannot be turned away on the day.

      drop_in     — a walk-in patient with no prior appointment.
                    Fills whatever capacity is left after outpatients are seated.
                    Excess drop-ins overflow to the next day (or are re-routed).

    CAPACITY GUARANTEE
    ─────────────────────────────────────────────────────────────────────────────
    The number of outpatients scheduled on any (provider, day) pair is always
    strictly less than _outpatient_cap(provider).  This invariant is enforced
    by schedule_outpatient(), which advances the booking day until a slot is
    free, and verified by a hard assertion after every booking.

    This guarantee holds regardless of how PROVIDER_CAPACITY or OUTPATIENT_FRACTION
    are set in config — the cap is recomputed from config at each call to
    schedule_outpatient(), so changing those values between scenario runs
    automatically applies to all subsequent bookings.

    Attributes
    ----------
    outpatient[provider][day] : list[Patient]
        Patients with a confirmed appointment on that day.
        Invariant: len(outpatient[p][d]) < _outpatient_cap(p) for all p, d.

    dropin[provider] : list[Patient]
        Today's walk-ins.  Reset at the start of each provider's processing.

    followup[day] : list[(Patient, dict)]
        Follow-up appointments due on a specific future day.
        dict carries the clinical context: {"cancer": ..., "step": ...}

    daily_slots[day][procedure] : int
        Remaining procedure slots available on a future day.
        Initialised lazily from cfg.CAPACITIES.
    """

    def __init__(self):
        self.outpatient   = defaultdict(lambda: defaultdict(list))
        self.dropin       = defaultdict(list)
        self.followup     = defaultdict(list)
        self.daily_slots  = defaultdict(dict)

    # ── Outpatient scheduling ─────────────────────────────────────────────────

    def schedule_outpatient(
        self, p: Patient, provider: str, earliest_day: int
    ) -> int:
        """
        Book a patient into the earliest available outpatient slot on or after
        earliest_day, then return the booked day.

        CAPACITY CONTRACT
        -----------------
        After every booking this method asserts:
            len(outpatient[provider][booked_day]) <= cap
        If the assertion ever fails, a bug was introduced that let two code
        paths book the same slot simultaneously.  The assert fires immediately
        rather than allowing a silent capacity violation.

        The cap is read fresh from config at each call — so if PROVIDER_CAPACITY
        or OUTPATIENT_FRACTION are changed between scenario runs, the new values
        are automatically respected for all future bookings without any
        additional code changes.

        Zero-capacity providers (ER)
        ----------------------------
        ER has OUTPATIENT_FRACTION = 0, so its outpatient cap is 0.
        Attempting to schedule an outpatient there would loop forever
        (0 >= 0 is always True).  Instead, zero-cap providers receive the
        patient as a drop-in for earliest_day.  This is the correct clinical
        model: established patients never use the ER as their primary
        scheduled provider.
        """
        cap = _outpatient_cap(provider)
        if cap <= 0:
            # Zero-capacity provider — add to drop-in queue, not outpatient schedule
            self.dropin[provider].append(p)
            return earliest_day

        # Advance day until a slot is available (never overfills).
        # Count only active patients — inactive (dead/exited) patients leave
        # their slot logically vacated so replacement entrants can fill it.
        day = earliest_day
        while sum(1 for q in self.outpatient[provider][day] if q.active) >= cap:
            day += 1

        self.outpatient[provider][day].append(p)

        # Hard post-condition: the booked day must not exceed capacity.
        # This catches any future bug where two threads / code paths race on
        # the same slot, or where the cap calculation is wrong.
        booked_count = sum(1 for q in self.outpatient[provider][day] if q.active)
        assert booked_count <= cap, (
            f"CAPACITY VIOLATION: {provider} day {day} has {booked_count} active "
            f"outpatients but cap={cap}. Check _outpatient_cap() and config."
        )

        return day

    # ── Drop-in management ────────────────────────────────────────────────────

    def add_dropin(self, p: Patient, provider: str) -> None:
        """Add patient to a provider's walk-in queue."""
        self.dropin[provider].append(p)

    # ── Daily queue processing ────────────────────────────────────────────────

    def process_day(self, provider: str, day: int):
        """
        Seat today's patients for one provider, respecting total capacity.

        NO INPATIENTS — only two patient types exist:
          • outpatient (scheduled) — always seen; guaranteed slot by scheduler.
          • drop_in  (walk-in)    — fills whatever capacity remains after
                                    outpatients are seated; excess overflows.

        Seating priority
        ----------------
        1. ALL scheduled outpatients are seated unconditionally. The scheduler
           already guaranteed their count ≤ outpatient cap (see schedule_outpatient).
        2. Drop-ins fill any unused outpatient slots first, then their own
           dedicated drop-in slots.  Available drop-in capacity =
               dropin_cap + max(0, outpt_cap - n_outpatients_today)
           Using the ACTUAL scheduled headcount (not config cap) ensures that
           if a scenario reduces capacity after patients were booked, the fill
           math is still correct.
        3. Excess drop-ins (overflow) are returned to the caller for re-routing.

        Returns
        -------
        seen     : list[Patient]   — all patients who will be processed today
        overflow : list[Patient]   — drop-ins who could not be accommodated
        """
        # Capacities from config — govern drop-in fill only
        outpt_cap  = _outpatient_cap(provider)
        dropin_cap = _dropin_cap(provider)
        total_cap  = outpt_cap + dropin_cap          # = PROVIDER_CAPACITY[provider]

        # Outpatients are always all seated (capacity was guaranteed at booking)
        seen_outpts     = self.outpatient[provider].pop(day, [])

        # Count only ACTIVE outpatients for the drop-in capacity calculation.
        # schedule_outpatient() also counts only active patients when checking
        # the cap, so a dead patient whose slot was "refilled" by a replacement
        # entrant must not reduce the available drop-in headroom.  Using the
        # raw len() would double-count those slots and incorrectly zero out
        # drop-in capacity on mortality-sweep days.
        n_active_outpts = sum(1 for q in seen_outpts if q.active)

        # Drop-ins fill remaining total capacity, computed from ACTIVE headcount.
        remaining     = max(0, total_cap - n_active_outpts)

        # NYP MODEL ASSUMPTION — age-based drop-in priority (cfg.AGE_PRIORITY_THRESHOLD)
        # Women aged 40+ are sorted to the front of the drop-in queue before the
        # capacity slice is applied. This ensures that when the drop-in pool exceeds
        # available slots, younger patients overflow rather than high-revenue 40+ patients.
        # Ordering within each age group preserves arrival order (stable sort).
        today_dropins = sorted(
            self.dropin.get(provider, []),
            key=lambda p: (0 if p.age >= cfg.AGE_PRIORITY_THRESHOLD else 1),
        )
        seen_dropins     = today_dropins[:remaining]
        overflow         = today_dropins[remaining:]
        self.dropin[provider] = []

        return seen_outpts + seen_dropins, overflow

    # ── Schedule integrity check ──────────────────────────────────────────────

    def validate_capacity(self, raise_on_violation: bool = True) -> list:
        """
        Scan the entire outpatient schedule and verify no day exceeds its cap.

        Returns a list of violation strings (empty = all clear). Call this
        after warmup initialisation or after loading a scenario config to
        confirm the capacity guarantee holds before a long run starts.

        Parameters
        ----------
        raise_on_violation : if True (default), raises AssertionError on the
                             first violation found so the bug surfaces
                             immediately rather than corrupting a full run.

        Example
        -------
            sim._queues.validate_capacity()   # called after _initialize_population()
        """
        violations = []
        for provider, day_map in self.outpatient.items():
            cap = _outpatient_cap(provider)
            if cap <= 0:
                continue
            for day, patients in day_map.items():
                if len(patients) > cap:
                    msg = (
                        f"CAPACITY VIOLATION: provider={provider} "
                        f"day={day} scheduled={len(patients)} cap={cap}"
                    )
                    violations.append(msg)
                    if raise_on_violation:
                        raise AssertionError(msg)
        return violations

    # ── Follow-up scheduling ──────────────────────────────────────────────────

    def schedule_followup(
        self, p: Patient, context: dict, due_day: int
    ) -> None:
        """Queue a follow-up appointment for a specific future day."""
        self.followup[due_day].append((p, context))

    def get_due_followups(self, day: int):
        """Return (and clear) all follow-ups due today."""
        return self.followup.pop(day, [])

    # ── Procedure slot management ─────────────────────────────────────────────

    def consume_slot(self, procedure: str, day: int) -> bool:
        """
        Attempt to consume one procedure slot on a given day.
        Returns True if a slot was available, False if fully booked.
        Slots are initialised from cfg.CAPACITIES on first access.
        """
        if procedure not in self.daily_slots[day]:
            self.daily_slots[day][procedure] = cfg.CAPACITIES.get(procedure, 0)
        if self.daily_slots[day][procedure] > 0:
            self.daily_slots[day][procedure] -= 1
            return True
        return False


# =============================================================================
# Simulation Runner
# =============================================================================

class SimulationRunner:
    """
    Day-by-day discrete event simulation of NYP women's health screening.

    Two operating modes
    -------------------
    Standard mode (use_stable_population=False, default)
        New patients arrive every day via a Poisson process at daily_rate.
        No cycling, no mortality. Suitable for short scenario runs.

    Stable-population mode (use_stable_population=True)
        A fixed cohort of cfg.SIMULATED_POPULATION established patients
        cycles through the provider system annually. Each patient is
        rescheduled immediately after each visit. A mortality sweep runs
        every cfg.MORTALITY_CHECK_DAYS and removes patients who die (drawn
        from age-adjusted US life-table rates). Deaths are replaced by new
        drop-in entrants at a rate of cfg.NEW_PATIENT_DAILY_RATE, keeping
        the total population stable. Exited patients are batch-flushed to a
        SQLite database every cfg.DB_FLUSH_INTERVAL days.

        The initial cohort is spread evenly across the first 365 days
        (warmup), so providers are near capacity from day 1 rather than
        ramping up slowly.

    Parameters
    ----------
    n_days                  : Number of days to simulate.
    seed                    : Random seed for reproducibility.
    daily_rate              : Mean new arrivals/day (standard mode only).
    use_stable_population   : Enable the 70-year cycling stable-population model.
    db_path                 : SQLite path override (default from config).
    reset_db                : If True, wipe the database before the run.
    """

    def __init__(
        self,
        n_days:                 int  = cfg.SIM_DAYS,
        seed:                   int  = cfg.RANDOM_SEED,
        daily_rate:             int  = cfg.DAILY_PATIENTS,
        use_stable_population:  bool = False,
        db_path:                Optional[str] = None,
        reset_db:               bool = False,
    ):
        self.n_days                = n_days
        self.seed                  = seed
        self.daily_rate            = daily_rate
        self.use_stable_population = use_stable_population
        self.db_path               = db_path
        self.reset_db              = reset_db
        self.metrics               = None
        self._queues               = None
        self._pid                  = 0

        # Stable-population state — populated by _initialize_population()
        self._established_pool:    list     = []   # all living established patients
        self._flush_buffer:        list     = []   # exited patients awaiting DB write
        self._pending_new_entries: int      = 0    # replacement entrants still to create
        self._last_mortality_day:  int      = 0    # last day a mortality sweep ran
        self._db:                  Optional[SimulationDB] = None

    # =========================================================================
    # Public interface
    # =========================================================================

    def run(self) -> dict:
        """
        Run the full simulation.

        In stable-population mode:
          1. Opens (or resets) the SQLite database.
          2. Generates the initial established cohort and spreads them
             across the first year as the warmup schedule.
          3. Runs the day loop — each tick ages patients, checks mortality,
             processes arrivals, and seats patients at providers.
          4. Flushes any remaining patients to the database at the end.

        Returns
        -------
        metrics dict — pass to summary(), revenue_summary(), plot_all().
        """
        random.seed(self.seed)
        self.metrics = initialize_metrics()
        self._queues = PatientQueues()
        self._pid    = 0

        if self.use_stable_population:
            # If resetting, delete the old file first so _create_schema() always
            # builds on a clean slate (avoids stale-column errors on index creation
            # when schema has been extended since the last run).
            if self.reset_db and self.db_path:
                import os
                try:
                    os.remove(self.db_path)
                except FileNotFoundError:
                    pass
            self._db = SimulationDB(db_path=self.db_path)
            self._initialize_population()

        for day in range(self.n_days):
            self._tick(day)

        # Final year checkpoint (captures year-70 stats at end of loop)
        if self.metrics.get("year_checkpoints") is not None:
            last_year = self.n_days // 365
            self.metrics["year_checkpoints"].append({
                "year":                last_year,
                "day":                 self.n_days,
                "pool_size":           len(self._established_pool) if self.use_stable_population else None,
                "cum_cervical":        self.metrics["n_screened"]["cervical"],
                "cum_lung":            self.metrics["n_screened"]["lung"],
                "cum_colposcopy":      self.metrics["n_colposcopy"],
                "cum_leep":            self.metrics["n_treatment"].get("leep", 0),
                "cum_treated":         self.metrics["n_treated"],
                "cum_ltfu":            self.metrics["n_ltfu"],
                "cum_lung_biopsy":     self.metrics["lung_biopsy_completed"],
                "cum_lung_treatment":  self.metrics["lung_treatment_given"],
                "cum_mortality":       self.metrics.get("mortality_count", 0),
                "cum_n_patients":      self.metrics["n_patients"],
            })

        # Final flush of any remaining exited patients
        if self.use_stable_population and self._db:
            self._flush_exited_patients(force=True)
            # Do NOT close the DB here — keep it open for post-run queries
            # via db_summary() and plot_pool_stability().
            # Call sim.close_db() explicitly when done with all queries.

        return self.metrics

    def close_db(self) -> None:
        """
        Close the SQLite database connection.

        Call this after all post-run queries (db_summary, custom queries) are
        complete. Separating this from run() allows the database to remain
        open for notebook analysis after the simulation finishes.
        """
        if self._db is not None:
            self._db.close()
            self._db = None

    def summary(self) -> None:
        """Print clinical outcomes summary."""
        self._require_run()
        print_summary(self.metrics)

    def revenue_summary(self) -> None:
        """Print realized vs. foregone revenue."""
        self._require_run()
        print_revenue_summary(self.metrics)

    def plot_all(self) -> None:
        """2×2 plot: cervical funnel, lung funnel, RADS distribution, revenue."""
        self._require_run()
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(2, 2, figsize=(14, 10))
        fig.suptitle(
            f"NYP Screening Simulation  "
            f"(n={self.metrics['n_patients']:,} patients, {self.n_days} days)",
            fontsize=13, fontweight="bold",
        )
        self._plot_cervical_funnel(axes[0, 0])
        self._plot_lung_funnel(axes[0, 1])
        self._plot_rads_distribution(axes[1, 0])
        self._plot_revenue(axes[1, 1])
        plt.tight_layout()
        plt.show()

    def db_summary(self) -> None:
        """
        Print a summary of the SQLite database contents (stable-population mode only).

        Shows total patients flushed, breakdown by exit reason, established vs.
        new-entrant counts, mean visit count, and mean age at exit.
        Useful as a quick sanity check after a long run.
        """
        if not self.use_stable_population or self._db is None:
            print("Database not available — run with use_stable_population=True.")
            return
        stats = self._db.summary_stats()
        print("\n── Database Summary ──────────────────────────────────────────")
        print(f"  Total flushed patients : {stats.get('total_flushed', 0):,}")
        print(f"  Established (cycling)  : {stats.get('established', 0):,}")
        print(f"  New entrants           : {stats.get('new_entrant', 0):,}")
        print(f"  Mean visits per patient: {stats.get('mean_visit_count', 0):.1f}")
        print(f"  Mean age at exit       : {stats.get('mean_age_at_exit', 0):.1f}")
        print(f"  Mortality total        : {self.metrics.get('mortality_count', 0):,}")
        if "by_exit_reason" in stats:
            print("  Exit reasons:")
            for reason, count in sorted(stats["by_exit_reason"].items()):
                print(f"    {reason:<25} {count:,}")
        print()

    def plot_pool_stability(self) -> None:
        """
        Line chart showing the established-patient pool size over time.

        A flat line confirms the stable-population assumption holds —
        mortality is balanced by replacement entrants. A declining trend
        would indicate the replacement rate is too low; a rising trend
        would indicate the mortality rate is too low relative to config.
        Only meaningful when use_stable_population=True.
        """
        self._require_run()
        snapshots = self.metrics.get("pool_size_snapshot", [])
        if not snapshots:
            print("No pool snapshots recorded. Run with use_stable_population=True.")
            return
        import matplotlib.pyplot as plt
        days, sizes = zip(*snapshots)
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(days, sizes, color="#4472C4", linewidth=1.2)
        ax.axhline(
            cfg.SIMULATED_POPULATION, color="red", linestyle="--",
            linewidth=1.0, label=f"Target ({cfg.SIMULATED_POPULATION:,})"
        )
        ax.set_title("Established-Patient Pool Size Over Time (Stability Check)")
        ax.set_xlabel("Simulation Day")
        ax.set_ylabel("Pool Size")
        ax.legend()
        plt.tight_layout()
        plt.show()

    def plot_queues(self) -> None:
        """Bar chart: total drop-in overflow per provider over the simulation."""
        self._require_run()
        import matplotlib.pyplot as plt
        overflow = self.metrics.get("overflow", {})
        if not overflow:
            print("No overflow recorded.")
            return
        providers = list(overflow.keys())
        totals    = [overflow[p] for p in providers]
        fig, ax   = plt.subplots(figsize=(7, 4))
        bars = ax.bar(providers, totals, color="#ED7D31")
        ax.set_title("Drop-In Overflow by Provider (cumulative, all days)")
        ax.set_ylabel("Patients overflowed")
        ax.set_xlabel("Provider")
        _max = max(totals) if totals else 1
        for bar, val in zip(bars, totals):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + _max * 0.01,
                f"{val:,}", ha="center", fontsize=9,
            )
        plt.tight_layout()
        plt.show()

    # =========================================================================
    # Daily tick
    # =========================================================================

    def _tick(self, day: int) -> None:
        """
        Process one simulation day in order:

          Stable-population additions (runs when use_stable_population=True):
            0a. Mortality sweep (every MORTALITY_CHECK_DAYS): age all established
                patients, draw Bernoulli mortality for each, remove the dead,
                queue replacement new-entrants.
            0b. Spawn replacement new-entrants to balance mortality exits.
            0c. Flush exited patients to SQLite (every DB_FLUSH_INTERVAL days).

          Standard steps (always run):
            1. Process follow-up appointments due today.
            2. Generate new patient arrivals → route to queues.
            3. For each provider: seat patients, route overflow, screen seen.
        """
        if self.use_stable_population:
            # Mortality sweep — runs every MORTALITY_CHECK_DAYS
            if (day - self._last_mortality_day) >= cfg.MORTALITY_CHECK_DAYS:
                self._mortality_sweep(day)
                self._last_mortality_day = day

            # Spawn new entrants to replace those who exited since last tick
            self._spawn_replacement_entrants(day)

            # Flush exited patients to SQLite in batches
            self._flush_exited_patients(day=day)

        # Annual checkpoint — record cumulative stats at each year boundary
        if day > 0 and day % 365 == 0:
            year = day // 365
            self.metrics["year_checkpoints"].append({
                "year":                year,
                "day":                 day,
                "pool_size":           len(self._established_pool) if self.use_stable_population else None,
                # Screening volumes
                "cum_cervical":        self.metrics["n_screened"]["cervical"],
                "cum_lung":            self.metrics["n_screened"]["lung"],
                # Follow-up / treatment
                "cum_colposcopy":      self.metrics["n_colposcopy"],
                "cum_leep":            self.metrics["n_treatment"].get("leep", 0),
                "cum_treated":         self.metrics["n_treated"],
                "cum_ltfu":            self.metrics["n_ltfu"],
                # Lung pathway milestones
                "cum_lung_biopsy":     self.metrics["lung_biopsy_completed"],
                "cum_lung_treatment":  self.metrics["lung_treatment_given"],
                # Population exits
                "cum_mortality":       self.metrics.get("mortality_count", 0),
                "cum_n_patients":      self.metrics["n_patients"],
            })

        # 1. Follow-ups due today
        for p, context in self._queues.get_due_followups(day):
            self._run_followup(p, context, day)

        # 2. New arrivals
        self._generate_arrivals(day)

        # 3. Provider queues
        for provider in _ALL_PROVIDERS:
            seen, overflow = self._queues.process_day(provider, day)

            self.metrics.setdefault("overflow", defaultdict(int))
            self.metrics["overflow"][provider] += len(overflow)

            self._route_overflow(overflow, provider, day)

            for p in seen:
                p.wait_days = day - p.day_created
                self._screen_patient(p, day)

    # =========================================================================
    # Arrivals
    # =========================================================================

    def _generate_arrivals(self, day: int) -> None:
        """
        Create today's new patients and route them to provider queues.

        Standard mode
        -------------
        Poisson draw of daily_rate new patients. Outpatients are scheduled
        at the next free slot on or after (day + lead_time). Drop-ins and ER
        patients go directly to today's drop-in queue.

        Stable-population mode
        ----------------------
        Only new-entrant / replacement drop-ins are created here (at rate
        cfg.NEW_PATIENT_DAILY_RATE). Established patients are NOT generated
        here — they were pre-scheduled during warmup and then rescheduled
        immediately after each visit. This separates the two flows cleanly.
        """
        if self.use_stable_population:
            # Organic new patients: first-time visitors, women turning 21, new
            # movers, etc. They arrive as drop-ins and are seen like any walk-in.
            # After their first visit they join the established cycling pool
            # (handled in _screen_patient) so they become regular annual patients.
            # This is distinct from mortality replacements (_spawn_replacement_entrants).
            rate = cfg.ORGANIC_NEW_PATIENT_DAILY_RATE
        else:
            rate = self.daily_rate

        _dest_keys = list(cfg.DESTINATION_PROBS.keys())
        _dest_w    = list(cfg.DESTINATION_PROBS.values())
        _type_keys = list(cfg.PATIENT_TYPE_PROBS.keys())
        _type_w    = list(cfg.PATIENT_TYPE_PROBS.values())

        for _ in range(_poisson(rate)):
            dest  = random.choices(_dest_keys, weights=_dest_w)[0]
            ptype = random.choices(_type_keys, weights=_type_w)[0]

            p = sample_patient(self._pid, day, dest, ptype)
            self._pid += 1

            if ptype == "outpatient" and dest != "er":
                lo, hi    = cfg.OUTPATIENT_LEAD_DAYS.get(dest, (1, 7))
                earliest  = day + random.randint(lo, hi)
                self._queues.schedule_outpatient(p, dest, earliest)
            else:
                # Drop-in or ER (ER has no outpatient slots)
                self._queues.add_dropin(p, dest)

    # =========================================================================
    # Overflow routing
    # =========================================================================

    def _route_overflow(
        self, overflow: list, provider: str, day: int
    ) -> None:
        """
        Re-route patients who could not be seen today.

        PCP / Gyno / Specialist:
            → tomorrow's drop-in queue for the same provider.

        ER:
            ER_OVERFLOW_RETRY_PROB → tomorrow's ER drop-in queue.
            otherwise              → scheduled outpatient at PCP / Gyno / Specialist,
                                     earliest available slot.
        """
        for p in overflow:
            if provider == "er":
                if random.random() < cfg.ER_OVERFLOW_RETRY_PROB:
                    self._queues.add_dropin(p, "er")
                else:
                    # Route to a non-ER provider weighted by DESTINATION_PROBS
                    # (excluding ER's share, redistributed proportionally).
                    # Previously used uniform random.choice — fixed to match the
                    # population-level provider distribution.
                    non_er_probs = {
                        k: v for k, v in cfg.DESTINATION_PROBS.items()
                        if k != "er"
                    }
                    alt      = random.choices(
                        list(non_er_probs.keys()),
                        weights=list(non_er_probs.values()),
                    )[0]
                    lo, hi   = cfg.OUTPATIENT_LEAD_DAYS.get(alt, (1, 7))
                    earliest = day + random.randint(lo, hi)
                    self._queues.schedule_outpatient(p, alt, earliest)
            else:
                self._queues.add_dropin(p, provider)

    # =========================================================================
    # Screening
    # =========================================================================

    def _screen_patient(self, p: Patient, day: int) -> None:
        """
        Assess eligibility and run screening for each due cancer type.

        In stable-population mode this method also:
          • Increments the patient's visit_count (total provider contacts).
          • Reschedules established patients for their next annual visit
            AFTER screening completes, whether or not they were screened.
            (Being ineligible for a specific cancer does not mean the patient
            stops seeing their provider — they return next year regardless.)

        Abnormal results trigger a follow-up appointment queued for a future day.
        """
        # Guard: patient may have been killed by the mortality sweep between
        # being placed in the outpatient queue and today's processing.
        if not p.active:
            return

        eligible = get_eligible_screenings(p)
        self.metrics["n_patients"] += 1

        # Track total provider contacts for longitudinal analysis
        if self.use_stable_population:
            p.visit_count += 1

        if not eligible:
            self.metrics["n_unscreened"] += 1

            # Find the soonest day this patient will become eligible for any
            # active cancer.  Three cases:
            #   d > 0  → not yet eligible (e.g. turning 21, approaching 20 pk-yrs,
            #             not yet 50) → schedule a return visit at that future day.
            #   d == 0 → already eligible (shouldn't reach here, but guard anyway).
            #   d < 0  → permanently ineligible (no cervix, aged out, never-smoker,
            #             quit window closed).
            soonest = -1
            for cancer in cfg.ACTIVE_CANCERS:
                d = days_until_eligible(p, cancer)
                if d > 0 and (soonest < 0 or d < soonest):
                    soonest = d

            if soonest > 0:
                self._queues.schedule_outpatient(p, p.destination, day + soonest)
                self.metrics["n_reschedule"] += 1
                p.log(day, f"NOT YET ELIGIBLE — return visit scheduled in {soonest} days")
            elif p.is_established:
                # Permanently ineligible established patients (aged out of all
                # screenings, no cervix, quit window closed) exit the pool and
                # trigger a replacement younger entrant. This models the real
                # inflow of women newly entering the eligible age range,
                # keeping the pool's age distribution realistic over 70 years.
                p.log(day, "INELIGIBLE for all cancers — exiting pool, replacement queued")
                p.exit_system(day, "ineligible")
                record_exit(self.metrics, "ineligible")
                self._flush_buffer.append(p)
                self._pending_new_entries += 1
            else:
                # One-time / new patient permanently ineligible → exit silently;
                # no LTFU revenue impact because no screening was warranted.
                p.exit_system(day, "ineligible")
                record_exit(self.metrics, "ineligible")
            return

        self.metrics["n_eligible_any"] += 1

        for cancer in eligible:
            if not p.active:
                break

            result = run_screening_step(p, cancer, day, self.metrics)

            if result is None:
                # Two distinct cases when run_screening_step returns None:
                #
                # A) LTFU (p.exit_reason is set) — patient was lost from a
                #    clinical pathway (e.g. lung referral not placed, LDCT
                #    not scheduled). Established patients are re-activated so
                #    the annual cycle continues; non-established exit for good.
                #
                # B) Skip (p.exit_reason is None) — patient was either not
                #    yet due (interval not met) or ineligible for this specific
                #    cancer. This is the common case: a cytology patient visits
                #    every year but is only due every 3 years. We must NOT
                #    return here — we continue the for-loop so any remaining
                #    cancer types are checked, and the reschedule at the bottom
                #    of _screen_patient still runs.
                if p.exit_reason:
                    record_exit(self.metrics, p.exit_reason)
                    if p.is_established:
                        # Re-activate so they can be rescheduled for future visits
                        p.active      = True
                        p.exit_reason = None
                        p.exit_day    = None
                        # Reschedule BEFORE returning so the annual cycle continues
                        self._reschedule_established(p, day)
                    else:
                        return   # non-established exits permanently
                    return       # LTFU handled — skip remaining cancers today
                # Case B: no exit_reason → simple skip; continue to next cancer
                continue

            record_screening(self.metrics, p, cancer, result)
            self.metrics["wait_times"]["screening_seen"].append(p.wait_days)

            # ── Schedule follow-up ─────────────────────────────────────────────
            if cancer == "cervical" and result not in ("NORMAL", "HPV_NEGATIVE"):
                due = day + cfg.FOLLOWUP_DELAY_DAYS.get("colposcopy", 30)
                self._queues.schedule_followup(
                    p, {"cancer": "cervical", "step": "colposcopy"}, due
                )

            elif cancer == "lung":
                # Result routing (communication + RADS classification) happens
                # on the same day as LDCT.  Biopsy, if needed, is a future appt.
                self._lung_result_routing(p, day)

        # ── Reschedule established patients for next annual visit ──────────────
        # Runs after all cancer loops complete (whether or not screening occurred).
        # Established patients who went LTFU for a cancer pathway were re-activated
        # above; non-established patients who exited mid-loop will not reach here.
        if self.use_stable_population and p.is_established and p.active:
            self._reschedule_established(p, day)

        # ── Open-loop: convert first-time visitors into established patients ───
        # Organic new patients (from _generate_arrivals) arrive as drop-ins with
        # is_established=False. After their first successful visit, they join the
        # established cycling pool so they become regular annual patients.
        # Pool cap (SIMULATED_POPULATION) is enforced — if the pool is already at
        # target size (maintained by mortality replacement), the patient completes
        # their visit but does not join the cycling cohort.
        elif (
            self.use_stable_population
            and not p.is_established
            and p.active
            and len(self._established_pool) < cfg.SIMULATED_POPULATION
        ):
            p.is_established       = True
            p.age_at_entry         = p.age
            p.simulation_entry_day = day
            self._established_pool.append(p)

            # Book the full ADVANCE_SCHEDULE_YEARS window from scratch, mirroring
            # _spawn_replacement_entrants. _reschedule_established only extends
            # the FAR END of an existing window (years 1–N already booked), so
            # calling it here would only add year-5 and leave years 1–4 unbooked —
            # the patient would then go 5 years without a visit.
            first_visit   = day + cfg.ANNUAL_VISIT_INTERVAL
            first_booked  = self._queues.schedule_outpatient(p, p.destination, first_visit)
            p.next_visit_day = first_booked
            for yr in range(1, cfg.ADVANCE_SCHEDULE_YEARS):
                self._queues.schedule_outpatient(
                    p, p.destination, first_booked + yr * cfg.ANNUAL_VISIT_INTERVAL
                )
            p.log(
                day,
                f"FIRST VISIT COMPLETE — joined established cycling pool; "
                f"scheduled years 1–{cfg.ADVANCE_SCHEDULE_YEARS} "
                f"(next visit day {first_booked})"
            )

    # =========================================================================
    # Stable population management
    # =========================================================================

    def _initialize_population(self) -> None:
        """
        Create the initial established-patient cohort and schedule their
        first visits across the warmup window (days 0 … WARMUP_DAYS-1).

        The warmup avoids a cold-start bias: without it, all providers would
        be completely empty on day 0 and fill up slowly over the first year,
        making year-1 metrics incomparable to later years.

        Strategy
        --------
        Patients are evenly spread across days 0 … cfg.WARMUP_DAYS.  Each
        patient is booked as an outpatient at their sampled destination.
        If the preferred slot is full (outpatient cap exceeded), the scheduler
        automatically pushes to the next free day — so no day is ever
        overfilled.

        This generates `cfg.SIMULATED_POPULATION` patients, advancing
        self._pid so subsequent arrivals have non-colliding IDs.
        """
        pool = generate_established_population(
            n         = cfg.SIMULATED_POPULATION,
            start_pid = self._pid,
            entry_day = 0,
        )
        self._pid += len(pool)

        warmup = max(1, cfg.WARMUP_DAYS)
        for i, p in enumerate(pool):
            # Spread first appointments evenly across the warmup window
            target_day = int(i * warmup / len(pool))
            booked_day = self._queues.schedule_outpatient(p, p.destination, target_day)
            p.next_visit_day = booked_day

            # Pre-book ADVANCE_SCHEDULE_YEARS - 1 additional annual visits so
            # providers are booked out years in advance from day 0.
            for yr in range(1, cfg.ADVANCE_SCHEDULE_YEARS):
                self._queues.schedule_outpatient(
                    p, p.destination, booked_day + yr * cfg.ANNUAL_VISIT_INTERVAL
                )

        self._established_pool = pool

        # Verify capacity contract holds across the entire warmup schedule
        violations = self._queues.validate_capacity(raise_on_violation=True)
        assert not violations, f"Warmup produced {len(violations)} capacity violations."

        print(
            f"[INIT] Stable population: {len(pool):,} established patients "
            f"scheduled across {warmup} warmup days.  Capacity check: OK."
        )

    def _mortality_sweep(self, day: int) -> None:
        """
        Age all established patients and apply a Bernoulli mortality draw.

        Called every cfg.MORTALITY_CHECK_DAYS days. For each established patient:
          1. Update age from age_at_entry + elapsed years (integer years only).
          2. Draw mortality (age-adjusted annual rate scaled to sweep interval).
          3. If the patient dies: exit them, remove from pool, buffer for DB flush.

        Patients who survive the draw remain in the pool unchanged.

        The number of deaths is recorded in metrics["mortality_count"] and
        added to self._pending_new_entries so the replacement flow (
        _spawn_replacement_entrants) can create matching new drop-ins.
        """
        sweep_days  = day - self._last_mortality_day if self._last_mortality_day > 0 else cfg.MORTALITY_CHECK_DAYS
        survivors   = []
        death_count = 0

        year_fraction = sweep_days / 365.0

        for p in self._established_pool:
            # 1. Age the patient (integer years elapsed since pool entry)
            years_elapsed = (day - p.simulation_entry_day) // 365
            p.age = p.age_at_entry + years_elapsed

            # Skip patients already exited between sweeps (e.g. aged out in _screen_patient)
            if not p.active:
                self._flush_buffer.append(p)
                continue

            # 2. Update time-varying attributes that affect screening eligibility
            if p.smoker:
                # Accumulate pack-years (1 pack-year per year of smoking)
                p.pack_years += year_fraction
                # Smoking cessation draw
                if random.random() < cfg.ANNUAL_SMOKING_CESSATION_PROB * year_fraction:
                    p.smoker          = False
                    p.years_since_quit = 0.0
            elif p.pack_years > 0:
                # Former smoker — advance years-since-quit; lose lung eligibility after 15 yrs
                p.years_since_quit += year_fraction

            # HPV clearance — affects cervical result probabilities
            if p.hpv_positive:
                if random.random() < cfg.ANNUAL_HPV_CLEARANCE_PROB * year_fraction:
                    p.hpv_positive = False

            # 3. Mortality draw
            if draw_mortality(p, sweep_days=sweep_days):
                p.exit_system(day, "mortality")
                record_exit(self.metrics, "mortality")
                self._flush_buffer.append(p)
                death_count += 1
            else:
                survivors.append(p)

        self._established_pool = survivors
        self._pending_new_entries += death_count

        if death_count > 0:
            self.metrics["mortality_count"] += death_count

        # Snapshot pool size for longitudinal pool-stability plots
        self.metrics["pool_size_snapshot"].append((day, len(self._established_pool)))

    def _reschedule_established(self, p: Patient, day: int) -> None:
        """
        Book an established patient's next annual visit.

        Called at the end of each visit (from _screen_patient). Schedules
        the patient as an outpatient at their same destination approximately
        cfg.ANNUAL_VISIT_INTERVAL days from today. If that day's outpatient
        slots are full, schedule_outpatient() pushes to the next free day.

        The booked day is stored in p.next_visit_day for inspection /
        debugging. The patient object itself is placed in the future
        outpatient queue — so no separate pool management is needed.
        """
        # Each visit, extend the advance-schedule window by one year at the far end.
        # The near-end appointments (years 1 through ADVANCE_SCHEDULE_YEARS-1 from now)
        # were already pre-booked from the previous visit's rescheduling or warmup.
        # We only need to add the appointment at the new far horizon.
        far_day    = day + cfg.ADVANCE_SCHEDULE_YEARS * cfg.ANNUAL_VISIT_INTERVAL
        booked_far = self._queues.schedule_outpatient(p, p.destination, far_day)

        # next_visit_day tracks the NEXT actual visit (1 year from now), not the far booking
        p.next_visit_day = day + cfg.ANNUAL_VISIT_INTERVAL
        p.log(day, (
            f"ESTABLISHED — advance window extended to day {booked_far} "
            f"(year {booked_far // 365}); next visit ~day {p.next_visit_day}"
        ))

    def _spawn_replacement_entrants(self, day: int) -> None:
        """
        Create new established patients to replace those removed by mortality.

        Each replacement patient is added directly to _established_pool and
        scheduled as an outpatient for the near future (1–14 days ahead),
        maintaining the pool at exactly cfg.SIMULATED_POPULATION. This is
        what makes the simulation a stable open-loop model:

            Deaths exit the pool  →  replacements immediately fill the gap
            Pool size stays constant across all 70 simulated years

        The replacement patient has is_established=True so they participate
        in mortality sweeps and annual rescheduling going forward.

        Spawning is rate-limited to cfg.NEW_PATIENT_DAILY_RATE per day to
        spread replacements smoothly rather than adding all deaths at once
        on the mortality-sweep day (which would create unrealistic bursts).
        """
        if self._pending_new_entries <= 0:
            return

        # If organic new patients (from _generate_arrivals) have already filled
        # the pool to target, we don't need additional replacements this day.
        if len(self._established_pool) >= cfg.SIMULATED_POPULATION:
            self._pending_new_entries = 0
            return

        # Spread replacements: spawn up to NEW_PATIENT_DAILY_RATE per day
        # Cap so we never over-fill beyond SIMULATED_POPULATION
        headroom = cfg.SIMULATED_POPULATION - len(self._established_pool)
        to_spawn = min(self._pending_new_entries, cfg.NEW_PATIENT_DAILY_RATE, headroom)

        for _ in range(to_spawn):
            from population import _sample_established_destination
            dest = _sample_established_destination()
            p    = sample_patient(self._pid, day, dest, "outpatient")
            self._pid += 1

            # New replacement joins the established cycling pool
            p.is_established       = True
            p.age_at_entry         = p.age
            p.simulation_entry_day = day

            # Schedule their first visit in the next 1–14 days, then pre-book
            # ADVANCE_SCHEDULE_YEARS - 1 subsequent annual visits so the replacement
            # joins the multi-year advance schedule immediately.
            earliest  = day + random.randint(1, 14)
            booked    = self._queues.schedule_outpatient(p, dest, earliest)
            p.next_visit_day = booked

            for yr in range(1, cfg.ADVANCE_SCHEDULE_YEARS):
                self._queues.schedule_outpatient(
                    p, dest, booked + yr * cfg.ANNUAL_VISIT_INTERVAL
                )

            self._established_pool.append(p)

        self._pending_new_entries -= to_spawn

    def _flush_exited_patients(self, day: int = 0, force: bool = False) -> None:
        """
        Batch-write buffered exited patients to the SQLite database.

        Runs every cfg.DB_FLUSH_INTERVAL days (or immediately when force=True,
        e.g. at simulation end). Writes both the patient summary row and the
        full event log for each buffered patient, then clears the buffer.

        Writing in batches (rather than per patient) keeps SQLite overhead
        low — a single executemany call for the whole buffer costs only one
        transaction regardless of batch size.
        """
        if not self._flush_buffer:
            return
        if not force and (day % cfg.DB_FLUSH_INTERVAL != 0):
            return

        self._db.flush_patients(self._flush_buffer)
        self._db.flush_events(self._flush_buffer)
        self._flush_buffer.clear()

    # =========================================================================
    # Follow-up processing
    # =========================================================================

    def _run_followup(self, p: Patient, context: dict, day: int) -> None:
        """
        Process a follow-up appointment on its due day.
        Attempts to consume a procedure slot; if none available, re-queues
        for the next day.
        """
        if not p.active:
            return

        cancer = context["cancer"]
        step   = context["step"]

        if cancer == "cervical":
            self._cervical_followup_step(p, step, day)
        elif cancer == "lung":
            self._lung_biopsy_step(p, day)

        if p.exit_reason:
            record_exit(self.metrics, p.exit_reason)

    def _cervical_followup_step(
        self, p: Patient, step: str, day: int
    ) -> None:
        """
        Execute one cervical follow-up step and schedule the next if needed.

        Steps in order:
          colposcopy → (leep | cone_biopsy | surveillance)
        """
        if step == "colposcopy":
            if not self._queues.consume_slot("colposcopy", day):
                # No slot today — push to tomorrow
                self._queues.schedule_followup(
                    p, {"cancer": "cervical", "step": "colposcopy"}, day + 1
                )
                return

            self.metrics["wait_times"]["colposcopy"].append(day - p.day_created)
            cin = run_colposcopy(p, day, self.metrics)
            if cin is None:
                return

            disposition = run_treatment(p, day, self.metrics)

            if disposition not in ("exit", "surveillance") and p.treatment_type:
                ttype = p.treatment_type
                due   = day + cfg.FOLLOWUP_DELAY_DAYS.get(ttype, 14)
                self._queues.schedule_followup(
                    p, {"cancer": "cervical", "step": ttype}, due
                )

        elif step in ("leep", "cone_biopsy"):
            if not self._queues.consume_slot(step, day):
                self._queues.schedule_followup(
                    p, {"cancer": "cervical", "step": step}, day + 1
                )
                return
            self.metrics["wait_times"][step].append(day - p.day_created)
            # Treatment already recorded by run_treatment(); slot now consumed.

    def _lung_result_routing(self, p: Patient, day: int) -> None:
        """
        Run Lung-RADS routing on LDCT result day.
        For RADS 4 patients still active after routing, schedule biopsy.
        """
        run_lung_followup(p, day, self.metrics)

        if p.active and p.lung_result in ("RADS_4A", "RADS_4B_4X"):
            due = day + cfg.FOLLOWUP_DELAY_DAYS.get("lung_biopsy", 14)
            self._queues.schedule_followup(
                p, {"cancer": "lung", "step": "biopsy"}, due
            )

        if p.exit_reason:
            record_exit(self.metrics, p.exit_reason)

    def _lung_biopsy_step(self, p: Patient, day: int) -> None:
        """
        Process a lung biopsy appointment.
        If no slot available today, push to tomorrow.
        """
        if not self._queues.consume_slot("lung_biopsy", day):
            self._queues.schedule_followup(
                p, {"cancer": "lung", "step": "biopsy"}, day + 1
            )
            return
        self.metrics["wait_times"]["lung_biopsy"].append(day - p.day_created)

    # =========================================================================
    # Plot helpers
    # =========================================================================

    def _plot_cervical_funnel(self, ax) -> None:
        m = self.metrics
        total_abnormal = sum(
            v for k, v in m["cervical_results"].items()
            if k not in ("NORMAL", "HPV_NEGATIVE")
        )
        steps = [
            ("Eligible",        m["n_eligible_any"]),
            ("Screened",        m["n_screened"].get("cervical", 0)),
            ("Abnormal result", total_abnormal),
            ("Colposcopy",      m["n_colposcopy"]),
            ("Treated",         m["n_treated"]),
        ]
        _funnel_bar(ax, steps, "#4472C4", "Cervical Screening Funnel")

    def _plot_lung_funnel(self, ax) -> None:
        m = self.metrics
        steps = [
            ("Eligible",             m["lung_eligible"]),
            ("LDCT ordered",         m["lung_referral_placed"]),
            ("LDCT completed",       m["lung_ldct_completed"]),
            ("Results communicated", m["lung_result_communicated"]),
            ("Biopsy completed",     m["lung_biopsy_completed"]),
            ("Malignancy confirmed", m["lung_malignancy_confirmed"]),
            ("Treatment given",      m["lung_treatment_given"]),
        ]
        _funnel_bar(ax, steps, "#ED7D31", "Lung LDCT Pathway Funnel")

    def _plot_rads_distribution(self, ax) -> None:
        m      = self.metrics
        cats   = ["RADS_0", "RADS_1", "RADS_2", "RADS_3", "RADS_4A", "RADS_4B_4X"]
        counts = [m["lung_rads_distribution"].get(r, 0) for r in cats]
        colors = ["#A9D18E", "#70AD47", "#FFD966", "#F4B183", "#FF0000", "#C00000"]
        ax.bar(cats, counts, color=colors)
        ax.set_title("Lung-RADS Distribution (completed LDCTs)")
        ax.set_xlabel("Lung-RADS Category")
        ax.set_ylabel("Count")
        ax.tick_params(axis="x", rotation=30)

    def _plot_revenue(self, ax) -> None:
        r    = compute_revenue(self.metrics)
        vals = [r["realized_total"], r["foregone_total"]]
        bars = ax.bar(["Realized", "Foregone"], vals, color=["#4472C4", "#C00000"])
        ax.set_title("Revenue: Realized vs Foregone")
        ax.set_ylabel("USD")
        _max = max(vals) if any(vals) else 1
        for bar, val in zip(bars, vals):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + _max * 0.01,
                f"${val:,.0f}", ha="center", fontsize=10, fontweight="bold",
            )
        if sum(vals) > 0:
            ax.set_ylim(0, _max * 1.18)
            ax.set_xlabel(
                f"Revenue capture rate: {100 * vals[0] / sum(vals):.1f}%",
                fontsize=10,
            )

    def _require_run(self) -> None:
        if self.metrics is None:
            raise RuntimeError("Call run() first.")


# =============================================================================
# Shared plot utility
# =============================================================================

def _funnel_bar(ax, steps: list, color: str, title: str) -> None:
    labels = [s[0] for s in steps]
    values = [s[1] for s in steps]
    bars   = ax.barh(labels[::-1], values[::-1], color=color)
    ax.set_title(title)
    ax.set_xlabel("Patients")
    _max = max(values) if any(values) else 1
    for bar, val in zip(bars, values[::-1]):
        ax.text(
            bar.get_width() + _max * 0.01,
            bar.get_y() + bar.get_height() / 2,
            f"{val:,}", va="center", fontsize=9,
        )
