# =============================================================================
# metrics.py
# Metric collection, aggregation, and summary reporting.
# =============================================================================
#
# ROLE IN THE SIMULATION
# ─────────────────────────────────────────────────────────────────────────────
# This module is the observability layer of the simulation. It defines:
#   - The metrics dict schema (initialize_metrics) — a single dict that
#     accumulates counts and lists as events fire across all patients and days.
#   - Recording functions (record_screening, record_exit) — called inline by
#     screening.py and followup.py whenever a significant event occurs.
#   - Aggregation and reporting (compute_rates, print_summary) — convert raw
#     counts into interpretable rates and print a formatted results table.
#   - Revenue analysis (compute_revenue, print_revenue_summary) — translate
#     procedure volumes into estimated revenue for finance planning.
#
# The metrics dict is intentionally a plain Python dict (not a class) so it
# can be passed into any function without import dependencies. Every function
# in screening.py and followup.py accepts an optional `metrics` argument —
# passing None disables recording (useful for unit tests and isolated demos).
# =============================================================================

from collections import defaultdict
from typing import List

import config as cfg
from patient import Patient

# Analysis warmup cutoff — metrics recorded before this day are excluded
_WARMUP_DAY = cfg.WARMUP_YEARS * cfg.DAYS_PER_YEAR


def initialize_metrics() -> dict:
    """
    Create and return a fresh metrics dictionary for one simulation run.

    The dict is structured into logical groups:
      - Volume counters: how many patients were seen, eligible, or unscreened.
      - Screening counts: per-cancer screening totals, cervical result distributions.
      - Cervical follow-up: colposcopy counts, CIN grade distribution, treatment types.
      - Outcomes: treated, LTFU totals.
      - LTFU breakdown: how many patients were lost at each specific node.
      - Lung pathway funnel: step-by-step counts from referral through treatment.
      - Wait times: lists of days waited at each resource (for scheduling analysis).

    Call this at the start of each simulation replication so state doesn't carry
    over between runs.
    """
    return {
        # ── Volume ────────────────────────────────────────────────────────────
        "n_patients":     0,
        "n_eligible_any": 0,   # eligible for ≥1 cancer (any)
        "n_eligible":     defaultdict(int),  # cancer → eligible count (per-cancer)
        "n_unscreened":   0,
        "n_reschedule":   0,

        # ── Entry / Arrival breakdown ─────────────────────────────────────────
        # destination: "pcp" | "gynecologist" | "specialist" | "er"
        # patient_type: "outpatient" | "drop_in"
        "entries_by_destination": defaultdict(int),   # provider destination → count
        "entries_by_type":        defaultdict(int),   # patient_type → count

        # ── Exit / Retention breakdown ────────────────────────────────────────
        "exits_by_reason":    defaultdict(int),   # exit_reason string → count
        "days_in_system":     [],                 # list of ints (retention days per patient)
        "days_in_system_screened": [],             # same but only patients with visit_count > 0

        # ── Screenings ────────────────────────────────────────────────────────
        "n_screened":             defaultdict(int),                 # cancer → count (all patients)
        "n_screened_established": defaultdict(int),                # cancer → count (established pool only)
        "n_screened_by_test":     defaultdict(int),                # test modality → count (cytology / hpv_alone / ldct)

        # ── Cervical results ──────────────────────────────────────────────────
        "cervical_results": defaultdict(int),                      # result → count
        "cervical_results_by_test": defaultdict(                   # test → result → count
            lambda: defaultdict(int)                               # e.g. cervical_results_by_test["cytology"]["ASCUS"]
        ),
        "cervical_by_age_stratum": defaultdict(                    # stratum → result → count
            lambda: defaultdict(int)
        ),

        # ── Cervical follow-up ────────────────────────────────────────────────
        "n_colposcopy":       0,
        "colposcopy_results": defaultdict(int),                    # CIN grade → count
        "n_treatment":        defaultdict(int),                    # treatment type → count

        # ── Outcomes ──────────────────────────────────────────────────────────
        "n_treated":   0,
        "n_ltfu":      0,
        "n_exited":    0,

        # ── LTFU by node ──────────────────────────────────────────────────────
        "ltfu_unscreened":          0,
        "ltfu_queue_primary":       0,   # abandoned primary screening retry queue
        "ltfu_queue_secondary":     0,   # abandoned secondary (colposcopy/biopsy) retry queue
        "ltfu_queue_treatment":     0,   # abandoned treatment (LEEP/cone) retry queue

        # ── Lung pathway funnel ───────────────────────────────────────────────
        "lung_eligible":            0,
        "lung_referral_placed":     0,
        "lung_ldct_scheduled":      0,
        "lung_ldct_completed":      0,
        "lung_rads_distribution":   defaultdict(int),   # RADS category → count
        "lung_result_communicated": 0,
        "lung_biopsy_referral":     0,
        "lung_biopsy_scheduled":    0,
        "lung_biopsy_completed":    0,
        "lung_malignancy_confirmed": 0,
        "lung_treatment_given":     0,

        # ── Wait times (days, by resource) ────────────────────────────────────
        "wait_times": defaultdict(list),
        # Abandoned waits: patients who died/attrited while queued for a slot
        "wait_times_abandoned": defaultdict(list),

        # ── Daily screening demand vs capacity ────────────────────────────────
        # Each entry is one workday: (demand, supplied, overflow)
        # demand = patients who attempted a primary screening slot
        # supplied = slots consumed (screening happened)
        # overflow = patients rescheduled (no slot available)
        "daily_screening_demand": [],
        "daily_secondary_demand": [],    # colposcopy + lung_biopsy
        "daily_treatment_demand": [],    # leep + cone_biopsy

        # ── Stable population (only populated when use_stable_population=True) ─
        "mortality_count":    0,   # total patients removed by mortality events
        "pool_size_snapshot": [],  # (day, pool_size) snapshots for longitudinal plot

        # ── Multi-source tracking ─────────────────────────────────────────────
        "arrivals_by_source":  defaultdict(int),   # arrival source → count
        "exits_by_source":     defaultdict(int),   # exit source/subtype → count
        # ── Annual checkpoints (one dict per year, for longitudinal plots) ──────
        # Each entry: {year, day, pool_size, cum_cervical, cum_lung,
        #              cum_mortality, cum_colposcopy, cum_treated}
        "year_checkpoints": [],
    }


def record_screening(
    metrics: dict, p: Patient, cancer: str, result: str,
    test: str = "", current_day: int = 0
) -> None:
    """
    Record a completed screening event in the metrics dict.

    Events during the warmup period (day < WARMUP_YEARS * 365) are silently
    skipped — they still happen in the simulation but are excluded from
    analysis metrics.

    Increments the per-cancer screening counter and, for cervical screenings,
    also tallies the result category and the age-stratum breakdown. The stratum
    breakdown is used to verify that the simulation's result distribution matches
    expected rates for young vs. middle-aged women separately.

    The optional `test` parameter (e.g. "cytology", "hpv_alone", "ldct") is used
    to track first-stage screening volume by modality — the primary USPSTF metric.
    Falls back to the patient's last recorded test if not explicitly provided.
    """
    if current_day < _WARMUP_DAY:
        return
    from screening import get_cervical_age_stratum   # local import to avoid circularity
    metrics["n_screened"][cancer] += 1
    if getattr(p, "is_established", False):
        metrics["n_screened_established"][cancer] += 1

    # Track by test modality — infer from patient if not supplied
    if not test and cancer == "cervical":
        test = getattr(p, "last_cervical_screening_test", "") or "cytology"
    elif not test and cancer == "lung":
        test = "ldct"
    if test:
        metrics["n_screened_by_test"][test] += 1

    if cancer == "cervical":
        metrics["cervical_results"][result] += 1
        metrics["cervical_results_by_test"][test][result] += 1
        stratum = get_cervical_age_stratum(p.age)
        metrics["cervical_by_age_stratum"][stratum][result] += 1


def record_exit(metrics: dict, reason: str, patient=None, current_day: int = 0) -> None:
    """
    Record a patient's exit from the system and classify it into an outcome bucket.

    Events during the warmup period (day < WARMUP_YEARS * 365) are silently
    skipped — they still happen in the simulation but are excluded from
    analysis metrics.

    Called whenever a patient's pathway ends, whether through successful treatment,
    voluntary departure without treatment, or LTFU. The reason string comes from
    patient.exit_reason (set by patient.exit_system()) and maps to one of three
    outcome counters: treated or lost_to_followup.

    Optional patient and current_day are used to record retention duration
    (days from patient creation to exit) in metrics["days_in_system"].
    """
    if current_day < _WARMUP_DAY:
        return
    metrics["n_exited"] += 1
    metrics["exits_by_reason"][reason] += 1

    if reason == "treated":
        metrics["n_treated"] += 1
    elif reason == "lost_to_followup":
        metrics["n_ltfu"] += 1

    if patient is not None and current_day > 0:
        retention = current_day - getattr(patient, "day_created", current_day)
        if retention >= 0:
            metrics["days_in_system"].append(retention)
            if getattr(patient, "visit_count", 0) >= 2:
                metrics["days_in_system_screened"].append(retention)


def compute_rates(metrics: dict) -> dict:
    """
    Derive key percentage rates from the raw event counts in the metrics dict.

    Converts raw counts into the rates that appear in the summary report:
    screening rate, abnormal rate, colposcopy completion rate, treatment
    completion rate, and overall LTFU rate. Uses max(..., 1) denominators to
    avoid division-by-zero in runs where a particular event never occurred.

    Notes on correctness:
    - "Abnormal" for cervical means any result that triggers a follow-up action:
      ASCUS, LSIL, ASC-H, HSIL, and HPV_POSITIVE. HPV_NEGATIVE is excluded
      because it is a normal result (patient does not carry high-risk HPV).
    - treatment_completion_pct uses only cervical excisional treatments (LEEP /
      cone biopsy) as the numerator, NOT n_treated, because n_treated also
      accumulates lung malignancy treatments and would produce a rate > 100%
      against the cervical-only colposcopy denominator.
    """
    n     = max(metrics["n_patients"], 1)
    cerv  = max(metrics["n_screened"]["cervical"], 1)
    colpo = max(metrics["n_colposcopy"], 1)

    # Abnormal cervical result: any category that requires follow-up action.
    # HPV_NEGATIVE is explicitly excluded — it is a normal HPV-alone result.
    # NORMAL is excluded too. Everything else (ASCUS, LSIL, ASC-H, HSIL,
    # HPV_POSITIVE) triggers either colposcopy or a 1-year repeat.
    _NORMAL_CERVICAL = {"NORMAL", "HPV_NEGATIVE"}
    total_abnormal = sum(
        v for k, v in metrics["cervical_results"].items()
        if k not in _NORMAL_CERVICAL
    )

    # Cervical excisional treatment count (LEEP + cone biopsy).
    # Used as the numerator for treatment_completion_pct to keep the rate
    # within [0, 100%] against the colposcopy denominator.
    cerv_excisional = (
        metrics["n_treatment"].get("leep", 0)
        + metrics["n_treatment"].get("cone_biopsy", 0)
    )

    n_cytol    = metrics["n_screened_by_test"]["cytology"]
    n_hpv      = metrics["n_screened_by_test"]["hpv_alone"]
    n_ldct     = metrics["n_screened_by_test"]["ldct"]
    total_cerv = max(n_cytol + n_hpv, 1)

    return {
        # ── First-stage screening uptake (primary USPSTF metric) ───────────
        "n_cytology":                  n_cytol,
        "n_hpv_alone":                 n_hpv,
        "n_ldct":                      n_ldct,
        "cytology_pct_of_cerv":        100 * n_cytol / total_cerv,
        "hpv_alone_pct_of_cerv":       100 * n_hpv   / total_cerv,
        "screening_rate_cervical_pct": 100 * metrics["n_screened"]["cervical"] / n,
        "screening_rate_lung_pct":     100 * metrics["n_screened"]["lung"] / max(metrics["lung_eligible"], 1),
        # ── Downstream clinical rates (validation / secondary) ─────────────
        "unscreened_pct":              100 * metrics["n_unscreened"] / n,
        "reschedule_rate_pct":         100 * metrics["n_reschedule"] / max(metrics["n_unscreened"], 1),
        "abnormal_rate_cervical_pct":  100 * total_abnormal / cerv,
        "colposcopy_completion_pct":   100 * metrics["n_colposcopy"] / max(total_abnormal, 1),
        # Fraction of colposcopy patients who went on to excisional treatment.
        # Patients with CIN1/NORMAL colposcopy result are placed on surveillance
        # and are correctly excluded from this numerator.
        "treatment_completion_pct":    100 * cerv_excisional / max(colpo, 1),
        "ltfu_rate_pct":               100 * metrics["n_ltfu"] / n,
    }


def print_summary(metrics: dict) -> None:
    """
    Print a formatted summary of the simulation results to stdout.

    Covers all major pipeline sections in order: patient volumes, screening
    counts by cancer, cervical result distribution (with age-stratum breakdown),
    colposcopy and treatment counts, outcome totals, LTFU breakdown by node,
    and the full lung LDCT pathway funnel. Calls compute_rates() internally
    to derive the percentage columns.
    """
    rates = compute_rates(metrics)

    print("=" * 65)
    print("NYP WOMEN'S HEALTH SCREENING SIMULATION — RESULTS")
    print("=" * 65)

    print(f"\n{'Patients simulated:':<40} {metrics['n_patients']:>8,}")
    print(f"{'Eligible for ≥1 screening:':<40} {metrics['n_eligible_any']:>8,}")
    print(f"{'Unscreened (declined / no-show):':<40} {metrics['n_unscreened']:>8,}  "
          f"({rates['unscreened_pct']:.1f}%)")
    print(f"{'  ↳ agreed to reschedule:':<40} {metrics['n_reschedule']:>8,}  "
          f"({rates['reschedule_rate_pct']:.1f}% of unscreened)")

    print("\nScreenings completed by cancer type:")
    for cancer, count in sorted(metrics["n_screened"].items()):
        print(f"  {cancer:<22} {count:>8,}")

    if metrics["cervical_results"]:
        total_cerv = sum(metrics["cervical_results"].values())
        print(f"\nCervical result distribution  (n={total_cerv:,}):")
        for result, count in sorted(metrics["cervical_results"].items()):
            pct = 100 * count / max(total_cerv, 1)
            print(f"  {result:<30} {count:>6,}  ({pct:.1f}%)")
        print(f"  {'Abnormal rate:':<30} {rates['abnormal_rate_cervical_pct']:.1f}%")

    if metrics["cervical_by_age_stratum"]:
        print("\nCervical results by age stratum:")
        for stratum in ("young", "middle", "older"):
            sub = metrics["cervical_by_age_stratum"].get(stratum, {})
            if sub:
                total_s = sum(sub.values())
                print(f"  {stratum}  (n={total_s:,})")
                for result, count in sorted(sub.items()):
                    print(f"    {result:<28} {count:>6,}")

    print(f"\nColposcopies performed:           {metrics['n_colposcopy']:>8,}  "
          f"({rates['colposcopy_completion_pct']:.1f}% of abnormals)")
    if metrics["colposcopy_results"]:
        print("  CIN grade distribution:")
        for grade, count in sorted(metrics["colposcopy_results"].items()):
            print(f"    {grade:<12} {count:>6,}")

    if metrics["n_treatment"]:
        print("\nTreatments by type:")
        for ttype, count in sorted(metrics["n_treatment"].items()):
            print(f"  {ttype:<22} {count:>8,}")

    print(f"\nOutcomes:")
    print(f"  {'Treated:':<38} {metrics['n_treated']:>6,}  "
          f"({rates['treatment_completion_pct']:.1f}% of colposcopies)")
    print(f"  {'Lost to follow-up:':<38} {metrics['n_ltfu']:>6,}  "
          f"({rates['ltfu_rate_pct']:.1f}% of all patients)")

    print(f"\nLTFU breakdown:")
    print(f"  {'Declined screening:':<38} {metrics['ltfu_unscreened']:>6,}")
    print(f"  {'Queue — primary screening:':<38} {metrics['ltfu_queue_primary']:>6,}")
    print(f"  {'Queue — secondary (colpo/biopsy):':<38} {metrics['ltfu_queue_secondary']:>6,}")
    print(f"  {'Queue — treatment (LEEP/cone):':<38} {metrics['ltfu_queue_treatment']:>6,}")

    if metrics["lung_eligible"] > 0:
        print(f"\nLung LDCT pathway funnel:")
        steps = [
            ("Eligible (USPTF: age 50-80, ≥20 pk-yrs)", "lung_eligible"),
            ("LDCT order placed",                        "lung_referral_placed"),
            ("LDCT appointment scheduled",               "lung_ldct_scheduled"),
            ("LDCT completed",                           "lung_ldct_completed"),
            ("Results communicated to patient",          "lung_result_communicated"),
            ("Biopsy referral made (RADS 4)",            "lung_biopsy_referral"),
            ("Biopsy scheduled",                         "lung_biopsy_scheduled"),
            ("Biopsy completed",                         "lung_biopsy_completed"),
            ("Malignancy confirmed",                     "lung_malignancy_confirmed"),
            ("Treatment given",                          "lung_treatment_given"),
        ]
        prev = max(metrics["lung_eligible"], 1)
        for label, key in steps:
            val  = metrics[key]
            drop = f"  (↓{100*(1-val/prev):.0f}%)" if prev > 0 and key != "lung_eligible" else ""
            print(f"  {label:<45} {val:>6,}{drop}")
            prev = max(val, 1)

        if metrics["lung_rads_distribution"]:
            print(f"\n  Lung-RADS distribution (of completed LDCTs):")
            total_ldct = max(metrics["lung_ldct_completed"], 1)
            for rads in ["RADS_0","RADS_1","RADS_2","RADS_3","RADS_4A","RADS_4B_4X"]:
                cnt = metrics["lung_rads_distribution"].get(rads, 0)
                print(f"    {rads:<12} {cnt:>5,}  ({100*cnt/total_ldct:.1f}%)")


    print("=" * 65)


def print_patient_trace(patients: List[Patient], n: int = 5) -> None:
    """
    Print the full event log for the first n patients in the list.

    Each patient's log is a chronological list of (day, event_string) tuples
    recorded by patient.log() throughout the simulation. This is the primary
    tool for verifying that the clinical logic is flowing correctly — reading
    a trace makes it immediately obvious if a step fired out of order or a
    patient ended up in an unexpected state.
    """
    for p in patients[:n]:
        p.print_history()


# =============================================================================
# Revenue Analysis
# =============================================================================

def compute_revenue(metrics: dict) -> dict:
    """
    Calculate realized and foregone procedure revenue from a completed simulation run.

    Realized revenue  — billed for procedures that actually occurred.
    Foregone revenue  — lost because patients dropped out at a LTFU node.
                        Foregone amounts are the *minimum* revenue lost (screening
                        only); downstream cascade (e.g. missed LEEP after missed
                        colposcopy) is reported separately in foregone_cascade.

    All rates are PLACEHOLDERS — replace with NYP finance / contract data.
    Set individual values in config.PROCEDURE_REVENUE.

    Returns
    -------
    dict with keys:
        realized_total        : float
        foregone_total        : float
        realized_by_procedure : dict[str, float]
        foregone_by_node      : dict[str, float]
    """
    import config as cfg
    rev = cfg.PROCEDURE_REVENUE

    # ── Realized revenue ──────────────────────────────────────────────────────
    realized = {
        # Cervical screenings — split by test type not tracked separately in
        # metrics, so use average of cytology + hpv_alone as a proxy.
        # For exact split, add per-test counter to initialize_metrics().
        "cervical_screening": (
            metrics["n_screened"].get("cervical", 0)
            * (rev["cytology"] + rev["hpv_alone"]) / 2
        ),
        "colposcopy":  metrics["n_colposcopy"] * rev["colposcopy"],
        "leep":        metrics["n_treatment"].get("leep", 0)        * rev["leep"],
        "cone_biopsy": metrics["n_treatment"].get("cone_biopsy", 0) * rev["cone_biopsy"],

        # Lung
        "ldct":           metrics["lung_ldct_completed"]       * rev["ldct"],
        "lung_biopsy":    metrics["lung_biopsy_completed"]     * rev["lung_biopsy"],
        "lung_treatment": metrics["lung_treatment_given"]      * rev["lung_treatment"],
    }
    realized_total = sum(realized.values())

    # ── Foregone revenue ──────────────────────────────────────────────────────
    # Each node: patients who dropped out × revenue of the missed procedure
    # (+ a conservative estimate of one downstream procedure where applicable).

    # Use per-cancer eligible count so lung-only-eligible patients do not
    # inflate the cervical denominator.  n_eligible_any would overstate foregone
    # cervical revenue whenever lung-only patients exist.
    cervical_eligible  = metrics["n_eligible"].get("cervical", 0)
    cervical_screened  = metrics["n_screened"].get("cervical", 0)
    avg_cerv_screen    = (rev["cytology"] + rev["hpv_alone"]) / 2

    # How many abnormal cervical results were there?
    total_abnormal = sum(
        v for k, v in metrics["cervical_results"].items()
        if k not in ("NORMAL", "HPV_NEGATIVE")
    )

    # Lung pathway clinical dropoffs
    lung_eligible      = metrics.get("lung_eligible", 0)
    lung_ldct_done     = metrics.get("lung_ldct_completed", 0)
    lung_biopsy_ref    = metrics.get("lung_biopsy_referral", 0)
    lung_biopsy_done   = metrics.get("lung_biopsy_completed", 0)

    foregone = {
        # Unscreened — eligible patients who declined / no-showed
        "unscreened": (
            metrics.get("ltfu_unscreened", 0) * avg_cerv_screen
        ),
        # Queue LTFU — primary screening queue
        "queue_ltfu_primary": (
            metrics["ltfu_queue_primary"] * avg_cerv_screen
        ),
        # Queue LTFU — diagnostic queue (colposcopy / biopsy)
        "queue_ltfu_secondary": (
            metrics["ltfu_queue_secondary"] * rev["colposcopy"]
        ),
        # Lung clinical LTFU — eligible but never completed LDCT
        "lung_screening_ltfu": (
            max(lung_eligible - lung_ldct_done, 0) * rev["ldct"]
        ),
        # Lung biopsy LTFU — referred for biopsy but never completed
        "lung_biopsy_ltfu": (
            max(lung_biopsy_ref - lung_biopsy_done, 0) * rev["lung_biopsy"]
        ),
    }
    foregone_total = sum(foregone.values())

    # ── Uncaptured population revenue ────────────────────────────────────────
    # Eligible NYC women who are NOT in the patient pool at all.
    # These women never entered the system — distinct from LTFU (entered but
    # dropped out).  Estimated from the gap between NYC_ELIGIBLE_POPULATION
    # and the average pool size × POPULATION_SCALE_FACTOR.
    #
    # Revenue estimate: each uncaptured woman represents one missed screening
    # per screening interval (cervical every 3 yrs, lung every 1 yr).
    # We annualize the missed screening revenue.

    # Get average pool size from year-end checkpoints
    year_chk = metrics.get("year_checkpoints", [])
    pool_sizes = [c.get("pool_size", 0) for c in year_chk if c.get("pool_size") is not None]
    avg_pool_sim = sum(pool_sizes) / len(pool_sizes) if pool_sizes else 0
    avg_pool_real = avg_pool_sim * cfg.POPULATION_SCALE_FACTOR

    uncaptured_women = max(cfg.NYC_ELIGIBLE_POPULATION - avg_pool_real, 0)

    # Annualized missed screening revenue per uncaptured woman:
    # - Cervical: eligible ages 21-65; screened every 3 years → 1/3 per year
    # - Lung: eligible ages 50-80, smokers only; screened annually
    # Use population prevalence estimates for eligibility fractions
    cerv_eligible_frac = 0.80    # PLACEHOLDER — fraction of eligible-age women due for cervical
    lung_eligible_frac = 0.04    # PLACEHOLDER — fraction meeting lung screening criteria
    cerv_interval_yrs  = 3.0
    lung_interval_yrs  = 1.0

    uncaptured_annual_cerv = (
        uncaptured_women * cerv_eligible_frac / cerv_interval_yrs * avg_cerv_screen
    )
    uncaptured_annual_lung = (
        uncaptured_women * lung_eligible_frac / lung_interval_yrs * rev["ldct"]
    )
    uncaptured_annual_total = uncaptured_annual_cerv + uncaptured_annual_lung

    # Scale to simulation duration (post-warmup years)
    sim_years = max((cfg.SIM_YEARS - cfg.WARMUP_YEARS), 1)
    uncaptured_total = uncaptured_annual_total * sim_years

    uncaptured = {
        "cervical_screening": uncaptured_annual_cerv * sim_years,
        "lung_ldct":          uncaptured_annual_lung * sim_years,
    }

    return {
        "realized_total":        realized_total,
        "foregone_total":        foregone_total,
        "realized_by_procedure": realized,
        "foregone_by_node":      foregone,
        # Population capture
        "uncaptured_total":      uncaptured_total,
        "uncaptured_by_cancer":  uncaptured,
        "uncaptured_women":      uncaptured_women,
        "avg_pool_real":         avg_pool_real,
        "nyc_eligible":          cfg.NYC_ELIGIBLE_POPULATION,
        "capture_rate":          avg_pool_real / max(cfg.NYC_ELIGIBLE_POPULATION, 1),
    }


def print_revenue_summary(metrics: dict) -> None:
    """
    Print a formatted revenue summary showing realized vs. foregone procedure revenue.

    Calls compute_revenue() to translate procedure volume counts into dollar amounts
    using the CPT-based rates in config.PROCEDURE_REVENUE, then prints two sections:
      - Realized revenue: procedures that actually occurred (screening, colposcopy, LEEP, LDCT, etc.)
      - Foregone revenue: revenue lost because patients dropped out at LTFU nodes

    Also prints the revenue capture rate (realized / total addressable) so it is
    immediately clear what fraction of potential revenue was actually collected.
    All dollar amounts use PLACEHOLDER CPT rates — replace with NYP contract rates.
    """
    r = compute_revenue(metrics)

    print("\n" + "=" * 65)
    print("REVENUE ANALYSIS  (PLACEHOLDER CPT rates — replace with NYP data)")
    print("=" * 65)

    print("\nRealized revenue (procedures completed):")
    for proc, amt in r["realized_by_procedure"].items():
        if amt > 0:
            print(f"  {proc:<30} ${amt:>12,.0f}")
    print(f"  {'TOTAL':<30} ${r['realized_total']:>12,.0f}")

    print("\nForegone revenue (lost to LTFU / unscreened):")
    for node, amt in r["foregone_by_node"].items():
        if amt > 0:
            print(f"  {node:<30} ${amt:>12,.0f}")
    print(f"  {'TOTAL':<30} ${r['foregone_total']:>12,.0f}")

    total = r["realized_total"] + r["foregone_total"]
    if total > 0:
        pct_lost = 100 * r["foregone_total"] / total
        print(f"\n  Revenue capture rate: {100 - pct_lost:.1f}%  "
              f"({pct_lost:.1f}% foregone)")
    print("=" * 65)
