# =============================================================================
# runner.py
# SimulationRunner — daily discrete-event simulation using SimPy.
# =============================================================================
#
# Daily cycle
# -----------
# Each simulation day is one SimPy time unit. The main process ticks forward
# one day at a time and processes all providers in sequence.
#
# PCP / Gynecologist / Specialist
#   1. Scheduled outpatients fill their reserved slots first (OUTPATIENT_FRACTION).
#   2. Drop-ins fill remaining capacity (including unused outpatient slots).
#   3. Drop-in overflow → appended to tomorrow's drop-in queue for same provider.
#
# ER
#   - No scheduled outpatient slots — all walk-in.
#   - Overflow splits:
#       ER_OVERFLOW_RETRY_PROB  → retry ER tomorrow (drop-in, day+1)
#       1 - above               → convert to outpatient at PCP/Gyno/Specialist
#
# Patient generation
#   Each day, DAILY_PATIENTS new patients are created.
#   - outpatients (70%): booked for a future day (OUTPATIENT_LEAD_DAYS ahead)
#   - drop-ins    (30%): join today's drop-in queue immediately
#
# Usage
# -----
#   sim = SimulationRunner(n_days=365, seed=42)
#   sim.run()
#   sim.summary()
#   sim.revenue_summary()
#   sim.plot_all()
# =============================================================================

import random
from collections import defaultdict

import simpy

import config as cfg
from patient import Patient
from population import sample_patient
from screening import (
    get_eligible_screenings,
    assign_screening_test,
    run_screening_step,
    handle_unscreened,
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

_NON_ER_PROVIDERS = ["pcp", "gynecologist", "specialist"]
_ALL_PROVIDERS    = _NON_ER_PROVIDERS + ["er"]


# =============================================================================
# Patient Queue Manager
# =============================================================================

class PatientQueues:
    """
    Manages scheduled outpatient and drop-in queues for all providers.

    outpatient[provider][day] → [Patient, ...]   (scheduled for that day)
    dropin[provider]          → [Patient, ...]   (today's walk-ins)
    """

    def __init__(self):
        # provider → {day: [patients]}
        self.outpatient: dict = defaultdict(lambda: defaultdict(list))
        # provider → [patients]  (reset each day after processing)
        self.dropin: dict = defaultdict(list)

    def schedule_outpatient(self, p: Patient, provider: str, day: int) -> None:
        """Book patient into a provider's outpatient slot on a future day."""
        self.outpatient[provider][day].append(p)

    def add_dropin(self, p: Patient, provider: str) -> None:
        """Add patient to a provider's walk-in queue."""
        self.dropin[provider].append(p)

    def process_day(self, provider: str, day: int):
        """
        Process one provider's queues for the given day.

        Priority order:
          1. Scheduled outpatients fill their reserved slots.
          2. Drop-ins fill remaining capacity
             (including any unused outpatient slots).
          3. Leftover outpatients (rare edge case) → rescheduled for day+1.

        Returns
        -------
        seen     : list[Patient]  patients who will be seen today
        overflow : list[Patient]  drop-ins who could not be accommodated
        """
        total      = cfg.PROVIDER_CAPACITY.get(provider, 0)
        outpt_frac = cfg.OUTPATIENT_FRACTION.get(provider, 0.0)
        outpt_cap  = int(total * outpt_frac)
        dropin_cap = total - outpt_cap

        # ── Outpatients ───────────────────────────────────────────────────────
        today_outpts    = self.outpatient[provider].pop(day, [])
        seen_outpts     = today_outpts[:outpt_cap]
        bumped_outpts   = today_outpts[outpt_cap:]          # pushed to tomorrow
        for p in bumped_outpts:
            self.outpatient[provider][day + 1].append(p)

        # ── Drop-ins ──────────────────────────────────────────────────────────
        # Unused outpatient slots open to drop-ins
        extra          = outpt_cap - len(seen_outpts)
        available      = dropin_cap + extra
        today_dropins  = list(self.dropin.get(provider, []))
        seen_dropins   = today_dropins[:available]
        overflow       = today_dropins[available:]
        self.dropin[provider] = []                          # clear today's queue

        return seen_outpts + seen_dropins, overflow


# =============================================================================
# Simulation Runner
# =============================================================================

class SimulationRunner:
    """
    Daily discrete-event simulation of NYP women's health screening.

    Parameters
    ----------
    n_days     : int   Simulation horizon in days.
    seed       : int   Random seed.
    daily_rate : int   Mean number of new patients generated per day.
    """

    def __init__(
        self,
        n_days:     int = cfg.SIM_DAYS,
        seed:       int = cfg.RANDOM_SEED,
        daily_rate: int = cfg.DAILY_PATIENTS,
    ):
        self.n_days     = n_days
        self.seed       = seed
        self.daily_rate = daily_rate
        self.metrics    = None
        self._patients  = []   # completed patient records

    # ── Patient generation ────────────────────────────────────────────────────

    def _generate_daily_arrivals(
        self, day: int, queues: PatientQueues, patient_id_counter: list
    ) -> None:
        """
        Create today's new patients and route them into queues.

        Outpatients (70%): booked for a future day using OUTPATIENT_LEAD_DAYS.
        Drop-ins   (30%): added directly to today's drop-in queue.
        """
        _dest_keys    = list(cfg.DESTINATION_PROBS.keys())
        _dest_w       = list(cfg.DESTINATION_PROBS.values())
        _type_keys    = list(cfg.PATIENT_TYPE_PROBS.keys())
        _type_w       = list(cfg.PATIENT_TYPE_PROBS.values())

        n_today = random.poisson_approx(self.daily_rate)   # see helper below

        for _ in range(n_today):
            pid          = patient_id_counter[0]
            patient_id_counter[0] += 1
            destination  = random.choices(_dest_keys, weights=_dest_w)[0]
            patient_type = random.choices(_type_keys, weights=_type_w)[0]

            p = sample_patient(pid, day, destination, patient_type)

            if patient_type == "outpatient" and destination != "er":
                lo, hi   = cfg.OUTPATIENT_LEAD_DAYS.get(destination, (1, 7))
                appt_day = day + random.randint(lo, hi)
                queues.schedule_outpatient(p, destination, appt_day)
            else:
                # Drop-in (or outpatient directed to ER — treated as drop-in)
                queues.add_dropin(p, destination)

    # ── Overflow routing ──────────────────────────────────────────────────────

    def _route_overflow(
        self, overflow: list, provider: str, day: int, queues: PatientQueues
    ) -> None:
        """
        Route patients who could not be seen today.

        ER overflow:
          ER_OVERFLOW_RETRY_PROB  → tomorrow's ER drop-in queue
          otherwise               → outpatient slot at PCP / Gyno / Specialist

        Non-ER overflow:
          → tomorrow's drop-in queue for the same provider
        """
        for p in overflow:
            if provider == "er":
                if random.random() < cfg.ER_OVERFLOW_RETRY_PROB:
                    # Retry ER tomorrow
                    queues.add_dropin(p, "er")
                else:
                    # Convert to scheduled outpatient at a non-ER provider
                    alt    = random.choice(_NON_ER_PROVIDERS)
                    lo, hi = cfg.OUTPATIENT_LEAD_DAYS.get(alt, (1, 7))
                    queues.schedule_outpatient(p, alt, day + random.randint(lo, hi))
            else:
                # Same provider, tomorrow's drop-in queue
                queues.add_dropin(p, provider)

    # ── Screening + follow-up for one seen patient ────────────────────────────

    def _process_patient(
        self, env: simpy.Environment, p: Patient, resources: dict
    ):
        """
        SimPy process: screen + follow up one patient.
        Yields at resource requests and inter-step scheduling delays.
        """
        day = int(env.now)
        self.metrics["n_patients"] += 1

        eligible = get_eligible_screenings(p)

        if not eligible:
            outcome = handle_unscreened(p, day)
            self.metrics["n_unscreened"] += 1
            if outcome == "reschedule":
                self.metrics["n_reschedule"]    += 1
                self.metrics["ltfu_unscreened"] += 1
            return

        self.metrics["n_eligible_any"] += 1

        for cancer in eligible:
            if not p.active:
                break

            test = assign_screening_test(p, cancer)
            if test == "ineligible":
                continue

            # Acquire a screening resource slot (queue if at daily capacity)
            resource = resources.get(test)
            if resource:
                with resource.request() as req:
                    t0 = env.now
                    yield req
                    self.metrics["wait_times"][test].append(env.now - t0)
                yield env.timeout(1)   # test takes 1 day

            day    = int(env.now)
            result = run_screening_step(p, cancer, day, self.metrics)

            if result is None:
                # LTFU before test completed (lung pre-LDCT nodes)
                if p.exit_reason:
                    record_exit(self.metrics, p.exit_reason)
                return

            record_screening(self.metrics, p, cancer, result)

            if cancer == "cervical":
                yield from self._cervical_followup(env, p, resources)
            elif cancer == "lung":
                yield from self._lung_followup(env, p, resources)

            if p.exit_reason:
                record_exit(self.metrics, p.exit_reason)

        self._patients.append(p)

    # ── Cervical follow-up sub-process ────────────────────────────────────────

    def _cervical_followup(
        self, env: simpy.Environment, p: Patient, resources: dict
    ):
        """
        Cervical follow-up as a SimPy sub-process.
        Yields at scheduling delays and resource acquisition.

        Steps with SimPy timing:
          Abnormal result → wait FOLLOWUP_DELAY_DAYS["colposcopy"] days
                          → acquire colposcopy slot
                          → run colposcopy
          If excisional tx → wait FOLLOWUP_DELAY_DAYS[treatment] days
                           → acquire treatment slot
        """
        day       = int(env.now)
        next_step = route_cervical_result(p, day, self.metrics)

        if next_step in ("routine_surveillance", "one_year_repeat", "exit"):
            return

        if next_step == "colposcopy":
            # Wait for colposcopy appointment
            yield env.timeout(cfg.FOLLOWUP_DELAY_DAYS.get("colposcopy", 30))

            colpo_res = resources.get("colposcopy")
            if colpo_res:
                with colpo_res.request() as req:
                    t0 = env.now
                    yield req
                    self.metrics["wait_times"]["colposcopy"].append(env.now - t0)
                yield env.timeout(1)

            day = int(env.now)
            cin = run_colposcopy(p, day, self.metrics)
            if cin is None:
                return

            disposition = run_treatment(p, day, self.metrics)

            if disposition not in ("exit", "surveillance") and p.treatment_type:
                ttype = p.treatment_type
                yield env.timeout(cfg.FOLLOWUP_DELAY_DAYS.get(ttype, 14))

                treat_res = resources.get(ttype)
                if treat_res:
                    with treat_res.request() as req:
                        t0 = env.now
                        yield req
                        self.metrics["wait_times"][ttype].append(env.now - t0)
                    yield env.timeout(1)

    # ── Lung follow-up sub-process ────────────────────────────────────────────

    def _lung_followup(
        self, env: simpy.Environment, p: Patient, resources: dict
    ):
        """
        Lung follow-up as a SimPy sub-process.
        Models wait times for biopsy and treatment appointments.
        """
        day = int(env.now)
        run_lung_followup(p, day, self.metrics)

        if p.lung_biopsy_result is not None:
            yield env.timeout(cfg.FOLLOWUP_DELAY_DAYS.get("lung_biopsy", 14))

        if p.lung_biopsy_result == "malignant":
            yield env.timeout(cfg.FOLLOWUP_DELAY_DAYS.get("lung_treatment", 21))

        yield env.timeout(0)   # SimPy requires at least one yield

    # ── Daily tick ────────────────────────────────────────────────────────────

    def _daily_process(
        self, env: simpy.Environment, queues: PatientQueues, resources: dict
    ):
        """
        Main SimPy process — advances the simulation one day at a time.

        Each day:
          1. Generate new patient arrivals → route to outpatient / drop-in queues.
          2. For each provider, process daily queues respecting capacity.
          3. Route overflow per provider-specific rules.
          4. Launch a SimPy process for each patient who is seen.
          5. Advance clock by 1 day.
        """
        pid_counter = [0]   # mutable so nested functions can increment it

        while True:
            day = int(env.now)

            # 1. New arrivals
            self._generate_daily_arrivals(day, queues, pid_counter)

            # 2–4. Process each provider
            for provider in _ALL_PROVIDERS:
                seen, overflow = queues.process_day(provider, day)

                # Route overflow before processing seen patients
                self._route_overflow(overflow, provider, day, queues)

                # Track overflow volume in metrics
                self.metrics.setdefault("daily_overflow", defaultdict(int))
                self.metrics["daily_overflow"][provider] += len(overflow)

                # Launch SimPy process for each seen patient
                for p in seen:
                    env.process(self._process_patient(env, p, resources))

            # 5. Advance to next day
            yield env.timeout(1)

            if day >= self.n_days - 1:
                return

    # ── Resource setup ────────────────────────────────────────────────────────

    def _make_resources(self, env: simpy.Environment) -> dict:
        """Create SimPy Resources for each screening/procedure type."""
        return {
            name: simpy.Resource(env, capacity=cap)
            for name, cap in cfg.CAPACITIES.items()
        }

    # ── Run ───────────────────────────────────────────────────────────────────

    def run(self) -> dict:
        """
        Run the full simulation.

        Returns
        -------
        metrics dict — pass directly to print_summary() / compute_revenue(),
        or use the convenience methods below.
        """
        random.seed(self.seed)
        self.metrics   = initialize_metrics()
        self._patients = []

        env       = simpy.Environment()
        resources = self._make_resources(env)
        queues    = PatientQueues()

        env.process(self._daily_process(env, queues, resources))
        env.run(until=self.n_days + 1)   # +1 so last day's processes complete

        return self.metrics

    # ── Output ────────────────────────────────────────────────────────────────

    def summary(self) -> None:
        """Print clinical outcomes summary."""
        self._require_run()
        print_summary(self.metrics)

    def revenue_summary(self) -> None:
        """Print realized vs. foregone revenue summary."""
        self._require_run()
        print_revenue_summary(self.metrics)

    def plot_all(self) -> None:
        """Render all four summary plots in a 2×2 grid."""
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

    def plot_overflow(self) -> None:
        """Bar chart of total daily overflow by provider."""
        self._require_run()
        import matplotlib.pyplot as plt

        overflow = self.metrics.get("daily_overflow", {})
        if not overflow:
            print("No overflow recorded.")
            return

        providers = list(overflow.keys())
        totals    = [overflow[p] for p in providers]
        plt.figure(figsize=(7, 4))
        plt.bar(providers, totals, color="#ED7D31")
        plt.title("Total Patient Overflow by Provider")
        plt.ylabel("Patients overflowed (cumulative)")
        plt.xlabel("Provider")
        for i, v in enumerate(totals):
            plt.text(i, v + max(totals) * 0.01, f"{v:,}", ha="center", fontsize=9)
        plt.tight_layout()
        plt.show()

    # ── Plot helpers ──────────────────────────────────────────────────────────

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
        labels = [s[0] for s in steps]
        values = [s[1] for s in steps]
        bars   = ax.barh(labels[::-1], values[::-1], color="#4472C4")
        ax.set_title("Cervical Screening Funnel")
        ax.set_xlabel("Patients")
        _max = max(values) if any(values) else 1
        for bar, val in zip(bars, values[::-1]):
            ax.text(
                bar.get_width() + _max * 0.01,
                bar.get_y() + bar.get_height() / 2,
                f"{val:,}", va="center", fontsize=9,
            )

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
        labels = [s[0] for s in steps]
        values = [s[1] for s in steps]
        bars   = ax.barh(labels[::-1], values[::-1], color="#ED7D31")
        ax.set_title("Lung LDCT Pathway Funnel")
        ax.set_xlabel("Patients")
        _max = max(values) if any(values) else 1
        for bar, val in zip(bars, values[::-1]):
            ax.text(
                bar.get_width() + _max * 0.01,
                bar.get_y() + bar.get_height() / 2,
                f"{val:,}", va="center", fontsize=9,
            )

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
        r      = compute_revenue(self.metrics)
        vals   = [r["realized_total"], r["foregone_total"]]
        colors = ["#4472C4", "#C00000"]
        bars   = ax.bar(["Realized", "Foregone"], vals, color=colors)
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
            capture = 100 * vals[0] / sum(vals)
            ax.set_xlabel(f"Revenue capture rate: {capture:.1f}%", fontsize=10)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _require_run(self) -> None:
        if self.metrics is None:
            raise RuntimeError("Call run() first.")


# =============================================================================
# Helpers
# =============================================================================

def _poisson_approx(lam: float) -> int:
    """Normal approximation to Poisson for large lambda (daily arrivals)."""
    import math
    val = int(random.gauss(lam, math.sqrt(lam)) + 0.5)
    return max(0, val)


# Monkey-patch so _generate_daily_arrivals can call random.poisson_approx
random.poisson_approx = _poisson_approx
