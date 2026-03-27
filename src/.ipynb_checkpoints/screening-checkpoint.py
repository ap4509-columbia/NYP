# =============================================================================
# screening.py
# Steps 2–3: Eligibility checks, test assignment, and result draws.
# =============================================================================
# All probabilities are PLACEHOLDERS — replace with NYP data.
# =============================================================================

import random
from typing import List, Optional

import config as cfg
from patient import Patient


# ─── Eligibility ──────────────────────────────────────────────────────────────

def is_eligible_cervical(p: Patient) -> bool:
    e = cfg.ELIGIBILITY["cervical"]
    return e["age_min"] <= p.age <= e["age_max"] and p.has_cervix


def is_eligible_lung(p: Patient) -> bool:
    """
    USPSTF 2021 criteria: age 50–80, ≥20 pack-years,
    AND (current smoker OR quit within the past 15 years).
    """
    e = cfg.ELIGIBILITY["lung"]
    age_ok       = e["age_min"] <= p.age <= e["age_max"]
    pack_ok      = p.pack_years >= e["min_pack_years"]
    smoking_ok   = p.smoker or (p.years_since_quit <= e["max_years_since_quit"])
    return age_ok and pack_ok and smoking_ok


ELIGIBILITY_CHECKS = {
    "cervical": is_eligible_cervical,
    "lung":     is_eligible_lung,
}


def get_eligible_screenings(p: Patient) -> List[str]:
    """Return cancers this patient is eligible for, restricted to ACTIVE_CANCERS."""
    return [
        cancer for cancer, check in ELIGIBILITY_CHECKS.items()
        if cancer in cfg.ACTIVE_CANCERS and check(p)
    ]


# ─── Screening Interval Check ─────────────────────────────────────────────────

_LAST_SCREEN_FIELD = {
    "cervical": "last_cervical_screen_day",
    "lung":     "last_lung_screen_day",
}


def is_due_for_screening(p: Patient, cancer: str, current_day: int) -> bool:
    """True if the patient has never been screened or the interval has elapsed."""
    last_day = getattr(p, _LAST_SCREEN_FIELD[cancer], -1)
    if last_day < 0:
        return True
    test     = assign_screening_test(p, cancer)
    interval = cfg.SCREENING_INTERVALS_DAYS.get(test, 365)
    return (current_day - last_day) >= interval


# ─── Age Stratum ──────────────────────────────────────────────────────────────

def get_cervical_age_stratum(age: int) -> str:
    """Map patient age to USPSTF cervical screening stratum."""
    if 21 <= age <= 29:
        return "young"
    elif 30 <= age <= 65:
        return "middle"
    return "older"


# ─── Test Assignment ──────────────────────────────────────────────────────────

def assign_screening_test(p: Patient, cancer: str) -> str:
    """Choose a specific test modality for the given cancer type."""
    if cancer == "cervical":
        stratum = get_cervical_age_stratum(p.age)
        options = cfg.SCREENING_TESTS["cervical"].get(stratum, [])
        if not options:
            return "ineligible"
        return random.choice(options)

    options = cfg.SCREENING_TESTS.get(cancer, [])
    return options[0] if options else "ineligible"


# ─── Result Draws ─────────────────────────────────────────────────────────────

def _adjust_probs(probs: dict, inflate_keys: list, factor: float) -> dict:
    """Proportionally inflate selected categories and renormalise."""
    adjusted = {k: v * factor if k in inflate_keys else v for k, v in probs.items()}
    total    = sum(adjusted.values())
    return {k: v / total for k, v in adjusted.items()}


def draw_cervical_result(p: Patient, test: str) -> str:
    """
    Draw a cervical screening result appropriate to the test modality.

    Cytology (age 21–65): returns NORMAL | ASCUS | LSIL | ASC-H | HSIL
    HPV-alone (age 30–65): returns HPV_NEGATIVE | HPV_POSITIVE

    Risk-factor adjustments applied to cytology results only.
    PLACEHOLDER — calibrate against NYP data / ASCCP risk tables.
    """
    stratum = get_cervical_age_stratum(p.age)

    if test == "hpv_alone":
        # HPV-alone: binary result only; no cytology categories
        probs = dict(cfg.CERVICAL_RESULT_PROBS["middle_hpv"])
        if p.hpv_positive:
            probs = _adjust_probs(probs, inflate_keys=["HPV_POSITIVE"], factor=2.0)
    elif stratum == "young":
        probs = dict(cfg.CERVICAL_RESULT_PROBS["young"])
        if p.hpv_positive:
            probs = _adjust_probs(probs, inflate_keys=["ASCUS", "LSIL", "ASC-H", "HSIL"], factor=1.5)
        if p.prior_cin in ("CIN2", "CIN3"):
            probs = _adjust_probs(probs, inflate_keys=["ASC-H", "HSIL"], factor=1.8)
    else:
        probs = dict(cfg.CERVICAL_RESULT_PROBS["middle_cytology"])
        if p.hpv_positive:
            probs = _adjust_probs(probs, inflate_keys=["ASCUS", "LSIL", "ASC-H", "HSIL"], factor=1.5)
        if p.prior_cin in ("CIN2", "CIN3"):
            probs = _adjust_probs(probs, inflate_keys=["ASC-H", "HSIL"], factor=1.8)

    return random.choices(list(probs.keys()), weights=list(probs.values()), k=1)[0]


def draw_lung_rads_result() -> str:
    """Draw a Lung-RADS (v2022) result category. PLACEHOLDER — calibrate to NYP data."""
    return random.choices(
        list(cfg.LUNG_RADS_PROBS.keys()),
        weights=list(cfg.LUNG_RADS_PROBS.values()),
        k=1,
    )[0]


def run_lung_pre_ldct(
    p: Patient, current_day: int, metrics: Optional[dict] = None
) -> bool:
    """
    Simulate the pre-LDCT pathway:
      1. Referral placed? → LTFU if no
      2. Patient schedules LDCT? → LTFU if no
    Returns True if LDCT proceeds, False if LTFU.
    """
    if metrics is not None:
        metrics["lung_eligible"] += 1

    if random.random() > cfg.LUNG_PATHWAY_PROBS["referral_placed"]:
        p.log(current_day, "LUNG: no referral placed — LTFU")
        p.exit_system(current_day, "lost_to_followup")
        return False

    p.lung_referral_placed = True
    p.log(current_day, "LUNG: LDCT order placed")
    if metrics is not None:
        metrics["lung_referral_placed"] += 1

    if random.random() > cfg.LUNG_PATHWAY_PROBS["scheduled_after_referral"]:
        p.log(current_day, "LUNG: did not schedule LDCT — LTFU")
        p.exit_system(current_day, "lost_to_followup")
        return False

    p.lung_ldct_scheduled = True
    p.log(current_day, "LUNG: LDCT scheduled and completed")
    if metrics is not None:
        metrics["lung_ldct_scheduled"] += 1
        metrics["lung_ldct_completed"] += 1

    return True


# ─── Main Screening Step ──────────────────────────────────────────────────────

_RESULT_FIELD = {
    "cervical": "cervical_result",
    "lung":     "lung_result",
}


def run_screening_step(
    p: Patient, cancer: str, current_day: int,
    metrics: Optional[dict] = None,
) -> Optional[str]:
    """
    Execute one screening event for the given cancer type.
    Returns the result string, or None if skipped / LTFU.
    """
    if not ELIGIBILITY_CHECKS[cancer](p):
        p.log(current_day, f"SKIP {cancer} — not eligible (age={p.age})")
        return None

    if not is_due_for_screening(p, cancer, current_day):
        p.log(current_day, f"SKIP {cancer} — not yet due")
        return None

    test            = assign_screening_test(p, cancer)
    p.current_stage = "screening"
    p.log(current_day, f"SCREEN {cancer} via {test}")

    if cancer == "cervical":
        result                    = draw_cervical_result(p, test)
        p.cervical_result         = result
        p.last_cervical_screen_day = current_day

    elif cancer == "lung":
        if not run_lung_pre_ldct(p, current_day, metrics):
            return None
        result             = draw_lung_rads_result()
        p.lung_result      = result
        p.last_lung_screen_day = current_day
        if metrics is not None:
            metrics["lung_rads_distribution"][result] += 1

    else:
        result = "UNKNOWN"

    p.log(current_day, f"RESULT {cancer}: {result}")
    return result


# ─── Unscreened Pathway ───────────────────────────────────────────────────────

def handle_unscreened(p: Patient, current_day: int) -> str:
    """
    Decision node for patients who did not receive screening.
    Returns: "reschedule" | "exit"
    """
    if not p.active:
        return "exit"

    if random.random() < cfg.LTFU_PROBS["unscreened_will_reschedule"]:
        p.willing_to_reschedule = True
        p.log(current_day, "UNSCREENED — willing to reschedule")
        return "reschedule"

    p.willing_to_reschedule = False
    p.exit_system(current_day, "lost_to_followup")
    p.log(current_day, "UNSCREENED — exits system (will not reschedule)")
    return "exit"
