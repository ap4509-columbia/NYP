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
    e = cfg.ELIGIBILITY["lung"]
    return e["age_min"] <= p.age <= e["age_max"] and p.pack_years >= e["min_pack_years"]


def is_eligible_breast(p: Patient) -> bool:
    e = cfg.ELIGIBILITY["breast"]
    return e["age_min"] <= p.age <= e["age_max"]


def is_eligible_colorectal(p: Patient) -> bool:
    e = cfg.ELIGIBILITY["colorectal"]
    return e["age_min"] <= p.age <= e["age_max"]


def is_eligible_osteoporosis(p: Patient) -> bool:
    e = cfg.ELIGIBILITY["osteoporosis"]
    return p.age >= e["age_min"] or p.bmi < e["alt_bmi_threshold"]


ELIGIBILITY_CHECKS = {
    "cervical":     is_eligible_cervical,
    "lung":         is_eligible_lung,
    "breast":       is_eligible_breast,
    "colorectal":   is_eligible_colorectal,
    "osteoporosis": is_eligible_osteoporosis,
}


def get_eligible_screenings(p: Patient) -> List[str]:
    """Return the list of cancer types this patient is currently eligible for."""
    return [cancer for cancer, check in ELIGIBILITY_CHECKS.items() if check(p)]


# ─── Screening Interval Check ─────────────────────────────────────────────────

_LAST_SCREEN_FIELD = {
    "cervical":     "last_cervical_screen_day",
    "lung":         "last_lung_screen_day",
    "breast":       "last_breast_screen_day",
    "colorectal":   "last_colorectal_screen_day",
    "osteoporosis": "last_osteo_screen_day",
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
    """Map patient age to ASCCP cervical screening stratum."""
    if 21 <= age <= 29:
        return "young"
    elif 30 <= age <= 65:
        return "middle"
    return "older"


# ─── Test Assignment ──────────────────────────────────────────────────────────

def assign_screening_test(p: Patient, cancer: str) -> str:
    """
    Choose a specific test modality for the given cancer type.
    Cervical assignment follows ASCCP age-based guidelines.
    Other cancers use the first listed modality (stub).
    """
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
    Multinomial draw of cervical screening result.
    Age stratum and test type select the base probability table.
    Risk-factor adjustments applied on top.
    PLACEHOLDER — calibrate against NYP data / ASCCP risk tables.
    """
    stratum = get_cervical_age_stratum(p.age)

    if stratum == "young":
        probs = dict(cfg.CERVICAL_RESULT_PROBS["young"])
    elif test == "hpv_alone":
        probs = dict(cfg.CERVICAL_RESULT_PROBS["middle_hpv"])
    else:
        probs = dict(cfg.CERVICAL_RESULT_PROBS["middle_cytology"])

    # Risk-factor adjustments (PLACEHOLDER multipliers)
    if p.hpv_positive:
        probs = _adjust_probs(
            probs,
            inflate_keys=["ASCUS", "LSIL", "ASC-H", "HSIL", "HPV_POS_NORMAL_CYTO"],
            factor=1.5,
        )
    if p.prior_cin in ("CIN2", "CIN3"):
        probs = _adjust_probs(probs, inflate_keys=["ASC-H", "HSIL"], factor=1.8)

    categories = list(probs.keys())
    weights    = list(probs.values())
    return random.choices(categories, weights=weights, k=1)[0]


def draw_lung_rads_result() -> str:
    """
    Draw a Lung-RADS (v2022) result category from LDCT.
    PLACEHOLDER — calibrate to NYP LDCT data.
    """
    categories = list(cfg.LUNG_RADS_PROBS.keys())
    weights    = list(cfg.LUNG_RADS_PROBS.values())
    return random.choices(categories, weights=weights, k=1)[0]


def run_lung_pre_ldct(
    p: Patient, current_day: int, metrics: Optional[dict] = None
) -> bool:
    """
    Simulate the pre-LDCT pathway per the clinical flowchart:
      1. Referral placed (LDCT order)?  → LTFU if no  (unmet referral)
      2. Patient schedules LDCT?        → LTFU if no  (unmet referral — not scheduling)

    Updates patient fields and metrics in place.
    Returns True if LDCT proceeds, False if LTFU at either step.
    """
    if metrics is not None:
        metrics["lung_eligible"] += 1

    # Step 1: Is an LDCT order placed?
    if random.random() > cfg.LUNG_PATHWAY_PROBS["referral_placed"]:
        p.log(current_day, "LUNG pre-LDCT: no referral placed — unmet referral (LTFU)")
        p.exit_system(current_day, "lost_to_followup")
        return False

    p.lung_referral_placed = True
    p.log(current_day, "LUNG pre-LDCT: LDCT order placed")
    if metrics is not None:
        metrics["lung_referral_placed"] += 1

    # Step 2: Does the patient schedule the LDCT?
    if random.random() > cfg.LUNG_PATHWAY_PROBS["scheduled_after_referral"]:
        p.log(current_day, "LUNG pre-LDCT: scheduled but did not attend — unmet referral (LTFU)")
        p.exit_system(current_day, "lost_to_followup")
        return False

    p.lung_ldct_scheduled = True
    p.log(current_day, "LUNG pre-LDCT: LDCT scheduled and completed")
    if metrics is not None:
        metrics["lung_ldct_scheduled"] += 1
        metrics["lung_ldct_completed"] += 1

    return True


def draw_other_cancer_result(cancer: str) -> str:
    """Binary NEGATIVE / POSITIVE draw for non-cervical, non-lung cancers (stub)."""
    test     = cfg.SCREENING_TESTS.get(cancer, ["unknown"])[0]
    pos_rate = cfg.POSITIVE_RATES.get(test, 0.05)
    return "POSITIVE" if random.random() < pos_rate else "NEGATIVE"


# ─── Main Screening Step ──────────────────────────────────────────────────────

_RESULT_FIELD = {
    "cervical":     "cervical_result",
    "lung":         "lung_result",
    "breast":       "breast_result",
    "colorectal":   "colorectal_result",
    "osteoporosis": "osteo_result",
}


def run_screening_step(
    p: Patient, cancer: str, current_day: int,
    metrics: Optional[dict] = None
) -> Optional[str]:
    """
    Execute one screening event for the given cancer type.

    - Checks eligibility and due-date; skips if not applicable.
    - Assigns test modality, draws result, updates patient fields.
    - Returns the result string, or None if skipped / LTFU.

    Parameters
    ----------
    metrics : optional metrics dict — required for lung funnel counters.
    """
    if not ELIGIBILITY_CHECKS[cancer](p):
        p.log(current_day, f"SKIP {cancer} — not eligible (age={p.age})")
        return None

    if not is_due_for_screening(p, cancer, current_day):
        p.log(current_day, f"SKIP {cancer} — not yet due")
        return None

    test             = assign_screening_test(p, cancer)
    p.current_stage  = "screening"
    p.log(current_day, f"SCREEN {cancer} via {test}")

    # Draw result and update patient
    if cancer == "cervical":
        result               = draw_cervical_result(p, test)
        p.cervical_result    = result
        p.last_cervical_screen_day = current_day
    elif cancer == "lung":
        # Pre-LDCT pathway: referral → scheduling (two LTFU nodes before test)
        ldct_completed = run_lung_pre_ldct(p, current_day, metrics)
        if not ldct_completed:
            return None   # patient LTFU before test — exit already recorded
        result         = draw_lung_rads_result()
        p.lung_result  = result
        p.last_lung_screen_day = current_day
        if metrics is not None:
            metrics["lung_rads_distribution"][result] += 1
    elif cancer == "breast":
        result               = draw_other_cancer_result(cancer)
        p.breast_result      = result
        p.last_breast_screen_day = current_day
    elif cancer == "colorectal":
        result               = draw_other_cancer_result(cancer)
        p.colorectal_result  = result
        p.last_colorectal_screen_day = current_day
    elif cancer == "osteoporosis":
        result               = draw_other_cancer_result(cancer)
        p.osteo_result       = result
        p.last_osteo_screen_day = current_day
    else:
        result = "UNKNOWN"

    p.log(current_day, f"RESULT {cancer}: {result}")
    return result


# ─── Unscreened Pathway ───────────────────────────────────────────────────────

def handle_unscreened(p: Patient, current_day: int) -> str:
    """
    Decision node for patients who did not receive screening.
    Draws whether they will agree to reschedule.

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
