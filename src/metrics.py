# =============================================================================
# metrics.py
# Metric collection, aggregation, and summary reporting.
# =============================================================================

from collections import defaultdict
from typing import List

from patient import Patient


def initialize_metrics() -> dict:
    """Return a fresh metrics dictionary for one simulation run."""
    return {
        # ── Volume ────────────────────────────────────────────────────────────
        "n_patients":     0,
        "n_eligible_any": 0,
        "n_unscreened":   0,
        "n_reschedule":   0,

        # ── Screenings ────────────────────────────────────────────────────────
        "n_screened": defaultdict(int),                            # cancer → count

        # ── Cervical results ──────────────────────────────────────────────────
        "cervical_results": defaultdict(int),                      # result → count
        "cervical_by_age_stratum": defaultdict(                    # stratum → result → count
            lambda: defaultdict(int)
        ),

        # ── Cervical follow-up ────────────────────────────────────────────────
        "n_colposcopy":       0,
        "colposcopy_results": defaultdict(int),                    # CIN grade → count
        "n_treatment":        defaultdict(int),                    # treatment type → count

        # ── Outcomes ──────────────────────────────────────────────────────────
        "n_treated":   0,
        "n_untreated": 0,
        "n_ltfu":      0,
        "n_exited":    0,

        # ── LTFU by node ──────────────────────────────────────────────────────
        "ltfu_post_abnormal":   0,
        "ltfu_post_colposcopy": 0,
        "ltfu_unscreened":      0,

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
    }


def record_screening(
    metrics: dict, p: Patient, cancer: str, result: str
) -> None:
    """Record a completed screening event."""
    from screening import get_cervical_age_stratum   # local import to avoid circularity
    metrics["n_screened"][cancer] += 1
    if cancer == "cervical":
        metrics["cervical_results"][result] += 1
        stratum = get_cervical_age_stratum(p.age)
        metrics["cervical_by_age_stratum"][stratum][result] += 1


def record_exit(metrics: dict, reason: str) -> None:
    """Record a patient exit and classify outcome."""
    metrics["n_exited"] += 1
    if reason == "treated":
        metrics["n_treated"] += 1
    elif reason == "untreated":
        metrics["n_untreated"] += 1
    elif reason == "lost_to_followup":
        metrics["n_ltfu"] += 1


def compute_rates(metrics: dict) -> dict:
    """Derive key percentage rates from raw counts."""
    n     = max(metrics["n_patients"], 1)
    cerv  = max(metrics["n_screened"]["cervical"], 1)
    colpo = max(metrics["n_colposcopy"], 1)

    total_abnormal = sum(
        v for k, v in metrics["cervical_results"].items() if k != "NORMAL"
    )

    return {
        "screening_rate_cervical_pct": 100 * metrics["n_screened"]["cervical"] / n,
        "unscreened_pct":              100 * metrics["n_unscreened"] / n,
        "reschedule_rate_pct":         100 * metrics["n_reschedule"] / max(metrics["n_unscreened"], 1),
        "abnormal_rate_cervical_pct":  100 * total_abnormal / cerv,
        "colposcopy_completion_pct":   100 * metrics["n_colposcopy"] / max(total_abnormal, 1),
        "treatment_completion_pct":    100 * metrics["n_treated"] / max(colpo, 1),
        "ltfu_rate_pct":               100 * metrics["n_ltfu"] / n,
    }


def print_summary(metrics: dict) -> None:
    """Print a formatted simulation summary to stdout."""
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
    print(f"  {'Untreated:':<38} {metrics['n_untreated']:>6,}")
    print(f"  {'Lost to follow-up:':<38} {metrics['n_ltfu']:>6,}  "
          f"({rates['ltfu_rate_pct']:.1f}% of all patients)")

    print(f"\nLTFU breakdown:")
    print(f"  {'Post-abnormal screen:':<38} {metrics['ltfu_post_abnormal']:>6,}")
    print(f"  {'Post-colposcopy:':<38} {metrics['ltfu_post_colposcopy']:>6,}")
    print(f"  {'Declined screening:':<38} {metrics['ltfu_unscreened']:>6,}")

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
    """Print event logs for the first n patients (for debugging)."""
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

    cervical_eligible  = metrics["n_eligible_any"]
    cervical_screened  = metrics["n_screened"].get("cervical", 0)
    avg_cerv_screen    = (rev["cytology"] + rev["hpv_alone"]) / 2

    # How many abnormal cervical results were there?
    total_abnormal = sum(
        v for k, v in metrics["cervical_results"].items()
        if k not in ("NORMAL", "HPV_NEGATIVE")
    )

    foregone = {
        # Eligible cervical patients who were never screened
        "unscreened_cervical": (
            max(cervical_eligible - cervical_screened, 0) * avg_cerv_screen
        ),
        # Abnormal result but LTFU before colposcopy
        "ltfu_post_abnormal_cervical": (
            metrics["ltfu_post_abnormal"] * (rev["colposcopy"] + rev["leep"] * 0.3)
            # 0.3 ≈ fraction of colposcopies that lead to excisional treatment
        ),
        # Completed colposcopy but LTFU before treatment
        "ltfu_post_colposcopy": (
            metrics["ltfu_post_colposcopy"] * rev["leep"]
        ),

        # Lung: eligible but no LDCT order placed
        "lung_no_ldct": (
            max(
                metrics["lung_eligible"] - metrics["lung_referral_placed"], 0
            ) * rev["ldct"]
        ),
        # LDCT completed but RADS 4 result not followed up with biopsy
        "lung_no_biopsy": (
            max(
                metrics["lung_biopsy_referral"] - metrics["lung_biopsy_completed"], 0
            ) * rev["lung_biopsy"]
        ),
    }
    foregone_total = sum(foregone.values())

    return {
        "realized_total":        realized_total,
        "foregone_total":        foregone_total,
        "realized_by_procedure": realized,
        "foregone_by_node":      foregone,
    }


def print_revenue_summary(metrics: dict) -> None:
    """Print a formatted revenue summary to stdout."""
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
