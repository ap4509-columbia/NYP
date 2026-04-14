# =============================================================================
# followup.py
# Steps 4–5: Post-screening clinical follow-up pathways.
# =============================================================================
#
# ROLE IN THE SIMULATION
# ─────────────────────────────────────────────────────────────────────────────
# This module picks up where screening.py leaves off. Once a result is written
# onto the Patient object, followup.py determines what happens next clinically:
#   - Is the result normal? → return to routine surveillance schedule.
#   - Is the result abnormal? → refer for a confirmatory/treatment procedure,
#     subject to stochastic loss-to-follow-up (LTFU) at each decision node.
#
# Cervical pathway:
#   result routing → colposcopy → CIN grade draw → treatment (LEEP)
#   LTFU is handled by queue LTFU in runner.py (daily hazard while waiting
#   for a procedure slot).
#
# Lung pathway:
#   RADS routing → result communicated → biopsy referral → scheduling
#   → completion → malignancy confirmed → treatment
#   LTFU is handled by queue LTFU in runner.py (daily hazard while waiting
#   for a procedure slot), consistent with the cervical pathway.
#
# DEPENDENCY DIRECTION
# ─────────────────────────────────────────────────────────────────────────────
# followup.py  ← called by runner.py and notebook 04
# followup.py  → reads config.py for LTFU probs, colposcopy probs, treatment assignment
# followup.py  → reads/writes Patient fields (cervical_result, colposcopy_result, etc.)
#
# All probabilities are PLACEHOLDERS — replace with NYP data / ASCCP tables.
# =============================================================================

import random
from typing import Optional

import config as cfg
from patient import Patient

# Analysis warmup cutoff — metrics recorded before this day are excluded
_WARMUP_DAY = cfg.WARMUP_YEARS * cfg.DAYS_PER_YEAR


# ─── Cervical: Result Routing ─────────────────────────────────────────────────

# Cytology results that always trigger a colposcopy referral
_CYTOLOGY_COLPOSCOPY_TRIGGERS = {"ASCUS", "LSIL", "ASC-H", "HSIL"}

# Results that mean "no abnormality found — return to routine schedule"
_NORMAL_RESULTS = {"NORMAL", "HPV_NEGATIVE"}


def route_cervical_result(
    p: Patient, current_day: int, metrics: Optional[dict] = None
) -> str:
    """
    Decide the clinical next step based on the patient's cervical screening result.

    Cytology (age 21–65):
      NORMAL              → routine surveillance (no follow-up needed)
      ASCUS / LSIL / ASC-H / HSIL → colposcopy referral, subject to LTFU check

    HPV-alone (age 30–65):
      HPV_NEGATIVE        → routine surveillance
      HPV_POSITIVE        → ASCCP triage: ~40% managed with 1-year repeat cytology
                            (low-risk path), ~60% referred directly to colposcopy
                            (PLACEHOLDER split — replace with NYP risk-table values)

    Returns one of:
      "colposcopy"         — patient should proceed to colposcopy
      "one_year_repeat"    — patient gets a 1-year follow-up cytology instead
      "routine_surveillance" — return to normal screening interval
      "exit"               — patient lost to follow-up
    """
    result = p.cervical_result

    # Normal results: no follow-up needed, return to routine schedule
    if result in _NORMAL_RESULTS:
        p.log(current_day, f"ROUTE cervical {result} → routine surveillance")
        return "routine_surveillance"

    # Abnormal cytology: all four categories go to colposcopy
    if result in _CYTOLOGY_COLPOSCOPY_TRIGGERS:
        p.log(current_day, f"ROUTE {result} → colposcopy")
        return "colposcopy"

    # HPV positive: use ASCCP triage split
    if result == "HPV_POSITIVE":
        # HPV+ triage: route to 1-year repeat cytology (lower-risk) vs colposcopy.
        # Split is controlled by config.HPV_POSITIVE_COLPOSCOPY_PROB (PLACEHOLDER).
        if random.random() >= cfg.HPV_POSITIVE_COLPOSCOPY_PROB:
            p.log(current_day, "ROUTE HPV_POSITIVE → 1-year repeat cytology (low-risk mgmt)")
            return "one_year_repeat"
        p.log(current_day, "ROUTE HPV_POSITIVE → colposcopy")
        return "colposcopy"

    # Fallback: unknown result category — treat as normal to avoid hard failures
    p.log(current_day, f"ROUTE unknown result '{result}' → routine surveillance")
    return "routine_surveillance"


# ─── Cervical: Colposcopy ─────────────────────────────────────────────────────

def draw_colposcopy_result(p: Patient) -> str:
    """
    Draw a CIN grade from colposcopy, conditional on the result that triggered it.
    The key format matches COLPOSCOPY_RESULT_PROBS in config (e.g. 'from_HSIL').
    Falls back to a default distribution if the trigger is not found in config.
    """
    key   = f"from_{p.cervical_result}"   # e.g. "from_HSIL", "from_HPV_POSITIVE"
    probs = cfg.COLPOSCOPY_RESULT_PROBS.get(
        key, cfg.COLPOSCOPY_RESULT_PROBS_DEFAULT
    )
    return random.choices(list(probs.keys()), weights=list(probs.values()), k=1)[0]


def run_colposcopy(
    p: Patient, current_day: int, metrics: Optional[dict] = None
) -> Optional[str]:
    """
    Perform a colposcopy procedure and record the CIN grade on the patient.
    Advances the patient's stage to 'followup'.

    CIN grades:
      NORMAL  — no dysplasia found; patient goes to surveillance
      CIN1    — low-grade; typically managed with surveillance
      CIN2/3  — high-grade; treated with excisional procedure (LEEP or cone)

    Returns the CIN grade string.
    """
    p.current_stage     = "followup"
    cin                 = draw_colposcopy_result(p)
    # If colposcopy sample is insufficient, patient loops back for repeat colposcopy
    # Source: AiP Parameters PDF — "Patients with insufficient information to diagnose loop back"
    # INSUFFICIENT is included in COLPOSCOPY_RESULT_PROBS_DEFAULT with prob 0.07
    p.colposcopy_result = cin
    p.log(current_day, f"COLPOSCOPY → {cin}")

    if metrics is not None and current_day >= _WARMUP_DAY:
        metrics["n_colposcopy"] += 1
        metrics["colposcopy_results"][cin] += 1

    return cin


# ─── Cervical: Treatment ──────────────────────────────────────────────────────

def assign_treatment_type(cin_grade: str) -> str:
    """
    Map CIN grade to treatment modality using the config table.
    NORMAL/CIN1 → surveillance (watchful waiting)
    CIN2/CIN3   → leep (excisional treatment)
    """
    return cfg.TREATMENT_ASSIGNMENT.get(cin_grade, "surveillance")


def run_treatment(
    p: Patient, current_day: int, metrics: Optional[dict] = None
) -> str:
    """
    Execute the treatment step after colposcopy.

    If CIN grade maps to surveillance (NORMAL or CIN1):
      → patient enters 1-year surveillance schedule

    If CIN grade maps to an excisional procedure (CIN2 or CIN3):
      → patient is treated with LEEP; stage set to 'treated'

    Returns one of: "surveillance" | "treated"
    """
    treatment        = assign_treatment_type(p.colposcopy_result or "NORMAL")
    p.treatment_type = treatment

    if treatment == "surveillance":
        # Low-grade finding — no procedure needed, watch and repeat in 1 year
        p.current_stage = "surveillance"
        p.log(current_day, f"TREATMENT — {p.colposcopy_result}: 1-year surveillance")
        if metrics is not None and current_day >= _WARMUP_DAY:
            metrics["n_treatment"]["surveillance"] += 1
        return "surveillance"

    # High-grade finding — excisional treatment (LEEP or cold-knife cone)
    p.current_stage = "treated"
    p.log(current_day, f"TREATMENT — {treatment} (excisional) for {p.colposcopy_result}")
    if metrics is not None and current_day >= _WARMUP_DAY:
        metrics["n_treatment"][treatment] += 1
        metrics["n_treated"] += 1
    return "treated"


# ─── Cervical: Full Follow-Up Orchestrator ────────────────────────────────────

def run_cervical_followup(
    p: Patient, current_day: int, metrics: Optional[dict] = None
) -> str:
    """
    Main cervical follow-up pipeline. Chains the three steps in order:
      1. Route result → decide next clinical action
      2. Run colposcopy → get CIN grade
      3. Run treatment → excise or surveil

    Returns the final patient disposition:
      "routine_surveillance" — no abnormality / normal HPV → back to schedule
      "one_year_repeat"      — HPV+ low-risk path → 1-yr cytology repeat
      "surveillance"         — CIN1 / normal colposcopy → watchful waiting
      "treated"              — CIN2/3 → excisional treatment completed
      "exit"                 — patient lost to follow-up at some step
    """
    next_step = route_cervical_result(p, current_day, metrics)

    # These dispositions require no further action in this pipeline
    if next_step in ("routine_surveillance", "one_year_repeat", "exit"):
        return next_step

    if next_step == "colposcopy":
        cin = run_colposcopy(p, current_day, metrics)
        if cin is None:
            return "exit"
        # CIN grade known — proceed to treatment assignment
        return run_treatment(p, current_day, metrics)

    return "routine_surveillance"


# ─── Lung: Full Follow-Up Pathway (per clinical flowchart) ───────────────────
#
# Lung-RADS (v2022) post-LDCT disposition:
#
#   RADS 0            → communicate result → repeat LDCT in 1–3 months
#   RADS 1 / RADS 2   → communicate result → repeat LDCT in 12 months
#   RADS 3            → communicate result → repeat LDCT in 6 months
#   RADS 4A / 4B / 4X → communicate result → biopsy pathway → outcome
#
# At every node, communication failure or scheduling failure = LTFU.
# The biopsy pathway has five sequential LTFU nodes before treatment.

# RADS categories that lead to a repeat LDCT (no biopsy needed)
_LUNG_RADS_REPEAT = {"RADS_0", "RADS_1", "RADS_2", "RADS_3"}

# RADS categories that require a biopsy to rule out malignancy
_LUNG_RADS_BIOPSY = {"RADS_4A", "RADS_4B_4X"}


def _lung_result_communicated(p: Patient, current_day: int, metrics: Optional[dict]) -> bool:
    """
    Record that the LDCT result was communicated to the patient.

    Always returns True — LTFU is handled exclusively by the queue-based
    geometric waiting-time hazard, consistent with the cervical pathway.
    """
    if metrics is not None and current_day >= _WARMUP_DAY:
        metrics["lung_result_communicated"] += 1
    p.log(current_day, f"LUNG {p.lung_result}: results communicated to patient")
    return True


def run_lung_followup(
    p: Patient, current_day: int, metrics: Optional[dict] = None
) -> str:
    """
    Full lung follow-up pipeline per the clinical flowchart.

    For RADS 0/1/2/3 (repeat pathway):
      Communicate result → schedule repeat LDCT at the appropriate interval.

    For RADS 4A/4B/4X (biopsy pathway):
      Communicate result → biopsy referral → scheduling → completion
      → malignancy confirmation → treatment.
      LTFU is handled by queue-based geometric waiting-time hazard only
      (consistent with cervical pathway).

    Returns final disposition string:
      "repeat_ldct_12mo"    — RADS 1/2: routine annual repeat
      "repeat_ldct_6mo"     — RADS 3: 6-month repeat
      "repeat_ldct_1_3mo"   — RADS 0: incomplete scan, 1–3 month repeat
      "lung_treated"        — biopsy confirmed malignancy, treatment given
      "exit"                — guard (should not occur; LTFU via queue only)
    """
    rads = p.lung_result

    # ── RADS 0, 1, 2, 3 — communicate result then schedule repeat LDCT ────────
    if rads in _LUNG_RADS_REPEAT:
        if not _lung_result_communicated(p, current_day, metrics):
            return "exit"  # should not happen (always True), but guard

        repeat_days = cfg.LUNG_RADS_REPEAT_INTERVALS.get(rads, 365)
        if rads == "RADS_0":
            # Incomplete scan — must repeat sooner (1–3 months) to get a valid result
            p.log(current_day, f"LUNG {rads}: repeat LDCT in ~{repeat_days} days (1–3 months)")
            return "repeat_ldct_1_3mo"
        elif rads == "RADS_3":
            # Probably benign nodule — recheck in 6 months to confirm stability
            p.log(current_day, f"LUNG {rads}: repeat LDCT in {repeat_days} days (6 months)")
            return "repeat_ldct_6mo"
        else:
            # RADS 1/2: negative or clearly benign — routine annual surveillance
            p.log(current_day, f"LUNG {rads}: repeat LDCT in {repeat_days} days (12 months)")
            return "repeat_ldct_12mo"

    # ── RADS 4A / 4B / 4X — suspicious nodule, biopsy pathway ────────────────
    # LTFU is handled exclusively by queue-based geometric waiting-time hazard
    # (runner.py _check_queue_ltfu). No node-level Bernoulli LTFU here.
    if rads in _LUNG_RADS_BIOPSY:
        if not _lung_result_communicated(p, current_day, metrics):
            return "exit"  # should not happen (always True), but guard

        # Biopsy referral, scheduling, and completion — proceed to slot competition
        if metrics is not None and current_day >= _WARMUP_DAY:
            metrics["lung_biopsy_referral"] += 1
        p.log(current_day, f"LUNG {rads}: biopsy referral placed")

        if metrics is not None and current_day >= _WARMUP_DAY:
            metrics["lung_biopsy_scheduled"] += 1
        p.log(current_day, "LUNG biopsy: scheduled")

        if metrics is not None and current_day >= _WARMUP_DAY:
            metrics["lung_biopsy_completed"] += 1
        p.log(current_day, "LUNG biopsy: completed")

        # Malignancy confirmed by pathology? (clinical result, not LTFU)
        if random.random() > cfg.LUNG_PATHWAY_PROBS["malignancy_confirmed"]:
            # Biopsy came back benign — return to annual surveillance
            p.lung_biopsy_result = "benign"
            p.log(current_day, "LUNG biopsy: benign — return to surveillance")
            return "repeat_ldct_12mo"

        p.lung_biopsy_result = "malignant"
        if metrics is not None and current_day >= _WARMUP_DAY:
            metrics["lung_malignancy_confirmed"] += 1
        p.log(current_day, "LUNG biopsy: malignancy confirmed")

        # Treatment — proceeds to slot competition (LTFU via queue hazard only)
        p.current_stage = "treated"
        if metrics is not None and current_day >= _WARMUP_DAY:
            metrics["lung_treatment_given"] += 1
            metrics["n_treated"] += 1
        p.log(current_day, "LUNG: treatment given (surgery / radiation / med onc)")
        return "lung_treated"

    # Fallback — should never be reached if lung_result is always a valid RADS category
    p.log(current_day, f"LUNG: unknown RADS category '{rads}' — surveillance")
    return "repeat_ldct_12mo"
