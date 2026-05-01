# =============================================================================
# mc_baseline.py
# Monte Carlo analysis of the BASELINE (no-perturbation) digital twin.
#
# Runs the baseline simulation N times with different random seeds. For each
# seed, extracts per-year time-series metrics and single-number aggregate
# metrics into a long-format CSV (seed, year, metric, value).
#
# Renders two chart families (both scale cleanly as N grows):
#   • Spaghetti plot         — time-series metrics (one line per seed, ±1 SD band)
#   • Strip + histogram plot — single-number-per-seed metrics
#
# Nothing about N is hardcoded. Change the n_seeds parameter to scale up.
#
# Public API:
#   run_mc_baseline(n_seeds=100, …)      → path to long-format CSV
#   render_all_mc_baseline(csv, dir)     → list of saved PNG paths
# =============================================================================

import csv
import os
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


# =============================================================================
# Per-seed simulation driver (runs in subprocess)
# =============================================================================

def _ensure_sys_path() -> None:
    here = Path(__file__).resolve().parent.parent
    for sub in ("src", "ModelParameters", "."):
        cand = str(here / sub) if sub != "." else str(here)
        if cand not in sys.path:
            sys.path.insert(0, cand)


def _run_one_baseline(seed: int) -> Dict[str, Any]:
    """
    Run one full baseline simulation. Returns raw output needed for extraction.

    Each call uses a UNIQUE TEMPORARY SQLite file so parallel workers don't
    collide on a shared DB path (SQLite doesn't support concurrent writers).
    The temp file is deleted after the run.
    """
    _ensure_sys_path()
    import parameters as cfg
    from runner import SimulationRunner

    tmp_fd, tmp_db = tempfile.mkstemp(suffix=".db", prefix="mcb_")
    os.close(tmp_fd)  # SimulationDB will (re)create the file at this path

    try:
        sim = SimulationRunner(
            n_days=cfg.SIM_DAYS, seed=seed,
            use_stable_population=True,
            db_path=tmp_db, reset_db=True,
        )
        metrics = sim.run()
        sim.close_db()

        # Final-sim aggregate dicts we may want for cascade/funnel plots
        def _as_flat_dict(d):
            return {k: (int(v) if isinstance(v, (int, float)) else 0)
                    for k, v in (d or {}).items()}

        return {
            "seed": seed,
            "year_checkpoints": list(metrics.get("year_checkpoints", [])),
            "wait_times": {k: list(v) for k, v in metrics.get("wait_times", {}).items()},
            "procedure_revenue": dict(cfg.PROCEDURE_REVENUE),
            "population_scale_factor": cfg.POPULATION_SCALE_FACTOR,
            "warmup_years": cfg.WARMUP_YEARS,
            # Final aggregates for cascades + exit breakdowns
            "final_n_patients":           int(metrics.get("n_patients", 0)),
            "final_n_screened":           _as_flat_dict(metrics.get("n_screened", {})),
            "final_cervical_results":     _as_flat_dict(metrics.get("cervical_results", {})),
            "final_colposcopy_results":   _as_flat_dict(metrics.get("colposcopy_results", {})),
            "final_n_treatment":          _as_flat_dict(metrics.get("n_treatment", {})),
            "final_n_colposcopy":         int(metrics.get("n_colposcopy", 0)),
            "final_lung_rads_distribution": _as_flat_dict(metrics.get("lung_rads_distribution", {})),
            "final_lung_eligible":        int(metrics.get("lung_eligible", 0)),
            "final_lung_referral_placed": int(metrics.get("lung_referral_placed", 0)),
            "final_lung_ldct_scheduled":  int(metrics.get("lung_ldct_scheduled", 0)),
            "final_lung_ldct_completed":  int(metrics.get("lung_ldct_completed", 0)),
            "final_lung_biopsy_referral": int(metrics.get("lung_biopsy_referral", 0)),
            "final_lung_biopsy_completed": int(metrics.get("lung_biopsy_completed", 0)),
            "final_lung_malignancy_confirmed": int(metrics.get("lung_malignancy_confirmed", 0)),
            "final_lung_treatment_given": int(metrics.get("lung_treatment_given", 0)),
            "final_exits_by_reason":      _as_flat_dict(metrics.get("exits_by_reason", {})),
            # Sub-source breakdown — splits "mortality" into Gompertz baseline,
            # mortality_cervical_cancer, mortality_lung_cancer, etc.
            "final_exits_by_source":      _as_flat_dict(metrics.get("exits_by_source", {})),
        }
    finally:
        for ext in ("", "-shm", "-wal"):
            p = tmp_db + ext
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


def _worker(seed: int) -> Dict[str, Any]:
    _ensure_sys_path()
    from mc_baseline import _run_one_baseline
    t0 = time.time()
    r = _run_one_baseline(seed)
    r["elapsed_sec"] = time.time() - t0
    return r


# =============================================================================
# Per-seed metric extraction (long-format rows)
# =============================================================================

def _extract_metrics_for_seed(seed_result: Dict[str, Any]) -> pd.DataFrame:
    """Convert one seed's raw output into long-format rows keyed by (year, metric)."""
    seed = seed_result["seed"]
    ckpts = seed_result["year_checkpoints"]
    warmup = seed_result["warmup_years"]
    scale = seed_result["population_scale_factor"]
    rev = seed_result["procedure_revenue"]

    # Restrict to post-warmup years for analysis
    ckpts = [cp for cp in ckpts if cp.get("year", 0) >= warmup]
    rows: List[Dict[str, Any]] = []

    if len(ckpts) < 2:
        return pd.DataFrame(rows)

    avg_cerv_screen = (rev["cytology"] + rev["hpv_alone"]) / 2.0

    for i in range(1, len(ckpts)):
        cp_prev, cp = ckpts[i - 1], ckpts[i]
        year = cp["year"]

        def delta(key: str) -> float:
            return cp.get(key, 0) - cp_prev.get(key, 0)

        # ── Annual realized revenue (real NYP dollars) ───────────────────────
        ann_realized = scale * (
            delta("cum_cervical")       * avg_cerv_screen +
            delta("cum_colposcopy")     * rev["colposcopy"] +
            delta("cum_leep")           * rev["leep"] +
            delta("cum_ldct")           * rev["ldct"] +
            delta("cum_lung_biopsy")    * rev["lung_biopsy"] +
            delta("cum_lung_treatment") * rev["lung_treatment"]
        )
        rows.append({"seed": seed, "year": year,
                     "metric": "annual_realized_revenue_usd",
                     "value": ann_realized})

        # ── Annual foregone revenue ─────────────────────────────────────────
        ann_unserved_ppl = (
            (cp.get("cum_intake_total", 0) - cp.get("cum_intake_served", 0))
            - (cp_prev.get("cum_intake_total", 0) - cp_prev.get("cum_intake_served", 0))
        )
        ann_foregone = scale * (
            delta("cum_ltfu_unscreened")     * avg_cerv_screen +
            delta("cum_ltfu_queue_primary")  * avg_cerv_screen +
            delta("cum_ltfu_queue_secondary") * rev["colposcopy"] +
            max(0, ann_unserved_ppl)         * avg_cerv_screen
        )
        rows.append({"seed": seed, "year": year,
                     "metric": "annual_foregone_revenue_usd",
                     "value": ann_foregone})

        # ── Cumulative revenue capture rate (%) ─────────────────────────────
        cum_realized = (
            cp.get("cum_cervical", 0)       * avg_cerv_screen +
            cp.get("cum_colposcopy", 0)     * rev["colposcopy"] +
            cp.get("cum_leep", 0)           * rev["leep"] +
            cp.get("cum_ldct", 0)           * rev["ldct"] +
            cp.get("cum_lung_biopsy", 0)    * rev["lung_biopsy"] +
            cp.get("cum_lung_treatment", 0) * rev["lung_treatment"]
        )
        cum_unserved_ppl = max(
            0,
            cp.get("cum_intake_total", 0) - cp.get("cum_intake_served", 0),
        )
        cum_foregone = (
            cp.get("cum_ltfu_unscreened", 0)    * avg_cerv_screen +
            cp.get("cum_ltfu_queue_primary", 0) * avg_cerv_screen +
            cp.get("cum_ltfu_queue_secondary", 0) * rev["colposcopy"] +
            cum_unserved_ppl                    * avg_cerv_screen
        )
        total = cum_realized + cum_foregone
        if total > 0:
            rows.append({"seed": seed, "year": year,
                         "metric": "revenue_capture_rate_pct",
                         "value": 100.0 * cum_realized / total})

        # ── Population capture rate (% of intake-queue arrivals actually served) ─
        intake_total = cp.get("cum_intake_total", 0)
        if intake_total > 0:
            rows.append({"seed": seed, "year": year,
                         "metric": "population_capture_rate_pct",
                         "value": 100.0 * cp.get("cum_intake_served", 0) / intake_total})

        # ── LTFU rates (cumulative %) ────────────────────────────────────────
        served = cp.get("cum_intake_served", 0)
        if served > 0:
            rows.append({"seed": seed, "year": year,
                         "metric": "ltfu_rate_primary_pct",
                         "value": 100.0 * cp.get("cum_ltfu_queue_primary", 0) / served})
        total_colpo = cp.get("cum_colposcopy", 0)
        if total_colpo > 0:
            rows.append({"seed": seed, "year": year,
                         "metric": "ltfu_rate_secondary_pct",
                         "value": 100.0 * cp.get("cum_ltfu_queue_secondary", 0) / total_colpo})

    # ── Single-number-per-seed metrics: wait times ─────────────────────────
    wt = seed_result["wait_times"]

    # Per-modality mean wait (one scalar per seed per modality). Used by the
    # per-modality wait-time box plots (10, 11).
    for modality, waits in wt.items():
        if waits:
            rows.append({"seed": seed, "year": None,
                         "metric": f"final.wait_mean.{modality}",
                         "value": float(np.mean(waits))})

    # Aggregate primary + secondary means (used by 31, 32 histograms).
    primary_waits: List[float] = []
    for k in ("cytology", "hpv_alone", "co_test", "ldct"):
        primary_waits.extend(wt.get(k, []))
    if primary_waits:
        rows.append({"seed": seed, "year": None,
                     "metric": "mean_wait_primary_days",
                     "value": float(np.mean(primary_waits))})
    secondary_waits: List[float] = []
    for k in ("colposcopy", "lung_biopsy"):
        secondary_waits.extend(wt.get(k, []))
    if secondary_waits:
        rows.append({"seed": seed, "year": None,
                     "metric": "mean_wait_secondary_days",
                     "value": float(np.mean(secondary_waits))})

    # ── Full per-year dump of every scalar year_checkpoint field ───────────
    # Emitted as "cp.<fieldname>" so base viz renderers can average any field
    # across seeds (pool_size, cum_* fields, etc.) without needing bespoke
    # extraction for each viz.
    for cp in ckpts:
        year = cp["year"]
        for key, val in cp.items():
            if key in ("year", "day"):
                continue
            if isinstance(val, (int, float)) and val is not None:
                rows.append({"seed": seed, "year": year,
                             "metric": f"cp.{key}",
                             "value": float(val)})
            elif isinstance(val, dict):
                for sk, sv in val.items():
                    if isinstance(sv, (int, float)) and sv is not None:
                        rows.append({"seed": seed, "year": year,
                                     "metric": f"cp.{key}.{sk}",
                                     "value": float(sv)})

    # ── End-of-sim aggregates for cascade / funnel / breakdown plots ───────
    # Emitted as "final.<field>" single-number-per-seed metrics (year=None).
    # Flatten dict-valued aggregates as "final.<field>.<subkey>".
    final_scalar_keys = [
        "final_n_patients", "final_n_colposcopy", "final_lung_eligible",
        "final_lung_referral_placed", "final_lung_ldct_scheduled",
        "final_lung_ldct_completed", "final_lung_biopsy_referral",
        "final_lung_biopsy_completed", "final_lung_malignancy_confirmed",
        "final_lung_treatment_given",
    ]
    for key in final_scalar_keys:
        val = seed_result.get(key)
        if isinstance(val, (int, float)) and val is not None:
            rows.append({"seed": seed, "year": None,
                         "metric": key.replace("final_", "final."),
                         "value": float(val)})

    final_dict_keys = [
        "final_n_screened", "final_cervical_results", "final_colposcopy_results",
        "final_n_treatment", "final_lung_rads_distribution",
        "final_exits_by_reason", "final_exits_by_source",
    ]
    for key in final_dict_keys:
        d = seed_result.get(key) or {}
        base = key.replace("final_", "final.")
        for sk, sv in d.items():
            if isinstance(sv, (int, float)) and sv is not None:
                rows.append({"seed": seed, "year": None,
                             "metric": f"{base}.{sk}",
                             "value": float(sv)})

    return pd.DataFrame(rows)


# =============================================================================
# Parallel sweep runner
# =============================================================================

def run_mc_baseline(
    n_seeds: int = 100,
    seed_start: int = 42,
    n_workers: Optional[int] = None,
    out_csv: Optional[str] = None,
    progress: bool = True,
) -> str:
    """
    Run the baseline simulation `n_seeds` times in parallel and write a
    long-format CSV: one row per (seed, year, metric).

    n_seeds is a parameter — not hardcoded anywhere downstream. Every
    plot, label, and footer reads the seed count from the CSV at render
    time. Scale up to 500+ by passing a bigger n_seeds.

    Compute cost (rough, on 8 cores, each sim ~17s):
        n=40    → ~1.5 min
        n=100   → ~3.5 min
        n=500   → ~18 min
    """
    if n_seeds < 1:
        raise ValueError(f"n_seeds must be ≥ 1, got {n_seeds}")

    seeds = list(range(seed_start, seed_start + n_seeds))
    n_workers = n_workers or max(1, (os.cpu_count() or 1) - 1)

    if out_csv is None:
        here = Path(__file__).resolve().parent
        results_dir = here / "mc_baseline_data"
        results_dir.mkdir(parents=True, exist_ok=True)
        out_csv = str(results_dir / f"mc_baseline_n{n_seeds}_start{seed_start}.csv")

    if progress:
        print(f"[mc_baseline] {n_seeds} baseline sims × 80yr each, {n_workers} workers")
        print(f"[mc_baseline] writing → {out_csv}")

    dataframes: List[pd.DataFrame] = []
    t_start = time.time()
    with ProcessPoolExecutor(max_workers=n_workers) as ex:
        futures = {ex.submit(_worker, s): s for s in seeds}
        for i, fut in enumerate(as_completed(futures), start=1):
            r = fut.result()
            dataframes.append(_extract_metrics_for_seed(r))
            if progress and (i % max(1, n_seeds // 20) == 0 or i == n_seeds):
                elapsed = time.time() - t_start
                eta = (elapsed / i) * (n_seeds - i) / 60
                print(f"  [{i:>3}/{n_seeds}] seed={r['seed']:>4} "
                      f"{r['elapsed_sec']:.0f}s  ETA {eta:.1f}min")

    if dataframes:
        result = pd.concat(dataframes, ignore_index=True)
        result.to_csv(out_csv, index=False)
    else:
        raise RuntimeError("no seed results to write")

    if progress:
        print(f"[mc_baseline] done in {(time.time() - t_start)/60:.1f} min  "
              f"({len(result):,} rows across {n_seeds} seeds)")
    return out_csv


# =============================================================================
# Renderers — chart type per data shape
# =============================================================================

def _adaptive_alpha(n: int, target_ink: float = 5.0) -> float:
    """Choose line alpha so total ink is roughly constant regardless of n."""
    return float(max(0.03, min(0.5, target_ink / max(1, n))))


def _format_y_axis(ax, metric: str) -> None:
    """Pick a sensible y-axis formatter based on the metric's units."""
    if "usd" in metric.lower():
        ax.yaxis.set_major_formatter(plt.FuncFormatter(
            lambda v, _: (
                f"${v/1e9:.2f}B" if abs(v) >= 1e9 else
                f"${v/1e6:.1f}M" if abs(v) >= 1e6 else
                f"${v/1e3:.0f}K" if abs(v) >= 1e3 else
                f"${v:.0f}"
            )
        ))
    elif "_pct" in metric.lower():
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.1f}%"))
    elif "days" in metric.lower():
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.1f}"))


def render_mc_baseline_spaghetti(
    csv_path: str,
    metric: str,
    title: str,
    y_label: str,
    output_dir: str,
    filename: Optional[str] = None,
) -> Optional[str]:
    """Spaghetti time-series: one semi-transparent line per seed + mean + ±1 SD."""
    df = pd.read_csv(csv_path)
    sub = df[df["metric"] == metric].copy()
    sub["year"] = pd.to_numeric(sub["year"], errors="coerce")
    sub["value"] = pd.to_numeric(sub["value"], errors="coerce")
    sub = sub.dropna(subset=["year", "value"])
    if sub.empty:
        return None

    n_seeds = int(sub["seed"].nunique())
    alpha = _adaptive_alpha(n_seeds)

    fig, ax = plt.subplots(figsize=(10.0, 6.8))
    fig.patch.set_facecolor("white")

    spaghetti_color = "#2C7BB6"
    mean_color = "#1F4E79"

    # One thin line per seed
    for seed in sub["seed"].unique():
        s = sub[sub["seed"] == seed].sort_values("year")
        ax.plot(s["year"], s["value"],
                color=spaghetti_color, alpha=alpha, linewidth=0.9, zorder=2)

    # Mean + ±1 SD band
    pivot = sub.pivot_table(index="year", columns="seed",
                             values="value", aggfunc="first").sort_index()
    y_mean = pivot.mean(axis=1)
    y_std = pivot.std(axis=1, ddof=1) if n_seeds > 1 else pd.Series(
        np.zeros(len(y_mean)), index=y_mean.index
    )

    if n_seeds > 1:
        ax.fill_between(y_mean.index, y_mean - y_std, y_mean + y_std,
                        color=spaghetti_color, alpha=0.18, zorder=1,
                        label="±1 SD across seeds")
    ax.plot(y_mean.index, y_mean,
            color=mean_color, linewidth=2.6,
            marker="o", markersize=4, zorder=3,
            label=f"mean of {n_seeds} seeds")

    ax.set_xlabel("Simulation year", fontsize=11, fontweight="bold", labelpad=10)
    ax.set_ylabel(y_label, fontsize=11, fontweight="bold", labelpad=10)
    ax.set_title(
        f"{title}\nMonte Carlo across {n_seeds} baseline runs",
        fontsize=13, fontweight="bold", pad=14,
    )
    ax.legend(loc="best", fontsize=9, framealpha=0.9)
    ax.grid(True, alpha=0.25, linewidth=0.5)
    ax.spines[["top", "right"]].set_visible(False)
    _format_y_axis(ax, metric)

    mean_overall = float(y_mean.mean())
    sd_overall = float(y_std.mean()) if n_seeds > 1 else 0.0
    guide = (
        f"HOW TO READ THIS CHART\n"
        f"Each thin blue line is ONE complete 80-year simulation with a different random seed "
        f"({n_seeds} runs total).\n"
        f"The bold dark line is the MEAN across seeds. The shaded band is ±1 standard deviation — "
        f"how much this metric\nnaturally varies between runs due to stochastic noise.\n\n"
        f"INTERPRETING THIS CHART\n"
        f"  • A TIGHT band = metric is STABLE across runs; any single run is a trustworthy estimate.\n"
        f"  • A WIDE band = metric is NOISY; we would need more seeds to trust a single-run value.\n"
        f"Average over years (and across seeds): mean ≈ {mean_overall:,.2f}, "
        f"typical SD ≈ {sd_overall:,.2f}."
    )
    fig.text(0.5, 0.01, guide, ha="center", va="bottom",
             fontsize=8, family="monospace",
             bbox=dict(boxstyle="round,pad=0.6",
                       facecolor="#F5F5F5", edgecolor="#CCCCCC"))

    plt.tight_layout(rect=(0, 0.17, 1, 1))
    filename = filename or f"mc_{metric}.png"
    path = Path(output_dir) / filename
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return str(path)


def render_mc_baseline_distribution(
    csv_path: str,
    metric: str,
    title: str,
    x_label: str,
    output_dir: str,
    filename: Optional[str] = None,
) -> Optional[str]:
    """
    Fine-grained histogram for a single-number-per-seed metric.

    Viz-builder principles applied:
      • Each bar ≈ one seed (bins = max(50, n_seeds))
      • Chart matches message: distribution → histogram with mean/SD markers
      • Title states explicitly that each seed contributes ONE value
      • X-axis clearly labelled with units; Y-axis is integer "# seeds"
      • Footer tells reader exactly what each bar represents
    """
    df = pd.read_csv(csv_path)
    sub = df[df["metric"] == metric].copy()
    sub["value"] = pd.to_numeric(sub["value"], errors="coerce")
    sub = sub.dropna(subset=["value"])
    if sub.empty:
        return None

    values = sub["value"].values
    all_seeds = sorted({int(s) for s in sub["seed"].unique() if s is not None})
    n_seeds = len(all_seeds)
    mean = float(np.mean(values))
    sd = float(np.std(values, ddof=1)) if len(values) > 1 else 0.0
    seed_range = (f" (seeds {all_seeds[0]} through {all_seeds[-1]})"
                  if all_seeds else "")

    fig, ax = plt.subplots(figsize=(11, 5.5))
    fig.patch.set_facecolor("white")

    hist_color = "#2C7BB6"
    mean_color = "#1F4E79"

    # Very fine bins — each bar ≈ one seed
    bins = int(max(50, n_seeds))
    # Y-axis = probability mass: each seed contributes 1/n_seeds, so bar height
    # is the fraction of seeds falling into that bin.
    weights = np.ones_like(values, dtype=float) / float(len(values))
    ax.hist(values, bins=bins, weights=weights, color=hist_color, alpha=0.85,
            edgecolor=hist_color, linewidth=0.4)

    ax.axvline(mean, color=mean_color, linewidth=2.4,
               label=f"mean = {mean:,.2f}", zorder=3)
    if sd > 0:
        ax.axvline(mean - sd, color="#888", linewidth=1.0,
                   linestyle="--", zorder=2)
        ax.axvline(mean + sd, color="#888", linewidth=1.0, linestyle="--",
                   label=f"±1 SD = {sd:,.2f}", zorder=2)

    ax.set_xlabel(x_label, fontsize=11, fontweight="bold", labelpad=10)
    ax.set_ylabel("Probability (fraction of seeds)", fontsize=10)
    import matplotlib.ticker as mticker
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.2f}"))
    ax.legend(loc="upper right", fontsize=9, framealpha=0.9)
    ax.grid(axis="y", alpha=0.25, linewidth=0.5)
    ax.spines[["top", "right"]].set_visible(False)

    ax.set_title(
        f"{title} — each seed contributes ONE value\n"
        f"Distribution across {n_seeds} baseline runs",
        fontsize=13, fontweight="bold", pad=14,
    )

    guide = (
        f"HOW TO READ THIS CHART\n"
        f"Each seed contributes ONE value — shown as its own bar. Bins are ~1 seed wide, "
        f"so you can see every sample.\n"
        f"Monte Carlo across {n_seeds} baseline seeds{seed_range}. "
        f"Solid vertical line = mean across seeds; dashed = ±1 SD.\n\n"
        f"INTERPRETING THIS CHART\n"
        f"  • A TIGHT cluster = metric is STABLE across runs; any single run is reliable.\n"
        f"  • A WIDE spread  = metric is NOISY; more samples needed for a trusted estimate.\n"
        f"Summary: mean = {mean:,.2f}  ±  SD = {sd:,.2f}  across {n_seeds} seeds."
    )
    fig.text(0.5, 0.01, guide, ha="center", va="bottom",
             fontsize=8, family="monospace",
             bbox=dict(boxstyle="round,pad=0.6",
                       facecolor="#F5F5F5", edgecolor="#CCCCCC"))

    plt.tight_layout(rect=(0, 0.18, 1, 1))
    filename = filename or f"mc_{metric}.png"
    path = Path(output_dir) / filename
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return str(path)


# =============================================================================
# Orchestrator — renders the full 8-plot MC baseline bundle
# =============================================================================

_TIME_SERIES_PLOTS = [
    # (metric_key,                       title,                            y_label)
    ("annual_realized_revenue_usd", "Annual Realized Revenue",       "Annual revenue (USD)"),
    ("annual_foregone_revenue_usd", "Annual Foregone Revenue",       "Annual foregone revenue (USD)"),
    ("revenue_capture_rate_pct",    "Revenue Capture Rate",          "Capture rate (% of addressable)"),
    ("population_capture_rate_pct", "Population Capture Rate",       "Capture rate (% of intake queue)"),
    ("ltfu_rate_primary_pct",       "LTFU Rate — Primary Queue",     "LTFU rate (% of served throughput)"),
    ("ltfu_rate_secondary_pct",     "LTFU Rate — Secondary Queue",   "LTFU rate (% of colposcopies)"),
]

_DISTRIBUTION_PLOTS = [
    ("mean_wait_primary_days",   "Mean Wait Time — Primary Screening",   "Mean wait (days)"),
    ("mean_wait_secondary_days", "Mean Wait Time — Secondary Screening", "Mean wait (days)"),
]


def render_all_mc_baseline(csv_path: str, output_dir: str) -> list:
    """Render the full baseline MC bundle — 6 spaghetti + 2 distribution plots."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    saved: list = []

    for metric, title, y_label in _TIME_SERIES_PLOTS:
        p = render_mc_baseline_spaghetti(csv_path, metric, title, y_label, output_dir)
        if p:
            saved.append(p)

    for metric, title, x_label in _DISTRIBUTION_PLOTS:
        p = render_mc_baseline_distribution(csv_path, metric, title, x_label, output_dir)
        if p:
            saved.append(p)

    return saved
