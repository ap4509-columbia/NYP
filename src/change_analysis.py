# =============================================================================
# change_analysis.py
# Statistical change-analysis for scenario comparisons.
#
# Loads scenario CSVs (produced by mc_scenarios.py / mc_baseline.py), pivots
# them to per-seed scalar dataframes, and runs a pre-specified test per metric
# family with Holm-Bonferroni multiplicity correction within family.
#
# Test-per-family mapping (see run_pairwise_analysis):
#   LTFU family              -> Welch's t-test  (per-run rates, CLT-safe at n=50)
#   Mortality family         -> Welch's t-test pairwise + one-way ANOVA + Tukey HSD
#   Wait-time family         -> Wilcoxon rank-sum (Mann-Whitney U)  (right-skewed)
#   Wait-time variance       -> Levene's test    (robust to non-normality)
#   Multiplicity correction  -> Holm-Bonferroni within each metric family
#
# Effect sizes accompany every test:
#   Welch's t     -> Cohen's d + mean difference with 95% CI
#   Mann-Whitney  -> rank-biserial correlation + Hodges-Lehmann shift + bootstrap CI
#   Levene        -> variance ratio (var_intervention / var_baseline)
#   ANOVA         -> eta-squared (proportion of variance explained by scenario)
# =============================================================================

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats as sp_stats
from statsmodels.stats.multitest import multipletests


# =============================================================================
# Metric registry: which metrics belong to which family
# =============================================================================

# LTFU family - per-run rates and counts. Compared with Welch's t-test.
LTFU_METRICS: List[str] = [
    "ltfu_rate_primary_pct",
    "ltfu_rate_secondary_pct",
    "cp.cum_ltfu",
    "cp.cum_ltfu_queue_primary",
    "cp.cum_ltfu_queue_secondary",
    "cp.cum_ltfu_queue_treatment",
    "cp.cum_ltfu_unscreened",
]

# Mortality family - broken out by cause. Compared with Welch's t-test (pairwise)
# AND one-way ANOVA + Tukey HSD (omnibus across all scenarios).
#
# `cp.cum_mortality` sums all deaths counted in the pool (Gompertz all-cause plus
# cancer deaths under the always-count rule). This total is dominated by Gompertz
# mortality, which respects the active-guard (LTFU'd patients' Gompertz events are
# silenced). Retention therefore INFLATES this metric — interventions that keep
# more patients in the pool make more of the pool's natural deaths visible.
#
# For an honest between-scenario comparison of the mortality effect attributable
# to the intervention, use the cancer-specific counters below, which fire under
# the always-count rule regardless of pool status. Reporting all three (total,
# cervical cancer deaths, lung cancer deaths) lets a reader distinguish
# retention-driven counting effects from disease-modifying effects.
MORTALITY_METRICS: List[str] = [
    "cp.cum_mortality",
    # Untreated cancer deaths (cancer_death_* events) - the ones interventions
    # are meant to prevent. This is the key intervention-comparison metric.
    "final.exits_by_source.mortality_cervical_cancer",
    "final.exits_by_source.mortality_lung_cancer",
    # Post-treatment deaths (disease_mortality_* events) - by construction
    # HIGHER under interventions that raise treatment volume. Reported for
    # completeness but not the primary intervention-comparison metric.
    "final.exits_by_source.mortality_cervical_posttx",
    "final.exits_by_source.mortality_lung_posttx",
]

# Wait-time family - mean wait per seed per node. Compared with Mann-Whitney U.
# Same set of metrics is also used as input to Levene's variance test.
WAIT_TIME_MODALITIES: List[str] = [
    "cytology", "hpv_alone", "co_test", "ldct",
    "colposcopy", "lung_biopsy", "leep", "cone_biopsy",
]
WAIT_TIME_METRICS: List[str] = [f"final.wait_mean.{m}" for m in WAIT_TIME_MODALITIES]
WAIT_TIME_AGGREGATE_METRICS: List[str] = [
    "mean_wait_primary_days",
    "mean_wait_secondary_days",
]


DEFAULT_MC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mc_scenario_data")


# =============================================================================
# Loading / pivoting
# =============================================================================

def load_scenario_metrics(
    scenario_name: str,
    n_seeds: int = 100,
    seed_start: int = 42,
    mc_dir: Optional[str] = None,
) -> pd.DataFrame:
    """
    Load a scenario CSV and return a wide-format dataframe (seed x metric).

    Rows are one-per-seed scalars, built by:
      - Taking every row with year == NaN (already scalar-per-seed).
      - Taking the max-year row per metric for per-year cumulative metrics
        (so cp.cum_mortality becomes the total across the 70-year window).

    Missing metrics are silently omitted; downstream code checks for presence.
    """
    mc_dir = mc_dir or DEFAULT_MC_DIR
    path = os.path.join(mc_dir, f"{scenario_name}_n{n_seeds}_start{seed_start}.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Scenario CSV not found: {path}")
    df = pd.read_csv(path)

    scalar = df[df["year"].isna()].copy()

    yearly = df[df["year"].notna()].copy()
    if not yearly.empty:
        yearly["year"] = yearly["year"].astype(float)
        max_year = yearly["year"].max()
        final_year = yearly[yearly["year"] == max_year]
    else:
        final_year = yearly

    combined = pd.concat([scalar, final_year], ignore_index=True)
    wide = combined.pivot_table(
        index="seed", columns="metric", values="value", aggfunc="first"
    ).reset_index()
    wide.columns.name = None
    return wide


def build_comparison_frame(
    scenarios: List[str],
    metrics: Optional[List[str]] = None,
    n_seeds: int = 100,
    seed_start: int = 42,
    mc_dir: Optional[str] = None,
) -> pd.DataFrame:
    """
    Load multiple scenarios and stack into long format: seed | scenario | metric | value.

    If `metrics` is provided, only those metrics are retained.
    """
    frames = []
    for sc in scenarios:
        wide = load_scenario_metrics(sc, n_seeds, seed_start, mc_dir)
        long = wide.melt(id_vars="seed", var_name="metric", value_name="value")
        long["scenario"] = sc
        if metrics is not None:
            long = long[long["metric"].isin(metrics)]
        frames.append(long)
    return pd.concat(frames, ignore_index=True)


def _get_values(frame: pd.DataFrame, scenario: str, metric: str) -> np.ndarray:
    """Extract the per-seed values array for one scenario x metric. NaNs dropped."""
    sub = frame[(frame["scenario"] == scenario) & (frame["metric"] == metric)]
    if sub.empty:
        return np.array([], dtype=float)
    vals = sub["value"].to_numpy(dtype=float)
    return vals[~np.isnan(vals)]


# =============================================================================
# Test functions - one per test type, each returns a flat dict
# =============================================================================

def welch_ttest(a: np.ndarray, b: np.ndarray) -> Dict[str, float]:
    """
    Welch's two-sample t-test (unequal variance). Returns Cohen's d effect size
    and 95% CI on the mean difference via Welch-Satterthwaite dof.

    Convention: `a` is the intervention arm, `b` is the baseline arm.
    A positive mean_diff means the intervention has a HIGHER value than baseline.
    """
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    stat, p = sp_stats.ttest_ind(a, b, equal_var=False)

    mean_diff = float(np.mean(a) - np.mean(b))
    var_a, var_b = float(np.var(a, ddof=1)), float(np.var(b, ddof=1))
    se = float(np.sqrt(var_a / len(a) + var_b / len(b)))

    if se > 0:
        df_num = se ** 4
        df_den = ((var_a / len(a)) ** 2 / (len(a) - 1)
                  + (var_b / len(b)) ** 2 / (len(b) - 1))
        df = df_num / df_den if df_den > 0 else float("inf")
        t_crit = sp_stats.t.ppf(0.975, df)
        ci_low, ci_high = mean_diff - t_crit * se, mean_diff + t_crit * se
    else:
        ci_low = ci_high = mean_diff

    pooled_sd = float(np.sqrt((var_a + var_b) / 2))
    cohens_d = mean_diff / pooled_sd if pooled_sd > 0 else 0.0

    return {
        "test": "welch_ttest",
        "n_a": len(a), "n_b": len(b),
        "mean_a": float(np.mean(a)), "mean_b": float(np.mean(b)),
        "statistic": float(stat), "p_value": float(p),
        "effect_size": float(cohens_d), "effect_size_name": "cohens_d",
        "mean_diff": mean_diff, "ci_low": float(ci_low), "ci_high": float(ci_high),
    }


def mann_whitney(a: np.ndarray, b: np.ndarray, n_boot: int = 2000, seed: int = 42) -> Dict[str, float]:
    """
    Wilcoxon rank-sum test (a.k.a. Mann-Whitney U).

    Effect size: rank-biserial correlation and the Hodges-Lehmann median-shift
    estimator (median of all pairwise differences). Bootstrap 95% CI on the HL.

    Convention: `a` is intervention, `b` is baseline. Positive HL means the
    intervention has stochastically LARGER values than baseline.
    """
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    stat, p = sp_stats.mannwhitneyu(a, b, alternative="two-sided")

    n1, n2 = len(a), len(b)
    rank_biserial = 1.0 - (2.0 * float(stat)) / (n1 * n2) if n1 * n2 > 0 else 0.0

    diffs = np.subtract.outer(a, b).ravel()
    hl = float(np.median(diffs))

    rng = np.random.default_rng(seed)
    shifts = np.empty(n_boot)
    for i in range(n_boot):
        ai = rng.choice(a, size=n1, replace=True)
        bi = rng.choice(b, size=n2, replace=True)
        shifts[i] = np.median(np.subtract.outer(ai, bi).ravel())
    ci_low, ci_high = float(np.quantile(shifts, 0.025)), float(np.quantile(shifts, 0.975))

    return {
        "test": "mann_whitney",
        "n_a": n1, "n_b": n2,
        "mean_a": float(np.mean(a)), "mean_b": float(np.mean(b)),
        "statistic": float(stat), "p_value": float(p),
        "effect_size": float(rank_biserial), "effect_size_name": "rank_biserial",
        "hodges_lehmann": hl, "ci_low": ci_low, "ci_high": ci_high,
        "mean_diff": float(np.mean(a) - np.mean(b)),
    }


def levene_test(a: np.ndarray, b: np.ndarray) -> Dict[str, float]:
    """
    Levene's test for equal variances, using deviations from the median
    (a.k.a. Brown-Forsythe variant) - robust to non-normality of wait times.

    Effect size: variance ratio var(intervention) / var(baseline).
    A ratio < 1 means the intervention reduced wait-time variance.
    """
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    stat, p = sp_stats.levene(a, b, center="median")
    var_a, var_b = float(np.var(a, ddof=1)), float(np.var(b, ddof=1))
    var_ratio = var_a / var_b if var_b > 0 else float("nan")

    return {
        "test": "levene",
        "n_a": len(a), "n_b": len(b),
        "mean_a": float(np.mean(a)), "mean_b": float(np.mean(b)),
        "statistic": float(stat), "p_value": float(p),
        "effect_size": float(var_ratio), "effect_size_name": "variance_ratio",
        "var_a": var_a, "var_b": var_b,
        "mean_diff": var_a - var_b,
        "ci_low": float("nan"), "ci_high": float("nan"),
    }


def anova_tukey(groups: Dict[str, np.ndarray]) -> Dict:
    """
    One-way ANOVA + Tukey HSD post-hoc.

    Input: dict of {scenario_name: 1D array of per-seed values}.
    Returns the omnibus F/p/eta-squared plus a list of pairwise Tukey results
    (with family-wise-error-corrected p-values already applied by Tukey).
    """
    labels = list(groups.keys())
    arrays = [np.asarray(groups[k], dtype=float) for k in labels]
    arrays = [x[~np.isnan(x)] for x in arrays]

    F, p_anova = sp_stats.f_oneway(*arrays)

    all_data = np.concatenate(arrays)
    grand_mean = float(np.mean(all_data))
    ss_between = float(sum(len(x) * (np.mean(x) - grand_mean) ** 2 for x in arrays))
    ss_total = float(np.sum((all_data - grand_mean) ** 2))
    eta_sq = ss_between / ss_total if ss_total > 0 else 0.0

    tukey = sp_stats.tukey_hsd(*arrays)

    pairwise = []
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            pairwise.append({
                "scenario_a": labels[i],
                "scenario_b": labels[j],
                "mean_diff": float(np.mean(arrays[i]) - np.mean(arrays[j])),
                "statistic": float(tukey.statistic[i, j]),
                "p_value": float(tukey.pvalue[i, j]),
                "ci_low": float(tukey.confidence_interval(0.95).low[i, j]),
                "ci_high": float(tukey.confidence_interval(0.95).high[i, j]),
                "reject_h0": bool(tukey.pvalue[i, j] < 0.05),
            })

    return {
        "test": "anova_tukey",
        "labels": labels,
        "n_per_group": [len(x) for x in arrays],
        "means": [float(np.mean(x)) for x in arrays],
        "F": float(F),
        "p_anova": float(p_anova),
        "eta_squared": float(eta_sq),
        "tukey_pairwise": pairwise,
    }


def shapiro_normality(x: np.ndarray) -> float:
    """Shapiro-Wilk normality p-value. Returns nan if n < 3."""
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    if len(x) < 3:
        return float("nan")
    return float(sp_stats.shapiro(x).pvalue)


# =============================================================================
# Multiplicity correction
# =============================================================================

def holm_bonferroni(pvalues: List[float], alpha: float = 0.05) -> np.ndarray:
    """Holm-Bonferroni step-down correction. Delegates to statsmodels."""
    p = np.asarray(pvalues, dtype=float)
    if len(p) == 0:
        return p
    _, adj, _, _ = multipletests(p, alpha=alpha, method="holm")
    return adj


# =============================================================================
# Orchestrators
# =============================================================================

def run_pairwise_analysis(
    scenarios: List[str],
    baseline: str = "baseline_reference",
    n_seeds: int = 100,
    seed_start: int = 42,
    mc_dir: Optional[str] = None,
    alpha: float = 0.05,
) -> pd.DataFrame:
    """
    Run all pre-specified pairwise tests (each intervention vs. baseline) across
    every metric family. Applies Holm-Bonferroni within each family.

    Returns a tidy dataframe:
      metric_family | metric | scenario_a | scenario_b | test | n_a | n_b
      | mean_a | mean_b | mean_diff | statistic | p_value | p_adjusted
      | effect_size | effect_size_name | ci_low | ci_high | reject_h0
    """
    interventions = [s for s in scenarios if s != baseline]
    all_metrics = (LTFU_METRICS + MORTALITY_METRICS
                   + WAIT_TIME_METRICS + WAIT_TIME_AGGREGATE_METRICS)

    frame = build_comparison_frame(
        [baseline] + interventions,
        metrics=all_metrics,
        n_seeds=n_seeds, seed_start=seed_start, mc_dir=mc_dir,
    )

    results: List[Dict] = []

    for metric in LTFU_METRICS:
        _pairwise(frame, metric, baseline, interventions, "ltfu", welch_ttest, results)

    for metric in MORTALITY_METRICS:
        _pairwise(frame, metric, baseline, interventions, "mortality", welch_ttest, results)

    for metric in WAIT_TIME_METRICS + WAIT_TIME_AGGREGATE_METRICS:
        _pairwise(frame, metric, baseline, interventions, "wait_time", mann_whitney, results)

    for metric in WAIT_TIME_METRICS + WAIT_TIME_AGGREGATE_METRICS:
        _pairwise(frame, metric, baseline, interventions, "wait_time_variance", levene_test, results)

    df = pd.DataFrame(results)
    if df.empty:
        return df

    df["p_adjusted"] = np.nan
    for family in df["metric_family"].unique():
        mask = df["metric_family"] == family
        df.loc[mask, "p_adjusted"] = holm_bonferroni(df.loc[mask, "p_value"].tolist(), alpha=alpha)

    df["reject_h0"] = df["p_adjusted"] < alpha

    col_order = [
        "metric_family", "metric", "scenario_a", "scenario_b", "test",
        "n_a", "n_b", "mean_a", "mean_b", "mean_diff",
        "statistic", "p_value", "p_adjusted",
        "effect_size", "effect_size_name",
        "ci_low", "ci_high", "reject_h0",
    ]
    return df[[c for c in col_order if c in df.columns]]


def _pairwise(frame, metric, baseline, interventions, family, test_fn, results):
    """Run `test_fn(intervention_vals, baseline_vals)` for every intervention."""
    baseline_vals = _get_values(frame, baseline, metric)
    if len(baseline_vals) == 0:
        return
    for iv in interventions:
        iv_vals = _get_values(frame, iv, metric)
        if len(iv_vals) == 0:
            continue
        r = test_fn(iv_vals, baseline_vals)
        results.append({
            "metric_family": family, "metric": metric,
            "scenario_a": iv, "scenario_b": baseline,
            **r,
        })


def run_mortality_anova(
    scenarios: List[str],
    metric: str = "cp.cum_mortality",
    n_seeds: int = 100,
    seed_start: int = 42,
    mc_dir: Optional[str] = None,
) -> Dict:
    """
    One-way ANOVA + Tukey HSD across the passed scenarios on the mortality metric.
    Call with [baseline] + interventions to get the full 4-group omnibus + all pairs.
    """
    frame = build_comparison_frame(
        scenarios, metrics=[metric],
        n_seeds=n_seeds, seed_start=seed_start, mc_dir=mc_dir,
    )
    groups = {sc: _get_values(frame, sc, metric) for sc in scenarios}
    return anova_tukey(groups)


def describe_by_scenario(
    scenarios: List[str],
    metrics: List[str],
    n_seeds: int = 100,
    seed_start: int = 42,
    mc_dir: Optional[str] = None,
) -> pd.DataFrame:
    """
    Per-scenario per-metric descriptive stats: n, mean, sd, median, IQR,
    and Shapiro-Wilk normality p-value. Used as a diagnostic companion
    to the hypothesis tests.
    """
    frame = build_comparison_frame(
        scenarios, metrics=metrics,
        n_seeds=n_seeds, seed_start=seed_start, mc_dir=mc_dir,
    )
    rows = []
    for sc in scenarios:
        for metric in metrics:
            vals = _get_values(frame, sc, metric)
            if len(vals) == 0:
                continue
            rows.append({
                "scenario": sc, "metric": metric,
                "n": len(vals),
                "mean": float(np.mean(vals)),
                "sd": float(np.std(vals, ddof=1)),
                "median": float(np.median(vals)),
                "q25": float(np.quantile(vals, 0.25)),
                "q75": float(np.quantile(vals, 0.75)),
                "shapiro_p": shapiro_normality(vals),
            })
    return pd.DataFrame(rows)


# =============================================================================
# Convenience: pretty-print a summary
# =============================================================================

def print_summary(results: pd.DataFrame, alpha: float = 0.05) -> None:
    """Print a compact per-family summary of the pairwise-analysis results."""
    if results.empty:
        print("No results to summarize.")
        return
    for family in results["metric_family"].unique():
        sub = results[results["metric_family"] == family]
        n_sig = int(sub["reject_h0"].sum())
        print(f"\n=== {family} (n={len(sub)} tests, {n_sig} significant at Holm-adjusted alpha={alpha}) ===")
        cols = ["metric", "scenario_a", "mean_diff", "effect_size", "p_value", "p_adjusted", "reject_h0"]
        cols = [c for c in cols if c in sub.columns]
        print(sub[cols].to_string(index=False))
