# =============================================================================
# stats_extract.py
# Output-metric extraction for the simulation.
#
# One source of truth for every scalar output the team reviews in the
# "COMPREHENSIVE STATISTICAL INFERENCE" table. Used by:
#   - notebooks/simulation.ipynb  → render_stats_table() prints the table
#   - experiments/sensitivity/     → compute_output_metrics() returns a flat
#                                     dict per run, fed into elasticity calcs
#
# If the team adds a new row to the stats table, update compute_output_metrics
# here and the sensitivity analysis will automatically pick it up.
# =============================================================================

from typing import Any, Dict, List

import numpy as np


# =============================================================================
# Shared helpers
# =============================================================================

def post_warmup_checkpoints(ckpts: list, warmup_years: int) -> list:
    """Filter year_checkpoints list to years >= warmup_years."""
    return [cp for cp in ckpts if cp.get("year", 0) >= warmup_years]


def _annual(cum_key: str, ckpts: list) -> List[float]:
    """Year-over-year deltas from a cumulative checkpoint key."""
    vals = [cp.get(cum_key, 0) for cp in ckpts]
    return [vals[i] - vals[i - 1] for i in range(1, len(vals))]


def _mean(arr: list) -> float:
    """Mean of finite values; NaN if empty."""
    a = np.array(arr, dtype=float)
    a = a[np.isfinite(a)]
    return float(np.mean(a)) if len(a) > 0 else float("nan")


def _pct(numer: float, denom: float) -> float:
    """Percentage with safe divide (0 if denom is 0)."""
    return 100.0 * numer / denom if denom > 0 else 0.0


def _wait_mean(wt_dict: dict, key: str) -> float:
    """Mean wait-time for a given procedure key (NaN if empty)."""
    vals = wt_dict.get(key, [])
    return float(np.mean(vals)) if vals else float("nan")


def _wait_median(wt_dict: dict, key: str) -> float:
    vals = wt_dict.get(key, [])
    return float(np.median(vals)) if vals else float("nan")


# =============================================================================
# The scalar output extractor (used by SA)
# =============================================================================

def compute_output_metrics(
    metrics: dict,
    ckpts: list,
    n_workdays: int,
    capacities: dict,
    scale_factor: int,
) -> Dict[str, float]:
    """
    Extract a flat dict of ~50 scalar outputs from a simulation run.

    Parameters
    ----------
    metrics       : the `metrics` dict returned by SimulationRunner.run()
    ckpts         : post-warmup year checkpoints (from post_warmup_checkpoints)
    n_workdays    : count of post-warmup workdays in the sim window
    capacities    : cfg.CAPACITIES dict (passed in so the function is pure)
    scale_factor  : cfg.POPULATION_SCALE_FACTOR (for mortality extrapolation)

    Returns
    -------
    dict[str, float]  namespaced by the 9 sections of the stats table:
        pop.*         population & pool
        queue.*       queues & capacities
        cervical.*    primary cervical screening
        lung.*        primary lung screening
        colposcopy.*  secondary cervical
        lung_bx.*     secondary lung biopsy
        ltfu.*        loss-to-follow-up
        treatment.*   treatments completed
        mortality.*   mortality
    """
    m = metrics
    n_years = max(1, len(ckpts) - 1)
    out: Dict[str, float] = {}

    # ── Annual series (needed in multiple sections) ──────────────────────────
    pool_sizes = [cp["pool_size"] for cp in ckpts if cp.get("pool_size") is not None]
    mort_annual = _annual("cum_mortality", ckpts)
    pts_annual = _annual("cum_n_patients", ckpts)
    cerv_annual = _annual("cum_cervical_est", ckpts)
    lung_annual = _annual("cum_lung_est", ckpts)
    cyto_annual = _annual("cum_cytology", ckpts)
    hpv_annual = _annual("cum_hpv_alone", ckpts)
    ldct_annual = _annual("cum_ldct", ckpts)
    colpo_annual = _annual("cum_colposcopy", ckpts)
    leep_annual = _annual("cum_leep", ckpts)
    lung_bx_annual = _annual("cum_lung_biopsy", ckpts)
    lung_tx_annual = _annual("cum_lung_treatment", ckpts)
    ltfu_annual = _annual("cum_ltfu", ckpts)

    # ── Section 1: POPULATION & POOL ─────────────────────────────────────────
    out["pop.pool_size_mean"] = _mean(pool_sizes)
    out["pop.annual_mortality_mean"] = _mean(mort_annual)
    out["pop.annual_throughput_mean"] = _mean(pts_annual)

    arr_src = m.get("arrivals_by_source", {})
    total_arr = sum(arr_src.values())
    for src in ("aging_in", "new_mover", "referral"):
        out[f"pop.arrivals.{src}_share_pct"] = _pct(arr_src.get(src, 0), total_arr)
    out["pop.arrivals.total"] = total_arr

    exit_rsn = dict(m.get("exits_by_reason", {}))
    total_exits = sum(exit_rsn.values())
    out["pop.exits.total"] = total_exits

    # ── Section 2: QUEUES & CAPACITIES ───────────────────────────────────────
    proc_used = m.get("procedure_used", {})
    proc_overflow = m.get("procedure_overflow", {})
    for proc in ("cytology", "hpv_alone", "ldct", "colposcopy", "leep",
                 "cone_biopsy", "lung_biopsy"):
        used = proc_used.get(proc, 0)
        ovfl = proc_overflow.get(proc, 0)
        cap = capacities.get(proc, 0)
        daily = used / max(n_workdays, 1)
        out[f"queue.util.{proc}_pct"] = _pct(daily, cap) if cap > 0 else 0.0
        out[f"queue.overflow.{proc}_total"] = ovfl

    primary_cap = sum(capacities.get(k, 0) for k in ("cytology", "hpv_alone", "co_test", "ldct"))
    secondary_cap = sum(capacities.get(k, 0) for k in ("colposcopy", "lung_biopsy"))
    treatment_cap = sum(capacities.get(k, 0) for k in ("leep", "cone_biopsy"))

    dsd = m.get("daily_screening_demand", [])
    dsd2 = m.get("daily_secondary_demand", [])
    dsd3 = m.get("daily_treatment_demand", [])

    def _queue_stats(demand_list, cap, prefix):
        if not demand_list:
            out[f"{prefix}.demand_mean"] = float("nan")
            out[f"{prefix}.overflow_mean"] = float("nan")
            out[f"{prefix}.overcap_pct"] = float("nan")
            return
        dem = np.array([d[0] for d in demand_list])
        ovf = np.array([d[2] for d in demand_list])
        out[f"{prefix}.demand_mean"] = float(dem.mean())
        out[f"{prefix}.overflow_mean"] = float(ovf.mean())
        out[f"{prefix}.overcap_pct"] = _pct((dem > cap).sum(), len(dem))

    _queue_stats(dsd, primary_cap, "queue.primary")
    _queue_stats(dsd2, secondary_cap, "queue.secondary")
    _queue_stats(dsd3, treatment_cap, "queue.treatment")

    wt = m.get("wait_times", {})
    wta = m.get("wait_times_abandoned", {})
    for key in ("cytology", "hpv_alone", "co_test", "ldct",
                "colposcopy", "one_year_repeat", "lung_biopsy",
                "leep", "cone_biopsy"):
        out[f"queue.wait_median.{key}"] = _wait_median(wt, key)
        out[f"queue.wait_abandoned_median.{key}"] = _wait_median(wta, key)

    # ── Section 3: PRIMARY SCREENING — CERVICAL ──────────────────────────────
    out["cervical.annual_total_mean"] = _mean(cerv_annual)
    out["cervical.annual_cytology_mean"] = _mean(cyto_annual)
    out["cervical.annual_hpv_alone_mean"] = _mean(hpv_annual)

    uptake_cerv = [
        _pct(cerv_annual[i], pool_sizes[i + 1])
        if i + 1 < len(pool_sizes) and pool_sizes[i + 1] > 0 else float("nan")
        for i in range(len(cerv_annual))
    ]
    out["cervical.uptake_mean_pct"] = _mean(uptake_cerv)

    cerv_by_test = dict(m.get("cervical_results_by_test", {}))
    cyto_results = dict(cerv_by_test.get("cytology", {}))
    total_cyto = sum(cyto_results.values())
    for grade in ("NORMAL", "ASCUS", "LSIL", "ASC-H", "HSIL"):
        out[f"cervical.cytology.{grade}_rate_pct"] = _pct(cyto_results.get(grade, 0), total_cyto)
    out["cervical.cytology.abnormal_rate_pct"] = _pct(
        sum(v for k, v in cyto_results.items() if k != "NORMAL"), total_cyto
    )

    hpv_results = dict(cerv_by_test.get("hpv_alone", {}))
    total_hpv = sum(hpv_results.values())
    out["cervical.hpv_alone.positive_rate_pct"] = _pct(
        hpv_results.get("HPV_POSITIVE", 0), total_hpv
    )

    # ── Section 4: PRIMARY SCREENING — LUNG ──────────────────────────────────
    out["lung.annual_ldct_mean"] = _mean(ldct_annual)

    uptake_lung = [
        _pct(lung_annual[i], pool_sizes[i + 1])
        if i + 1 < len(pool_sizes) and pool_sizes[i + 1] > 0 else float("nan")
        for i in range(len(lung_annual))
    ]
    out["lung.uptake_mean_pct"] = _mean(uptake_lung)

    lung_elig = m.get("lung_eligible", 0)
    lung_ref = m.get("lung_referral_placed", 0)
    lung_sch = m.get("lung_ldct_scheduled", 0)
    lung_comp = m.get("lung_ldct_completed", 0)
    out["lung.funnel.eligible"] = lung_elig
    out["lung.funnel.referral_rate_pct"] = _pct(lung_ref, lung_elig)
    out["lung.funnel.scheduled_rate_pct"] = _pct(lung_sch, lung_ref)
    out["lung.funnel.completed_rate_pct"] = _pct(lung_comp, lung_sch)

    rads = dict(m.get("lung_rads_distribution", {}))
    total_rads = sum(rads.values())
    for cat in ("RADS_0", "RADS_1", "RADS_2", "RADS_3", "RADS_4A", "RADS_4B_4X"):
        out[f"lung.rads.{cat}_rate_pct"] = _pct(rads.get(cat, 0), total_rads)
    out["lung.rads.abnormal_rate_pct"] = _pct(
        sum(v for k, v in rads.items() if k in ("RADS_3", "RADS_4A", "RADS_4B_4X")),
        total_rads,
    )

    # ── Section 5: SECONDARY SCREENING — CERVICAL (Colposcopy) ──────────────
    out["colposcopy.annual_mean"] = _mean(colpo_annual)

    cerv_all = dict(m.get("cervical_results", {}))
    abnormal_primary_cerv = sum(
        v for k, v in cerv_all.items() if k not in ("NORMAL", "HPV_NEGATIVE")
    )
    total_colpo = m.get("n_colposcopy", 0)
    out["colposcopy.abnormal_primary_total"] = abnormal_primary_cerv
    out["colposcopy.performed_total"] = total_colpo
    out["colposcopy.completion_rate_pct"] = _pct(total_colpo, abnormal_primary_cerv)

    colpo_res = dict(m.get("colposcopy_results", {}))
    total_colpo_res = sum(colpo_res.values())
    for grade in ("NORMAL", "CIN1", "CIN2", "CIN3", "INSUFFICIENT"):
        out[f"colposcopy.diag.{grade}_rate_pct"] = _pct(colpo_res.get(grade, 0), total_colpo_res)

    # ── Section 6: SECONDARY SCREENING — LUNG (Biopsy) ──────────────────────
    out["lung_bx.annual_mean"] = _mean(lung_bx_annual)

    abnormal_rads_total = sum(
        v for k, v in rads.items() if k in ("RADS_3", "RADS_4A", "RADS_4B_4X")
    )
    lung_bx_ref = m.get("lung_biopsy_referral", 0)
    lung_bx_sch = m.get("lung_biopsy_scheduled", 0)
    lung_bx_comp = m.get("lung_biopsy_completed", 0)
    lung_malig = m.get("lung_malignancy_confirmed", 0)

    out["lung_bx.funnel.abnormal_total"] = abnormal_rads_total
    out["lung_bx.funnel.referral_rate_pct"] = _pct(lung_bx_ref, abnormal_rads_total)
    out["lung_bx.funnel.scheduled_rate_pct"] = _pct(lung_bx_sch, lung_bx_ref)
    out["lung_bx.funnel.completed_rate_pct"] = _pct(lung_bx_comp, lung_bx_sch)
    out["lung_bx.malignancy_rate_pct"] = _pct(lung_malig, lung_bx_comp)

    # ── Section 7: LTFU BY NODE ──────────────────────────────────────────────
    total_ltfu = m.get("n_ltfu", 0)
    out["ltfu.total"] = total_ltfu
    out["ltfu.unscreened"] = m.get("ltfu_unscreened", 0)
    out["ltfu.queue_primary"] = m.get("ltfu_queue_primary", 0)
    out["ltfu.queue_secondary"] = m.get("ltfu_queue_secondary", 0)
    out["ltfu.queue_treatment"] = m.get("ltfu_queue_treatment", 0)
    out["ltfu.post_abnormal"] = m.get("ltfu_post_abnormal", 0)
    out["ltfu.post_colposcopy"] = m.get("ltfu_post_colposcopy", 0)

    ltfu_rate = [
        _pct(ltfu_annual[i], pts_annual[i])
        if i < len(pts_annual) and pts_annual[i] > 0 else float("nan")
        for i in range(len(ltfu_annual))
    ]
    out["ltfu.rate_of_throughput_mean_pct"] = _mean(ltfu_rate)

    # ── Section 8: TREATMENT ─────────────────────────────────────────────────
    tx = dict(m.get("n_treatment", {}))
    out["treatment.leep_total"] = tx.get("leep", 0)
    out["treatment.cone_biopsy_total"] = tx.get("cone_biopsy", 0)
    out["treatment.surveillance_total"] = tx.get("surveillance", 0)
    out["treatment.leep_annual_mean"] = _mean(leep_annual)

    cin23 = colpo_res.get("CIN2", 0) + colpo_res.get("CIN3", 0) if colpo_res else 0
    cerv_excisional = tx.get("leep", 0) + tx.get("cone_biopsy", 0)
    out["treatment.cin23_diagnosed"] = cin23
    out["treatment.cervical_excisional_total"] = cerv_excisional
    out["treatment.cervical_completion_rate_pct"] = _pct(cerv_excisional, cin23)

    lung_tx = m.get("lung_treatment_given", 0)
    out["treatment.lung_total"] = lung_tx
    out["treatment.lung_annual_mean"] = _mean(lung_tx_annual)
    out["treatment.lung_completion_rate_pct"] = _pct(lung_tx, lung_malig)

    # ── Section 9: MORTALITY ─────────────────────────────────────────────────
    out["mortality.total_sim"] = m.get("mortality_count", 0)
    out["mortality.total_scaled"] = m.get("mortality_count", 0) * scale_factor
    mort_rate = [
        _pct(mort_annual[i], pool_sizes[i + 1])
        if i + 1 < len(pool_sizes) and pool_sizes[i + 1] > 0 else float("nan")
        for i in range(len(mort_annual))
    ]
    out["mortality.annual_rate_mean_pct"] = _mean(mort_rate)

    # ── Finance (not in stats table but central for SA) ──────────────────────
    # Imported lazily to keep stats_extract import-cheap
    from model import compute_revenue
    r = compute_revenue(m)
    out["finance.realized_total_usd"] = r["realized_total"]
    out["finance.foregone_total_usd"] = r["foregone_total"]
    out["finance.unserved_total_usd"] = r["unserved_total"]
    out["finance.capture_rate_pct"] = 100.0 * r["demand_capture_rate"]

    return out
