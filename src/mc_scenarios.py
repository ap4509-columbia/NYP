# =============================================================================
# mc_scenarios.py
# Monte Carlo runner for scenario analysis.
#
# Mirrors mc_baseline.run_mc_baseline but wraps each per-seed simulation in
# scenarios.apply_scenario(scenario) so the per-seed worker sees the scenario's
# parameter overrides + cotesting flag. Each worker runs in an isolated
# subprocess (ProcessPoolExecutor), so cfg mutations never leak across workers
# or back to the parent.
#
# Reuses mc_baseline._run_one_baseline (the actual sim loop) and
# mc_baseline._extract_metrics_for_seed (the CSV row extractor) so the output
# CSV has BYTE-IDENTICAL schema to the baseline CSV. That's what lets
# mc_base_viz.render_all_base_mc render scenarios with zero code changes.
#
# Public API:
#   run_mc_scenario(scenario, n_seeds, seed_start, n_workers, out_csv, progress)
#       → path to CSV (columns: seed, year, metric, value)
# =============================================================================

import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd


def _ensure_sys_path() -> None:
    here = Path(__file__).resolve().parent.parent
    for sub in ("src", "ModelParameters", "."):
        cand = str(here / sub) if sub != "." else str(here)
        if cand not in sys.path:
            sys.path.insert(0, cand)


def _run_one_scenario(seed: int, scenario) -> Dict[str, Any]:
    """Run one full scenario simulation. Mirrors mc_baseline._run_one_baseline
    but enters apply_scenario(scenario) first so cfg is perturbed for this run."""
    _ensure_sys_path()
    from mc_baseline import _run_one_baseline
    from scenarios import apply_scenario

    with apply_scenario(scenario):
        return _run_one_baseline(seed)


def _worker(seed: int, scenario) -> Dict[str, Any]:
    _ensure_sys_path()
    from mc_scenarios import _run_one_scenario
    t0 = time.time()
    r = _run_one_scenario(seed, scenario)
    r["elapsed_sec"] = time.time() - t0
    return r


def run_mc_scenario(
    scenario,
    n_seeds: int = 100,
    seed_start: int = 42,
    n_workers: Optional[int] = None,
    out_csv: Optional[str] = None,
    progress: bool = True,
) -> str:
    """Run `n_seeds` independent sims under `scenario` in parallel and write
    a long-format CSV (columns: seed, year, metric, value) identical in schema
    to mc_baseline.

    n_seeds is a free parameter — pass any N. Scaling behavior mirrors the
    baseline (≈40s/sim on one core; 12-core machine does n=100 in ~6 min).
    """
    _ensure_sys_path()
    from mc_baseline import _extract_metrics_for_seed

    if n_seeds < 1:
        raise ValueError(f"n_seeds must be ≥ 1, got {n_seeds}")

    seeds = list(range(seed_start, seed_start + n_seeds))
    n_workers = n_workers or max(1, (os.cpu_count() or 1) - 1)

    if out_csv is None:
        here = Path(__file__).resolve().parent
        results_dir = here / "mc_scenario_data"
        results_dir.mkdir(parents=True, exist_ok=True)
        out_csv = str(results_dir / f"{scenario.name}_n{n_seeds}_start{seed_start}.csv")

    if progress:
        print(f"[mc_scenario:{scenario.name}] {n_seeds} sims × 80yr each, "
              f"{n_workers} workers")
        print(f"[mc_scenario:{scenario.name}] writing → {out_csv}")

    dataframes: List[pd.DataFrame] = []
    t_start = time.time()
    with ProcessPoolExecutor(max_workers=n_workers) as ex:
        futures = {ex.submit(_worker, s, scenario): s for s in seeds}
        for i, fut in enumerate(as_completed(futures), start=1):
            r = fut.result()
            dataframes.append(_extract_metrics_for_seed(r))
            if progress and (i % max(1, n_seeds // 20) == 0 or i == n_seeds):
                elapsed = time.time() - t_start
                eta = (elapsed / i) * (n_seeds - i) / 60
                print(f"  [{i:>3}/{n_seeds}] seed={r['seed']:>4} "
                      f"{r['elapsed_sec']:.0f}s  ETA {eta:.1f}min")

    if not dataframes:
        raise RuntimeError("no seed results to write")

    result = pd.concat(dataframes, ignore_index=True)
    result.to_csv(out_csv, index=False)

    if progress:
        print(f"[mc_scenario:{scenario.name}] done in "
              f"{(time.time() - t_start)/60:.1f} min  "
              f"({len(result):,} rows across {n_seeds} seeds)")
    return out_csv
