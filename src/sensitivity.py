# =============================================================================
# sensitivity.py
# One-at-a-time (OAT) sensitivity analysis for the NYP screening simulation.
#
# FOR EACH parameter in SENSITIVITY_PARAMS, FOR EACH grid value:
#   1. Temporarily perturb that single parameter in the `parameters` module
#      (in-memory only, auto-reverted when the run finishes)
#   2. Run ONE full 80-year simulation via SimulationRunner with all other
#      parameters at baseline
#   3. Restore the perturbed value, extract output metrics, move to next grid point
# Then: compute elasticities (ΔY% / ΔX%) per (param, output), render heatmaps.
#
# IMPORTANT — this is NOT a scenario analysis:
#   • Each sim run perturbs exactly ONE parameter at a time (single-param)
#   • The perturbation is temporary, in-memory, and auto-reverted — no file
#     on disk is modified; the parameters module is identical before and
#     after each run
#   • No "scenario bundle" is ever assembled; runs are fully independent
#   • This is the minimum mechanism required to compute sensitivity — you
#     cannot measure dY/dX without evaluating Y at multiple values of X
#
# Entry point for the notebook:
#     from sensitivity import run_sweep, compute_elasticities, render_all
#     csv_path = run_sweep()
#     elas = compute_elasticities(csv_path)
#     render_all(elas, "notebooks/Sensitivity Visualizations")
# =============================================================================

import contextlib
import csv
import os
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Union

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


# =============================================================================
# Parameter shortlist (the inputs whose sensitivity we'll measure)
# =============================================================================

class ParamSweep(NamedTuple):
    """
    One parameter to sweep. Grid index 2 (the middle of five values) is the
    baseline; the sweep visits the two lower and two higher values too.
    """
    name: str                                 # "DAILY_PATIENTS" or "CAPACITIES.cytology"
    grid: List[Union[float, int]]             # 5 values; baseline at index 2
    section: str                              # category label (operational / clinical / population)


SENSITIVITY_PARAMS: List[ParamSweep] = [
    # Operational levers
    ParamSweep("DAILY_PATIENTS",                    [1, 2, 2, 3, 4],                 "operational"),
    ParamSweep("CAPACITIES.cytology",               [2, 3, 4, 6, 8],                 "operational"),
    ParamSweep("CAPACITIES.colposcopy",             [2, 3, 4, 6, 8],                 "operational"),
    ParamSweep("CAPACITIES.ldct",                   [2, 3, 4, 6, 8],                 "operational"),
    ParamSweep("CAPACITIES.lung_biopsy",            [2, 3, 4, 6, 8],                 "operational"),
    ParamSweep("CAPACITIES.leep",                   [2, 3, 4, 6, 8],                 "operational"),
    ParamSweep("FOLLOWUP_DELAY_DAYS.colposcopy",    [20, 35, 50, 75, 100],           "operational"),
    ParamSweep("TURNAROUND_DAYS.ldct_notification", [3, 7, 10, 15, 21],              "operational"),
    # Clinical behavior
    ParamSweep("LTFU_PROBS.queue_primary_daily",    [0.03, 0.07, 0.10, 0.15, 0.20],  "clinical"),
    ParamSweep("LTFU_PROBS.queue_secondary_daily",  [0.03, 0.07, 0.10, 0.15, 0.20],  "clinical"),
    ParamSweep("HPV_POSITIVE_COLPOSCOPY_PROB",      [0.40, 0.50, 0.60, 0.70, 0.80],  "clinical"),
    # Population / arrivals
    ParamSweep("SMOKER_RATE",                       [0.08, 0.10, 0.109, 0.13, 0.16], "population"),
    ParamSweep("HPV_POSITIVE_RATE",                 [0.15, 0.20, 0.25, 0.30, 0.35],  "population"),
    ParamSweep("TOTAL_DAILY_ARRIVALS",              [2.0, 2.6, 3.2, 3.8, 4.5],       "population"),
    # Scale sanity check: dollar outputs should respond linearly (ε ≈ 1.0)
    ParamSweep("POPULATION_SCALE_FACTOR",           [50, 75, 100, 150, 200],         "scale_sanity"),
]


# =============================================================================
# Temporary parameter perturbation (in-memory only, auto-reverted)
# =============================================================================

@contextlib.contextmanager
def _perturb_parameter(name: str, value: Any):
    """
    Temporarily change one attribute on the `parameters` module for the
    duration of a single simulation run.

    On enter:  snapshot the original value, apply the perturbed value.
    On exit:   restore the original value (even if the sim raised).

    Nothing on disk is ever modified. This context manager is the minimum
    machinery needed for OAT sensitivity analysis.
    """
    import parameters as cfg
    if "." in name:
        top, sub = name.split(".", 1)
        original = getattr(cfg, top)
        if not isinstance(original, dict):
            raise TypeError(f"{top} is not a dict; cannot perturb {sub}")
        updated = dict(original)
        updated[sub] = value
        try:
            setattr(cfg, top, updated)
            yield cfg
        finally:
            setattr(cfg, top, original)
    else:
        if not hasattr(cfg, name):
            raise AttributeError(f"parameter {name!r} does not exist on parameters.py")
        original = getattr(cfg, name)
        try:
            setattr(cfg, name, value)
            yield cfg
        finally:
            setattr(cfg, name, original)


def _run_one_simulation(param_name: Optional[str], value: Any, seed: int) -> Dict[str, float]:
    """
    Run one full 80-year simulation.
      • If param_name is None → baseline run (no perturbation).
      • Otherwise → perturb that single parameter to `value` for this run only.
    Returns the flat dict of ~113 output metrics from stats_extract.

    Each call uses a UNIQUE TEMPORARY SQLite file so parallel workers don't
    collide on the same DB (SQLite doesn't support concurrent writers).
    The temp file is deleted when the run finishes.
    """
    from runner import SimulationRunner
    from stats_extract import compute_output_metrics, post_warmup_checkpoints

    # Unique DB per run — avoids "database is locked" when workers run in parallel
    tmp_fd, tmp_db = tempfile.mkstemp(suffix=".db", prefix="sa_")
    os.close(tmp_fd)   # only the path is needed; SimulationDB will (re)create it

    def _do_run():
        import parameters as cfg
        sim = SimulationRunner(
            n_days=cfg.SIM_DAYS, seed=seed, use_stable_population=True,
            db_path=tmp_db, reset_db=True,
        )
        metrics = sim.run()
        ckpts = post_warmup_checkpoints(metrics.get("year_checkpoints", []), cfg.WARMUP_YEARS)
        n_workdays = sum(1 for d in range(cfg.WARMUP_YEARS * 365, cfg.SIM_DAYS) if d % 7 not in (5, 6))
        outputs = compute_output_metrics(
            metrics=metrics, ckpts=ckpts, n_workdays=n_workdays,
            capacities=cfg.CAPACITIES, scale_factor=cfg.POPULATION_SCALE_FACTOR,
        )
        sim.close_db()
        return outputs

    try:
        if param_name is None:
            return _do_run()
        with _perturb_parameter(param_name, value):
            return _do_run()
    finally:
        for ext in ("", "-shm", "-wal"):
            p = tmp_db + ext
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


# =============================================================================
# Parallel sweep driver
# =============================================================================

def _ensure_sys_path():
    """Worker-side: make sure src/ and ModelParameters/ are importable."""
    here = Path(__file__).resolve().parent.parent  # project root
    for p in ("src", "ModelParameters", "."):
        cand = str(here / p) if p != "." else str(here)
        if cand not in sys.path:
            sys.path.insert(0, cand)


def _worker(job: Dict[str, Any]) -> Dict[str, Any]:
    _ensure_sys_path()
    from sensitivity import _run_one_simulation
    t0 = time.time()
    outputs = _run_one_simulation(job["param_name"], job["value"], job["seed"])
    return {
        "param": job["param_label"], "grid_index": job["grid_index"],
        "grid_value": job["value"], "seed": job["seed"],
        "elapsed_sec": time.time() - t0, "outputs": outputs,
    }


def run_sweep(
    seed: int = 42,
    n_workers: Optional[int] = None,
    out_csv: Optional[str] = None,
    progress: bool = True,
) -> str:
    """
    Run the full OAT sweep: 1 baseline + (15 params × 5 grid points) = 76 full 80-yr sims.
    All runs use the same random seed (fixed) so differences are attributable to
    the perturbed parameter, not to stochastic noise. Parallelized across workers.
    Writes results to a long-format CSV; returns the file path.
    """
    jobs: List[Dict[str, Any]] = [
        {"param_label": "__baseline__", "param_name": None, "value": None,
         "grid_index": -1, "seed": seed}
    ]
    for p in SENSITIVITY_PARAMS:
        for idx, v in enumerate(p.grid):
            jobs.append({
                "param_label": p.name, "param_name": p.name, "value": v,
                "grid_index": idx, "seed": seed,
            })

    n_workers = n_workers or max(1, (os.cpu_count() or 1) - 1)
    if out_csv is None:
        here = Path(__file__).resolve().parent.parent
        results_dir = here / "notebooks" / "Sensitivity Visualizations"
        results_dir.mkdir(parents=True, exist_ok=True)
        out_csv = str(results_dir / f"sweep_seed{seed}.csv")

    if progress:
        print(f"[sweep] {len(jobs)} runs × full 80 yr each, {n_workers} parallel workers")
        print(f"[sweep] writing → {out_csv}")

    results: List[Dict[str, Any]] = []
    t_start = time.time()
    with ProcessPoolExecutor(max_workers=n_workers) as ex:
        futures = {ex.submit(_worker, j): j for j in jobs}
        for i, fut in enumerate(as_completed(futures), start=1):
            r = fut.result()
            results.append(r)
            if progress:
                elapsed = time.time() - t_start
                eta = (elapsed / i) * (len(jobs) - i) / 60
                print(f"  [{i:>3}/{len(jobs)}] {r['param']:<40} "
                      f"grid={str(r['grid_value']):<8} {r['elapsed_sec']:.0f}s  ETA {eta:.1f}min")

    output_names = sorted(results[0]["outputs"].keys()) if results else []
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["param", "grid_index", "grid_value", "output_name", "output_value", "seed"])
        for r in results:
            for name in output_names:
                w.writerow([r["param"], r["grid_index"], r["grid_value"], name,
                            r["outputs"].get(name), r["seed"]])

    if progress:
        print(f"[sweep] done in {(time.time() - t_start)/60:.1f} min  "
              f"({len(results) * len(output_names):,} rows written)")
    return out_csv


# =============================================================================
# Monte Carlo sweep — the same OAT design, replicated across many seeds
# =============================================================================
#
# run_sweep() above fixes the random seed across all runs so any observed
# change is purely due to the perturbed parameter. run_mc_sweep() runs the
# same sweep MANY times with different seeds, so each (param, grid_value)
# point has n_seeds independent samples — letting us visualize the
# stochastic spread of every output as the parameter is varied.


def run_mc_sweep(
    n_seeds: int,
    seed_start: int = 42,
    n_workers: Optional[int] = None,
    out_csv: Optional[str] = None,
    progress: bool = True,
) -> str:
    """
    Run the OAT sweep across `n_seeds` independent random seeds.

    Total runs = n_seeds × (1 baseline + 15 params × 5 grid points) = 76 × n_seeds.
    Each run is a full 80-year simulation. All runs are independent and
    parallelized across available cores.

    Output is a long-format CSV with one row per
    (seed, param, grid_index, grid_value, output_name). Downstream rendering
    groups by (param, output_name) and plots one line per seed.

    Nothing about `n_seeds` is hardcoded — change this argument to scale up
    when you want more samples. 5 seeds is reasonable for an initial look;
    30+ for a proper Monte Carlo characterization.
    """
    if n_seeds < 1:
        raise ValueError(f"n_seeds must be ≥ 1, got {n_seeds}")

    seeds = list(range(seed_start, seed_start + n_seeds))
    jobs: List[Dict[str, Any]] = []
    for seed in seeds:
        jobs.append({
            "param_label": "__baseline__", "param_name": None, "value": None,
            "grid_index": -1, "seed": seed,
        })
        for p in SENSITIVITY_PARAMS:
            for idx, v in enumerate(p.grid):
                jobs.append({
                    "param_label": p.name, "param_name": p.name, "value": v,
                    "grid_index": idx, "seed": seed,
                })

    n_workers = n_workers or max(1, (os.cpu_count() or 1) - 1)
    if out_csv is None:
        here = Path(__file__).resolve().parent.parent
        results_dir = here / "notebooks" / "Sensitivity Monte Carlo"
        results_dir.mkdir(parents=True, exist_ok=True)
        out_csv = str(results_dir / f"mc_sweep_n{n_seeds}_start{seed_start}.csv")

    if progress:
        print(f"[mc_sweep] {len(jobs)} runs = {n_seeds} seeds × 76 per-seed sweep")
        print(f"[mc_sweep] {n_workers} parallel workers → {out_csv}")

    results: List[Dict[str, Any]] = []
    t_start = time.time()
    with ProcessPoolExecutor(max_workers=n_workers) as ex:
        futures = {ex.submit(_worker, j): j for j in jobs}
        for i, fut in enumerate(as_completed(futures), start=1):
            r = fut.result()
            results.append(r)
            if progress and (i % max(1, len(jobs) // 20) == 0 or i == len(jobs)):
                elapsed = time.time() - t_start
                eta = (elapsed / i) * (len(jobs) - i) / 60
                print(f"  [{i:>4}/{len(jobs)}]  elapsed {elapsed/60:.1f}min  ETA {eta:.1f}min")

    output_names = sorted(results[0]["outputs"].keys()) if results else []
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["param", "grid_index", "grid_value", "output_name", "output_value", "seed"])
        for r in results:
            for name in output_names:
                w.writerow([r["param"], r["grid_index"], r["grid_value"], name,
                            r["outputs"].get(name), r["seed"]])

    if progress:
        print(f"[mc_sweep] done in {(time.time() - t_start)/60:.1f} min  "
              f"({len(results) * len(output_names):,} rows written)")
    return out_csv


# =============================================================================
# Elasticity computation
# =============================================================================

def compute_elasticities(csv_path: str) -> pd.DataFrame:
    """
    Load the sweep CSV, return an elasticity matrix.

    Rows    : parameter names
    Columns : output metric names (~113)
    Cells   : ε = ΔY% / ΔX%  (slope of log(Y) vs log(X) across the 5 grid points)

    Elasticity 0 = output is insensitive to the parameter.
    Elasticity 1 = 1% change in input → 1% change in output (linear pass-through).
    Negative   = output decreases as parameter rises.
    """
    df = pd.read_csv(csv_path)
    baseline = df[df["param"] == "__baseline__"]
    if baseline.empty:
        raise ValueError("sweep CSV is missing the __baseline__ row")
    y_base = baseline.set_index("output_name")["output_value"].to_dict()

    sweep = df[df["param"] != "__baseline__"].copy()
    sweep["output_value"] = pd.to_numeric(sweep["output_value"], errors="coerce")

    params = sorted(sweep["param"].unique())
    outputs = sorted(sweep["output_name"].unique())
    elas = pd.DataFrame(index=params, columns=outputs, dtype=float)

    for param in params:
        rows = sweep[sweep["param"] == param]
        try:
            x_base = float(rows[rows["grid_index"] == 2]["grid_value"].iloc[0])
        except (IndexError, ValueError):
            continue
        if x_base == 0:
            continue
        for out_name in outputs:
            yb = y_base.get(out_name)
            if yb is None or not np.isfinite(yb) or yb == 0:
                continue
            pts = rows[rows["output_name"] == out_name]
            x = pts["grid_value"].astype(float).values
            y = pts["output_value"].astype(float).values
            mask = np.isfinite(x) & np.isfinite(y) & (x > 0) & (y > 0)
            if mask.sum() < 3:
                continue
            try:
                slope = np.polyfit(np.log(x[mask]), np.log(y[mask]), 1)[0]
                elas.loc[param, out_name] = float(slope)
            except (np.linalg.LinAlgError, ValueError):
                continue
    return elas


def top_sensitive_pairs(elas: pd.DataFrame, n: int = 15) -> pd.DataFrame:
    """Rank the top-N (param, output) pairs by absolute elasticity."""
    s = elas.stack().reset_index()
    s.columns = ["param", "output", "elasticity"]
    s = s.dropna()
    s["magnitude"] = s["elasticity"].abs()
    return s.sort_values("magnitude", ascending=False).head(n).reset_index(drop=True)


# =============================================================================
# Heatmap rendering
# =============================================================================

def _section(output_name: str) -> str:
    return output_name.split(".", 1)[0]


_SECTION_DESCRIPTIONS = {
    "cervical":     "Primary cervical screening (Pap + HPV tests, uptake, abnormal rates)",
    "colposcopy":   "Secondary cervical diagnostic (colposcopy procedures + CIN diagnoses)",
    "finance":      "Revenue outputs — realized, foregone, and unserved demand (real NYP $)",
    "ltfu":         "Loss-to-follow-up counts by node (unscreened, queue dropout, post-colposcopy)",
    "lung":         "Primary lung screening (LDCT uptake, funnel, Lung-RADS distribution)",
    "lung_bx":      "Secondary lung diagnostic (biopsy funnel + malignancy rate)",
    "mortality":    "Deaths — simulation scale and extrapolated to real NYC population",
    "pop":          "Population dynamics (pool size, throughput, arrivals by source)",
    "queue":        "Queues & capacities — daily demand, overflow, wait times, utilization",
    "treatment":    "Treatment volumes (LEEP, cone biopsy, lung treatment) + completion rates",
    "scale_sanity": "Linear-scale sanity check (should give ε ≈ 1 on dollar outputs)",
}


_SECTION_TITLE = {
    "cervical":     "Primary Cervical Screening",
    "colposcopy":   "Cervical Follow-Up Procedures",
    "finance":      "Financial Outcomes",
    "ltfu":         "Loss-to-Follow-Up",
    "lung":         "Primary Lung Screening",
    "lung_bx":      "Lung Follow-Up Procedures",
    "mortality":    "Mortality Outcomes",
    "pop":          "Population Dynamics",
    "queue":        "Queues & Capacity Utilization",
    "treatment":    "Treatment Procedures",
    "scale_sanity": "Population Scale Sanity Check",
}


_PARAM_INFO: Dict[str, Dict[str, str]] = {
    "DAILY_PATIENTS": {
        "name": "Daily screening-slot bandwidth",
        "explain": "how many patient slots per day are available across the primary-screening schedule",
    },
    "CAPACITIES.cytology": {
        "name": "Cytology scheduling capacity",
        "explain": "the number of cytology (Pap test) appointment slots NYP offers each day",
    },
    "CAPACITIES.colposcopy": {
        "name": "Colposcopy scheduling capacity",
        "explain": "the number of colposcopy appointment slots NYP offers each day (colposcopy is the cervical follow-up procedure after an abnormal primary screen)",
    },
    "CAPACITIES.ldct": {
        "name": "LDCT scheduling capacity",
        "explain": "the number of LDCT (low-dose CT) scan slots NYP offers each day for lung screening",
    },
    "CAPACITIES.lung_biopsy": {
        "name": "Lung biopsy scheduling capacity",
        "explain": "the number of lung biopsy appointment slots NYP offers each day (biopsy is the lung follow-up procedure after a suspicious LDCT)",
    },
    "CAPACITIES.leep": {
        "name": "LEEP scheduling capacity",
        "explain": "the number of LEEP (loop electrosurgical excision) treatment slots NYP offers each day (LEEP treats CIN2 / CIN3 lesions)",
    },
    "FOLLOWUP_DELAY_DAYS.colposcopy": {
        "name": "Delay to colposcopy follow-up",
        "explain": "the number of days between an abnormal cervical primary result and the scheduled colposcopy appointment",
    },
    "TURNAROUND_DAYS.ldct_notification": {
        "name": "LDCT result turnaround time",
        "explain": "the number of days between an LDCT scan and the patient receiving their Lung-RADS result",
    },
    "LTFU_PROBS.queue_primary_daily": {
        "name": "Primary-queue daily drop-off probability",
        "explain": "the daily probability a patient waiting in the primary-screening queue drops out before their appointment (loss to follow-up)",
    },
    "LTFU_PROBS.queue_secondary_daily": {
        "name": "Secondary-queue daily drop-off probability",
        "explain": "the daily probability a patient waiting in the secondary-procedure queue (colposcopy or lung biopsy) drops out before their appointment",
    },
    "HPV_POSITIVE_COLPOSCOPY_PROB": {
        "name": "HPV-positive to colposcopy referral rate",
        "explain": "the share of HPV-positive primary screens that get referred on to colposcopy",
    },
    "SMOKER_RATE": {
        "name": "Smoker prevalence in the population",
        "explain": "the share of the eligible population who are current or recent smokers (this drives USPSTF lung-screening eligibility)",
    },
    "HPV_POSITIVE_RATE": {
        "name": "HPV positivity rate",
        "explain": "the share of cervical primary screens whose HPV component returns positive",
    },
    "TOTAL_DAILY_ARRIVALS": {
        "name": "Total daily patient arrivals",
        "explain": "the total number of new patients arriving per day into the NYP screening pool",
    },
    "POPULATION_SCALE_FACTOR": {
        "name": "Population scale factor",
        "explain": "the multiplier used to scale simulation-pool outputs up to the full NYP addressable population — included as a linearity sanity check (dollar outputs should move 1-for-1 with this factor)",
    },
}


def _param_display(param: str) -> str:
    """Human-readable label for a parameter name."""
    info = _PARAM_INFO.get(param)
    return info["name"] if info else param.replace("_", " ").title()


def _param_explanation(param: str) -> str:
    """Intuitive NYP-sim-specific description of the parameter."""
    info = _PARAM_INFO.get(param)
    return info["explain"] if info else (
        f"the simulation parameter '{param}' varied across its configured sweep grid"
    )


def _output_display(output: str) -> str:
    """Human-readable label for an output metric name.

    Strips the leading section prefix ("queue.overflow.ldct_total" -> "overflow
    ldct total") and replaces separators with spaces for title readability.
    """
    short = output.split(".", 1)[1] if "." in output else output
    return short.replace(".", " ").replace("_", " ")


# -----------------------------------------------------------------------------
# Layout helpers — shared across all sensitivity renderers to keep text off
# the plot area and guarantee consistent title / subtitle / description bands.
# -----------------------------------------------------------------------------

import textwrap


def _seed_range(seeds) -> str:
    """Return 'seeds X–Y' for use in subtitles and footers."""
    try:
        sl = sorted({int(s) for s in seeds if s is not None})
    except (TypeError, ValueError):
        sl = []
    return f"seeds {sl[0]}–{sl[-1]}" if sl else ""


def _finalize_sensitivity_figure(
    fig,
    *,
    title: str,
    subtitle: str = "",
    description: str = "",
    footer: str = "",
    top: float = 0.91,
    bottom: float = 0.20,
    left: float = 0.08,
    right: float = 0.95,
    wrap_width: int = 140,
) -> None:
    """
    Attach a professional title, italic subtitle, description paragraph, and
    footer to the figure, reserving space so nothing overlaps the plot area.
    Positions are anchored in absolute inches so behavior is stable across
    figure heights.
    """
    fig_h = fig.get_size_inches()[1]

    title_y = 1.0 - max(0.015, 0.30 / fig_h)
    subtitle_y = 1.0 - max(0.055, 0.62 / fig_h)
    fig.suptitle(title, fontsize=14, fontweight="bold", y=title_y)
    if subtitle:
        fig.text(0.5, subtitle_y, subtitle, ha="center",
                 fontsize=11, color="#444", style="italic")

    foot_y = max(0.010, 0.15 / fig_h)
    desc_y = max(0.035, 0.50 / fig_h)

    n_lines = 0
    if description:
        wrapped = "\n".join(
            textwrap.fill(p.strip(), width=wrap_width)
            for p in description.strip().split("\n") if p.strip()
        )
        n_lines = wrapped.count("\n") + 1
        fig.text(0.5, desc_y, wrapped, ha="center", va="bottom",
                 fontsize=9.5, color="#333", parse_math=False)

    if footer:
        fig.text(0.5, foot_y, footer, ha="center",
                 fontsize=8, color="#888", style="italic")

    needed_bottom_inches = 1.25 + 0.18 * n_lines
    needed_bottom_frac = needed_bottom_inches / fig_h
    bottom_eff = max(bottom, needed_bottom_frac)

    needed_top_inches = 1.00 if subtitle else 0.65
    top_eff = min(top, 1.0 - needed_top_inches / fig_h)

    plt.subplots_adjust(top=top_eff, bottom=bottom_eff, left=left, right=right)


def _external_legend(ax, loc: str = "upper right") -> None:
    """Place a legend OUTSIDE the axes so it never overlaps plot content."""
    handles, labels = ax.get_legend_handles_labels()
    if not handles:
        return
    if loc == "upper right":
        anchor, loc_key = (1.005, 1.0), "upper left"
    elif loc == "lower right":
        anchor, loc_key = (1.005, 0.0), "lower left"
    elif loc == "right":
        anchor, loc_key = (1.005, 0.5), "center left"
    else:
        anchor, loc_key = (1.005, 1.0), "upper left"
    ax.legend(handles, labels, loc=loc_key, bbox_to_anchor=anchor,
              frameon=True, framealpha=0.95, edgecolor="#CCC", fontsize=9)


_OAT_EXPLAINER = (
    "This is a One-at-a-Time (OAT) sensitivity analysis: we pick one input parameter, sweep it across a small grid "
    "of plausible values, and hold every other parameter fixed at its baseline. The full 80-year simulation is "
    "then re-run at each grid point, so every chart here captures the isolated effect of changing that single "
    "parameter. Elasticity (ε) is the percent change in the output per percent change in the input, measured from "
    "a log-log fit across the grid; ε is positive when the output rises with the parameter, negative when it falls, "
    "and |ε| > 1 means the output amplifies the input change."
)


def _pick_worked_example(sub: pd.DataFrame) -> Optional[str]:
    """
    Find the cell with the largest |ε| in this section's elasticity matrix and
    build a plain-English example sentence for the reader.
    """
    abs_vals = sub.abs()
    # argmax over a DataFrame returns a (row, col) label pair via stack().idxmax()
    stacked = abs_vals.stack()
    if stacked.empty or stacked.isna().all():
        return None
    row, col = stacked.idxmax()
    eps = float(sub.loc[row, col])
    if not np.isfinite(eps):
        return None
    # Short column label (strip section prefix for readability)
    short_col = col.split(".", 1)[1] if "." in col else col
    direction = "rises" if eps > 0 else "falls"
    pct10 = eps * 10
    return (
        f"EXAMPLE FROM THIS CHART\n"
        f"Cell [{row}]  →  [{short_col}]  has ε = {eps:+.2f}.\n"
        f"Meaning: a 10 % increase in {row} {direction} "
        f"{short_col} by roughly {abs(pct10):.1f} %."
    )


def render_section_heatmap(elas: pd.DataFrame, section: str, output_dir: str, vmax: float = 1.5) -> Optional[str]:
    """Heatmap of every output in one section vs every parameter. ε per cell.

    Rows are parameters rendered with their human-readable labels; columns are
    the outputs in this section. Colorbar sits OUTSIDE the axes; description
    paragraph explains OAT methodology and how to read the colors.
    """
    cols = [o for o in elas.columns if _section(o) == section]
    if not cols:
        return None
    sub = elas[cols]
    labels = [o.replace(section + ".", "", 1).replace("_", " ") for o in cols]
    param_labels = [_param_display(p) for p in sub.index]

    section_name = _SECTION_TITLE.get(section, section.replace("_", " ").title())
    section_blurb = _SECTION_DESCRIPTIONS.get(section, "")
    example = _pick_worked_example(sub)

    fig_w = max(12, 0.60 * len(cols) + 6.5)
    fig_h = max(7.5, 0.42 * len(sub.index) + 4.8)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    fig.patch.set_facecolor("white")

    data = sub.values.astype(float)
    im = ax.imshow(data, aspect="auto", cmap="RdBu_r", vmin=-vmax, vmax=vmax)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax.set_yticks(range(len(sub.index)))
    ax.set_yticklabels(param_labels, fontsize=9)
    ax.set_xlabel("Output metric", fontsize=10, labelpad=8)
    ax.set_ylabel("Input parameter varied", fontsize=10, labelpad=8)

    for i in range(data.shape[0]):
        for j in range(data.shape[1]):
            v = data[i, j]
            if np.isfinite(v):
                ax.text(j, i, f"{v:+.2f}", ha="center", va="center",
                        fontsize=7, color=("white" if abs(v) > 0.8 else "black"))
            else:
                ax.text(j, i, "·", ha="center", va="center", fontsize=10, color="#888")

    cbar = fig.colorbar(im, ax=ax, shrink=0.75, pad=0.015)
    cbar.set_label("Elasticity (ε)", fontsize=10)
    cbar.ax.tick_params(labelsize=8)

    description_parts = [
        f"This heatmap shows how the {section_blurb.lower() if section_blurb else section} outputs respond when "
        f"each input parameter is varied, one parameter at a time.",
        _OAT_EXPLAINER,
        (
            "How to read the colors: red cells mean the output rises when the parameter rises; blue cells mean the "
            "output falls when the parameter rises; near-white cells mean the parameter has little or no effect on "
            "that output. Each cell shows the ε value. A dot (·) means ε could not be computed (often because the "
            "baseline output was zero, so a percent change is undefined)."
        ),
    ]
    if example:
        description_parts.append(example.replace("EXAMPLE FROM THIS CHART\n", "Worked example from this chart: ")
                                          .replace("\n", " "))
    description = "\n".join(description_parts)

    _finalize_sensitivity_figure(
        fig,
        title=f"Parameter Sensitivity — {section_name}",
        subtitle=section_blurb,
        description=description,
        footer="One-at-a-time (OAT) sweep of 15 parameters × 5 grid values, one full 80-year simulation per grid point.",
        top=0.91, bottom=0.20, left=0.22, right=0.94,
    )
    path = Path(output_dir) / f"sensitivity_{section}.png"
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return str(path)


def render_top_pairs_table(top_df: pd.DataFrame, output_dir: str) -> str:
    """Top-N highest-leverage (parameter, output, ε) pairs as a ranked table.

    Parameters use human-readable labels; outputs retain their engineering names
    (full dotted path) so the reader can cross-reference against the section
    heatmaps and the pair plots.
    """
    n = len(top_df)
    fig, ax = plt.subplots(figsize=(14, max(6.5, 0.40 * n + 5.5)))
    fig.patch.set_facecolor("white")
    ax.axis("off")

    cell_data = [
        [i + 1, _param_display(r["param"]), r["output"], f"{r['elasticity']:+.3f}"]
        for i, (_, r) in enumerate(top_df.iterrows())
    ]
    t = ax.table(
        cellText=cell_data,
        colLabels=["Rank", "Input parameter", "Output metric", "Elasticity ε"],
        loc="center", cellLoc="left",
        colWidths=[0.06, 0.34, 0.48, 0.12],
    )
    t.auto_set_font_size(False)
    t.set_fontsize(9.5)
    t.scale(1, 1.45)
    for j in range(4):
        c = t[0, j]
        c.set_facecolor("#2C3E50")
        c.set_text_props(color="white", fontweight="bold")
    # Zebra-stripe body rows for readability
    for r_idx in range(1, n + 1):
        bg = "#FFFFFF" if r_idx % 2 else "#F5F7FA"
        for j in range(4):
            t[r_idx, j].set_facecolor(bg)

    description_parts = [
        f"These are the {n} highest-leverage input-to-output relationships in the NYP cancer screening simulation, "
        f"ranked by the absolute value of elasticity. The higher a pair sits on this list, the more sensitive that "
        f"output is to changes in that parameter — meaning small changes to the parameter produce outsized changes "
        f"in the output.",
        _OAT_EXPLAINER,
    ]
    if not top_df.empty:
        top = top_df.iloc[0]
        eps = float(top["elasticity"])
        direction = "rises" if eps > 0 else "falls"
        description_parts.append(
            f"Worked example from row 1: {_param_display(top['param'])} has ε = {eps:+.2f} for '{top['output']}'. "
            f"A 10% increase in {_param_display(top['param']).lower()} {direction} that output by roughly "
            f"{abs(eps) * 10:.1f}% on average."
        )
    description = "\n".join(description_parts)

    _finalize_sensitivity_figure(
        fig,
        title=f"Top {n} Most Sensitive Parameter–Output Pairs",
        subtitle="Ranked by elasticity magnitude (|ε|); largest leverage first",
        description=description,
        footer="One-at-a-time (OAT) sweep of 15 parameters × 5 grid values, one full 80-year simulation per grid point.",
        top=0.91, bottom=0.24, left=0.04, right=0.96,
    )
    path = Path(output_dir) / "sensitivity_top_pairs.png"
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return str(path)


# =============================================================================
# Sensitive-pair plots — one chart per (input parameter, output metric) pair
# =============================================================================
#
# Design per professor's sketch: one line per chart, X = input parameter value
# (raw units), Y = output metric value (raw units). No normalization, no
# multi-curve overlays. Replaces the older % change-from-baseline sweeps.

# Parameters the user specifically asked to plot, in their original listing
# order. Any other parameter in SENSITIVITY_PARAMS is still valid — pass a
# custom list to render_sensitive_pair_plots() to override.
IMPORTANT_PARAMETERS_FOR_PAIR_PLOTS: List[str] = [
    "DAILY_PATIENTS",
    "CAPACITIES.cytology",
    "CAPACITIES.colposcopy",
    "CAPACITIES.ldct",
    "CAPACITIES.lung_biopsy",
    "CAPACITIES.leep",
    "LTFU_PROBS.queue_primary_daily",
    "LTFU_PROBS.queue_secondary_daily",
    "HPV_POSITIVE_RATE",
    "SMOKER_RATE",
    "FOLLOWUP_DELAY_DAYS.colposcopy",
]


def _output_unit_hint(output_name: str) -> str:
    """Return a short unit hint for an output name (for axis labels)."""
    if output_name.endswith("_usd"):
        return "USD (real NYP dollars)"
    if output_name.endswith("_pct"):
        return "percent"
    if "wait" in output_name:
        return "days"
    if "overflow" in output_name or "total" in output_name or "count" in output_name:
        return "count"
    return "value"


def render_sensitive_pair_plot(
    param: str,
    output: str,
    sweep_df: pd.DataFrame,
    eps: float,
    output_dir: str,
    rank: int = 0,
) -> Optional[str]:
    """
    Render ONE plot showing how a single output varies with a single input parameter.

    X-axis : the parameter's 5 grid values (raw units, native scale).
    Y-axis : the output's value at each grid point (raw units, native scale).
    One line, five markers. No normalization.

    A dashed vertical line marks the parameter's baseline value (grid_index 2).
    ε is shown as a small annotation in the lower-right corner.

    Pure rendering — no simulations run. All data from the sweep CSV.
    """
    rows = sweep_df[
        (sweep_df["param"] == param) & (sweep_df["output_name"] == output)
    ].sort_values("grid_index")
    if rows.empty:
        return None

    x = rows["grid_value"].astype(float).values
    y = pd.to_numeric(rows["output_value"], errors="coerce").astype(float).values
    if not np.all(np.isfinite(y)):
        # Keep only finite pairs; if all NaN, skip this pair
        mask = np.isfinite(x) & np.isfinite(y)
        if mask.sum() < 2:
            return None
        x, y = x[mask], y[mask]

    # Parameter baseline = grid_index 2 by convention in SENSITIVITY_PARAMS
    baseline_x: Optional[float] = None
    try:
        bx = rows[rows["grid_index"] == 2]["grid_value"]
        if not bx.empty:
            baseline_x = float(bx.iloc[0])
    except (IndexError, ValueError):
        baseline_x = None

    fig, ax = plt.subplots(figsize=(12, 7))
    fig.patch.set_facecolor("white")

    short_output = _output_display(output)
    unit_hint = _output_unit_hint(output)
    param_name = _param_display(param)

    ax.plot(x, y, marker="o", markersize=9, linewidth=2.4, color="#2C7BB6",
            markeredgecolor="white", markeredgewidth=1.5,
            label="Simulation result at each grid value")

    if baseline_x is not None:
        ax.axvline(baseline_x, color="#888", linewidth=1.0, linestyle="--",
                   alpha=0.7, zorder=0,
                   label=f"Baseline parameter value ({baseline_x:g})")

    ax.set_xlabel(param_name, fontsize=11, fontweight="bold", labelpad=10)
    ax.set_ylabel(f"{short_output.capitalize()}  ({unit_hint})",
                  fontsize=11, fontweight="bold", labelpad=10)
    ax.grid(True, alpha=0.25, linestyle="-", linewidth=0.5)
    ax.spines[["top", "right"]].set_visible(False)

    try:
        if np.nanmax(np.abs(y)) >= 1000:
            ax.yaxis.set_major_formatter(
                plt.FuncFormatter(lambda v, _: f"{v:,.0f}")
            )
    except ValueError:
        pass

    _external_legend(ax, loc="upper right")

    direction = ("rises" if eps > 0
                 else "falls" if eps < 0
                 else "stays roughly constant")
    eps_clause = (
        f"Elasticity ε = {eps:+.2f} — a 10% increase in {param_name.lower()} {direction} this output by "
        f"roughly {abs(eps) * 10:.1f}%."
    )
    description = (
        f"This chart asks: how does the output change when we vary only {param_name.lower()}? "
        f"In this simulation, {param_name.lower()} is {_param_explanation(param)}. "
        f"{_OAT_EXPLAINER} "
        f"The X-axis shows the {len(x)} grid values we tested for this parameter. The Y-axis shows the resulting "
        f"output in its native units ({unit_hint}). Each point is one full 80-year simulation run at that "
        f"parameter value (single fixed seed). The dashed vertical line marks the baseline parameter value. "
        f"{eps_clause}"
    )

    _finalize_sensitivity_figure(
        fig,
        title=f"{short_output.capitalize()}",
        subtitle=f"Sensitivity to {param_name.lower()}",
        description=description,
        footer=f"One-at-a-time (OAT) sweep · 1 seed × {len(x)} grid points.",
        top=0.91, bottom=0.28, left=0.08, right=0.78,
    )

    safe_param = param.replace("/", "_")
    safe_output = output.replace("/", "_")
    filename = f"pair_{rank:02d}_{safe_param}_to_{safe_output}.png"
    path = Path(output_dir) / filename
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return str(path)


def render_sensitive_pair_plots(
    csv_path: str,
    elas: pd.DataFrame,
    output_dir: str,
    params: Optional[List[str]] = None,
    n_outputs_per_param: int = 3,
) -> list:
    """
    For each parameter in `params` (default: IMPORTANT_PARAMETERS_FOR_PAIR_PLOTS),
    pick its top-N most-sensitive outputs (by |ε|) and render one plot per pair.

    File naming: pair_<rank>_<param>_to_<output>.png, ranked by |ε| across all pairs.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    if params is None:
        params = IMPORTANT_PARAMETERS_FOR_PAIR_PLOTS

    df = pd.read_csv(csv_path)
    sweep = df[df["param"] != "__baseline__"].copy()
    sweep["output_value"] = pd.to_numeric(sweep["output_value"], errors="coerce")

    # Build (param, output, ε) triples and rank globally by |ε|
    triples = []
    for param in params:
        if param not in elas.index:
            continue
        top_outs = elas.loc[param].abs().dropna().nlargest(n_outputs_per_param).index.tolist()
        for out_name in top_outs:
            eps = elas.loc[param, out_name]
            if np.isfinite(eps):
                triples.append((param, out_name, float(eps)))

    triples.sort(key=lambda t: -abs(t[2]))

    saved = []
    for rank, (param, output, eps) in enumerate(triples, start=1):
        path = render_sensitive_pair_plot(
            param=param,
            output=output,
            sweep_df=sweep,
            eps=eps,
            output_dir=output_dir,
            rank=rank,
        )
        if path:
            saved.append(path)
    return saved


# =============================================================================
# Monte Carlo pair plots — same (X, Y) structure as render_sensitive_pair_plot,
# but with multiple seeds visualized as a spaghetti + mean + ±1 SD band.
# =============================================================================

def render_mc_pair_plot(
    param: str,
    output: str,
    mc_df: pd.DataFrame,
    eps: float,
    output_dir: str,
    rank: int = 0,
) -> Optional[str]:
    """
    Render ONE Monte Carlo style plot for a single (input, output) pair.

    Visual structure (same axes as render_sensitive_pair_plot):
      • X-axis : the parameter's grid values (raw units)
      • Y-axis : the output's value (raw units, native scale)
      • Thin semi-transparent line per seed — each line is one full 80-year run
      • Bold mean line (across seeds) overlaid
      • Shaded ±1 SD envelope
      • Dashed vertical line at the baseline parameter value (grid_index=2)
      • ε annotation in the lower-right corner; n_seeds stated in the title

    n_seeds is inferred from the data, not hardcoded. The chart scales
    naturally as more seeds are added to the input CSV.
    """
    rows = mc_df[
        (mc_df["param"] == param) & (mc_df["output_name"] == output)
    ].copy()
    if rows.empty:
        return None

    rows["output_value"] = pd.to_numeric(rows["output_value"], errors="coerce")
    rows["grid_value"] = rows["grid_value"].astype(float)

    seeds = sorted(rows["seed"].unique())
    n_seeds = len(seeds)
    if n_seeds < 1:
        return None

    # Grid values in index order (take mean across seeds; all seeds share grid)
    grid_pairs = (
        rows[["grid_index", "grid_value"]]
        .drop_duplicates()
        .sort_values("grid_index")
        .reset_index(drop=True)
    )
    x_grid = grid_pairs["grid_value"].values

    # Pivot: rows = grid_index, columns = seed, values = output_value
    pivot = rows.pivot_table(
        index="grid_index", columns="seed",
        values="output_value", aggfunc="first",
    ).sort_index()

    y_mean = pivot.mean(axis=1).values
    y_std = pivot.std(axis=1, ddof=1).values if n_seeds > 1 else np.zeros_like(y_mean)

    # Baseline parameter value — grid index 2 by convention
    baseline_x: Optional[float] = None
    try:
        bx = grid_pairs[grid_pairs["grid_index"] == 2]["grid_value"]
        if not bx.empty:
            baseline_x = float(bx.iloc[0])
    except (IndexError, ValueError):
        baseline_x = None

    fig, ax = plt.subplots(figsize=(12, 7))
    fig.patch.set_facecolor("white")

    short_output = _output_display(output)
    unit_hint = _output_unit_hint(output)
    param_name = _param_display(param)

    spaghetti_color = "#2C7BB6"
    alpha_seed = min(0.7, max(0.15, 1.5 / n_seeds))
    first = True
    for seed in seeds:
        col = pivot.get(seed)
        if col is None:
            continue
        y_seed = col.values
        ax.plot(
            x_grid, y_seed,
            color=spaghetti_color, alpha=alpha_seed,
            linewidth=1.0, zorder=2,
            label="Individual simulation runs" if first else None,
        )
        first = False

    if n_seeds > 1:
        ax.fill_between(
            x_grid, y_mean - y_std, y_mean + y_std,
            color=spaghetti_color, alpha=0.15, zorder=1,
            label="±1 standard deviation across runs",
        )

    mean_color = "#1F4E79"
    ax.plot(
        x_grid, y_mean,
        color=mean_color, linewidth=2.8,
        marker="o", markersize=9,
        markeredgecolor="white", markeredgewidth=1.5,
        zorder=3, label=f"Average across {n_seeds} runs",
    )

    if baseline_x is not None:
        ax.axvline(
            baseline_x, color="#888", linewidth=1.0, linestyle="--",
            alpha=0.7, zorder=0, label=f"Baseline parameter value ({baseline_x:g})",
        )

    ax.set_xlabel(param_name, fontsize=11, fontweight="bold", labelpad=10)
    ax.set_ylabel(f"{short_output.capitalize()}  ({unit_hint})",
                  fontsize=11, fontweight="bold", labelpad=10)
    ax.grid(True, alpha=0.25, linestyle="-", linewidth=0.5)
    ax.spines[["top", "right"]].set_visible(False)

    try:
        if np.nanmax(np.abs(y_mean)) >= 1000:
            ax.yaxis.set_major_formatter(
                plt.FuncFormatter(lambda v, _: f"{v:,.0f}")
            )
    except ValueError:
        pass

    _external_legend(ax, loc="upper right")

    direction = ("rises" if eps > 0
                 else "falls" if eps < 0
                 else "stays roughly constant")
    eps_clause = (
        f"Elasticity ε = {eps:+.2f} — a 10% increase in {param_name.lower()} {direction} this output by "
        f"roughly {abs(eps) * 10:.1f}% on average."
    )
    description = (
        f"This chart asks: how does the output change when we vary only {param_name.lower()}? "
        f"In this simulation, {param_name.lower()} is {_param_explanation(param)}. "
        f"{_OAT_EXPLAINER} "
        f"The X-axis shows the {len(x_grid)} grid values we tested for this parameter; the Y-axis shows the "
        f"resulting output in its native units ({unit_hint}). "
        f"For each grid value we ran {n_seeds} independent 80-year simulations, each using a different random seed. "
        f"Each thin blue line is one of those {n_seeds} runs; the bold line is the average across runs; the shaded "
        f"band is ±1 standard deviation (how much the output naturally jitters due to stochastic noise). "
        f"The dashed vertical line marks the baseline parameter value. "
        f"{eps_clause}"
    )

    _finalize_sensitivity_figure(
        fig,
        title=f"{short_output.capitalize()}",
        subtitle=f"Sensitivity to {param_name.lower()}",
        description=description,
        footer=f"One-at-a-time (OAT) sweep · {n_seeds} seeds × {len(x_grid)} grid points · {_seed_range(seeds)}.",
        top=0.91, bottom=0.28, left=0.08, right=0.78,
    )

    safe_param = param.replace("/", "_")
    safe_output = output.replace("/", "_")
    filename = f"pair_{rank:02d}_{safe_param}_to_{safe_output}.png"
    path = Path(output_dir) / filename
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return str(path)


def render_mc_pair_plots(
    mc_csv_path: str,
    elas: pd.DataFrame,
    output_dir: str,
    params: Optional[List[str]] = None,
    n_outputs_per_param: int = 3,
) -> list:
    """
    Monte Carlo pair plots for the same (param, output) pairs used by
    render_sensitive_pair_plots. Reads `mc_csv_path` (produced by
    run_mc_sweep) and renders one plot per pair with multi-seed spread.

    The elasticity matrix `elas` is used ONLY for pair selection and for
    the ε annotation on each chart. It should be computed from the
    fixed-seed sweep CSV, not the MC CSV (the MC noise makes elasticity
    fits less stable — they're summary numbers here, not the primary
    signal).
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    if params is None:
        params = IMPORTANT_PARAMETERS_FOR_PAIR_PLOTS

    df = pd.read_csv(mc_csv_path)
    mc = df[df["param"] != "__baseline__"].copy()
    mc["output_value"] = pd.to_numeric(mc["output_value"], errors="coerce")

    # Same ranking logic as the fixed-seed renderer
    triples = []
    for param in params:
        if param not in elas.index:
            continue
        top_outs = elas.loc[param].abs().dropna().nlargest(n_outputs_per_param).index.tolist()
        for out_name in top_outs:
            eps = elas.loc[param, out_name]
            if np.isfinite(eps):
                triples.append((param, out_name, float(eps)))

    triples.sort(key=lambda t: -abs(t[2]))

    saved = []
    for rank, (param, output, eps) in enumerate(triples, start=1):
        path = render_mc_pair_plot(
            param=param,
            output=output,
            mc_df=mc,
            eps=eps,
            output_dir=output_dir,
            rank=rank,
        )
        if path:
            saved.append(path)
    return saved


def render_all(
    elas: pd.DataFrame,
    output_dir: str,
    top_n: int = 15,
) -> list:
    """
    Render the heatmap bundle: per-section heatmaps + top-pairs ranked table.

    For the one-per-pair line plots (X = input, Y = output raw value), call
    `render_sensitive_pair_plots(csv_path, elas, output_dir)` separately
    — typically into a dedicated folder so they don't mix with the heatmaps.

    Returns the list of saved file paths.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    saved = []
    for section in sorted(set(_section(o) for o in elas.columns)):
        p = render_section_heatmap(elas, section, output_dir)
        if p:
            saved.append(p)
    saved.append(render_top_pairs_table(top_sensitive_pairs(elas, top_n), output_dir))
    return saved
