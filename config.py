# =============================================================================
# config.py
# NYP Women's Health Screening Simulation — Central Configuration
# =============================================================================
# All clinical parameters, probabilities, capacities, and workflow settings
# live here. Replace PLACEHOLDER values with NYP data as it becomes available.
# =============================================================================

RANDOM_SEED = 42

# ── Simulation Horizon ────────────────────────────────────────────────────────
SIM_YEARS      = 30
DAYS_PER_YEAR  = 365
SIM_DAYS       = SIM_YEARS * DAYS_PER_YEAR
NUM_REPS       = 10       # number of replications for variance analysis

# ── Workflow Mode ─────────────────────────────────────────────────────────────
# "fragmented"  = current state (separate appointments per specialty)
# "coordinated" = future state (bundled multi-screening program)
WORKFLOW_MODE = "fragmented"

# ── Active Cancer Pathways ────────────────────────────────────────────────────
# Controls which cancers are live in the simulation.
# Lung / colorectal / osteoporosis code is preserved but inactive until
# clinical pathways and data are ready.
ACTIVE_CANCERS = ["cervical", "breast"]

# ── Arrivals (mirrors Sophia's parameters) ────────────────────────────────────
DAILY_PATIENTS = 200

PATIENT_TYPE_PROBS = {"outpatient": 0.70, "drop_in": 0.30}

DESTINATION_PROBS = {
    "pcp":         0.35,
    "gynecologist": 0.25,
    "specialist":  0.20,
    "er":          0.20,
}

ER_CRITICAL_PROB     = 0.30
OUTPATIENT_SHOW_PROB = 1.00   # raise to model no-shows

# ── Provider Daily Capacities ─────────────────────────────────────────────────
PROVIDER_CAPACITY = {
    "pcp":         40,
    "gynecologist": 30,
    "specialist":  20,
    "er":          25,
}

# ── Age Strata ────────────────────────────────────────────────────────────────
AGE_STRATA = {
    "young":  (21, 29),
    "middle": (30, 65),
    "older":  (66, 80),
}

# ── Screening Eligibility Rules ───────────────────────────────────────────────
ELIGIBILITY = {
    "cervical":     {"age_min": 21, "age_max": 65, "requires_cervix": True},
    "lung":         {"age_min": 50, "age_max": 80, "min_pack_years": 20},
    "breast":       {"age_min": 40, "age_max": 80},
    "colorectal":   {"age_min": 45, "age_max": 80},
    "osteoporosis": {"age_min": 65, "age_max": 80, "alt_bmi_threshold": 19.0},
}

# ── Screening Test Modalities ─────────────────────────────────────────────────
# Cervical: age-stratified per ASCCP guidelines
# Others:   single default modality (stubs — expand as pathways are built)
SCREENING_TESTS = {
    "cervical": {
        # USPSTF Grade A recommendations:
        # Base case excludes co-testing — cytology and HPV-alone only.
        "young":  ["cytology"],              # age 21–29: cytology only (every 3 yrs)
        "middle": ["cytology", "hpv_alone"], # age 30–65: cytology (3 yrs) or HPV-alone (5 yrs)
        "older":  [],                         # age 65+ with adequate prior screening: do not screen
    },
    "lung":         ["ldct"],
    "breast":       ["mammogram"],
    "colorectal":   ["colonoscopy", "fit"],
    "osteoporosis": ["dexa"],
}

# ── Screening Intervals (days) ────────────────────────────────────────────────
SCREENING_INTERVALS_DAYS = {
    "cytology":    365 * 3,
    "hpv_alone":   365 * 5,
    "co_test":     365 * 5,
    "ldct":        365 * 1,
    "mammogram":   365 * 1,
    "colonoscopy": 365 * 10,
    "fit":         365 * 1,
    "dexa":        365 * 2,
}

# ── Cervical Result Probabilities ─────────────────────────────────────────────
# Multinomial over: NORMAL | ASCUS | LSIL | ASC-H | HSIL | HPV_POS_NORMAL_CYTO
# PLACEHOLDER — replace with NYP EHR rates / ASCCP risk table values
CERVICAL_RESULT_PROBS = {
    "young": {                      # age 21–29, cytology only
        "NORMAL":              0.880,
        "ASCUS":               0.040,
        "LSIL":                0.045,
        "ASC-H":               0.015,
        "HSIL":                0.010,
        "HPV_POS_NORMAL_CYTO": 0.010,
    },
    "middle_cytology": {            # age 30–65, cytology component of co-test
        "NORMAL":              0.900,
        "ASCUS":               0.035,
        "LSIL":                0.030,
        "ASC-H":               0.015,
        "HSIL":                0.010,
        "HPV_POS_NORMAL_CYTO": 0.010,
    },
    "middle_hpv": {                 # age 30–65, HPV-alone test
        "NORMAL":              0.880,
        "ASCUS":               0.000,   # N/A for HPV-alone
        "LSIL":                0.000,
        "ASC-H":               0.000,
        "HSIL":                0.000,
        "HPV_POS_NORMAL_CYTO": 0.120,   # HPV+ result
    },
}

# ── Lung-RADS Result Distribution (v2022) ────────────────────────────────────
# Multinomial over LDCT result categories.
# PLACEHOLDER — calibrate to NYP LDCT volume data.
# Approximate values from published NLST / ACR registry data.
LUNG_RADS_PROBS = {
    "RADS_0":    0.01,    # Incomplete — part of lung cannot be evaluated
    "RADS_1":    0.27,    # Negative / no nodules
    "RADS_2":    0.49,    # Benign appearance / behaviour
    "RADS_3":    0.11,    # Probably benign
    "RADS_4A":   0.08,    # Suspicious
    "RADS_4B_4X": 0.04,   # Very suspicious
}

# ── Lung Pathway Step Probabilities ──────────────────────────────────────────
# Each value is the probability of successfully clearing that step.
# 1 - value = LTFU / unmet referral at that node.
# PLACEHOLDER — replace with NYP EHR-derived rates.
LUNG_PATHWAY_PROBS = {
    "referral_placed":          0.72,   # eligible patient gets LDCT order placed
    "scheduled_after_referral": 0.80,   # referred patient actually schedules LDCT
    "result_communicated":      0.90,   # results are communicated to patient (all RADS)
    "biopsy_referral_made":     0.80,   # RADS 4 patient gets biopsy referral
    "biopsy_scheduled":         0.78,   # biopsy referral → appointment scheduled
    "biopsy_completed":         0.88,   # scheduled biopsy is completed
    "malignancy_confirmed":     0.25,   # biopsy → confirmed malignancy (RADS 4)
    "treatment_given":          0.92,   # confirmed malignancy → treatment received
}

# ── Lung-RADS Repeat Intervals (days) ────────────────────────────────────────
LUNG_RADS_REPEAT_INTERVALS = {
    "RADS_0":   60,    # 1–3 months (inflammatory/infectious — use 2-month midpoint)
    "RADS_1":   365,   # 12 months (routine annual)
    "RADS_2":   365,   # 12 months (routine annual)
    "RADS_3":   180,   # 6 months
    # RADS 4A/4B/4X → biopsy pathway, not repeat LDCT
}

# ── Other Cancer Positive Screen Rates (stubs) ────────────────────────────────
# PLACEHOLDER — replace with literature / NYP rates
POSITIVE_RATES = {
    "mammogram":   0.10,
    "colonoscopy": 0.05,
    "fit":         0.07,
    "dexa":        0.15,
}

# ── Loss-to-Follow-Up Probabilities ──────────────────────────────────────────
# PLACEHOLDER — replace with NYP EHR-derived attrition rates
LTFU_PROBS = {
    # Cervical
    "post_abnormal_to_colposcopy":   0.20,
    "post_colposcopy_to_treatment":  0.10,
    # General
    "unscreened_will_reschedule":    0.50,
    # Lung (derived as 1 - LUNG_PATHWAY_PROBS values; kept here for convenience)
    "lung_no_referral":              0.28,   # 1 - 0.72
    "lung_no_scheduling":            0.20,   # 1 - 0.80
    "lung_no_result_communication":  0.10,   # 1 - 0.90
    "lung_no_biopsy_referral":       0.20,   # 1 - 0.80
    "lung_no_biopsy_scheduling":     0.22,   # 1 - 0.78
    "lung_no_biopsy_completion":     0.12,   # 1 - 0.88
    "lung_no_treatment":             0.08,   # 1 - 0.92
}

# ── Colposcopy Result Probabilities ───────────────────────────────────────────
# Conditional on triggering result; keyed as "from_{result}"
# PLACEHOLDER — to be powered by ASCCP risk tables (clinician to share slides)
COLPOSCOPY_RESULT_PROBS = {
    "from_ASCUS":               {"NORMAL": 0.60, "CIN1": 0.25, "CIN2": 0.10, "CIN3": 0.05},
    "from_LSIL":                {"NORMAL": 0.40, "CIN1": 0.35, "CIN2": 0.15, "CIN3": 0.10},
    "from_ASC-H":               {"NORMAL": 0.25, "CIN1": 0.20, "CIN2": 0.30, "CIN3": 0.25},
    "from_HSIL":                {"NORMAL": 0.10, "CIN1": 0.10, "CIN2": 0.30, "CIN3": 0.50},
    "from_HPV_POS_NORMAL_CYTO": {"NORMAL": 0.50, "CIN1": 0.30, "CIN2": 0.15, "CIN3": 0.05},
}

# ── Treatment Assignment by CIN Grade ─────────────────────────────────────────
# Default protocol; real care varies (especially for younger patients)
TREATMENT_ASSIGNMENT = {
    "NORMAL": "surveillance",
    "CIN1":   "surveillance",
    "CIN2":   "leep",
    "CIN3":   "leep",          # or cold_knife_cone; simplified here
}

# ── Post-Treatment / Post-Negative Re-entry Delays (days) ─────────────────────
POST_TREATMENT_DELAY_DAYS = {
    "cervical":     180,
    "lung":         365,
    "breast":       365,
    "colorectal":   365,
    "osteoporosis": 730,
}

# ── Screening / Procedure Resource Capacities ─────────────────────────────────
# (Daily slots — expand as real capacity data become available)
CAPACITIES = {
    "cytology":    8,
    "hpv_alone":   8,
    "co_test":     8,
    "ldct":        4,
    "mammogram":  10,
    "colonoscopy": 6,
    "fit":        20,
    "dexa":        5,
    "colposcopy":  8,
    "leep":        5,
    "cone_biopsy": 3,
}

# ── Unscreened Re-entry Delay (days) ──────────────────────────────────────────
RESCHEDULE_DELAY_DAYS = 90   # how far out a willing patient is re-scheduled
