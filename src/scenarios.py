# =============================================================================
# scenarios.py
# Scenario definitions for coordinated vs. fragmented workflow comparison.
# =============================================================================
# The central question: how much does co-scheduling (bundling cervical and
# lung screenings into fewer patient encounters) improve throughput, reduce
# queue-based LTFU, and increase detected cancers?
#
# Two workflow models:
#
#   FRAGMENTED (current state)
#     Each provider covers only their relevant cancer types.
#     A patient needing both cervical and lung screening must make separate
#     appointments, each competing for procedure slots independently.
#
#   COORDINATED (future state / co-scheduling)
#     On first contact, all due eligible screenings are identified together.
#     A single coordinated encounter handles all of them.
#     Reduces total encounters and queue exposure.
#
# NOTE: LTFU in the main simulation is handled exclusively by the queue-based
# geometric waiting-time hazard in runner.py (_check_queue_ltfu). This module
# defines scenario parameters (capacity multipliers, scheduling delays) that
# the runner uses when running scenario comparisons.
# =============================================================================

from typing import List, Dict

import config as cfg
from patient import Patient
from screening import get_eligible_screenings, is_due_for_screening


# ─── Provider → Cancer Mapping (Fragmented Mode) ──────────────────────────────
# In fragmented care, each provider type only screens for their domain.
# Patients must make separate visits for cancers outside their provider's scope.

PROVIDER_CANCER_MAP = {
    "pcp":          ["cervical", "lung"],
    "gynecologist": ["cervical"],
    "specialist":   ["cervical", "lung"],
    "er":           [],   # no preventive screenings in ER
}

# In coordinated mode: all active eligible screenings regardless of entry provider
COORDINATED_CANCER_MAP = {
    provider: list(cfg.ACTIVE_CANCERS)
    for provider in PROVIDER_CANCER_MAP
}


# ─── Scenario Definitions ────────────────────────────────────────────────────
# Each scenario overrides specific config values and scheduling behaviour.
# These parameters are consumed by the runner when executing scenario runs.
# LTFU is always queue-based (geometric waiting-time); scenarios modify
# capacity and scheduling delays, which indirectly affect queue LTFU rates.

SCENARIOS: Dict[str, dict] = {

    "baseline_fragmented": {
        "label":                "Baseline — Fragmented",
        "description":          (
            "Current state. Each provider screens only their domain cancers. "
            "Patients needing multiple screenings must make separate appointments, "
            "each competing for procedure slots independently."
        ),
        "cancer_map":           PROVIDER_CANCER_MAP,
        "co_schedule":          False,   # one cancer per encounter
        "scheduling_delay_days": 30,     # avg days to get a follow-up slot
        "capacity_multiplier":  1.0,
    },

    "gyn_coordinated": {
        "label":                "GYN-Led Coordination",
        "description":          (
            "Gynecologist visits are expanded to also identify lung-eligible patients "
            "and place LDCT referrals. PCP visits remain fragmented. "
            "Reflects a realistic near-term improvement with minimal infrastructure change."
        ),
        "cancer_map":           {
            **PROVIDER_CANCER_MAP,
            "gynecologist": ["cervical", "lung"],
        },
        "co_schedule":          ["cervical", "lung"],
        "scheduling_delay_days": 21,
        "capacity_multiplier":  1.1,
    },

    "coordinated_all": {
        "label":                "Full Co-Scheduling",
        "description":          (
            "All due eligible screenings are identified on first contact and "
            "bundled into a single coordinated encounter. Reduces total encounters "
            "and queue exposure. Represents the full programme vision."
        ),
        "cancer_map":           COORDINATED_CANCER_MAP,
        "co_schedule":          True,    # all eligible cancers per encounter
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
    age_bins = [(21, 29), (30, 39), (40, 49), (50, 59), (60, 69), (70, 79), (80, 89), (90, 99)]
    summary  = defaultdict(lambda: defaultdict(int))

    for p in patients:
        n_eligible = len(get_eligible_screenings(p))
        for lo, hi in age_bins:
            if lo <= p.age <= hi:
                summary[f"{lo}–{hi}"][f"{n_eligible}_screenings"] += 1
                break

    return dict(summary)
