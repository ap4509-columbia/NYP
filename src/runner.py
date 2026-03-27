# =============================================================================
# runner.py
# SimulationRunner — discrete-event simulation entry point using SimPy.
# =============================================================================
#
# Architecture:
#   - SimPy Environment provides the clock (unit = days).
#   - Each patient is a SimPy process: arrives, waits in resource queues,
#     gets screened, waits for follow-up appointments, exits.
#   - Resources (cytology slots, colposcopy slots, LDCT slots, etc.) have
#     finite daily capacity from config.CAPACITIES. When all slots are taken,
#     patients queue — this is where fragmented vs. coordinated differences
#     become measurable.
#   - Inter-step delays (e.g., 30-day wait for colposcopy) come from
#     config.FOLLOWUP_DELAY_DAYS.
#
# Usage:
#   sim = SimulationRunner(n_patients=2000, seed=42)
#   sim.run()
#   sim.summary()
#   sim.revenue_summary()
#   sim.plot_all()
# =============================================================================

import random
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


class SimulationRunner:
    """
    Discrete-event simulation of NYP women's health screening.

    Parameters
    ----------
    n_patients : int
        Total number of patients to generate (arrival process stops after this).
    seed       : int
        Random seed for reproducibility.
    sim_days   : int
        Simulation horizon in days (default: SIM_DAYS from config).
    """

    def __init__(
        self,
        n_patients: int = 1000,
        seed:       int = cfg.RANDOM_SEED,
        sim_days:   int = cfg.SIM_DAYS,
    ):
        self.n_patients = n_patients
        self.seed       = seed
        self.sim_days   = sim_days
        self.metrics    = None
        self._patients  = []   # completed patient records (for tracing / debugging)

    # ── Resource setup ────────────────────────────────────────────────────────

    def _make_resources(self, env: simpy.Environment) -> dict:
        """
        Create one SimPy Resource per procedure type, capacity from config.
        Patients that cannot immediately acquire a slot are queued by SimPy.
        """
        return {
            name: simpy.Resource(env, capacity=cap)
            for name, cap in cfg.CAPACITIES.items()
        }

    # ── Patient process ───────────────────────────────────────────────────────

    def _patient_process(
        self, env: simpy.Environment, p: Patient, resources: dict
    ):
        """
        SimPy generator process for one patient's full journey.

        Flow
        ----
        1. Eligibility check
        2. For each eligible cancer:
             a. Wait for + acquire a screening resource slot
             b. Perform screening → draw result
             c. If abnormal:
                  - Wait for follow-up appointment (scheduling delay)
                  - Wait for + acquire follow-up resource slot
                  - Run clinical follow-up (colposcopy / biopsy chain)
                  - If treatment needed: wait + acquire treatment slot
        3. Record exits and wait times throughout
        """
        # ── Eligibility ───────────────────────────────────────────────────────
        eligible = get_eligible_screenings(p)
        self.metrics["n_patients"] += 1

        if not eligible:
            outcome = handle_unscreened(p, int(env.now))
            self.metrics["n_unscreened"] += 1
            if outcome == "reschedule":
                self.metrics["n_reschedule"]   += 1
                self.metrics["ltfu_unscreened"] += 1
            return

        self.metrics["n_eligible_any"] += 1

        # ── Screening ─────────────────────────────────────────────────────────
        for cancer in eligible:
            if not p.active:
                break

            test = assign_screening_test(p, cancer)
            if test == "ineligible":
                continue

            # Acquire a screening slot (queue if at capacity)
            resource = resources.get(test)
            if resource:
                with resource.request() as req:
                    t0 = env.now
                    yield req                            # wait in queue
                    self.metrics["wait_times"][test].append(env.now - t0)
                yield env.timeout(1)                    # 1 day to complete test

            day    = int(env.now)
            result = run_screening_step(p, cancer, day, self.metrics)

            if result is None:
                # Patient LTFU before test completed (e.g. lung pre-LDCT nodes)
                if p.exit_reason:
                    record_exit(self.metrics, p.exit_reason)
                return

            record_screening(self.metrics, p, cancer, result)

            # ── Cervical follow-up ─────────────────────────────────────────────
            if cancer == "cervical":
                yield from self._cervical_followup(env, p, result, resources)

            # ── Lung follow-up ─────────────────────────────────────────────────
            elif cancer == "lung":
                yield from self._lung_followup(env, p, resources)

            if p.exit_reason:
                record_exit(self.metrics, p.exit_reason)

        self._patients.append(p)

    # ── Cervical follow-up sub-process ────────────────────────────────────────

    def _cervical_followup(
        self, env: simpy.Environment, p: Patient, result: str, resources: dict
    ):
        """
        Cervical follow-up as a SimPy sub-process.
        Yields at scheduling delays and resource requests so the clock advances
        correctly between screening, colposcopy, and treatment.
        """
        # Route the result
        day        = int(env.now)
        next_step  = route_cervical_result(p, day, self.metrics)

        if next_step in ("routine_surveillance", "one_year_repeat", "exit"):
            return

        if next_step == "colposcopy":
            # Wait for colposcopy appointment slot
            yield env.timeout(cfg.FOLLOWUP_DELAY_DAYS.get("colposcopy", 30))

            colpo_resource = resources.get("colposcopy")
            if colpo_resource:
                with colpo_resource.request() as req:
                    t0 = env.now
                    yield req
                    self.metrics["wait_times"]["colposcopy"].append(env.now - t0)
                yield env.timeout(1)

            day = int(env.now)
            cin = run_colposcopy(p, day, self.metrics)
            if cin is None:
                return

            # Determine and schedule treatment
            disposition = run_treatment(p, day, self.metrics)

            if disposition not in ("exit", "surveillance") and p.treatment_type:
                ttype = p.treatment_type
                yield env.timeout(cfg.FOLLOWUP_DELAY_DAYS.get(ttype, 14))

                treat_resource = resources.get(ttype)
                if treat_resource:
                    with treat_resource.request() as req:
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
        Handles result communication, and for RADS 4 patients the biopsy wait.
        """
        day = int(env.now)
        run_lung_followup(p, day, self.metrics)

        # If biopsy was triggered (RADS 4A/4B/4X) and completed, model the wait
        if p.lung_biopsy_result is not None:
            yield env.timeout(cfg.FOLLOWUP_DELAY_DAYS.get("lung_biopsy", 14))

        # If malignancy confirmed, model the wait to treatment
        if p.lung_biopsy_result == "malignant":
            yield env.timeout(cfg.FOLLOWUP_DELAY_DAYS.get("lung_treatment", 21))

        # Required by SimPy: must yield at least once in a generator
        yield env.timeout(0)

    # ── Arrival process ───────────────────────────────────────────────────────

    def _arrival_process(
        self, env: simpy.Environment, resources: dict
    ):
        """
        Generate patients via a Poisson process.
        Mean inter-arrival time = 1 / DAILY_PATIENTS days.
        Each patient is launched as an independent SimPy process.
        """
        _dest_keys     = list(cfg.DESTINATION_PROBS.keys())
        _dest_weights  = list(cfg.DESTINATION_PROBS.values())
        _type_keys     = list(cfg.PATIENT_TYPE_PROBS.keys())
        _type_weights  = list(cfg.PATIENT_TYPE_PROBS.values())

        for patient_id in range(self.n_patients):
            # Exponential inter-arrival (Poisson process)
            inter_arrival = random.expovariate(cfg.DAILY_PATIENTS)
            yield env.timeout(inter_arrival)

            destination  = random.choices(_dest_keys,  weights=_dest_weights)[0]
            patient_type = random.choices(_type_keys,  weights=_type_weights)[0]

            p = sample_patient(patient_id, int(env.now), destination, patient_type)
            env.process(self._patient_process(env, p, resources))

    # ── Run ───────────────────────────────────────────────────────────────────

    def run(self) -> dict:
        """
        Run the full simulation.

        Returns
        -------
        metrics dict — pass to print_summary() / compute_revenue() directly,
        or use the convenience methods below.
        """
        random.seed(self.seed)
        self.metrics   = initialize_metrics()
        self._patients = []

        env       = simpy.Environment()
        resources = self._make_resources(env)

        env.process(self._arrival_process(env, resources))
        env.run(until=self.sim_days)

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
            f"NYP Screening Simulation  (n={self.metrics['n_patients']:,} patients)",
            fontsize=13, fontweight="bold",
        )

        self._plot_cervical_funnel(axes[0, 0])
        self._plot_lung_funnel(axes[0, 1])
        self._plot_rads_distribution(axes[1, 0])
        self._plot_revenue(axes[1, 1])

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
            ("Eligible",           m["n_eligible_any"]),
            ("Screened",           m["n_screened"].get("cervical", 0)),
            ("Abnormal result",    total_abnormal),
            ("Colposcopy",         m["n_colposcopy"]),
            ("Treated",            m["n_treated"]),
        ]
        labels = [s[0] for s in steps]
        values = [s[1] for s in steps]
        bars = ax.barh(labels[::-1], values[::-1], color="#4472C4")
        ax.set_title("Cervical Screening Funnel")
        ax.set_xlabel("Patients")
        _max = max(values) if values else 1
        for bar, val in zip(bars, values[::-1]):
            ax.text(
                bar.get_width() + _max * 0.01,
                bar.get_y() + bar.get_height() / 2,
                f"{val:,}", va="center", fontsize=9,
            )

    def _plot_lung_funnel(self, ax) -> None:
        m = self.metrics
        steps = [
            ("Eligible",              m["lung_eligible"]),
            ("LDCT ordered",          m["lung_referral_placed"]),
            ("LDCT completed",        m["lung_ldct_completed"]),
            ("Results communicated",  m["lung_result_communicated"]),
            ("Biopsy completed",      m["lung_biopsy_completed"]),
            ("Malignancy confirmed",  m["lung_malignancy_confirmed"]),
            ("Treatment given",       m["lung_treatment_given"]),
        ]
        labels = [s[0] for s in steps]
        values = [s[1] for s in steps]
        bars = ax.barh(labels[::-1], values[::-1], color="#ED7D31")
        ax.set_title("Lung LDCT Pathway Funnel")
        ax.set_xlabel("Patients")
        _max = max(values) if values else 1
        for bar, val in zip(bars, values[::-1]):
            ax.text(
                bar.get_width() + _max * 0.01,
                bar.get_y() + bar.get_height() / 2,
                f"{val:,}", va="center", fontsize=9,
            )

    def _plot_rads_distribution(self, ax) -> None:
        m        = self.metrics
        cats     = ["RADS_0", "RADS_1", "RADS_2", "RADS_3", "RADS_4A", "RADS_4B_4X"]
        counts   = [m["lung_rads_distribution"].get(r, 0) for r in cats]
        colors   = ["#A9D18E", "#70AD47", "#FFD966", "#F4B183", "#FF0000", "#C00000"]
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
        _max = max(vals) if vals else 1
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
