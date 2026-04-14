# =============================================================================
# runner.py
# SimulationRunner — day-by-day discrete event simulation.
# =============================================================================
#
# The clock is an integer day counter (0 … n_days-1).  Each tick processes
# one full day: new arrivals, provider visits, procedure slot allocation,
# and any follow-up appointments that fall due.
#
# Provider routing
# ----------------
# Patients are distributed to providers proportionally based on config
# probabilities.  Outpatients (PCP / GYN / Specialist) are scheduled;
# drop-ins (ER) are walk-ins.  There is no provider-level capacity
# constraint — the distribution is proportional, not a queue.
#
# Procedure slot queueing
# -----------------------
# The real capacity bottleneck lives at procedure slots (consume_slot).
# Primary screenings (cytology, HPV, LDCT) are served with priority:
# outpatients (PCP/GYN/Specialist) before drop-ins (ER).
# When a slot overflows, the patient is rescheduled to the next workday.
#
# Secondary screenings (colposcopy, biopsy) and treatment (LEEP, cone)
# follow FIFO logic — only patients with abnormal primary results enter.
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
import time
from collections import defaultdict
from typing import Optional

import config as cfg
from patient import Patient
from population import (
    sample_patient,
    generate_established_population,
    draw_death_day,
    draw_attrition_day,
    draw_cessation_day,
    draw_hpv_clearance_day,
)
from screening import (
    get_eligible_screenings,
    is_eligible_cervical,
    is_eligible_lung,
    is_due_for_screening,
    assign_screening_test,
    run_screening_step,
    days_until_eligible,
    draw_cervical_result,
    draw_lung_rads_result,
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


# =============================================================================
# Patient Queue Manager
# =============================================================================

class PatientQueues:
    """
    Day-by-day queue manager for patient visits and procedure slots.

    Provider routing is proportional (no provider-level capacity constraint).
    The real bottleneck lives at procedure slots (consume_slot), where
    primary screenings get priority for scheduled patients over ER walk-ins.

    Attributes
    ----------
    outpatient[provider][day] : list[Patient]
        Patients with a confirmed appointment on that day.

    dropin[provider] : list[Patient]
        Today's walk-ins (ER).

    followup[day] : list[(Patient, dict)]
        Follow-up appointments due on a specific future day.
        dict carries the clinical context: {"cancer": ..., "step": ...}

    daily_slots[day][procedure] : int
        Remaining procedure slots available on a future day.
        Initialised lazily from cfg.CAPACITIES.
    """

    def __init__(self, warmup_day: int = 0):
        self.outpatient   = defaultdict(lambda: defaultdict(list))
        self.dropin       = defaultdict(list)
        self.followup     = defaultdict(list)
        self.daily_slots  = defaultdict(dict)
        self._warmup_day  = warmup_day
        # Procedure utilization counters (post-warmup only, for statistical summary)
        self.procedure_used      = {}   # procedure → total slots consumed
        self.procedure_overflow  = {}   # procedure → total slot overflows (capacity full)
        # Life events: day → [(patient, event_type), ...]
        # Event types: "mortality", "attrition", "smoking_cessation", "hpv_clearance"
        self.life_events  = defaultdict(list)

    # ── Outpatient scheduling ─────────────────────────────────────────────────

    def schedule_outpatient(
        self, p: Patient, provider: str, earliest_day: int
    ) -> int:
        """
        Book a patient for a visit on earliest_day and return that day.

        Provider distribution is proportional to config caps — there is no
        provider-level capacity constraint.  The real bottleneck lives at
        procedure slots (consume_slot).
        """
        self.outpatient[provider][earliest_day].append(p)
        return earliest_day

    # ── Drop-in management ────────────────────────────────────────────────────

    def add_dropin(self, p: Patient, provider: str) -> None:
        """Add patient to a provider's walk-in queue."""
        self.dropin[provider].append(p)

    # ── Daily queue processing ────────────────────────────────────────────────

    def process_day(self, provider: str, day: int):
        """
        Return all patients scheduled or walking in for this provider today.

        Provider distribution is proportional — there is no provider-level
        capacity constraint, so there is never overflow.  Outpatients are
        returned before drop-ins so that downstream procedure-slot logic
        can prioritise scheduled patients over ER walk-ins.

        Returns
        -------
        seen : list[Patient] — all patients to be processed today
        """
        seen_outpts  = self.outpatient[provider].pop(day, [])
        today_dropins = self.dropin.pop(provider, [])
        return seen_outpts + today_dropins

    # ── Follow-up scheduling ──────────────────────────────────────────────────

    def schedule_followup(
        self, p: Patient, context: dict, due_day: int
    ) -> None:
        """Queue a follow-up appointment for a specific future day."""
        self.followup[due_day].append((p, context))

    def get_due_followups(self, day: int):
        """Return (and clear) all follow-ups due today."""
        return self.followup.pop(day, [])

    # ── Life event scheduling ────────────────────────────────────────────────

    def schedule_life_event(self, p: "Patient", event_type: str, day: int) -> None:
        """Schedule an independent life event (mortality, attrition, etc.)."""
        self.life_events[day].append((p, event_type))

    def get_due_life_events(self, day: int):
        """Return (and clear) all life events due today."""
        return self.life_events.pop(day, [])

    def procedure_queue_depth(self, from_day: int) -> dict:
        """
        Count how many patients are waiting for each procedure type
        in the followup queue (from_day onward).
        Returns {procedure_step: count}.
        """
        depth = defaultdict(int)
        for day, entries in self.followup.items():
            if day >= from_day:
                for _patient, context in entries:
                    step = context.get("step", "unknown")
                    depth[step] += 1
        return dict(depth)

    def screening_queue_depth(self, from_day: int) -> int:
        """
        Count patients waiting specifically for primary screening slots
        (screening_retry followups from from_day onward).
        """
        count = 0
        for day, entries in self.followup.items():
            if day >= from_day:
                for _patient, context in entries:
                    if context.get("step") == "screening_retry":
                        count += 1
        return count

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
            # Track procedure utilization (post-warmup only)
            if day >= self._warmup_day:
                self.procedure_used[procedure] = self.procedure_used.get(procedure, 0) + 1
            return True
        # Track overflow (capacity full, post-warmup only)
        if day >= self._warmup_day:
            self.procedure_overflow[procedure] = self.procedure_overflow.get(procedure, 0) + 1
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
        Seeds cfg.INITIAL_POOL_SIZE established patients who cycle through
        the provider system annually. New patients arrive via multi-source
        ARRIVAL_SOURCES and join the cycling pool after their first visit.
        Patients exit via mortality, attrition (EXIT_SOURCES), LTFU, or
        ineligibility. The pool size is emergent — NOT maintained at a
        fixed target. Exited patients are batch-flushed to SQLite.

        Life events (death, attrition, smoking cessation, HPV clearance)
        are drawn once at patient entry and scheduled as independent
        events in the priority queue — they fire on their scheduled day
        regardless of visit activity.

        The initial cohort is spread across cfg.WARMUP_DAYS so the
        system reaches steady state quickly.

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

        # Population state — pool grows/shrinks organically
        self._established_pool:          list = []   # all living established patients
        self._flush_buffer:              list = []   # exited patients awaiting DB write
        self._pool_removals:             set  = set()  # ids of patients to purge from pool

        # Daily screening demand counters (reset each workday)
        self._day_screening_demand:  int = 0   # attempted
        self._day_screening_supply:  int = 0   # slot consumed
        self._day_screening_overflow:  int = 0   # overflow (no slot)
        # Secondary (colposcopy, lung_biopsy) and treatment (leep, cone_biopsy)
        self._day_secondary_demand:  int = 0
        self._day_secondary_supply:  int = 0
        self._day_secondary_overflow:  int = 0
        self._day_treatment_demand:  int = 0
        self._day_treatment_supply:  int = 0
        self._day_treatment_overflow:  int = 0
        self._db:                        Optional[SimulationDB] = None

        # Warmup cutoff: metrics recorded before this day are excluded from
        # analysis.  Wait times, demand/supply/overflow tuples, and screening
        # counters are only recorded for day >= _warmup_day.
        self._warmup_day: int = cfg.WARMUP_YEARS * cfg.DAYS_PER_YEAR

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
        self._queues = PatientQueues(warmup_day=self._warmup_day)
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

        self._run_start = time.time()
        self._last_progress = 0
        for day in range(self.n_days):
            self._tick(day)
            # Progress clock — print every 5 simulated years
            year = day // 365
            if year > self._last_progress and year % 5 == 0:
                elapsed = time.time() - self._run_start
                pool = len(self._established_pool) if self.use_stable_population else 0
                print(f"  Year {year:>3}/{self.n_days // 365}  |  "
                      f"pool {pool:>6,}  |  "
                      f"mortality {self.metrics.get('mortality_count', 0):>5,}  |  "
                      f"{elapsed:>6.1f}s elapsed")
                self._last_progress = year

        # Final year checkpoint (captures year-70 stats at end of loop)
        if self.metrics.get("year_checkpoints") is not None:
            last_year = self.n_days // 365
            day_final = self.n_days
            due_cerv_f = due_lung_f = 0
            if self.use_stable_population:
                for p in self._established_pool:
                    if p.active:
                        if is_eligible_cervical(p) and is_due_for_screening(p, "cervical", day_final):
                            due_cerv_f += 1
                        if is_eligible_lung(p) and is_due_for_screening(p, "lung", day_final):
                            due_lung_f += 1
            self.metrics["year_checkpoints"].append({
                "year":                last_year,
                "day":                 day_final,
                "pool_size":           len(self._established_pool) if self.use_stable_population else None,
                "due_cervical":        due_cerv_f,
                "due_lung":            due_lung_f,
                "cum_cervical":        self.metrics["n_screened"]["cervical"],
                "cum_cervical_est":    self.metrics["n_screened_established"]["cervical"],
                "cum_lung":            self.metrics["n_screened"]["lung"],
                "cum_lung_est":        self.metrics["n_screened_established"]["lung"],
                "cum_cytology":        self.metrics["n_screened_by_test"]["cytology"],
                "cum_hpv_alone":       self.metrics["n_screened_by_test"]["hpv_alone"],
                "cum_ldct":            self.metrics["n_screened_by_test"]["ldct"],
                "cum_colposcopy":      self.metrics["n_colposcopy"],
                "cum_leep":            self.metrics["n_treatment"].get("leep", 0),
                "cum_treated":         self.metrics["n_treated"],
                "cum_ltfu":            self.metrics["n_ltfu"],
                "cum_ltfu_queue_primary":   self.metrics["ltfu_queue_primary"],
                "cum_ltfu_queue_secondary": self.metrics["ltfu_queue_secondary"],
                "cum_ltfu_queue_treatment": self.metrics["ltfu_queue_treatment"],
                "cum_ltfu_unscreened":      self.metrics["ltfu_unscreened"],
                "cum_cervical_eligible":    self.metrics["n_eligible"].get("cervical", 0),
                "cum_lung_eligible":        self.metrics.get("lung_eligible", 0),
                "cum_lung_referral_placed": self.metrics.get("lung_referral_placed", 0),
                "cum_lung_biopsy_referral": self.metrics.get("lung_biopsy_referral", 0),
                "cum_lung_biopsy":     self.metrics["lung_biopsy_completed"],
                "cum_lung_treatment":  self.metrics["lung_treatment_given"],
                "cum_mortality":       self.metrics.get("mortality_count", 0),
                "cum_n_patients":      self.metrics["n_patients"],
                "cum_exited":          self.metrics["n_exited"],
                "cum_exits_by_reason": dict(self.metrics["exits_by_reason"]),
                "cum_arrivals":        sum(self.metrics["arrivals_by_source"].values()),
            })

        # Expose queue/procedure utilization in metrics for stats table
        self.metrics["procedure_used"]     = dict(self._queues.procedure_used)
        self.metrics["procedure_overflow"] = dict(self._queues.procedure_overflow)

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
        exits_by_src = self.metrics.get("exits_by_source", {})
        if exits_by_src:
            print("  Exit sources (detailed):")
            for src, count in sorted(exits_by_src.items(), key=lambda x: -x[1]):
                print(f"    {src:<25} {count:,}")
        arrivals_by_src = self.metrics.get("arrivals_by_source", {})
        if arrivals_by_src:
            print("  Arrival sources:")
            for src, count in sorted(arrivals_by_src.items(), key=lambda x: -x[1]):
                print(f"    {src:<25} {count:,}")
        print()

    def plot_pool_stability(self) -> None:
        """
        Line chart showing the established-patient pool size over time.

        In the flow model the pool size is emergent — it grows from the
        initial seed as organic arrivals join, and shrinks as patients exit
        via mortality, attrition, LTFU, or ineligibility. The chart shows
        the natural equilibrium the system reaches.
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
            cfg.INITIAL_POOL_SIZE, color="gray", linestyle="--",
            linewidth=0.8, alpha=0.5, label=f"Initial seed ({cfg.INITIAL_POOL_SIZE:,})"
        )
        ax.set_title("Established-Patient Pool Size Over Time")
        ax.set_xlabel("Simulation Day")
        ax.set_ylabel("Pool Size")
        ax.legend()
        plt.tight_layout()
        plt.show()

    # =========================================================================
    # Daily tick
    # =========================================================================

    def _tick(self, day: int) -> None:
        """
        Process one simulation day in order:

          Background (runs every day when use_stable_population=True):
            0a. Process life events due today (mortality, attrition,
                smoking cessation, HPV clearance) — all pre-scheduled.
            0b. Flush exited patients to SQLite.

          Clinical steps (always run, skipped on weekends):
            1. Process follow-up appointments due today.
            2. Generate new patient arrivals → route to queues.
            3. For each provider: collect all patients, screen them.

        Weekends (day % 7 in {5, 6}) are skipped when cfg.SKIP_WEEKENDS is True.
        Day 0 is treated as Monday.
        """
        # ── Weekend skip ──────────────────────────────────────────────────────
        # Only the clinical steps (arrivals + provider queues) are suppressed.
        # Life events and DB flush still run on weekends (death doesn't wait).
        is_weekend = cfg.SKIP_WEEKENDS and (day % 7 >= 5)  # 5=Saturday, 6=Sunday

        if self.use_stable_population:
            # Process life events due today
            self._process_life_events(day)

            # Flush exited patients to SQLite in batches
            self._flush_exited_patients(day=day)

        # Annual checkpoint — record cumulative stats at each year boundary
        if day > 0 and day % 365 == 0:
            year = day // 365

            # Count established patients who are *due* for screening today.
            # This gives the correct denominator for uptake rate charts
            # (total eligible ≠ due, because screening intervals are 3-5 yrs).
            due_cerv = due_lung = 0
            if self.use_stable_population:
                for p in self._established_pool:
                    if p.active:
                        if is_eligible_cervical(p) and is_due_for_screening(p, "cervical", day):
                            due_cerv += 1
                        if is_eligible_lung(p) and is_due_for_screening(p, "lung", day):
                            due_lung += 1

            self.metrics["year_checkpoints"].append({
                "year":                year,
                "day":                 day,
                "pool_size":           len(self._established_pool) if self.use_stable_population else None,
                "due_cervical":        due_cerv,
                "due_lung":            due_lung,
                # Screening volumes — overall and by first-stage test modality
                "cum_cervical":        self.metrics["n_screened"]["cervical"],
                "cum_cervical_est":    self.metrics["n_screened_established"]["cervical"],
                "cum_lung":            self.metrics["n_screened"]["lung"],
                "cum_lung_est":        self.metrics["n_screened_established"]["lung"],
                "cum_cytology":        self.metrics["n_screened_by_test"]["cytology"],
                "cum_hpv_alone":       self.metrics["n_screened_by_test"]["hpv_alone"],
                "cum_ldct":            self.metrics["n_screened_by_test"]["ldct"],
                # Follow-up / treatment
                "cum_colposcopy":      self.metrics["n_colposcopy"],
                "cum_leep":            self.metrics["n_treatment"].get("leep", 0),
                "cum_treated":         self.metrics["n_treated"],
                "cum_ltfu":            self.metrics["n_ltfu"],
                # LTFU by queue stage
                "cum_ltfu_queue_primary":   self.metrics["ltfu_queue_primary"],
                "cum_ltfu_queue_secondary": self.metrics["ltfu_queue_secondary"],
                "cum_ltfu_queue_treatment": self.metrics["ltfu_queue_treatment"],
                "cum_ltfu_unscreened":      self.metrics["ltfu_unscreened"],
                # Eligibility denominators
                "cum_cervical_eligible":    self.metrics["n_eligible"].get("cervical", 0),
                "cum_lung_eligible":        self.metrics.get("lung_eligible", 0),
                "cum_lung_referral_placed": self.metrics.get("lung_referral_placed", 0),
                "cum_lung_biopsy_referral": self.metrics.get("lung_biopsy_referral", 0),
                # Lung pathway milestones
                "cum_lung_biopsy":     self.metrics["lung_biopsy_completed"],
                "cum_lung_treatment":  self.metrics["lung_treatment_given"],
                # Population exits
                "cum_mortality":       self.metrics.get("mortality_count", 0),
                "cum_n_patients":      self.metrics["n_patients"],
                "cum_exited":          self.metrics["n_exited"],
                "cum_exits_by_reason": dict(self.metrics["exits_by_reason"]),
                "cum_arrivals":        sum(self.metrics["arrivals_by_source"].values()),
                # Procedure queue snapshot
                "procedure_queue_depth": self._queues.procedure_queue_depth(day),
                "screening_queue_depth": self._queues.screening_queue_depth(day),
                "procedure_overflow":    dict(self._queues.procedure_overflow),
            })

        # Clinical steps — skipped on weekends (no hospital screenings/appointments)
        if not is_weekend:
            # 1. Follow-ups due today
            for p, context in self._queues.get_due_followups(day):
                self._run_followup(p, context, day)

            # 2. New arrivals
            self._generate_arrivals(day)

            # 3. Provider queues — proportional split, no provider-level
            #    capacity constraint.  Real bottleneck is at procedure slots.
            #    Outpatients (PCP/GYN/Specialist) are processed BEFORE
            #    drop-ins (ER) so they get first priority on primary
            #    screening slots.
            for provider in _NON_ER + ["er"]:
                seen = self._queues.process_day(provider, day)

                for p in seen:
                    p.wait_days = 0  # reset — no queue delay until overflow

                    # Provider → screening delay: screening appointment is
                    # scheduled cfg.TURNAROUND_DAYS["provider_to_screening"]
                    # days after the provider visit.
                    delay = cfg.TURNAROUND_DAYS.get("provider_to_screening", 0)
                    if delay > 0:
                        self._queues.schedule_followup(
                            p, {"cancer": "_all", "step": "provider_screening",
                                 "referral_day": day}, day + delay
                        )
                    else:
                        self._screen_patient(p, day)

            # Flush daily demand counters (post-warmup only)
            if day >= self._warmup_day:
                if self._day_screening_demand > 0 or self._day_screening_supply > 0:
                    self.metrics["daily_screening_demand"].append(
                        (self._day_screening_demand,
                         self._day_screening_supply,
                         self._day_screening_overflow)
                    )
                if self._day_secondary_demand > 0 or self._day_secondary_supply > 0:
                    self.metrics["daily_secondary_demand"].append(
                        (self._day_secondary_demand,
                         self._day_secondary_supply,
                         self._day_secondary_overflow)
                    )
                if self._day_treatment_demand > 0 or self._day_treatment_supply > 0:
                    self.metrics["daily_treatment_demand"].append(
                        (self._day_treatment_demand,
                         self._day_treatment_supply,
                         self._day_treatment_overflow)
                    )
            self._day_screening_demand = self._day_screening_supply = self._day_screening_overflow = 0
            self._day_secondary_demand = self._day_secondary_supply = self._day_secondary_overflow = 0
            self._day_treatment_demand = self._day_treatment_supply = self._day_treatment_overflow = 0

    # =========================================================================
    # Arrivals
    # =========================================================================

    def _generate_arrivals(self, day: int) -> None:
        """
        Create today's new patients and route them to provider queues.

        In stable-population mode, arrivals come from distinct sources
        defined in cfg.ARRIVAL_SOURCES — each with its own daily rate,
        age range, and routing.  In standard mode, a single Poisson
        process at self.daily_rate generates arrivals with mixed routing.
        """
        _op_dest_keys = list(cfg.DESTINATION_PROBS_OUTPATIENT.keys())
        _op_dest_w    = list(cfg.DESTINATION_PROBS_OUTPATIENT.values())

        if self.use_stable_population and hasattr(cfg, 'ARRIVAL_SOURCES'):
            # ── Multi-source arrivals ─────────────────────────────────────
            for source_name, source_cfg in cfg.ARRIVAL_SOURCES.items():
                n = _poisson(source_cfg["daily_rate"])
                age_range = source_cfg.get("age_range")
                routing   = source_cfg.get("routing", "outpatient")

                for _ in range(n):
                    if routing == "er":
                        dest  = "er"
                        ptype = "drop_in"
                        p = sample_patient(self._pid, day, dest, ptype,
                                           age_range=age_range)
                        self._pid += 1
                        self._queues.add_dropin(p, "er")
                    else:
                        dest  = random.choices(_op_dest_keys, weights=_op_dest_w)[0]
                        ptype = "outpatient"
                        p = sample_patient(self._pid, day, dest, ptype,
                                           age_range=age_range)
                        self._pid += 1
                        lo, hi   = cfg.OUTPATIENT_LEAD_DAYS.get(dest, (1, 7))
                        earliest = day + random.randint(lo, hi)
                        self._queues.schedule_outpatient(p, dest, earliest)

                    self.metrics["arrivals_by_source"][source_name] += 1
        else:
            # ── Standard mode: single Poisson process ─────────────────────
            _arrival_keys = list(cfg.ARRIVAL_TYPE_PROBS.keys())
            _arrival_w    = list(cfg.ARRIVAL_TYPE_PROBS.values())

            for _ in range(_poisson(self.daily_rate)):
                arrival = random.choices(_arrival_keys, weights=_arrival_w)[0]

                if arrival == "er":
                    dest  = "er"
                    ptype = "drop_in"
                    p = sample_patient(self._pid, day, dest, ptype)
                    self._pid += 1
                    self._queues.add_dropin(p, "er")
                else:
                    dest  = random.choices(_op_dest_keys, weights=_op_dest_w)[0]
                    ptype = "outpatient"
                    p = sample_patient(self._pid, day, dest, ptype)
                    self._pid += 1
                    lo, hi   = cfg.OUTPATIENT_LEAD_DAYS.get(dest, (1, 7))
                    earliest = day + random.randint(lo, hi)
                    self._queues.schedule_outpatient(p, dest, earliest)

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
        # Guard: patient may have died or exited between being scheduled and today.
        if not p.active:
            return

        # Update age (arithmetic — not a draw)
        if p.is_established:
            p.age = p.age_at_entry + (day - p.simulation_entry_day) // 365

        eligible = get_eligible_screenings(p)
        _post_warmup = day >= self._warmup_day
        if _post_warmup:
            self.metrics["n_patients"] += 1
            self.metrics["entries_by_destination"][p.destination] += 1
            self.metrics["entries_by_type"][p.patient_type] += 1

        # Track total provider contacts for longitudinal analysis
        if self.use_stable_population:
            p.visit_count += 1

        if not eligible:
            if _post_warmup:
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
                if p.is_established:
                    self._queues.schedule_outpatient(p, p.destination, day + soonest)
                    if _post_warmup:
                        self.metrics["n_reschedule"] += 1
                    p.log(day, f"NOT YET ELIGIBLE — return visit scheduled in {soonest} days")
                else:
                    # Non-established patients (organic arrivals, ER drop-ins) who
                    # are not yet eligible should NOT be rescheduled.  For ER patients
                    # in particular, schedule_outpatient would route them back to the
                    # drop-in queue immediately (ER has 0 outpatient capacity), creating
                    # an infinite daily loop that inflates ER visit counts.  These
                    # patients complete their one-time visit and exit; they may return
                    # naturally as a future organic arrival.
                    p.exit_system(day, "ineligible")
                    record_exit(self.metrics, "ineligible", patient=p, current_day=day)
                    p.log(day, f"NOT YET ELIGIBLE — non-established, exiting (soonest={soonest}d)")
                return
            elif p.is_established:
                # Permanently ineligible established patients (aged out of all
                # screenings, no cervix, quit window closed) exit the pool.
                p.log(day, "INELIGIBLE for all cancers — exiting pool")
                p.exit_system(day, "ineligible")
                record_exit(self.metrics, "ineligible", patient=p, current_day=day)
                self._flush_buffer.append(p)
            else:
                # One-time / new patient permanently ineligible → exit silently;
                # no LTFU revenue impact because no screening was warranted.
                p.exit_system(day, "ineligible")
                record_exit(self.metrics, "ineligible", patient=p, current_day=day)
            return

        if _post_warmup:
            self.metrics["n_eligible_any"] += 1
            for c in eligible:
                self.metrics["n_eligible"][c] += 1

        for cancer in eligible:
            if not p.active:
                break

            # Check if patient is actually due before consuming a slot.
            # (Eligible ≠ due — cervical intervals are 3-5 years.)
            if not is_due_for_screening(p, cancer, day):
                continue

            # Assign test modality, then check procedure slot availability.
            # If no slot today, reschedule for tomorrow rather than skipping.
            # Primary screenings compete for slots — PCP/GYN/Specialist are
            # processed before ER, so scheduled patients get priority.
            test = assign_screening_test(p, cancer)

            # Reschedule check: patient may not be able to make it today
            # (10% PLACEHOLDER). If so, re-enter queue for next day.
            if random.random() < cfg.LTFU_PROBS.get("reschedule_primary", 0.10):
                orig_ref = day - p.wait_days if p.wait_days > 0 else day
                self._queues.schedule_followup(
                    p, {"cancer": cancer, "step": "screening_retry",
                         "referral_day": orig_ref}, day + 1
                )
                p.log(day, f"RESCHEDULE {test} — patient unavailable, moved to day {day + 1}")
                continue

            self._day_screening_demand += 1
            if not self._queues.consume_slot(test, day):
                self._day_screening_overflow += 1
                orig_ref = day - p.wait_days if p.wait_days > 0 else day
                self._queues.schedule_followup(
                    p, {"cancer": cancer, "step": "screening_retry",
                         "referral_day": orig_ref}, day + 1
                )
                p.log(day, f"NO SLOT for {test} — rescheduled to day {day + 1}")
                continue

            self._day_screening_supply += 1
            result = run_screening_step(p, cancer, day, self.metrics,
                                        test_override=test)

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
                    if p.is_established:
                        # Established patients stay in the pool even after a
                        # pathway LTFU event (e.g. lung referral not placed).
                        # Do NOT call record_exit — that counts system-level
                        # exits and would inflate totals since the same patient
                        # can hit pathway LTFU multiple times over 70 years.
                        # The LTFU is counted by record_exit when it fires.
                        p.active      = True
                        p.exit_reason = None
                        p.exit_day    = None
                        # Reschedule BEFORE returning so the annual cycle continues
                        self._reschedule_established(p, day)
                    else:
                        record_exit(self.metrics, p.exit_reason, patient=p, current_day=day)
                        return   # non-established exits permanently
                    return       # LTFU handled — skip remaining cancers today
                # Case B: no exit_reason → simple skip; continue to next cancer
                continue

            record_screening(self.metrics, p, cancer, result, current_day=day)
            # Wait = days queued for a procedure slot (0 if got slot on first attempt).
            if p.wait_days > 0 and day >= self._warmup_day:
                self.metrics["wait_times"][test].append(p.wait_days)

            # ── Schedule result routing after lab turnaround delay ────────────
            # Results are not available instantly — the lab/radiology processing
            # time from cfg.TURNAROUND_DAYS must elapse before the patient is
            # notified and follow-up actions are scheduled.
            if cancer == "cervical":
                turnaround = cfg.TURNAROUND_DAYS.get(test, 7)
                self._queues.schedule_followup(
                    p, {"cancer": "cervical", "step": "result_routing",
                         "result": result, "referral_day": day},
                    day + turnaround
                )

            elif cancer == "lung":
                turnaround = cfg.TURNAROUND_DAYS.get("ldct_notification", 5)
                self._queues.schedule_followup(
                    p, {"cancer": "lung", "step": "result_routing",
                         "referral_day": day},
                    day + turnaround
                )

        # ── Reschedule established patients for next annual visit ──────────────
        # Runs after all cancer loops complete (whether or not screening occurred).
        # Established patients who went LTFU for a cancer pathway were re-activated
        # above; non-established patients who exited mid-loop will not reach here.
        if self.use_stable_population and p.is_established and p.active:
            self._reschedule_established(p, day)

        # ── Convert first-time visitors into established patients ─────────────
        # Organic new patients (from _generate_arrivals) arrive as drop-ins with
        # is_established=False. After their first successful visit, they join the
        # established cycling pool so they become regular annual patients.
        # No pool cap — the pool size is emergent (arrivals vs. exits).
        # Patients keep their Census-drawn age (no re-stamping).
        elif (
            self.use_stable_population
            and not p.is_established
            and p.active
        ):
            p.age_at_entry         = p.age
            p.is_established       = True
            p.simulation_entry_day = day

            # ER patients who join the established pool must be reassigned
            # to a non-ER regular provider — ER is for unplanned visits only.
            if p.destination == "er":
                from population import _sample_established_destination
                p.destination  = _sample_established_destination()
                p.patient_type = "outpatient"

            self._established_pool.append(p)

            # Schedule independent life events for the new established patient
            self._schedule_life_events(p, entry_day=day)

            # Book the full ADVANCE_SCHEDULE_YEARS window from scratch.
            first_visit   = day + cfg.ANNUAL_VISIT_INTERVAL
            first_booked  = self._queues.schedule_outpatient(p, p.destination, first_visit)
            p.next_visit_day = first_booked
            for yr in range(1, cfg.ADVANCE_SCHEDULE_YEARS):
                self._queues.schedule_outpatient(
                    p, p.destination, first_booked + yr * cfg.ANNUAL_VISIT_INTERVAL
                )
            p.log(
                day,
                f"FIRST VISIT COMPLETE — joined established pool (age {p.age}); "
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

        The pool size after seeding is NOT maintained — it grows or shrinks
        organically as arrivals join and patients exit.
        """
        pool = generate_established_population(
            n         = cfg.INITIAL_POOL_SIZE,
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

            # Schedule independent life events (mortality, attrition, etc.)
            self._schedule_life_events(p, entry_day=0)

        self._established_pool = pool

        print(
            f"[INIT] Stable population: {len(pool):,} established patients "
            f"scheduled across {warmup} warmup days."
        )

    def _process_life_events(self, day: int) -> None:
        """
        Process all life events scheduled for today.

        Life events are pre-drawn at patient entry and fire independently of
        visits.  Each event type is handled here; exited patients are collected
        in _pool_removals and batch-purged from the pool periodically (every
        30 days) to avoid O(n) list.remove() on every single event.
        """
        for p, event_type in self._queues.get_due_life_events(day):
            if not p.active:
                continue  # already exited via an earlier event

            # Update age to current day (arithmetic, not a draw)
            p.age = p.age_at_entry + (day - p.simulation_entry_day) // 365

            if event_type == "mortality":
                p.exit_system(day, "mortality")
                record_exit(self.metrics, "mortality", patient=p, current_day=day)
                if day >= self._warmup_day:
                    self.metrics["mortality_count"] = self.metrics.get("mortality_count", 0) + 1
                    self.metrics["exits_by_source"]["mortality"] += 1
                self._flush_buffer.append(p)
                self._pool_removals.add(id(p))

            elif event_type == "attrition":
                subtype = p.attrition_subtype or "attrition"
                p.exit_system(day, f"attrition:{subtype}")
                record_exit(self.metrics, "attrition", patient=p, current_day=day)
                if day >= self._warmup_day:
                    self.metrics["exits_by_source"][subtype] += 1
                self._flush_buffer.append(p)
                self._pool_removals.add(id(p))

            elif event_type == "smoking_cessation":
                if p.smoker:  # guard — may have exited or already quit
                    # Accumulate pack-years up to quit day
                    years_smoked = (day - p.simulation_entry_day) / 365.0
                    p.pack_years += years_smoked
                    p.smoker           = False
                    p.years_since_quit = 0.0

            elif event_type == "hpv_clearance":
                if p.hpv_positive:
                    p.hpv_positive = False

        # Batch-purge exited patients from the pool every 30 days
        # (single O(n) filter pass instead of per-event O(n) remove)
        if day % 30 == 0 and self._pool_removals:
            self._established_pool = [
                p for p in self._established_pool
                if id(p) not in self._pool_removals
            ]
            self._pool_removals.clear()

        # Pool-size snapshot (sample every 30 days to keep metrics lean)
        if day % 30 == 0:
            self.metrics["pool_size_snapshot"].append(
                (day, len(self._established_pool))
            )

    def _schedule_life_events(self, p: "Patient", entry_day: int) -> None:
        """
        Draw and schedule all independent life events for a patient at entry.

        Called once per patient — at population init for established patients,
        and at organic conversion for new arrivals.
        """
        n_days = self.n_days  # don't schedule beyond simulation horizon

        # Mortality — Gompertz draw
        death_day = draw_death_day(p, entry_day)
        p.scheduled_death_day = death_day
        if death_day < n_days:
            self._queues.schedule_life_event(p, "mortality", death_day)

        # Attrition — competing-risks draw across EXIT_SOURCES
        att_day, att_subtype = draw_attrition_day(entry_day)
        p.scheduled_attrition_day = att_day
        p.attrition_subtype       = att_subtype
        if att_day < n_days and att_day < death_day:  # no attrition after death
            self._queues.schedule_life_event(p, "attrition", att_day)

        # Smoking cessation (only for current smokers)
        if p.smoker:
            cess_day = draw_cessation_day(entry_day)
            p.scheduled_cessation_day = cess_day
            if cess_day < n_days and cess_day < min(death_day, att_day):
                self._queues.schedule_life_event(p, "smoking_cessation", cess_day)

        # HPV clearance (only for HPV-positive patients)
        if p.hpv_positive:
            clear_day = draw_hpv_clearance_day(entry_day)
            p.scheduled_hpv_clear_day = clear_day
            if clear_day < n_days and clear_day < min(death_day, att_day):
                self._queues.schedule_life_event(p, "hpv_clearance", clear_day)

    def _reschedule_established(self, p: Patient, day: int) -> None:
        """
        Book an established patient's next annual visit.

        Called at the end of each visit (from _screen_patient). Schedules
        the patient as an outpatient at their same destination approximately
        cfg.ANNUAL_VISIT_INTERVAL days from today.
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
            # Record abandoned wait time for patients who died/attrited in queue
            step = context.get("step", "")
            referral_day = context.get("referral_day", day)
            wait = day - referral_day
            if day >= self._warmup_day:
                if step == "screening_retry" and wait > 0:
                    cancer = context.get("cancer", "unknown")
                    test = assign_screening_test(p, cancer) if cancer != "unknown" else "unknown"
                    self.metrics["wait_times_abandoned"][test].append(wait)
                elif step in ("leep", "cone_biopsy", "colposcopy", "lung_biopsy") and wait > 0:
                    self.metrics["wait_times_abandoned"][step].append(wait)
            return

        cancer = context["cancer"]
        step   = context["step"]

        if step == "provider_screening":
            # Provider → screening delay has elapsed. Run screening now.
            self._screen_patient(p, day)
            return

        if step == "screening_retry":
            referral = context.get("referral_day", day)
            # Queue LTFU: daily hazard — patient may abandon while waiting
            cancer_q = context.get("cancer", "unknown")
            test_q = assign_screening_test(p, cancer_q) if cancer_q != "unknown" else "unknown"
            if self._check_queue_ltfu(p, day, referral, "primary", test_q):
                return

            # Retry a primary screening that overflowed (no slot) yesterday.
            p.wait_days = day - referral
            self._screen_patient(p, day)
            return

        if step == "result_routing":
            # Delayed result notification — lab turnaround has elapsed.
            # Now route the result to the appropriate follow-up pathway.
            if cancer == "cervical":
                result = context.get("result", p.cervical_result)
                self._route_cervical_followup(p, result, day)
            elif cancer == "lung":
                self._lung_result_routing(p, day)
            return

        referral_day = context.get("referral_day", day)

        if cancer == "cervical":
            self._cervical_followup_step(p, step, day, referral_day, context)
        elif cancer == "lung":
            if step == "biopsy":
                self._lung_biopsy_step(p, day, referral_day)
            elif step == "repeat_ldct":
                scheduled_delay = context.get("scheduled_delay", 0)
                self._lung_repeat_ldct_step(p, day, referral_day, scheduled_delay)
            elif step == "lung_post_treatment_surveillance":
                treatment_day = context.get("treatment_day", referral_day)
                visit_number  = context.get("visit_number", 0)
                self._lung_post_treatment_surveillance_step(p, day, treatment_day, visit_number)

        if p.exit_reason:
            record_exit(self.metrics, p.exit_reason, patient=p, current_day=day)

    def _route_cervical_followup(self, p: Patient, result: str, day: int) -> None:
        """
        Schedule the correct cervical follow-up action based on screening result.

        Normal results need no follow-up. Abnormal cytology results (ASCUS,
        LSIL, ASC-H, HSIL) are referred directly to colposcopy. HPV_POSITIVE
        results use the ASCCP triage split: ~40% low-risk managed with a 1-year
        repeat cytology, ~60% referred to immediate colposcopy.

        Previously all non-normal results (including HPV_POSITIVE) were sent
        directly to colposcopy, bypassing the one_year_repeat path entirely.
        """
        if result in ("NORMAL", "HPV_NEGATIVE"):
            return  # routine surveillance — no follow-up needed

        if result == "HPV_POSITIVE":
            # ASCCP triage split — controlled by config.HPV_POSITIVE_COLPOSCOPY_PROB.
            if random.random() >= cfg.HPV_POSITIVE_COLPOSCOPY_PROB:
                p.log(day, "ROUTE HPV_POSITIVE → 1-year repeat cytology (low-risk mgmt)")
                self._queues.schedule_followup(
                    p, {"cancer": "cervical", "step": "one_year_repeat",
                         "referral_day": day}, day + 365
                )
            else:
                p.log(day, "ROUTE HPV_POSITIVE → colposcopy")
                due = day + cfg.FOLLOWUP_DELAY_DAYS.get("colposcopy", 30)
                self._queues.schedule_followup(
                    p, {"cancer": "cervical", "step": "colposcopy",
                         "referral_day": day}, due
                )
        else:
            # ASCUS / LSIL / ASC-H / HSIL → colposcopy
            due = day + cfg.FOLLOWUP_DELAY_DAYS.get("colposcopy", 30)
            self._queues.schedule_followup(
                p, {"cancer": "cervical", "step": "colposcopy",
                     "referral_day": day}, due
            )

    def _check_queue_ltfu(self, p: Patient, day: int, referral_day: int,
                          queue_type: str, procedure: str) -> bool:
        """
        Daily hazard LTFU check for patients in a procedure retry queue.
        Returns True if patient abandoned (exited), False if still active.
        queue_type: "primary" | "secondary" | "treatment"
        """
        key = f"queue_{queue_type}_daily"
        ltfu_q = cfg.LTFU_PROBS.get(key, 0)
        if ltfu_q > 0 and random.random() < ltfu_q:
            wait = day - referral_day
            if day >= self._warmup_day:
                self.metrics["wait_times_abandoned"][procedure].append(wait)
            p.log(day, f"LTFU queue — abandoned {procedure} after {wait}d")
            p.exit_system(day, f"ltfu_queue_{queue_type}")
            record_exit(self.metrics, "lost_to_followup", patient=p, current_day=day)
            if day >= self._warmup_day:
                self.metrics["n_ltfu"] += 1
                self.metrics[f"ltfu_queue_{queue_type}"] += 1
            if p.is_established:
                self._flush_buffer.append(p)
                self._pool_removals.add(id(p))
            return True
        return False

    def _cervical_followup_step(
        self, p: Patient, step: str, day: int,
        referral_day: int = 0, context: dict = None
    ) -> None:
        """
        Execute one cervical follow-up step and schedule the next if needed.

        Wait time = pure queue delay only (days overflowed and retrying).
        Computed as (day performed − referral_day) − scheduled follow-up delay,
        so the configured FOLLOWUP_DELAY_DAYS is excluded.

        Steps in order:
          one_year_repeat → (colposcopy if still abnormal)
          colposcopy      → (leep | cone_biopsy | surveillance)
          leep / cone_biopsy → procedure slot consumed; treatment recorded by run_treatment
        """
        if step == "one_year_repeat":
            # Queue LTFU check for secondary retry
            if day > referral_day and self._check_queue_ltfu(p, day, referral_day, "secondary", "cytology"):
                return
            # 1-year repeat cytology for HPV+ low-risk path.
            # Reschedule check: patient may not make it today
            if random.random() < cfg.LTFU_PROBS.get("reschedule_secondary", 0.10):
                self._queues.schedule_followup(
                    p, {"cancer": "cervical", "step": "one_year_repeat",
                         "referral_day": referral_day}, day + 1
                )
                p.log(day, "RESCHEDULE cytology repeat — patient unavailable, moved to next day")
                return
            self._day_secondary_demand += 1
            if not self._queues.consume_slot("cytology", day):
                self._day_secondary_overflow += 1
                self._queues.schedule_followup(
                    p, {"cancer": "cervical", "step": "one_year_repeat",
                         "referral_day": referral_day}, day + 1
                )
                return
            self._day_secondary_supply += 1

            # Force cytology — HPV-alone is not appropriate for the 1-year follow-up
            test                           = "cytology"
            result                         = draw_cervical_result(p, test)
            p.cervical_result              = result
            p.last_cervical_screen_day     = day
            p.last_cervical_screening_test = test
            p.log(day, f"ONE-YEAR REPEAT CYTOLOGY → {result}")
            record_screening(self.metrics, p, "cervical", result, current_day=day)
            queue_wait = (day - referral_day) - 365  # subtract scheduled 1-year delay
            if queue_wait > 0 and day >= self._warmup_day:
                self.metrics["wait_times"]["one_year_repeat"].append(queue_wait)
            # Route the new cytology result: normal → done; abnormal → colposcopy
            self._route_cervical_followup(p, result, day)

        elif step == "colposcopy":
            # Queue LTFU check for secondary retry
            if day > referral_day and self._check_queue_ltfu(p, day, referral_day, "secondary", "colposcopy"):
                return
            # Reschedule check: patient may not make it today
            if random.random() < cfg.LTFU_PROBS.get("reschedule_secondary", 0.10):
                self._queues.schedule_followup(
                    p, {"cancer": "cervical", "step": "colposcopy",
                         "referral_day": referral_day}, day + 1
                )
                p.log(day, "RESCHEDULE colposcopy — patient unavailable, moved to next day")
                return
            self._day_secondary_demand += 1
            if not self._queues.consume_slot("colposcopy", day):
                self._day_secondary_overflow += 1
                self._queues.schedule_followup(
                    p, {"cancer": "cervical", "step": "colposcopy",
                         "referral_day": referral_day}, day + 1
                )
                return
            self._day_secondary_supply += 1

            scheduled_delay = cfg.FOLLOWUP_DELAY_DAYS.get("colposcopy", 30)
            queue_wait = (day - referral_day) - scheduled_delay
            if queue_wait > 0 and day >= self._warmup_day:
                self.metrics["wait_times"]["colposcopy"].append(queue_wait)
            cin = run_colposcopy(p, day, self.metrics)
            if cin is None:
                return

            # Delay pathology result by colposcopy_result turnaround days
            result_delay = cfg.TURNAROUND_DAYS.get("colposcopy_result", 0)
            if result_delay > 0:
                self._queues.schedule_followup(
                    p, {"cancer": "cervical", "step": "colposcopy_result",
                         "referral_day": day}, day + result_delay
                )
            else:
                self._route_colposcopy_result(p, day)

        elif step == "colposcopy_result":
            # Pathology result turnaround has elapsed — route to treatment
            self._route_colposcopy_result(p, day)

        elif step in ("leep", "cone_biopsy"):
            # Queue LTFU check for treatment retry
            if day > referral_day and self._check_queue_ltfu(p, day, referral_day, "treatment", step):
                return
            self._day_treatment_demand += 1
            if not self._queues.consume_slot(step, day):
                self._day_treatment_overflow += 1
                self._queues.schedule_followup(
                    p, {"cancer": "cervical", "step": step,
                         "referral_day": referral_day}, day + 1
                )
                return
            self._day_treatment_supply += 1
            scheduled_delay = cfg.FOLLOWUP_DELAY_DAYS.get(step, 14)
            queue_wait = (day - referral_day) - scheduled_delay
            if queue_wait > 0 and day >= self._warmup_day:
                self.metrics["wait_times"][step].append(queue_wait)

            # Post-treatment surveillance: schedule first follow-up per ASCCP guidelines
            # (co-test at 6 months, then per POST_TREATMENT_SURVEILLANCE_CERVICAL schedule)
            first_interval = self._get_post_treatment_interval("cervical", 0)
            surv_due = day + first_interval
            self._queues.schedule_followup(
                p, {"cancer": "cervical", "step": "post_treatment_surveillance",
                     "referral_day": day, "treatment_day": day,
                     "visit_number": 0},
                surv_due
            )
            p.log(day, f"POST-TREATMENT surveillance scheduled: first visit day {surv_due}")

        elif step == "cin1_surveillance":
            # CIN1/normal colposcopy: annual surveillance with resolution/escalation
            clean_count = context.get("clean_count", 0) if context else 0
            self._cin1_surveillance_step(p, day, referral_day, clean_count)

        elif step == "post_treatment_surveillance":
            # Post-LEEP/cone surveillance visit
            treatment_day = context.get("treatment_day", referral_day) if context else referral_day
            visit_number  = context.get("visit_number", 0) if context else 0
            self._post_treatment_surveillance_step(p, day, treatment_day, visit_number)

    def _route_colposcopy_result(self, p: Patient, day: int) -> None:
        """Route colposcopy pathology result to treatment or surveillance."""
        disposition = run_treatment(p, day, self.metrics)

        if disposition == "surveillance":
            # CIN1 or normal colposcopy → schedule 1-year surveillance repeat
            self._queues.schedule_followup(
                p, {"cancer": "cervical", "step": "cin1_surveillance",
                     "referral_day": day, "clean_count": 0},
                day + cfg.CIN1_SURVEILLANCE_INTERVAL_DAYS
            )
        elif disposition not in ("exit",) and p.treatment_type:
            ttype = p.treatment_type
            due   = day + cfg.FOLLOWUP_DELAY_DAYS.get(ttype, 14)
            self._queues.schedule_followup(
                p, {"cancer": "cervical", "step": ttype,
                     "referral_day": day}, due
            )

    def _cin1_surveillance_step(self, p: Patient, day: int,
                                referral_day: int, clean_count: int) -> None:
        """
        CIN1 / normal colposcopy surveillance: annual cytology repeat.

        At each visit, draw resolution vs escalation vs persistence:
          - Resolved (clean): increment clean_count; if ≥ CIN1_MAX_CLEAN_VISITS,
            return to routine screening via provider cycle.
          - Escalated to CIN2/3: route to treatment (LEEP/cone).
          - Persistent CIN1: schedule another surveillance visit in 1 year.
        """
        if not p.active:
            return

        # Reschedule check: patient may not make it today
        if random.random() < cfg.LTFU_PROBS.get("reschedule_secondary", 0.10):
            self._queues.schedule_followup(
                p, {"cancer": "cervical", "step": "cin1_surveillance",
                     "referral_day": referral_day, "clean_count": clean_count},
                day + 1
            )
            p.log(day, "RESCHEDULE cytology (CIN1 surveillance) — patient unavailable, moved to next day")
            return

        # Consume a cytology slot for the surveillance visit
        self._day_secondary_demand += 1
        if not self._queues.consume_slot("cytology", day):
            self._day_secondary_overflow += 1
            self._queues.schedule_followup(
                p, {"cancer": "cervical", "step": "cin1_surveillance",
                     "referral_day": referral_day, "clean_count": clean_count},
                day + 1
            )
            return
        self._day_secondary_supply += 1

        # Draw outcome
        r = random.random()
        if r < cfg.CIN1_RESOLUTION_PROB_PER_VISIT:
            # Resolved
            clean_count += 1
            p.log(day, f"CIN1 SURVEILLANCE — resolved (clean {clean_count}/"
                        f"{cfg.CIN1_MAX_CLEAN_VISITS_BEFORE_ROUTINE})")
            record_screening(self.metrics, p, "cervical", "NORMAL", current_day=day)
            if clean_count >= cfg.CIN1_MAX_CLEAN_VISITS_BEFORE_ROUTINE:
                p.log(day, "CIN1 SURVEILLANCE — cleared, returning to routine screening")
                p.current_stage = "routine"
                p.colposcopy_result = None
                # Patient returns to routine via normal annual provider cycle
                return
            # Not enough clean visits yet — schedule another surveillance
            self._queues.schedule_followup(
                p, {"cancer": "cervical", "step": "cin1_surveillance",
                     "referral_day": day, "clean_count": clean_count},
                day + cfg.CIN1_SURVEILLANCE_INTERVAL_DAYS
            )

        elif r < cfg.CIN1_RESOLUTION_PROB_PER_VISIT + cfg.CIN1_ESCALATION_PROB_PER_VISIT:
            # Escalated to CIN2/3 — route to treatment
            p.colposcopy_result = random.choice(["CIN2", "CIN3"])
            p.log(day, f"CIN1 SURVEILLANCE — escalated to {p.colposcopy_result}")
            record_screening(self.metrics, p, "cervical", p.colposcopy_result, current_day=day)
            self._route_colposcopy_result(p, day)

        else:
            # Persistent CIN1 — schedule next surveillance
            p.log(day, "CIN1 SURVEILLANCE — persistent, repeat in 1 year")
            record_screening(self.metrics, p, "cervical", "CIN1", current_day=day)
            self._queues.schedule_followup(
                p, {"cancer": "cervical", "step": "cin1_surveillance",
                     "referral_day": day, "clean_count": 0},
                day + cfg.CIN1_SURVEILLANCE_INTERVAL_DAYS
            )

    def _post_treatment_surveillance_step(self, p: Patient, day: int,
                                           treatment_day: int,
                                           visit_number: int) -> None:
        """
        Post-LEEP/cone surveillance: co-test at intervals per ASCCP guidelines.

        Uses POST_TREATMENT_SURVEILLANCE_CERVICAL schedule from config.
        Each visit draws a normal vs recurrent result. Recurrence routes
        back to colposcopy. Normal results continue the surveillance schedule
        until POST_TREATMENT_ACTIVE_YEARS_CERVICAL is reached, then the
        patient returns to routine screening.
        """
        if not p.active:
            return

        years_since_treatment = (day - treatment_day) / 365.0

        # Check if surveillance period is complete
        if years_since_treatment >= cfg.POST_TREATMENT_ACTIVE_YEARS_CERVICAL:
            p.log(day, "POST-TREATMENT surveillance complete — returning to routine")
            p.current_stage = "routine"
            return

        # Reschedule check: patient may not make it today
        if random.random() < cfg.LTFU_PROBS.get("reschedule_secondary", 0.10):
            self._queues.schedule_followup(
                p, {"cancer": "cervical", "step": "post_treatment_surveillance",
                     "referral_day": day, "treatment_day": treatment_day,
                     "visit_number": visit_number},
                day + 1
            )
            p.log(day, "RESCHEDULE cytology (post-treatment surveillance) — patient unavailable, moved to next day")
            return

        # Consume a cytology slot for the surveillance co-test
        self._day_secondary_demand += 1
        if not self._queues.consume_slot("cytology", day):
            self._day_secondary_overflow += 1
            self._queues.schedule_followup(
                p, {"cancer": "cervical", "step": "post_treatment_surveillance",
                     "referral_day": day, "treatment_day": treatment_day,
                     "visit_number": visit_number},
                day + 1
            )
            return
        self._day_secondary_supply += 1

        visit_number += 1
        # Draw recurrence: use prior_cin risk factor — simplified model
        # Post-treatment recurrence rate ~5–10% per visit (PLACEHOLDER)
        recurrence_prob = 0.07  # PLACEHOLDER — calibrate to NYP/ASCCP data
        if random.random() < recurrence_prob:
            p.cervical_result = random.choice(["ASCUS", "HSIL"])
            p.log(day, f"POST-TREATMENT surveillance visit {visit_number} — "
                        f"RECURRENCE ({p.cervical_result}), referring to colposcopy")
            record_screening(self.metrics, p, "cervical", p.cervical_result, current_day=day)
            due = day + cfg.FOLLOWUP_DELAY_DAYS.get("colposcopy", 30)
            self._queues.schedule_followup(
                p, {"cancer": "cervical", "step": "colposcopy",
                     "referral_day": day}, due
            )
            return

        # Normal result — schedule next surveillance visit per schedule
        p.cervical_result = "NORMAL"
        p.last_cervical_screen_day = day
        record_screening(self.metrics, p, "cervical", "NORMAL", current_day=day)
        next_interval = self._get_post_treatment_interval("cervical", years_since_treatment)
        p.log(day, f"POST-TREATMENT surveillance visit {visit_number} — normal, "
                    f"next in {next_interval} days")
        self._queues.schedule_followup(
            p, {"cancer": "cervical", "step": "post_treatment_surveillance",
                 "referral_day": day, "treatment_day": treatment_day,
                 "visit_number": visit_number},
            day + next_interval
        )

    def _get_post_treatment_interval(self, cancer: str, years_since: float) -> int:
        """
        Look up the correct surveillance interval (in days) from the config
        schedule based on years since treatment.
        """
        if cancer == "cervical":
            schedule = cfg.POST_TREATMENT_SURVEILLANCE_CERVICAL
        else:
            schedule = cfg.POST_TREATMENT_SURVEILLANCE_LUNG
        for max_year, interval_months in schedule:
            if years_since < max_year:
                return interval_months * 30  # approximate months → days
        # Past all schedule entries — annual
        return 365

    def _lung_post_treatment_surveillance_step(self, p: Patient, day: int,
                                                treatment_day: int,
                                                visit_number: int) -> None:
        """
        Post-lung-treatment surveillance: repeat LDCT at intervals per NCCN.

        Uses POST_TREATMENT_SURVEILLANCE_LUNG schedule from config.
        """
        if not p.active:
            return

        years_since_treatment = (day - treatment_day) / 365.0

        if years_since_treatment >= cfg.POST_TREATMENT_ACTIVE_YEARS_LUNG:
            p.log(day, "LUNG POST-TREATMENT surveillance complete — returning to routine")
            p.current_stage = "routine"
            return

        # Consume an LDCT slot
        if not self._queues.consume_slot("ldct", day):
            self._queues.schedule_followup(
                p, {"cancer": "lung", "step": "lung_post_treatment_surveillance",
                     "referral_day": day, "treatment_day": treatment_day,
                     "visit_number": visit_number},
                day + 1
            )
            return

        visit_number += 1
        result = draw_lung_rads_result()
        p.lung_result = result
        p.last_lung_screen_day = day
        p.log(day, f"LUNG POST-TREATMENT surveillance visit {visit_number} → {result}")
        if day >= self._warmup_day:
            self.metrics["lung_rads_distribution"][result] += 1
        record_screening(self.metrics, p, "lung", result, current_day=day)

        # If suspicious, escalate to biopsy pathway
        if result in ("RADS_4A", "RADS_4B_4X"):
            due = day + cfg.FOLLOWUP_DELAY_DAYS.get("lung_biopsy", 14)
            self._queues.schedule_followup(
                p, {"cancer": "lung", "step": "biopsy",
                     "referral_day": day}, due
            )
            return

        # Otherwise schedule next surveillance visit
        next_interval = self._get_post_treatment_interval("lung", years_since_treatment)
        p.log(day, f"LUNG POST-TREATMENT next surveillance in {next_interval} days")
        self._queues.schedule_followup(
            p, {"cancer": "lung", "step": "lung_post_treatment_surveillance",
                 "referral_day": day, "treatment_day": treatment_day,
                 "visit_number": visit_number},
            day + next_interval
        )

    def _lung_result_routing(self, p: Patient, day: int) -> None:
        """
        Run Lung-RADS routing on LDCT result day.

        RADS 4A/4B/4X — schedule biopsy follow-up.
        RADS 0/1/2/3  — schedule a repeat LDCT at the clinically correct interval
                         (1–3 months for RADS 0, 6 months for RADS 3, 12 months
                         for RADS 1/2) from cfg.LUNG_RADS_REPEAT_INTERVALS.

        Previously the return value of run_lung_followup was discarded, so RADS
        0/1/2/3 patients were never scheduled for their follow-up scan.  The
        annual visit cycle would bring them back in 12 months, which is wrong
        for RADS 0 (needs 1-3 months) and RADS 3 (needs 6 months).
        """
        disposition = run_lung_followup(p, day, self.metrics)

        if not p.active:
            if p.exit_reason:
                record_exit(self.metrics, p.exit_reason, patient=p, current_day=day)
            return

        if disposition == "lung_treated":
            # Schedule post-treatment surveillance per NCCN guidelines
            first_interval = self._get_post_treatment_interval("lung", 0)
            surv_due = day + first_interval
            self._queues.schedule_followup(
                p, {"cancer": "lung", "step": "lung_post_treatment_surveillance",
                     "referral_day": day, "treatment_day": day,
                     "visit_number": 0},
                surv_due
            )
            p.log(day, f"LUNG POST-TREATMENT surveillance scheduled: first visit day {surv_due}")
        elif p.lung_result in ("RADS_4A", "RADS_4B_4X"):
            due = day + cfg.FOLLOWUP_DELAY_DAYS.get("lung_biopsy", 14)
            self._queues.schedule_followup(
                p, {"cancer": "lung", "step": "biopsy",
                     "referral_day": day}, due
            )
        elif disposition in ("repeat_ldct_1_3mo", "repeat_ldct_6mo", "repeat_ldct_12mo"):
            # Schedule the repeat scan at the Lung-RADS–specified interval.
            repeat_days = cfg.LUNG_RADS_REPEAT_INTERVALS.get(p.lung_result, 365)
            self._queues.schedule_followup(
                p, {"cancer": "lung", "step": "repeat_ldct",
                     "referral_day": day,
                     "scheduled_delay": repeat_days}, day + repeat_days
            )

    def _lung_biopsy_step(self, p: Patient, day: int,
                          referral_day: int = 0) -> None:
        """
        Process a lung biopsy appointment (FIFO).
        If no slot available today, push to tomorrow.
        """
        # Queue LTFU check for secondary retry
        if day > referral_day and self._check_queue_ltfu(p, day, referral_day, "secondary", "lung_biopsy"):
            return
        # Reschedule check: patient may not make it today
        if random.random() < cfg.LTFU_PROBS.get("reschedule_secondary", 0.10):
            self._queues.schedule_followup(
                p, {"cancer": "lung", "step": "biopsy",
                     "referral_day": referral_day}, day + 1
            )
            p.log(day, "RESCHEDULE lung biopsy — patient unavailable, moved to next day")
            return
        self._day_secondary_demand += 1
        if not self._queues.consume_slot("lung_biopsy", day):
            self._day_secondary_overflow += 1
            self._queues.schedule_followup(
                p, {"cancer": "lung", "step": "biopsy",
                     "referral_day": referral_day}, day + 1
            )
            return
        self._day_secondary_supply += 1
        scheduled_delay = cfg.FOLLOWUP_DELAY_DAYS.get("lung_biopsy", 14)
        queue_wait = (day - referral_day) - scheduled_delay
        if queue_wait > 0 and day >= self._warmup_day:
            self.metrics["wait_times"]["lung_biopsy"].append(queue_wait)

    def _lung_repeat_ldct_step(self, p: Patient, day: int,
                              referral_day: int = 0,
                              scheduled_delay: int = 0) -> None:
        """
        Process a Lung-RADS–mandated follow-up LDCT (RADS 0/1/2/3 path).

        Bypasses the standard screening interval check.  Consumes an LDCT
        procedure slot; if none available today, pushes to next day (FIFO).
        """
        # Reschedule check: patient may not make it today
        if random.random() < cfg.LTFU_PROBS.get("reschedule_secondary", 0.10):
            self._queues.schedule_followup(
                p, {"cancer": "lung", "step": "repeat_ldct",
                     "referral_day": referral_day}, day + 1
            )
            p.log(day, "RESCHEDULE LDCT (repeat) — patient unavailable, moved to next day")
            return
        if not self._queues.consume_slot("ldct", day):
            self._queues.schedule_followup(
                p, {"cancer": "lung", "step": "repeat_ldct",
                     "referral_day": referral_day}, day + 1
            )
            return

        result                 = draw_lung_rads_result()
        p.lung_result          = result
        p.last_lung_screen_day = day
        p.log(day, f"LUNG REPEAT LDCT → {result}")
        if day >= self._warmup_day:
            self.metrics["lung_rads_distribution"][result] += 1
        record_screening(self.metrics, p, "lung", result, current_day=day)
        queue_wait = (day - referral_day) - scheduled_delay
        if queue_wait > 0 and day >= self._warmup_day:
            self.metrics["wait_times"]["ldct"].append(queue_wait)
        # Re-route the new result — may schedule another repeat or escalate to biopsy.
        disposition = run_lung_followup(p, day, self.metrics)
        if p.active:
            if p.lung_result in ("RADS_4A", "RADS_4B_4X"):
                due = day + cfg.FOLLOWUP_DELAY_DAYS.get("lung_biopsy", 14)
                self._queues.schedule_followup(
                    p, {"cancer": "lung", "step": "biopsy",
                         "referral_day": day}, due
                )
            elif disposition in ("repeat_ldct_1_3mo", "repeat_ldct_6mo", "repeat_ldct_12mo"):
                repeat_days = cfg.LUNG_RADS_REPEAT_INTERVALS.get(p.lung_result, 365)
                self._queues.schedule_followup(
                    p, {"cancer": "lung", "step": "repeat_ldct",
                         "referral_day": day,
                         "scheduled_delay": repeat_days}, day + repeat_days
                )

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
            # Use per-cancer cervical count so lung-only-eligible patients
            # don't inflate the funnel's top bar.
            ("Eligible (cervical)", m["n_eligible"].get("cervical", m["n_eligible_any"])),
            ("Screened",            m["n_screened"].get("cervical", 0)),
            ("Abnormal result",     total_abnormal),
            ("Colposcopy",          m["n_colposcopy"]),
            ("Treated",             m["n_treated"]),
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
