# =============================================================================
# screening.py
# Steps 2–3: Eligibility checks, test assignment, and result draws.
# =============================================================================
#
# ROLE IN THE SIMULATION
# ─────────────────────────────────────────────────────────────────────────────
# This module implements the decision layer that runs immediately after a patient
# is seen by a provider. It answers three sequential questions:
#   1. Should this patient be screened? (eligibility + interval check)
#   2. Which test should they receive? (age-stratified test assignment)
#   3. What was the result? (stochastic draw from a probability table)
#
# For lung cancer, it also models the two pre-LDCT steps (referral placed,
# scan scheduled) that can produce LTFU before the scan even happens.
#
# DEPENDENCY DIRECTION
# ─────────────────────────────────────────────────────────────────────────────
# screening.py ← called by runner.py and notebook 04
# screening.py → reads config.py for all parameters
# screening.py → writes result fields on Patient objects
# followup.py  ← picks up where screening.py leaves off (reads patient.cervical_result, etc.)
#
# All probabilities are PLACEHOLDERS — replace with NYP data.
# =============================================================================

import random
from typing import List, Optional

import config as cfg
from patient import Patient


# ─── Eligibility ──────────────────────────────────────────────────────────────

def is_eligible_cervical(p: Patient) -> bool:
    """
    Return True if the patient meets USPSTF cervical screening eligibility criteria.

    Criteria: age 21–65 AND anatomically has a cervix (no prior hysterectomy).
    Age bounds are read from config.ELIGIBILITY['cervical'] so they can be
    adjusted without touching this function.
    """
    e = cfg.ELIGIBILITY["cervical"]
    return e["age_min"] <= p.age <= e["age_max"] and p.has_cervix


def is_eligible_lung(p: Patient) -> bool:
    """
    USPSTF 2021 lung screening criteria — all three must be true:
      - Age 50–80
      - At least 20 pack-years of smoking history
      - Currently smoking OR quit within the last 15 years
    """
    e = cfg.ELIGIBILITY["lung"]
    age_ok     = e["age_min"] <= p.age <= e["age_max"]
    pack_ok    = p.pack_years >= e["min_pack_years"]
    # Current smoker counts; former smoker counts only if quit recently enough
    smoking_ok = p.smoker or (p.years_since_quit <= e["max_years_since_quit"])
    return age_ok and pack_ok and smoking_ok


# Maps each cancer name to its eligibility function — used to loop over all cancers
ELIGIBILITY_CHECKS = {
    "cervical": is_eligible_cervical,
    "lung":     is_eligible_lung,
}


def get_eligible_screenings(p: Patient) -> List[str]:
    """
    Return the list of cancer types this patient is eligible to be screened for today.

    Loops over ACTIVE_CANCERS (set in config) and runs each cancer's eligibility
    function. Returns only those where the patient passes. The result may be empty
    (patient is ineligible for everything today) or contain multiple cancers (patient
    is due for both cervical and lung on the same visit).
    """
    return [
        cancer for cancer, check in ELIGIBILITY_CHECKS.items()
        if cancer in cfg.ACTIVE_CANCERS and check(p)
    ]


# ─── Screening Interval Check ─────────────────────────────────────────────────

# Maps cancer name to the Patient attribute that stores the last screening date
_LAST_SCREEN_FIELD = {
    "cervical": "last_cervical_screen_day",
    "lung":     "last_lung_screen_day",
}


def is_due_for_screening(p: Patient, cancer: str, current_day: int) -> bool:
    """
    Return True if enough time has passed since this patient's last screen.

    A patient is always due if they have never been screened (last_day = -1).
    Otherwise, the interval between screens depends on the assigned test type:
    cytology → 3 years, HPV-alone → 5 years, LDCT → 1 year. These are defined
    in config.SCREENING_INTERVALS_DAYS.

    This check prevents the simulation from over-screening a patient who was
    recently seen at a different visit within the same interval window.
    """
    last_day = getattr(p, _LAST_SCREEN_FIELD[cancer], -1)
    if last_day < 0:
        return True  # never screened — always due
    test     = assign_screening_test(p, cancer)
    interval = cfg.SCREENING_INTERVALS_DAYS.get(test, 365)
    return (current_day - last_day) >= interval


# ─── Age Stratum ──────────────────────────────────────────────────────────────

def get_cervical_age_stratum(age: int) -> str:
    """
    Classify a patient's age into the USPSTF cervical screening stratum.

    The stratum controls both which tests are offered and which probability
    table is used when drawing a result:
      young  (21–29): cytology only, every 3 years
      middle (30–65): cytology every 3 years OR HPV-alone every 5 years
      older  (66+):   no routine screening if prior history is adequate
    """
    if 21 <= age <= 29:
        return "young"
    elif 30 <= age <= 65:
        return "middle"
    return "older"


# ─── Test Assignment ──────────────────────────────────────────────────────────

def assign_screening_test(p: Patient, cancer: str) -> str:
    """
    Pick the specific test modality for this patient and cancer type.
    For cervical, this is age-stratified (cytology vs. HPV-alone).
    For lung, it's always LDCT.
    Returns 'ineligible' if no test options exist for this patient.
    """
    if cancer == "cervical":
        stratum = get_cervical_age_stratum(p.age)
        options = cfg.SCREENING_TESTS["cervical"].get(stratum, [])
        if not options:
            return "ineligible"
        # For middle stratum, randomly pick cytology or HPV-alone each visit
        return random.choice(options)

    options = cfg.SCREENING_TESTS.get(cancer, [])
    return options[0] if options else "ineligible"


# ─── Result Draws ─────────────────────────────────────────────────────────────

def _adjust_probs(probs: dict, inflate_keys: list, factor: float) -> dict:
    """
    Apply a multiplicative risk adjustment to selected result categories, then renormalise.

    Multiplies each key in inflate_keys by factor, leaving the others unchanged.
    After inflation the whole dict is divided by its sum so all values still add to 1.
    This is how the model represents higher abnormal rates in higher-risk patients:
    HPV positivity inflates all abnormal cytology categories; prior CIN2/3 further
    inflates the high-grade categories specifically.
    """
    adjusted = {k: v * factor if k in inflate_keys else v for k, v in probs.items()}
    total    = sum(adjusted.values())
    return {k: v / total for k, v in adjusted.items()}


def draw_cervical_result(p: Patient, test: str) -> str:
    """
    Randomly draw a cervical screening result based on the test type and patient risk factors.

    Cytology returns one of: NORMAL | ASCUS | LSIL | ASC-H | HSIL
    HPV-alone returns one of: HPV_NEGATIVE | HPV_POSITIVE

    Risk adjustments:
      - HPV positive: inflates all abnormal cytology categories by 1.5×
      - Prior CIN2/CIN3: further inflates high-grade categories (ASC-H, HSIL) by 1.8×
    These multipliers are PLACEHOLDERS — calibrate against NYP data.
    """
    stratum = get_cervical_age_stratum(p.age)

    if test == "hpv_alone":
        # HPV-alone test: binary result, no Pap categories
        probs = dict(cfg.CERVICAL_RESULT_PROBS["middle_hpv"])
        if p.hpv_positive:
            # Known HPV carrier → higher chance of testing positive
            probs = _adjust_probs(probs, inflate_keys=["HPV_POSITIVE"], factor=2.0)

    elif stratum == "young":
        # Age 21–29: cytology only (USPSTF does not recommend HPV testing under 30)
        probs = dict(cfg.CERVICAL_RESULT_PROBS["young"])
        if p.hpv_positive:
            probs = _adjust_probs(probs, inflate_keys=["ASCUS", "LSIL", "ASC-H", "HSIL"], factor=1.5)
        if p.prior_cin in ("CIN2", "CIN3"):
            # History of high-grade CIN raises risk of HSIL recurrence
            probs = _adjust_probs(probs, inflate_keys=["ASC-H", "HSIL"], factor=1.8)

    else:
        # Age 30–65: cytology (middle stratum base rates)
        probs = dict(cfg.CERVICAL_RESULT_PROBS["middle_cytology"])
        if p.hpv_positive:
            probs = _adjust_probs(probs, inflate_keys=["ASCUS", "LSIL", "ASC-H", "HSIL"], factor=1.5)
        if p.prior_cin in ("CIN2", "CIN3"):
            probs = _adjust_probs(probs, inflate_keys=["ASC-H", "HSIL"], factor=1.8)

    return random.choices(list(probs.keys()), weights=list(probs.values()), k=1)[0]


def draw_lung_rads_result() -> str:
    """
    Draw a Lung-RADS v2022 result category from the configured distribution.
    Categories: RADS_0 (incomplete) through RADS_4B_4X (very suspicious).
    PLACEHOLDER — calibrate to NYP LDCT volume data.
    """
    return random.choices(
        list(cfg.LUNG_RADS_PROBS.keys()),
        weights=list(cfg.LUNG_RADS_PROBS.values()),
        k=1,
    )[0]


def run_lung_pre_ldct(
    p: Patient, current_day: int, metrics: Optional[dict] = None
) -> bool:
    """
    Simulate the two administrative steps that must clear before an LDCT scan occurs.

    In real-world lung cancer screening, a significant fraction of eligible patients
    never complete their scan because one of these two steps fails:
      1. Provider places a referral/order (many eligible patients are never referred).
      2. Patient schedules and shows up for the scan (many referred patients never book).

    Each step draws a Bernoulli sample against the configured probability. If either
    step fails the patient is classified as LTFU and the function returns False.
    Returns True only when both steps succeed and the scan should proceed.
    """
    if metrics is not None:
        metrics["lung_eligible"] += 1

    # Step 1: Was a referral order placed?
    if random.random() > cfg.LUNG_PATHWAY_PROBS["referral_placed"]:
        p.log(current_day, "LUNG: no referral placed — LTFU")
        p.exit_system(current_day, "lost_to_followup")
        return False

    p.lung_referral_placed = True
    p.log(current_day, "LUNG: LDCT order placed")
    if metrics is not None:
        metrics["lung_referral_placed"] += 1

    # Step 2: Did the patient schedule (and complete) the scan?
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

# Maps cancer name to the Patient attribute where the result is stored
_RESULT_FIELD = {
    "cervical": "cervical_result",
    "lung":     "lung_result",
}


def run_screening_step(
    p: Patient, cancer: str, current_day: int,
    metrics: Optional[dict] = None,
) -> Optional[str]:
    """
    Execute one full screening event for the given cancer type.

    Checks eligibility and interval, assigns the test, draws the result,
    and stores it on the patient. For lung, also runs the pre-LDCT pathway
    (referral + scheduling steps) before drawing a result.

    Returns the result string, or None if the patient was skipped or lost.
    """
    # Skip if not eligible (age, cervix, smoking history)
    if not ELIGIBILITY_CHECKS[cancer](p):
        p.log(current_day, f"SKIP {cancer} — not eligible (age={p.age})")
        return None

    # Skip if not enough time has passed since last screen
    if not is_due_for_screening(p, cancer, current_day):
        p.log(current_day, f"SKIP {cancer} — not yet due")
        return None

    test            = assign_screening_test(p, cancer)
    p.current_stage = "screening"
    p.log(current_day, f"SCREEN {cancer} via {test}")

    if cancer == "cervical":
        result                     = draw_cervical_result(p, test)
        p.cervical_result          = result
        p.last_cervical_screen_day = current_day

    elif cancer == "lung":
        # Lung requires referral + scheduling before the scan can happen
        if not run_lung_pre_ldct(p, current_day, metrics):
            return None  # patient was lost before scan
        result                 = draw_lung_rads_result()
        p.lung_result          = result
        p.last_lung_screen_day = current_day
        if metrics is not None:
            metrics["lung_rads_distribution"][result] += 1

    else:
        result = "UNKNOWN"

    p.log(current_day, f"RESULT {cancer}: {result}")
    return result


# ─── Future Eligibility ───────────────────────────────────────────────────────

def days_until_eligible(p: Patient, cancer: str) -> int:
    """
    Return how many days until this patient becomes eligible for the given cancer screen.

    This drives the three-way eligibility routing in the runner:
      0   — eligible right now (call run_screening_step)
      > 0 — will become eligible in ~N days (schedule a return visit)
      -1  — permanently ineligible (no cervix, aged out, never smoked, etc.);
            no return visit should be scheduled — exit the patient silently

    For lung, the function also handles the case where a current smoker will eventually
    accumulate enough pack-years to qualify, returning the estimated wait time.
    """
    if cancer == "cervical":
        e = cfg.ELIGIBILITY["cervical"]
        if not p.has_cervix:
            return -1                                 # hysterectomy or no cervix — will never qualify
        if p.age > e["age_max"]:
            return -1                                 # over 65 — aged out of screening
        if p.age < e["age_min"]:
            return (e["age_min"] - p.age) * 365      # turns 21 in ~N days
        return 0                                      # eligible now

    if cancer == "lung":
        e = cfg.ELIGIBILITY["lung"]
        if p.age > e["age_max"]:
            return -1                                 # over 80 — aged out
        if not p.smoker and p.pack_years == 0:
            return -1                                 # never smoked — can never qualify
        if not p.smoker and p.years_since_quit > e["max_years_since_quit"]:
            return -1                                 # quit too long ago — window closed

        days_needed = 0
        if p.age < e["age_min"]:
            # Must wait until age 50
            days_needed = max(days_needed, (e["age_min"] - p.age) * 365)
        if p.pack_years < e["min_pack_years"]:
            if not p.smoker:
                return -1                             # not smoking now, can't accumulate more pack-years
            # Currently smoking — will reach 20 pack-years in N days (1 pack-year ≈ 365 days)
            packs_needed = e["min_pack_years"] - p.pack_years
            days_needed  = max(days_needed, int(packs_needed * 365))
        return days_needed if days_needed > 0 else 0

    return -1  # unknown cancer type


# ─── Unscreened Pathway ───────────────────────────────────────────────────────

def handle_unscreened(p: Patient, current_day: int) -> str:
    """
    Decision node for ELIGIBLE patients who were not offered screening during a visit
    (e.g. provider ran out of time, patient declined).

    Reserved for future use when that specific scenario is modelled explicitly.
    The current runner uses days_until_eligible() instead to route ineligible patients.

    Returns: "reschedule" | "exit"
    """
    if not p.active:
        return "exit"

    if random.random() < cfg.LTFU_PROBS["unscreened_will_reschedule"]:
        p.willing_to_reschedule = True
        p.log(current_day, "UNSCREENED (eligible) — willing to reschedule")
        return "reschedule"

    p.willing_to_reschedule = False
    p.exit_system(current_day, "lost_to_followup")
    p.log(current_day, "UNSCREENED (eligible) — exits system (will not reschedule)")
    return "exit"
