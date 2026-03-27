# =============================================================================
# scenarios.py
# Scenario definitions and co-scheduling logic for improvement analysis.
# =============================================================================
# The central question: how much does co-scheduling (bundling multiple
# cancer screenings into fewer patient encounters) improve throughput,
# reduce loss-to-follow-up, and increase detected cancers?
#
# Two workflow models:
#
#   FRAGMENTED (current state)
#     Each provider covers only their relevant cancer types.
#     A patient needing cervical + breast + colorectal must make 3 separate
#     appointments across 3 specialties. Each encounter carries independent
#     LTFU risk. Scheduling friction compounds across visits.
#
#   COORDINATED (future state / co-scheduling)
#     On first contact, all due eligible screenings are identified together.
#     A single coordinated encounter (or tightly scheduled bundle) handles
#     all of them. One LTFU draw per encounter — not per screening.
#     Scheduling friction occurs once.
#
# Intermediate scenarios allow partial coordination (e.g., GYN-led programs
# bundling cervical + breast only) or high-access models with reduced barriers.
# =============================================================================

import random
import copy
from typing import List, Optional, Dict

import config as cfg
from patient import Patient
from screening import (
    get_eligible_screenings,
    is_due_for_screening,
    run_screening_step,
    handle_unscreened,
)
from followup import run_cervical_followup, run_stub_followup
from metrics import initialize_metrics, record_screening, record_exit


# ─── Provider → Cancer Mapping (Fragmented Mode) ──────────────────────────────
# In fragmented care, each provider type only screens for their domain.
# Patients must make separate visits for cancers outside their provider's scope.

PROVIDER_CANCER_MAP = {
    "pcp":          ["colorectal", "lung", "osteoporosis"],  # primary care
    "gynecologist": ["cervical", "breast"],
    "specialist":   ["cervical", "lung", "breast", "colorectal", "osteoporosis"],
    "er":           [],   # no preventive screenings in ER
}

# In coordinated mode: all eligible screenings regardless of entry provider
COORDINATED_CANCER_MAP = {
    provider: ["cervical", "lung", "breast", "colorectal", "osteoporosis"]
    for provider in PROVIDER_CANCER_MAP
}


# ─── Scenario Definitions ────────────────────────────────────────────────────
# Each scenario overrides specific config values and scheduling behaviour.
# "ltfu_multiplier" scales all LTFU_PROBS relative to baseline.
# "scheduling_delay_days" is additional delay before a bundled appointment.

SCENARIOS: Dict[str, dict] = {

    "baseline_fragmented": {
        "label":                "Baseline — Fragmented",
        "description":          (
            "Current state. Each provider screens only their domain cancers. "
            "Patients needing multiple screenings must make separate appointments. "
            "Each encounter has independent LTFU risk."
        ),
        "cancer_map":           PROVIDER_CANCER_MAP,
        "co_schedule":          False,   # one cancer per encounter
        "ltfu_multiplier":      1.0,     # baseline
        "scheduling_delay_days": 30,     # avg days to get a follow-up slot
        "capacity_multiplier":  1.0,
    },

    "gyn_coordinated": {
        "label":                "GYN-Led Coordination",
        "description":          (
            "Gynecologist visits bundle cervical + breast screenings. "
            "PCP and specialist visits remain fragmented. "
            "Reflects a realistic near-term improvement with minimal infrastructure change."
        ),
        "cancer_map":           {
            **PROVIDER_CANCER_MAP,
            "gynecologist": ["cervical", "breast"],   # already together
        },
        "co_schedule":          ["cervical", "breast"],   # these two bundled
        "ltfu_multiplier":      0.80,    # modest reduction in LTFU from fewer appts
        "scheduling_delay_days": 21,
        "capacity_multiplier":  1.1,
    },

    "coordinated_all": {
        "label":                "Full Co-Scheduling",
        "description":          (
            "All due eligible screenings are identified on first contact and "
            "bundled into a single coordinated encounter. One LTFU draw per patient "
            "instead of one per screening. Represents the full programme vision."
        ),
        "cancer_map":           COORDINATED_CANCER_MAP,
        "co_schedule":          True,    # all eligible cancers per encounter
        "ltfu_multiplier":      0.55,    # substantially fewer drop-out points
        "scheduling_delay_days": 10,
        "capacity_multiplier":  1.25,    # need more screening capacity per slot
    },

    "high_access_coordinated": {
        "label":                "High-Access Co-Scheduling",
        "description":          (
            "Full co-scheduling + significantly reduced scheduling friction "
            "and access barriers (same-day or next-day bundled slots). "
            "Models the upper bound of what coordinated care can achieve."
        ),
        "cancer_map":           COORDINATED_CANCER_MAP,
        "co_schedule":          True,
        "ltfu_multiplier":      0.30,    # near-elimination of access-related LTFU
        "scheduling_delay_days": 3,
        "capacity_multiplier":  1.4,
    },
}


# ─── Encounter Counter (scenario-level metric) ────────────────────────────────

def count_encounters_needed(
    p: Patient, provider: str, current_day: int, scenario: dict
) -> int:
    """
    Estimate how many separate encounters a fragmented patient would need
    vs. how many a coordinated patient needs.
    Used for ROI / capacity analysis.
    """
    cancer_map  = scenario["cancer_map"]
    eligible    = [
        c for c in get_eligible_screenings(p)
        if c in cancer_map.get(provider, [])
        and is_due_for_screening(p, c, current_day)
    ]
    if not eligible:
        return 0
    if scenario["co_schedule"] is True or (
        isinstance(scenario["co_schedule"], list)
        and all(c in scenario["co_schedule"] for c in eligible)
    ):
        return 1   # all handled in one encounter
    return len(eligible)   # fragmented: one encounter per cancer


# ─── Adjusted LTFU Draw ───────────────────────────────────────────────────────

def adjusted_ltfu(base_prob: float, scenario: dict) -> bool:
    """Draw LTFU with scenario-specific multiplier applied."""
    adjusted = min(base_prob * scenario["ltfu_multiplier"], 1.0)
    return random.random() < adjusted


# ─── Core Encounter Logic ─────────────────────────────────────────────────────

def run_encounter(
    p: Patient,
    provider: str,
    current_day: int,
    metrics: dict,
    scenario: dict,
) -> None:
    """
    Simulate one provider encounter under the given scenario.

    In fragmented mode:
      - Only screens for cancers within this provider's domain.
      - Each cancer is an independent LTFU risk.

    In coordinated mode:
      - Screens for ALL eligible due cancers in one encounter.
      - One shared LTFU draw determines whether the patient stays or leaves.

    Updates patient object and metrics in place.
    """
    metrics["n_patients"] += 1

    cancer_map       = scenario["cancer_map"]
    co_schedule      = scenario["co_schedule"]
    provider_cancers = cancer_map.get(provider, [])

    # Cancers this patient is eligible for AND within provider scope AND due
    eligible_here = [
        c for c in get_eligible_screenings(p)
        if c in provider_cancers
        and is_due_for_screening(p, c, current_day)
    ]

    if not eligible_here:
        metrics["n_unscreened"] += 1
        # Patient seen but no screening due — no LTFU risk, just a visit
        p.log(current_day, f"ENCOUNTER {provider} — no screenings due")
        return

    metrics["n_eligible_any"] += 1

    # ── Coordinated / co-scheduled encounter ─────────────────────────────────
    if co_schedule is True or (
        isinstance(co_schedule, list)
        and set(eligible_here).issubset(set(co_schedule))
    ):
        # Single LTFU draw for the whole bundle
        if adjusted_ltfu(cfg.LTFU_PROBS["unscreened_will_reschedule"], scenario):
            # Patient doesn't show / drops out before the bundled appointment
            outcome = handle_unscreened(p, current_day)
            metrics["n_unscreened"] += 1
            if outcome == "reschedule":
                metrics["n_reschedule"] += 1
                metrics["ltfu_unscreened"] += 1
            metrics["scenario_encounters_saved"] = (
                metrics.get("scenario_encounters_saved", 0) + len(eligible_here) - 1
            )
            return

        # All screenings happen in one encounter
        metrics["scenario_encounters_saved"] = (
            metrics.get("scenario_encounters_saved", 0) + len(eligible_here) - 1
        )
        for cancer in eligible_here:
            _screen_and_followup(p, cancer, current_day, metrics, scenario)

    # ── Fragmented encounter ──────────────────────────────────────────────────
    else:
        # Each cancer is an independent encounter with its own LTFU risk
        for cancer in eligible_here:
            if not p.active:
                break
            # Independent no-show / decline check per screening
            if adjusted_ltfu(1 - cfg.LTFU_PROBS["unscreened_will_reschedule"], scenario):
                # Patient misses this screening encounter
                metrics["n_unscreened"] += 1
                p.log(current_day, f"MISSED {cancer} screening encounter (fragmented LTFU)")
                continue
            _screen_and_followup(p, cancer, current_day, metrics, scenario)


def _screen_and_followup(
    p: Patient, cancer: str, current_day: int, metrics: dict, scenario: dict
) -> None:
    """Run one screening + follow-up for a specific cancer (shared helper)."""
    result = run_screening_step(p, cancer, current_day)
    if result is None:
        return

    record_screening(metrics, p, cancer, result)

    if cancer == "cervical":
        disposition = run_cervical_followup(p, current_day, metrics)
    else:
        disposition = run_stub_followup(p, cancer, result, current_day, metrics)

    if p.exit_reason:
        record_exit(metrics, p.exit_reason)


# ─── Scenario Runner ──────────────────────────────────────────────────────────

def run_scenario(
    scenario_name: str,
    patients: List[Patient],
    providers: List[str],
    sim_day: int = 0,
    seed: int = cfg.RANDOM_SEED,
) -> dict:
    """
    Run one scenario over a pre-generated patient cohort.

    Parameters
    ----------
    scenario_name : key from SCENARIOS dict
    patients      : list of Patient objects (from population.sample_patient)
    providers     : provider assignment for each patient (same order as patients)
    sim_day       : simulation day (for interval/due-date checks)
    seed          : random seed

    Returns
    -------
    metrics dict with all tracked outcomes + scenario metadata
    """
    assert scenario_name in SCENARIOS, (
        f"Unknown scenario '{scenario_name}'. Choose from: {list(SCENARIOS.keys())}"
    )
    assert len(patients) == len(providers), "patients and providers must have equal length"

    scenario = SCENARIOS[scenario_name]
    random.seed(seed)

    metrics = initialize_metrics()
    metrics["scenario_name"]  = scenario_name
    metrics["scenario_label"] = scenario["label"]
    metrics["scenario_encounters_saved"] = 0

    # Deep-copy patients so each scenario gets a clean slate
    fresh_patients = [copy.deepcopy(p) for p in patients]

    for p, provider in zip(fresh_patients, providers):
        run_encounter(p, provider, sim_day, metrics, scenario)

    return metrics


# ─── Multi-Scenario Comparison ────────────────────────────────────────────────

def compare_scenarios(
    scenario_names: List[str],
    patients: List[Patient],
    providers: List[str],
    sim_day: int = 0,
    seed: int = cfg.RANDOM_SEED,
) -> Dict[str, dict]:
    """
    Run multiple scenarios over the same patient cohort and return all metrics.
    Patients are deep-copied for each scenario so results are independent.
    """
    return {
        name: run_scenario(name, patients, providers, sim_day, seed)
        for name in scenario_names
    }


# ─── Age-Clustering Analysis ──────────────────────────────────────────────────
# Clinical observation: women aged ~40–50 are due for multiple screenings
# simultaneously. Co-scheduling has the highest impact in this cohort.

def get_multi_screening_eligible(patients: List[Patient]) -> List[Patient]:
    """Return patients eligible for 2+ screenings (co-scheduling candidates)."""
    return [p for p in patients if len(get_eligible_screenings(p)) >= 2]


def age_cluster_summary(patients: List[Patient]) -> dict:
    """
    Break down how many screenings each patient is eligible for, by age group.
    Highlights the 40–50 convergence zone.
    """
    from collections import defaultdict
    age_bins = [(21, 29), (30, 39), (40, 49), (50, 59), (60, 69), (70, 80)]
    summary  = defaultdict(lambda: defaultdict(int))

    for p in patients:
        n_eligible = len(get_eligible_screenings(p))
        for lo, hi in age_bins:
            if lo <= p.age <= hi:
                summary[f"{lo}–{hi}"][f"{n_eligible}_screenings"] += 1
                break

    return dict(summary)
