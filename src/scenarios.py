# =============================================================================
# scenarios.py
# Scenario analysis registry, parameter-override context manager, and
# end-to-end orchestrators for running any subset of scenarios through the
# baseline MC pipeline.
#
# A "scenario" is a named bundle of:
#   • parameter overrides (e.g. {"CAPACITIES.ldct": 10})
#   • a cotesting toggle
#
# When run, a scenario produces:
#   • src/mc_scenario_data/<name>_n<N>_start<K>.csv   (same schema as baseline)
#   • notebooks/Scenario Visualizations/<name>/00..23_*.png
#
# The baseline pipeline (mc_baseline + mc_base_viz) is untouched — scenarios
# reuse the same renderers 1:1 and write to a separate folder tree so
# baseline images and scenario images can be diffed side-by-side.
#
# Public API:
#   SCENARIOS                       — list of ScenarioConfig (one-line edits)
#   apply_scenario(scenario)        — context manager: setattr on parameters
#   run_scenario(name, n_seeds)     — run a single named scenario
#   run_all_scenarios(n_seeds)      — run every scenario in SCENARIOS
# =============================================================================

import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Tuple


# =============================================================================
# Scenario registry
# =============================================================================

@dataclass
class ScenarioConfig:
    """A named scenario: parameter overrides + cotesting toggle.

    Fields
    ------
    name               : filesystem-safe slug used for the CSV name and the
                         viz output folder (e.g. "expanded_capacity")
    description        : one-line human text shown in the viz footer
    param_overrides    : mapping from dotted-path parameter name to value.
                         Keys may be "DAILY_PATIENTS" (top-level attribute)
                         or "CAPACITIES.ldct" (nested dict key).
    cotesting_enabled  : flip cfg.COTESTING["enabled"] during the run
    """
    name: str
    description: str
    param_overrides: Dict[str, Any] = field(default_factory=dict)
    cotesting_enabled: bool = False


def _expanded_capacity_overrides() -> Dict[str, Any]:
    """Build the capacity override dict from parameters.py — no hardcoded
    numbers here. Edit cfg.SCENARIO_CAPACITIES_EXPANDED to retarget."""
    import parameters as cfg
    return {f"CAPACITIES.{proc}": value
            for proc, value in cfg.SCENARIO_CAPACITIES_EXPANDED.items()}


SCENARIOS: List[ScenarioConfig] = [
    ScenarioConfig(
        name="baseline_reference",
        description="Control: no parameter overrides, cotesting disabled",
        param_overrides={},
        cotesting_enabled=False,
    ),
    ScenarioConfig(
        name="expanded_capacity",
        description="Cervical + lung capacities raised per SCENARIO_CAPACITIES_EXPANDED",
        param_overrides=_expanded_capacity_overrides(),
        cotesting_enabled=False,
    ),
    ScenarioConfig(
        name="cotesting_only",
        description="Primary + secondary cotesting: bundle cervical & lung when same-day",
        param_overrides={},
        cotesting_enabled=True,
    ),
    ScenarioConfig(
        name="cotesting_plus_expanded_capacity",
        description="Primary + secondary cotesting on top of expanded capacities",
        param_overrides=_expanded_capacity_overrides(),
        cotesting_enabled=True,
    ),
]


# =============================================================================
# Parameter override (sensitivity.py-style, batched)
# =============================================================================

_MISSING = object()


def _get_nested(cfg, key: str):
    """Read cfg.<key> or cfg.<top>[<sub>]. Raises if nested dict key absent."""
    if "." in key:
        top, sub = key.split(".", 1)
        container = getattr(cfg, top)
        if not isinstance(container, dict):
            raise TypeError(f"{top} is not a dict — cannot nest '{key}'")
        return container[sub]
    return getattr(cfg, key)


def _set_nested(cfg, key: str, value: Any) -> None:
    """Write cfg.<key> = value, or cfg.<top> = {..., sub: value} (new dict).

    For nested keys we replace the whole dict with a copy + update so that
    restoring the old reference on exit leaves no trace in the patched dict.
    """
    if "." in key:
        top, sub = key.split(".", 1)
        container = getattr(cfg, top)
        if not isinstance(container, dict):
            raise TypeError(f"{top} is not a dict — cannot nest '{key}'")
        updated = dict(container)
        updated[sub] = value
        setattr(cfg, top, updated)
    else:
        setattr(cfg, key, value)


@contextlib.contextmanager
def apply_scenario(scenario: ScenarioConfig):
    """Apply param_overrides + cotesting flag to the `parameters` module
    for the duration of the block. Restores the originals on exit, even if
    the block raises.

    Works both in-process (notebook driver) and in worker subprocesses
    (each worker re-imports parameters freshly, so the override is isolated
    to that process).
    """
    import parameters as cfg

    originals: Dict[str, Any] = {}
    for key in scenario.param_overrides:
        originals[key] = _get_nested(cfg, key)
    saved_cotesting = getattr(cfg, "COTESTING", {"enabled": False})

    try:
        for key, value in scenario.param_overrides.items():
            _set_nested(cfg, key, value)
        cfg.COTESTING = {"enabled": scenario.cotesting_enabled}
        yield cfg
    finally:
        cfg.COTESTING = saved_cotesting
        for key, value in originals.items():
            if "." in key:
                top, sub = key.split(".", 1)
                container = getattr(cfg, top)
                if isinstance(container, dict):
                    updated = dict(container)
                    updated[sub] = value
                    setattr(cfg, top, updated)
            else:
                setattr(cfg, key, value)


# =============================================================================
# Orchestrators
# =============================================================================

def _scenario_viz_dir(name: str) -> Path:
    here = Path(__file__).resolve().parent.parent
    return here / "notebooks" / "Scenario Visualizations" / name


def get_scenario(name: str) -> ScenarioConfig:
    """Look up a scenario by name; raises KeyError if not registered."""
    for sc in SCENARIOS:
        if sc.name == name:
            return sc
    raise KeyError(f"unknown scenario '{name}'. Known: {[s.name for s in SCENARIOS]}")


def render_scenario(scenario: ScenarioConfig, csv_path: str) -> str:
    """Render the 24 base-viz charts for a scenario into its own subfolder.
    Returns the output directory path."""
    from mc_base_viz import render_all_base_mc

    out_dir = _scenario_viz_dir(scenario.name)
    out_dir.mkdir(parents=True, exist_ok=True)

    tag = f"Scenario: {scenario.name} — {scenario.description}"
    render_all_base_mc(csv_path, str(out_dir), scenario_tag=tag)
    return str(out_dir)


def run_scenario(name: str, n_seeds: int = 100,
                 seed_start: int = 42) -> Tuple[str, str]:
    """Run a single named scenario end-to-end.

    Returns (csv_path, viz_dir)."""
    from mc_scenarios import run_mc_scenario

    sc = get_scenario(name)
    print(f"[scenarios] ▶ {sc.name}  (cotesting={sc.cotesting_enabled}, "
          f"overrides={len(sc.param_overrides)})")
    csv = run_mc_scenario(sc, n_seeds=n_seeds, seed_start=seed_start)
    viz = render_scenario(sc, csv)
    print(f"[scenarios] ✓ {sc.name}  →  {viz}")
    return csv, viz


def run_all_scenarios(n_seeds: int = 100,
                      seed_start: int = 42) -> List[Tuple[str, str]]:
    """Run every scenario in SCENARIOS. Does NOT touch baseline output.

    Returns a list of (csv_path, viz_dir) tuples, one per scenario."""
    out: List[Tuple[str, str]] = []
    for sc in SCENARIOS:
        out.append(run_scenario(sc.name, n_seeds=n_seeds, seed_start=seed_start))
    return out
