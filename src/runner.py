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

import config as cfg
from patient import Patient
from population import sample_patient
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

    Attributes
    ----------
    outpatient[provider][day] : list[Patient]
        Patients with a confirmed appointment on that day.
        Guaranteed not to exceed _outpatient_cap(provider) per day —
        the scheduler pushes forward until a slot is free.

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
        Book patient into the earliest available outpatient slot on or after
        earliest_day.  Never overfills a day — guaranteed capacity.

        Returns the day the appointment was booked.
        """
        cap = _outpatient_cap(provider)
        day = earliest_day
        while len(self.outpatient[provider][day]) >= cap:
            day += 1
        self.outpatient[provider][day].append(p)
        return day

    # ── Drop-in management ────────────────────────────────────────────────────

    def add_dropin(self, p: Patient, provider: str) -> None:
        """Add patient to a provider's walk-in queue."""
        self.dropin[provider].append(p)

    # ── Daily queue processing ────────────────────────────────────────────────

    def process_day(self, provider: str, day: int):
        """
        Seat today's patients for one provider, respecting capacity.

        Priority:
          1. Scheduled outpatients fill their reserved slots.
          2. Drop-ins fill remaining capacity (including any unused outpt slots).
          3. Unseen drop-ins (overflow) are returned to the caller for re-routing.

        Returns
        -------
        seen     : list[Patient]
        overflow : list[Patient]
        """
        outpt_cap = _outpatient_cap(provider)
        dropin_cap = _dropin_cap(provider)

        # Outpatients (always fit — scheduler guarantees this)
        seen_outpts = self.outpatient[provider].pop(day, [])

        # Drop-ins fill remaining capacity
        extra          = max(0, outpt_cap - len(seen_outpts))  # unused outpt slots
        available      = dropin_cap + extra
        today_dropins  = list(self.dropin.get(provider, []))
        seen_dropins   = today_dropins[:available]
        overflow       = today_dropins[available:]
        self.dropin[provider] = []

        return seen_outpts + seen_dropins, overflow

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

    Parameters
    ----------
    n_days     : int   Number of days to simulate.
    seed       : int   Random seed for reproducibility.
    daily_rate : int   Mean number of new patient arrivals per day.
    """

    def __init__(
        self,
        n_days:     int = 365,
        seed:       int = cfg.RANDOM_SEED,
        daily_rate: int = cfg.DAILY_PATIENTS,
    ):
        self.n_days     = n_days
        self.seed       = seed
        self.daily_rate = daily_rate
        self.metrics    = None
        self._queues    = None
        self._pid       = 0

    # =========================================================================
    # Public interface
    # =========================================================================

    def run(self) -> dict:
        """
        Run the full simulation.

        Returns
        -------
        metrics dict — use with summary(), revenue_summary(), plot_all().
        """
        random.seed(self.seed)
        self.metrics = initialize_metrics()
        self._queues = PatientQueues()
        self._pid    = 0

        for day in range(self.n_days):
            self._tick(day)

        return self.metrics

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
          1. Process follow-up appointments due today (before new arrivals,
             so procedure slots are consumed before walk-ins arrive).
          2. Generate new patient arrivals → route to queues.
          3. For each provider: seat patients, route overflow, screen seen.
        """
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
        Create today's new patients (Poisson draw) and route them.

        Outpatients → schedule_outpatient() finds the next free slot on or
                       after (day + lead_time).  Slot count never exceeds
                       _outpatient_cap(provider) per day.
        Drop-ins    → join today's drop-in queue immediately.
        ER patients → always drop-in regardless of patient_type.
        """
        _dest_keys = list(cfg.DESTINATION_PROBS.keys())
        _dest_w    = list(cfg.DESTINATION_PROBS.values())
        _type_keys = list(cfg.PATIENT_TYPE_PROBS.keys())
        _type_w    = list(cfg.PATIENT_TYPE_PROBS.values())

        for _ in range(_poisson(self.daily_rate)):
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
                    alt     = random.choice(_NON_ER)
                    lo, hi  = cfg.OUTPATIENT_LEAD_DAYS.get(alt, (1, 7))
                    earliest = day + random.randint(lo, hi)
                    self._queues.schedule_outpatient(p, alt, earliest)
            else:
                self._queues.add_dropin(p, provider)

    # =========================================================================
    # Screening
    # =========================================================================

    def _screen_patient(self, p: Patient, day: int) -> None:
        """
        Assess eligibility and run screening for each due cancer.
        Abnormal results trigger a follow-up appointment queued for a future day.
        """
        eligible = get_eligible_screenings(p)
        self.metrics["n_patients"] += 1

        if not eligible:
            self.metrics["n_unscreened"] += 1

            # Find the soonest day this patient will become eligible for any
            # active cancer.  Three cases:
            #   d > 0  → not yet eligible (e.g. turning 21, approaching 20 pk-yrs,
            #             not yet 50) → schedule a return visit at that future day.
            #   d == 0 → already eligible (shouldn't reach here, but guard anyway).
            #   d < 0  → permanently ineligible (no cervix, aged out, never-smoker,
            #             quit window closed) → exit silently; no revenue foregone.
            soonest = -1
            for cancer in cfg.ACTIVE_CANCERS:
                d = days_until_eligible(p, cancer)
                if d > 0 and (soonest < 0 or d < soonest):
                    soonest = d

            if soonest > 0:
                self._queues.schedule_outpatient(p, p.destination, day + soonest)
                self.metrics["n_reschedule"] += 1
                p.log(day, f"NOT YET ELIGIBLE — return visit scheduled in {soonest} days")
            else:
                # Permanently ineligible — exit quietly, no LTFU revenue impact
                p.exit_system(day, "ineligible")
                record_exit(self.metrics, "ineligible")
            return

        self.metrics["n_eligible_any"] += 1

        for cancer in eligible:
            if not p.active:
                break

            result = run_screening_step(p, cancer, day, self.metrics)

            if result is None:
                # Patient LTFU inside run_screening_step (lung pre-LDCT nodes)
                if p.exit_reason:
                    record_exit(self.metrics, p.exit_reason)
                return

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
