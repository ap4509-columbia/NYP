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
#   result routing → (LTFU check) → colposcopy → CIN grade draw
#   → (LTFU check) → treatment assignment (surveillance or LEEP)
#
# Lung pathway:
#   RADS routing → result communicated → (LTFU check) → biopsy referral
#   → scheduling → completion → malignancy confirmed → treatment
#
# Each LTFU check is a Bernoulli draw against a config probability. If the
# patient fails any check, they exit the system as "lost_to_followup" and no
# further steps run. This models the real-world attrition that happens between
# referral and follow-up appointment completion.
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


# ─── Loss-to-Follow-Up Checks ─────────────────────────────────────────────────
# LTFU = patient drops out of care before completing the next clinical step.
# Each check draws one Bernoulli sample; if lost, the patient exits the system.

def check_ltfu_post_abnormal(p: Patient, metrics: Optional[dict] = None) -> bool:
    """
    Did the patient fail to follow up after receiving an abnormal screening result?
    Probability driven by config: LTFU_PROBS['post_abnormal_to_colposcopy'].
    Returns True if the patient is lost (did not go to colposcopy).
    """
    lost = random.random() < cfg.LTFU_PROBS["post_abnormal_to_colposcopy"]
    if lost and metrics is not None:
        metrics["ltfu_post_abnormal"] += 1
    return lost


def check_ltfu_post_colposcopy(p: Patient, metrics: Optional[dict] = None) -> bool:
    """
    Did the patient fail to follow up after colposcopy before completing treatment?
    Probability driven by config: LTFU_PROBS['post_colposcopy_to_treatment'].
    Returns True if the patient is lost (did not complete treatment).
    """
    lost = random.random() < cfg.LTFU_PROBS["post_colposcopy_to_treatment"]
    if lost and metrics is not None:
        metrics["ltfu_post_colposcopy"] += 1
    return lost


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

    # Abnormal cytology: all four categories go to colposcopy (with LTFU risk)
    if result in _CYTOLOGY_COLPOSCOPY_TRIGGERS:
        if check_ltfu_post_abnormal(p, metrics):
            p.exit_system(current_day, "lost_to_followup")
            p.log(current_day, f"LTFU after {result} — no colposcopy follow-up")
            return "exit"
        p.log(current_day, f"ROUTE {result} → colposcopy")
        return "colposcopy"

    # HPV positive: use ASCCP triage split
    if result == "HPV_POSITIVE":
        if check_ltfu_post_abnormal(p, metrics):
            p.exit_system(current_day, "lost_to_followup")
            p.log(current_day, "LTFU after HPV_POSITIVE — no follow-up")
            return "exit"
        # ~40% low-risk: repeat cytology in 1 year instead of immediate colposcopy
        # ~60% higher-risk: referred directly to colposcopy (PLACEHOLDER split)
        if random.random() < 0.40:
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
        key, {"NORMAL": 0.50, "CIN1": 0.25, "CIN2": 0.15, "CIN3": 0.10}
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
    p.colposcopy_result = cin
    p.log(current_day, f"COLPOSCOPY → {cin}")

    if metrics is not None:
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
    Execute the treatment step after colposcopy, applying an LTFU check first.

    If the patient drops out before treatment (LTFU):
      → patient exits the system as "untreated"

    If CIN grade maps to surveillance (NORMAL or CIN1):
      → patient enters 1-year surveillance schedule

    If CIN grade maps to an excisional procedure (CIN2 or CIN3):
      → patient is treated with LEEP; stage set to 'treated'

    Returns one of: "surveillance" | "treated" | "exit"
    """
    # Check if the patient dropped out before completing treatment
    if check_ltfu_post_colposcopy(p, metrics):
        p.exit_system(current_day, "untreated")
        p.log(current_day, "LTFU — did not complete treatment after colposcopy")
        return "exit"

    treatment        = assign_treatment_type(p.colposcopy_result or "NORMAL")
    p.treatment_type = treatment

    if treatment == "surveillance":
        # Low-grade finding — no procedure needed, watch and repeat in 1 year
        p.current_stage = "surveillance"
        p.log(current_day, f"TREATMENT — {p.colposcopy_result}: 1-year surveillance")
        if metrics is not None:
            metrics["n_treatment"]["surveillance"] += 1
        return "surveillance"

    # High-grade finding — excisional treatment (LEEP or cold-knife cone)
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
    Was the LDCT result successfully communicated to the patient?
    If not, the patient is classified as an unmet referral / LTFU.
    Returns True if communicated, False if lost.
    """
    if random.random() > cfg.LUNG_PATHWAY_PROBS["result_communicated"]:
        p.log(current_day, f"LUNG {p.lung_result}: results NOT communicated — LTFU")
        p.exit_system(current_day, "lost_to_followup")
        if metrics is not None:
            metrics["ltfu_post_abnormal"] += 1
        return False
    # Result was communicated — record the success
    if metrics is not None:
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
      → malignancy confirmation → treatment decision.
      Each step has its own LTFU probability (see LUNG_PATHWAY_PROBS in config).

    Returns final disposition string:
      "repeat_ldct_12mo"    — RADS 1/2: routine annual repeat
      "repeat_ldct_6mo"     — RADS 3: 6-month repeat
      "repeat_ldct_1_3mo"   — RADS 0: incomplete scan, 1–3 month repeat
      "lung_treated"        — biopsy confirmed malignancy, treatment given
      "lung_untreated"      — malignancy confirmed but patient did not receive treatment
      "exit"                — patient lost to follow-up at any step
    """
    rads = p.lung_result

    # ── RADS 0, 1, 2, 3 — communicate result then schedule repeat LDCT ────────
    if rads in _LUNG_RADS_REPEAT:
        if not _lung_result_communicated(p, current_day, metrics):
            return "exit"  # result never reached the patient

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
    if rads in _LUNG_RADS_BIOPSY:
        if not _lung_result_communicated(p, current_day, metrics):
            return "exit"

        # Node 1: Was a biopsy referral placed by the provider?
        if random.random() > cfg.LUNG_PATHWAY_PROBS["biopsy_referral_made"]:
            p.log(current_day, f"LUNG {rads}: no biopsy referral — unmet referral (LTFU)")
            p.exit_system(current_day, "lost_to_followup")
            if metrics is not None:
                metrics["ltfu_post_abnormal"] += 1
            return "exit"
        if metrics is not None:
            metrics["lung_biopsy_referral"] += 1
        p.log(current_day, f"LUNG {rads}: biopsy referral placed")

        # Node 2: Did the patient schedule the biopsy?
        if random.random() > cfg.LUNG_PATHWAY_PROBS["biopsy_scheduled"]:
            p.log(current_day, "LUNG biopsy: not scheduled — unmet referral (LTFU)")
            p.exit_system(current_day, "lost_to_followup")
            if metrics is not None:
                metrics["ltfu_post_abnormal"] += 1
            return "exit"
        if metrics is not None:
            metrics["lung_biopsy_scheduled"] += 1
        p.log(current_day, "LUNG biopsy: scheduled")

        # Node 3: Was the biopsy actually completed?
        if random.random() > cfg.LUNG_PATHWAY_PROBS["biopsy_completed"]:
            p.log(current_day, "LUNG biopsy: not completed — LTFU")
            p.exit_system(current_day, "lost_to_followup")
            if metrics is not None:
                # Use ltfu_post_abnormal (shared lung/cervical LTFU bucket).
                # Previously incremented ltfu_post_colposcopy — a cervical-only
                # metric — which contaminated the cervical LTFU breakdown and the
                # foregone LEEP revenue calculation.
                metrics["ltfu_post_abnormal"] += 1
            return "exit"
        if metrics is not None:
            metrics["lung_biopsy_completed"] += 1
        p.log(current_day, "LUNG biopsy: completed")

        # Node 4: Was malignancy confirmed by pathology?
        if random.random() > cfg.LUNG_PATHWAY_PROBS["malignancy_confirmed"]:
            # Biopsy came back benign — return to annual surveillance
            p.lung_biopsy_result = "benign"
            p.log(current_day, "LUNG biopsy: benign — return to surveillance")
            return "repeat_ldct_12mo"

        p.lung_biopsy_result = "malignant"
        if metrics is not None:
            metrics["lung_malignancy_confirmed"] += 1
        p.log(current_day, "LUNG biopsy: malignancy confirmed")

        # Node 5: Was treatment given (surgery / radiation / medical oncology)?
        if random.random() > cfg.LUNG_PATHWAY_PROBS["treatment_given"]:
            # Malignancy confirmed but patient did not receive treatment
            p.exit_system(current_day, "untreated")
            p.log(current_day, "LUNG: malignancy confirmed but no treatment given")
            return "lung_untreated"

        # Treatment successfully given
        p.current_stage = "treated"
        if metrics is not None:
            metrics["lung_treatment_given"] += 1
            metrics["n_treated"] += 1
        p.log(current_day, "LUNG: treatment given (surgery / radiation / med onc)")
        return "lung_treated"

    # Fallback — should never be reached if lung_result is always a valid RADS category
    p.log(current_day, f"LUNG: unknown RADS category '{rads}' — surveillance")
    return "repeat_ldct_12mo"
