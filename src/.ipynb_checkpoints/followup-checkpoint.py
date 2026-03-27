# =============================================================================
# followup.py
# Steps 4–5: Post-screening clinical follow-up pathways.
# =============================================================================
# Cervical: result routing → colposcopy → CIN grading → treatment / surveillance
# Lung:     Lung-RADS routing → repeat LDCT or biopsy chain → malignancy → treatment
#
# All probabilities are PLACEHOLDERS — replace with NYP data / ASCCP tables.
# =============================================================================

import random
from typing import Optional

import config as cfg
from patient import Patient


# ─── Loss-to-Follow-Up Checks ─────────────────────────────────────────────────

def check_ltfu_post_abnormal(p: Patient, metrics: Optional[dict] = None) -> bool:
    """Did the patient drop out after an abnormal screening result?"""
    lost = random.random() < cfg.LTFU_PROBS["post_abnormal_to_colposcopy"]
    if lost and metrics is not None:
        metrics["ltfu_post_abnormal"] += 1
    return lost


def check_ltfu_post_colposcopy(p: Patient, metrics: Optional[dict] = None) -> bool:
    """Did the patient drop out after colposcopy before completing treatment?"""
    lost = random.random() < cfg.LTFU_PROBS["post_colposcopy_to_treatment"]
    if lost and metrics is not None:
        metrics["ltfu_post_colposcopy"] += 1
    return lost


# ─── Cervical: Result Routing ─────────────────────────────────────────────────

# Results that trigger colposcopy referral
_COLPOSCOPY_TRIGGERS = {"ASCUS", "LSIL", "ASC-H", "HSIL", "HPV_POS_NORMAL_CYTO"}


def route_cervical_result(
    p: Patient, current_day: int, metrics: Optional[dict] = None
) -> str:
    """
    Determine the clinical next step after a cervical screening result.

    Returns one of:
      "colposcopy"         — patient referred and follows up
      "one_year_repeat"    — low-risk management (HPV+/normal cyto path)
      "routine_surveillance" — normal result, return to standard interval
      "exit"               — lost to follow-up
    """
    result = p.cervical_result

    if result == "NORMAL":
        p.log(current_day, "ROUTE cervical NORMAL → routine surveillance")
        return "routine_surveillance"

    if result in _COLPOSCOPY_TRIGGERS:
        if check_ltfu_post_abnormal(p, metrics):
            p.exit_system(current_day, "lost_to_followup")
            p.log(current_day, f"LTFU after {result} — no colposcopy follow-up")
            return "exit"

        # HPV+/normal cytology: 40% chance of 1-year repeat instead of immediate colposcopy
        # (per ASCCP low-risk management pathway)  PLACEHOLDER rate
        if result == "HPV_POS_NORMAL_CYTO" and random.random() < 0.40:
            p.log(current_day, "ROUTE HPV+/normal cyto → 1-year repeat (low-risk mgmt)")
            return "one_year_repeat"

        p.log(current_day, f"ROUTE {result} → colposcopy")
        return "colposcopy"

    # Fallback
    p.log(current_day, f"ROUTE unknown result '{result}' → routine surveillance")
    return "routine_surveillance"


# ─── Cervical: Colposcopy ─────────────────────────────────────────────────────

def draw_colposcopy_result(p: Patient) -> str:
    """
    Draw CIN grade from colposcopy, conditional on the triggering result.
    Uses COLPOSCOPY_RESULT_PROBS from config (PLACEHOLDER).
    """
    key   = f"from_{p.cervical_result}"
    probs = cfg.COLPOSCOPY_RESULT_PROBS.get(
        key, {"NORMAL": 0.50, "CIN1": 0.25, "CIN2": 0.15, "CIN3": 0.10}
    )
    return random.choices(list(probs.keys()), weights=list(probs.values()), k=1)[0]


def run_colposcopy(
    p: Patient, current_day: int, metrics: Optional[dict] = None
) -> Optional[str]:
    """
    Perform colposcopy and record the CIN result on the patient.

    Returns CIN grade string or None (should not be None in normal flow;
    included for safety).
    """
    p.current_stage = "followup"
    cin             = draw_colposcopy_result(p)
    p.colposcopy_result = cin
    p.log(current_day, f"COLPOSCOPY → {cin}")

    if metrics is not None:
        metrics["n_colposcopy"] += 1
        metrics["colposcopy_results"][cin] += 1

    return cin


# ─── Cervical: Treatment ──────────────────────────────────────────────────────

def assign_treatment_type(cin_grade: str) -> str:
    """Map CIN grade to treatment modality per config."""
    return cfg.TREATMENT_ASSIGNMENT.get(cin_grade, "surveillance")


def run_treatment(
    p: Patient, current_day: int, metrics: Optional[dict] = None
) -> str:
    """
    Execute treatment for cervical lesion after colposcopy.

    Applies LTFU check before treatment.
    Returns: "surveillance" | "treated" | "exit"
    """
    if check_ltfu_post_colposcopy(p, metrics):
        p.exit_system(current_day, "untreated")
        p.log(current_day, "LTFU — did not complete treatment after colposcopy")
        return "exit"

    treatment        = assign_treatment_type(p.colposcopy_result or "NORMAL")
    p.treatment_type = treatment

    if treatment == "surveillance":
        p.current_stage = "surveillance"
        p.log(current_day, f"TREATMENT — {p.colposcopy_result}: 1-year surveillance")
        if metrics is not None:
            metrics["n_treatment"]["surveillance"] += 1
        return "surveillance"

    # Excisional treatment (LEEP or cone)
    p.current_stage = "treated"
    p.log(current_day, f"TREATMENT — {treatment} (excisional) for {p.colposcopy_result}")
    if metrics is not None:
        metrics["n_treatment"][treatment] += 1
        metrics["n_treated"] += 1
    return "treated"


# ─── Cervical: Full Follow-Up Orchestrator ────────────────────────────────────

def run_cervical_followup(
    p: Patient, current_day: int, metrics: Optional[dict] = None
) -> str:
    """
    Main cervical follow-up pipeline.
    Chains: result routing → colposcopy → CIN grading → treatment / surveillance.

    Returns final disposition:
      "routine_surveillance" | "one_year_repeat" | "surveillance" | "treated" | "exit"
    """
    next_step = route_cervical_result(p, current_day, metrics)

    if next_step in ("routine_surveillance", "one_year_repeat", "exit"):
        return next_step

    if next_step == "colposcopy":
        cin = run_colposcopy(p, current_day, metrics)
        if cin is None:
            return "exit"
        return run_treatment(p, current_day, metrics)

    return "routine_surveillance"


# ─── Lung: Full Follow-Up Pathway (per clinical flowchart) ───────────────────
#
# Lung-RADS (v2022) post-LDCT flow:
#
#   RADS 0            → communicate results → repeat LDCT in 1–3 months
#   RADS 1 / RADS 2   → communicate results → repeat LDCT in 12 months
#   RADS 3            → communicate results → repeat LDCT in 6 months
#   RADS 4A           → communicate results → biopsy referral → ... → outcome
#   RADS 4B / 4X      → communicate results → biopsy referral → ... → outcome
#
# Communication failure at any tier → "Unmet referral, loss to follow up"
# Biopsy pathway LTFU nodes: referral → scheduling → completion → malignancy → treatment

_LUNG_RADS_REPEAT = {    # categories that lead to repeat LDCT (not biopsy)
    "RADS_0", "RADS_1", "RADS_2", "RADS_3",
}
_LUNG_RADS_BIOPSY = {    # categories that trigger biopsy pathway
    "RADS_4A", "RADS_4B_4X",
}


def _lung_result_communicated(p: Patient, current_day: int, metrics: Optional[dict]) -> bool:
    """
    Check: Were results communicated to the patient?
    Failure → unmet referral / loss to follow-up.
    """
    if random.random() > cfg.LUNG_PATHWAY_PROBS["result_communicated"]:
        p.log(current_day, f"LUNG {p.lung_result}: results NOT communicated — LTFU")
        p.exit_system(current_day, "lost_to_followup")
        if metrics is not None:
            metrics["ltfu_post_abnormal"] += 1
        return False
    if metrics is not None:
        metrics["lung_result_communicated"] += 1
    p.log(current_day, f"LUNG {p.lung_result}: results communicated to patient")
    return True


def run_lung_followup(
    p: Patient, current_day: int, metrics: Optional[dict] = None
) -> str:
    """
    Full lung follow-up pipeline per the clinical flowchart.

    Returns final disposition:
      "repeat_ldct_12mo" | "repeat_ldct_6mo" | "repeat_ldct_1_3mo"
      "lung_treated" | "lung_untreated" | "exit"
    """
    rads = p.lung_result

    # ── RADS 0, 1, 2, 3 — communicate then schedule repeat ───────────────────
    if rads in _LUNG_RADS_REPEAT:
        if not _lung_result_communicated(p, current_day, metrics):
            return "exit"

        repeat_days = cfg.LUNG_RADS_REPEAT_INTERVALS.get(rads, 365)
        if rads == "RADS_0":
            p.log(current_day, f"LUNG {rads}: repeat LDCT in ~{repeat_days} days (1–3 months)")
            return "repeat_ldct_1_3mo"
        elif rads == "RADS_3":
            p.log(current_day, f"LUNG {rads}: repeat LDCT in {repeat_days} days (6 months)")
            return "repeat_ldct_6mo"
        else:  # RADS 1, 2
            p.log(current_day, f"LUNG {rads}: repeat LDCT in {repeat_days} days (12 months)")
            return "repeat_ldct_12mo"

    # ── RADS 4A / 4B / 4X — communicate then biopsy pathway ──────────────────
    if rads in _LUNG_RADS_BIOPSY:
        if not _lung_result_communicated(p, current_day, metrics):
            return "exit"

        # Biopsy referral made?
        if random.random() > cfg.LUNG_PATHWAY_PROBS["biopsy_referral_made"]:
            p.log(current_day, f"LUNG {rads}: no biopsy referral — unmet referral (LTFU)")
            p.exit_system(current_day, "lost_to_followup")
            if metrics is not None:
                metrics["ltfu_post_abnormal"] += 1
            return "exit"
        if metrics is not None:
            metrics["lung_biopsy_referral"] += 1
        p.log(current_day, f"LUNG {rads}: biopsy referral placed")

        # Biopsy scheduled?
        if random.random() > cfg.LUNG_PATHWAY_PROBS["biopsy_scheduled"]:
            p.log(current_day, "LUNG biopsy: not scheduled — unmet referral (LTFU)")
            p.exit_system(current_day, "lost_to_followup")
            if metrics is not None:
                metrics["ltfu_post_abnormal"] += 1
            return "exit"
        if metrics is not None:
            metrics["lung_biopsy_scheduled"] += 1
        p.log(current_day, "LUNG biopsy: scheduled")

        # Biopsy completed?
        if random.random() > cfg.LUNG_PATHWAY_PROBS["biopsy_completed"]:
            p.log(current_day, "LUNG biopsy: not completed — LTFU")
            p.exit_system(current_day, "lost_to_followup")
            if metrics is not None:
                metrics["ltfu_post_colposcopy"] += 1
            return "exit"
        if metrics is not None:
            metrics["lung_biopsy_completed"] += 1
        p.log(current_day, "LUNG biopsy: completed")

        # Malignancy confirmed?
        if random.random() > cfg.LUNG_PATHWAY_PROBS["malignancy_confirmed"]:
            p.lung_biopsy_result = "benign"
            p.log(current_day, "LUNG biopsy: benign — return to surveillance")
            return "repeat_ldct_12mo"

        p.lung_biopsy_result = "malignant"
        if metrics is not None:
            metrics["lung_malignancy_confirmed"] += 1
        p.log(current_day, "LUNG biopsy: malignancy confirmed")

        # Treatment given?
        if random.random() > cfg.LUNG_PATHWAY_PROBS["treatment_given"]:
            p.exit_system(current_day, "untreated")
            p.log(current_day, "LUNG: malignancy confirmed but no treatment given")
            if metrics is not None:
                metrics["n_untreated"] += 1
            return "lung_untreated"

        p.current_stage = "treated"
        if metrics is not None:
            metrics["lung_treatment_given"] += 1
            metrics["n_treated"] += 1
        p.log(current_day, "LUNG: treatment given (surgery / radiation / med onc)")
        return "lung_treated"

    # Fallback (should not reach here)
    p.log(current_day, f"LUNG: unknown RADS category '{rads}' — surveillance")
    return "repeat_ldct_12mo"


