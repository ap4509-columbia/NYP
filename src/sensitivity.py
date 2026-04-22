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
    """Heatmap for all outputs whose names begin with `section.`, with a per-chart explanation."""
    cols = [o for o in elas.columns if _section(o) == section]
    if not cols:
        return None
    sub = elas[cols]
    labels = [o.replace(section + ".", "", 1) for o in cols]
    desc = _SECTION_DESCRIPTIONS.get(section, "")
    example = _pick_worked_example(sub)

    # Bottom reserved for the reader's guide (3 blocks)
    fig_w = max(9, 0.55 * len(cols) + 5)
    fig_h = max(6.5, 0.35 * len(sub.index) + 4.2)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    fig.patch.set_facecolor("white")

    data = sub.values.astype(float)
    im = ax.imshow(data, aspect="auto", cmap="RdBu_r", vmin=-vmax, vmax=vmax)
    ax.set_xticks(range(len(labels))); ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax.set_yticks(range(len(sub.index))); ax.set_yticklabels(sub.index, fontsize=8)
    ax.set_xlabel("Output metric (what we measure)", fontsize=9, labelpad=8)
    ax.set_ylabel("Input parameter (what we varied)", fontsize=9, labelpad=8)

    for i in range(data.shape[0]):
        for j in range(data.shape[1]):
            v = data[i, j]
            if np.isfinite(v):
                ax.text(j, i, f"{v:+.2f}", ha="center", va="center",
                        fontsize=7, color=("white" if abs(v) > 0.8 else "black"))
            else:
                ax.text(j, i, "·", ha="center", va="center", fontsize=10, color="#888")

    ax.set_title(
        f"Sensitivity Analysis — {section.replace('_', ' ').title()}\n"
        f"{desc}",
        fontsize=12, fontweight="bold", pad=14,
    )
    cbar = fig.colorbar(im, ax=ax, shrink=0.8, pad=0.02)
    cbar.set_label("elasticity (ε)", fontsize=9)

    # Three-block explanation at the bottom:
    #   1. What elasticity is
    #   2. A worked example from this chart's actual data
    #   3. Interpretation key
    definition = (
        "WHAT IS ELASTICITY (ε)?\n"
        "ε = percentage change in the OUTPUT  ÷  percentage change in the INPUT parameter.\n"
        "It measures how strongly an output responds when we vary one input, holding everything else constant."
    )
    interpretation = (
        "INTERPRETATION\n"
        "  ε ≈  0     output is INSENSITIVE to this parameter (gray/white cell).\n"
        "  ε ≈ +1     1-to-1 positive — a 1 % rise in the input raises the output by 1 %.\n"
        "  ε ≈ −1     1-to-1 inverse — a 1 % rise in the input lowers the output by 1 %.\n"
        "  |ε| > 1    AMPLIFIED — the output responds faster than the input (a leverage knob).\n"
        "Colors: RED = output rises with input, BLUE = output falls with input, WHITE ≈ no effect.\n"
        "A dot ( · ) means elasticity could not be computed (e.g. baseline output was zero)."
    )
    blocks = [definition]
    if example:
        blocks.append(example)
    blocks.append(interpretation)
    guide = "\n\n".join(blocks)

    fig.text(
        0.5, 0.01, guide,
        ha="center", va="bottom", fontsize=8, family="monospace",
        bbox=dict(boxstyle="round,pad=0.6", facecolor="#F5F5F5", edgecolor="#CCCCCC"),
    )

    plt.tight_layout(rect=(0, 0.25, 1, 1))  # reserve bottom 25% for the guide
    path = Path(output_dir) / f"sensitivity_{section}.png"
    plt.savefig(path, dpi=150, bbox_inches="tight"); plt.close(fig)
    return str(path)


def render_top_pairs_table(top_df: pd.DataFrame, output_dir: str) -> str:
    """Top-N (parameter, output, ε) table with plain-English explanation + worked example."""
    fig, ax = plt.subplots(figsize=(12, max(6, 0.38 * len(top_df) + 4)))
    fig.patch.set_facecolor("white")
    ax.axis("off")

    cell_data = [[i + 1, r["param"], r["output"], f"{r['elasticity']:+.3f}"]
                 for i, (_, r) in enumerate(top_df.iterrows())]
    t = ax.table(
        cellText=cell_data,
        colLabels=["Rank", "Parameter (input varied)", "Output (metric affected)", "Elasticity ε"],
        loc="center", cellLoc="left",
        colWidths=[0.06, 0.36, 0.46, 0.12],
    )
    t.auto_set_font_size(False); t.set_fontsize(9); t.scale(1, 1.4)
    for j in range(4):
        c = t[0, j]; c.set_facecolor("#2C3E50"); c.set_text_props(color="white", fontweight="bold")

    ax.set_title(
        f"Top {len(top_df)} Most-Sensitive Input → Output Pairs\n"
        f"Ranked by the absolute size of elasticity |ε| (bigger = higher leverage)",
        fontsize=13, fontweight="bold", pad=18,
    )

    # Worked example from the #1 pair in the ranking
    example_block = ""
    if not top_df.empty:
        top = top_df.iloc[0]
        eps = float(top["elasticity"])
        direction = "rises" if eps > 0 else "falls"
        example_block = (
            f"\n\nEXAMPLE FROM ROW 1\n"
            f"{top['param']}  →  {top['output']}  has ε = {eps:+.2f}.\n"
            f"Meaning: a 10 % increase in {top['param']} {direction} "
            f"{top['output']} by roughly {abs(eps) * 10:.1f} %."
        )

    definition = (
        "WHAT IS ELASTICITY (ε)?\n"
        "ε = percentage change in the OUTPUT  ÷  percentage change in the INPUT parameter.\n"
        "It measures how strongly an output responds when one input is varied, all else equal."
    )
    interpretation = (
        "INTERPRETATION\n"
        "  ε ≈  0     output is INSENSITIVE to this parameter.\n"
        "  ε ≈ +1     1-to-1 positive — a 1 % rise in the input raises the output by 1 %.\n"
        "  ε ≈ −1     1-to-1 inverse — a 1 % rise in the input lowers the output by 1 %.\n"
        "  |ε| > 1    AMPLIFIED leverage — the output moves faster than the input.\n"
        "These 15 rows are the model's highest-leverage input→output relationships."
    )
    guide = definition + example_block + "\n\n" + interpretation
    fig.text(
        0.5, 0.01, guide,
        ha="center", va="bottom", fontsize=8.5, family="monospace",
        bbox=dict(boxstyle="round,pad=0.6", facecolor="#F5F5F5", edgecolor="#CCCCCC"),
    )

    plt.tight_layout(rect=(0, 0.30, 1, 1))
    path = Path(output_dir) / "sensitivity_top_pairs.png"
    plt.savefig(path, dpi=150, bbox_inches="tight"); plt.close(fig)
    return str(path)


def render_parameter_sweep_plot(
    param: str,
    sweep_df: pd.DataFrame,
    baseline_map: Dict[str, float],
    elas_row: pd.Series,
    output_dir: str,
    n_outputs: int = 4,
) -> Optional[str]:
    """
    One 1D sweep plot for a single parameter.

    X-axis : the parameter's grid values (5 points from SENSITIVITY_PARAMS)
    Y-axis : % change from baseline for each output (shared scale → curves comparable)
    Curves : the top-N outputs most affected by this parameter (picked from elas_row)

    Pure rendering — no simulations. All data comes from the existing sweep CSV.
    """
    # Pick top-N outputs by |ε| (finite only), preserve rank order for legend
    top_outs = elas_row.abs().dropna().nlargest(n_outputs).index.tolist()
    if not top_outs:
        return None

    rows = sweep_df[sweep_df["param"] == param].copy()
    if rows.empty:
        return None

    # Unique grid values in grid-index order
    grid_pairs = (
        rows[["grid_index", "grid_value"]]
        .drop_duplicates()
        .sort_values("grid_index")
        .reset_index(drop=True)
    )
    grid_values = grid_pairs["grid_value"].astype(float).values

    # Parameter's baseline = grid_index 2 (middle) by convention
    try:
        baseline_param_value = float(
            grid_pairs[grid_pairs["grid_index"] == 2]["grid_value"].iloc[0]
        )
    except (IndexError, ValueError):
        baseline_param_value = None

    fig_w = 9.5
    fig_h = 7.5
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    fig.patch.set_facecolor("white")

    colors = plt.cm.tab10(np.linspace(0, 1, max(n_outputs, 10)))
    for i, out_name in enumerate(top_outs):
        y_base = baseline_map.get(out_name)
        if y_base is None or not np.isfinite(y_base) or y_base == 0:
            continue
        sub = rows[rows["output_name"] == out_name].sort_values("grid_index")
        y_raw = pd.to_numeric(sub["output_value"], errors="coerce").astype(float).values
        x = sub["grid_value"].astype(float).values
        y_pct = (y_raw / y_base - 1.0) * 100.0
        short_label = out_name.split(".", 1)[1] if "." in out_name else out_name
        eps = elas_row.get(out_name)
        legend_label = (
            f"{short_label}  (ε = {eps:+.2f})" if eps is not None and np.isfinite(eps)
            else short_label
        )
        ax.plot(x, y_pct, marker="o", markersize=6, linewidth=2,
                color=colors[i % len(colors)], label=legend_label)

    # Baseline anchor — vertical dashed line + y=0 horizontal
    ax.axhline(0, color="#888", linewidth=0.8, linestyle=":", zorder=0)
    if baseline_param_value is not None:
        ax.axvline(
            baseline_param_value, color="#888",
            linewidth=1.0, linestyle="--", alpha=0.8, zorder=0,
        )
        ax.text(
            baseline_param_value, ax.get_ylim()[1] * 0.97,
            f"  baseline = {baseline_param_value:g}",
            fontsize=8, color="#555", va="top",
        )

    ax.set_xlabel(f"{param}  (parameter value)", fontsize=10, labelpad=8)
    ax.set_ylabel("% change in output relative to baseline", fontsize=10, labelpad=8)
    ax.set_title(
        f"Parameter sweep — {param}\n"
        f"How the top-{len(top_outs)} most-affected outputs respond as this input is varied",
        fontsize=12, fontweight="bold", pad=14,
    )
    ax.grid(True, alpha=0.25, linestyle="-", linewidth=0.5)
    ax.legend(fontsize=8, loc="best", framealpha=0.9)
    ax.spines[["top", "right"]].set_visible(False)

    # Explanation block — matches the heatmap style so readers can skim one guide across all charts
    guide = (
        "HOW TO READ THIS CHART\n"
        f"We re-ran the simulation with {param} set to each of the 5 values on the X-axis, "
        "one run per point, all other parameters frozen at baseline. Each curve is one output metric.\n\n"
        "The Y-axis is % CHANGE FROM BASELINE — a curve that rises above 0 means the output "
        "increased vs baseline; below 0 means it decreased. Curves share one scale so you can compare\n"
        "their slopes directly. The dashed vertical line marks the parameter's baseline value (where\n"
        "every curve passes through 0). ε in the legend is the elasticity (% output change per % input\n"
        "change) — a summary number for each curve's average slope."
    )
    fig.text(
        0.5, 0.01, guide,
        ha="center", va="bottom", fontsize=8, family="monospace",
        bbox=dict(boxstyle="round,pad=0.6", facecolor="#F5F5F5", edgecolor="#CCCCCC"),
    )
    plt.tight_layout(rect=(0, 0.20, 1, 1))

    safe_name = param.replace("/", "_")
    path = Path(output_dir) / f"sweep_{safe_name}.png"
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return str(path)


def render_parameter_sweep_plots(
    csv_path: str,
    elas: pd.DataFrame,
    output_dir: str,
    n_outputs: int = 4,
) -> list:
    """
    Render one 1D sweep plot per parameter in the elasticity matrix.

    Data is pulled from the existing sweep CSV — no simulations are run.
    """
    df = pd.read_csv(csv_path)
    baseline = df[df["param"] == "__baseline__"]
    if baseline.empty:
        raise ValueError("sweep CSV is missing the __baseline__ row")
    baseline_map = baseline.set_index("output_name")["output_value"].to_dict()

    sweep = df[df["param"] != "__baseline__"].copy()
    sweep["output_value"] = pd.to_numeric(sweep["output_value"], errors="coerce")

    saved: list = []
    for param in elas.index:
        path = render_parameter_sweep_plot(
            param=param,
            sweep_df=sweep,
            baseline_map=baseline_map,
            elas_row=elas.loc[param],
            output_dir=output_dir,
            n_outputs=n_outputs,
        )
        if path:
            saved.append(path)
    return saved


def render_all(
    elas: pd.DataFrame,
    output_dir: str,
    top_n: int = 15,
    csv_path: Optional[str] = None,
    n_outputs_per_param: int = 4,
) -> list:
    """
    Render every SA visualization:
      1. Per-section heatmaps
      2. Top-pairs ranked table
      3. Per-parameter 1D sweep line plots (if csv_path is provided)

    Returns the list of saved file paths.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    saved = []
    for section in sorted(set(_section(o) for o in elas.columns)):
        p = render_section_heatmap(elas, section, output_dir)
        if p:
            saved.append(p)
    saved.append(render_top_pairs_table(top_sensitive_pairs(elas, top_n), output_dir))
    if csv_path is not None:
        saved.extend(render_parameter_sweep_plots(
            csv_path, elas, output_dir, n_outputs=n_outputs_per_param,
        ))
    return saved
