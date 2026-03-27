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
ACTIVE_CANCERS = ["cervical", "lung"]

# ── Arrivals (mirrors Sophia's parameters) ────────────────────────────────────
DAILY_PATIENTS = 200

PATIENT_TYPE_PROBS = {"outpatient": 0.70, "drop_in": 0.30}

DESTINATION_PROBS = {
    "pcp":          0.35,
    "gynecologist": 0.25,
    "specialist":   0.20,
    "er":           0.20,
}

OUTPATIENT_SHOW_PROB = 1.00   # raise to model no-shows

# ── Provider Daily Capacities ─────────────────────────────────────────────────
PROVIDER_CAPACITY = {
    "pcp":          40,
    "gynecologist": 30,
    "specialist":   20,
    "er":           25,
}

# ── Age Strata ────────────────────────────────────────────────────────────────
AGE_STRATA = {
    "young":  (21, 29),
    "middle": (30, 65),
    "older":  (66, 80),
}

# ── Screening Eligibility Rules ───────────────────────────────────────────────
ELIGIBILITY = {
    "cervical": {"age_min": 21, "age_max": 65, "requires_cervix": True},
    # USPSTF 2021: age 50-80, ≥20 pack-years, current smoker OR quit within 15 years
    "lung":     {"age_min": 50, "age_max": 80, "min_pack_years": 20, "max_years_since_quit": 15},
}

# ── Screening Test Modalities ─────────────────────────────────────────────────
# Cervical: age-stratified per USPSTF guidelines (no co-testing in base case)
SCREENING_TESTS = {
    "cervical": {
        "young":  ["cytology"],              # age 21–29: cytology only (every 3 yrs)
        "middle": ["cytology", "hpv_alone"], # age 30–65: cytology (3 yrs) or HPV-alone (5 yrs)
        "older":  [],                         # age 65+ with adequate prior screening: do not screen
    },
    "lung": ["ldct"],
}

# ── Screening Intervals (days) ────────────────────────────────────────────────
SCREENING_INTERVALS_DAYS = {
    "cytology":  365 * 3,
    "hpv_alone": 365 * 5,
    "ldct":      365 * 1,
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
    "middle_cytology": {            # age 30–65, cytology
        "NORMAL":              0.900,
        "ASCUS":               0.035,
        "LSIL":                0.030,
        "ASC-H":               0.015,
        "HSIL":                0.010,
        "HPV_POS_NORMAL_CYTO": 0.010,
    },
    "middle_hpv": {                 # age 30–65, HPV-alone
        "NORMAL":              0.880,
        "ASCUS":               0.000,
        "LSIL":                0.000,
        "ASC-H":               0.000,
        "HSIL":                0.000,
        "HPV_POS_NORMAL_CYTO": 0.120,
    },
}

# ── Lung-RADS Result Distribution (v2022) ────────────────────────────────────
# PLACEHOLDER — calibrate to NYP LDCT volume data.
LUNG_RADS_PROBS = {
    "RADS_0":    0.01,    # Incomplete
    "RADS_1":    0.27,    # Negative
    "RADS_2":    0.49,    # Benign appearance
    "RADS_3":    0.11,    # Probably benign
    "RADS_4A":   0.08,    # Suspicious
    "RADS_4B_4X": 0.04,   # Very suspicious
}

# ── Lung Pathway Step Probabilities ──────────────────────────────────────────
# Probability of successfully clearing each step (1 - value = LTFU at that node).
# PLACEHOLDER — replace with NYP EHR-derived rates.
LUNG_PATHWAY_PROBS = {
    "referral_placed":          0.72,
    "scheduled_after_referral": 0.80,
    "result_communicated":      0.90,
    "biopsy_referral_made":     0.80,
    "biopsy_scheduled":         0.78,
    "biopsy_completed":         0.88,
    "malignancy_confirmed":     0.25,
    "treatment_given":          0.92,
}

# ── Lung-RADS Repeat Intervals (days) ────────────────────────────────────────
LUNG_RADS_REPEAT_INTERVALS = {
    "RADS_0": 60,    # 1–3 months
    "RADS_1": 365,   # 12 months (routine annual)
    "RADS_2": 365,   # 12 months (routine annual)
    "RADS_3": 180,   # 6 months
    # RADS 4A/4B/4X → biopsy pathway, not repeat LDCT
}

# ── Loss-to-Follow-Up Probabilities ──────────────────────────────────────────
# PLACEHOLDER — replace with NYP EHR-derived attrition rates
LTFU_PROBS = {
    # Cervical
    "post_abnormal_to_colposcopy":  0.20,
    "post_colposcopy_to_treatment": 0.10,
    # General
    "unscreened_will_reschedule":   0.50,
}

# ── Colposcopy Result Probabilities ───────────────────────────────────────────
# PLACEHOLDER — to be powered by ASCCP risk tables
COLPOSCOPY_RESULT_PROBS = {
    "from_ASCUS":               {"NORMAL": 0.60, "CIN1": 0.25, "CIN2": 0.10, "CIN3": 0.05},
    "from_LSIL":                {"NORMAL": 0.40, "CIN1": 0.35, "CIN2": 0.15, "CIN3": 0.10},
    "from_ASC-H":               {"NORMAL": 0.25, "CIN1": 0.20, "CIN2": 0.30, "CIN3": 0.25},
    "from_HSIL":                {"NORMAL": 0.10, "CIN1": 0.10, "CIN2": 0.30, "CIN3": 0.50},
    "from_HPV_POS_NORMAL_CYTO": {"NORMAL": 0.50, "CIN1": 0.30, "CIN2": 0.15, "CIN3": 0.05},
}

# ── Treatment Assignment by CIN Grade ─────────────────────────────────────────
TREATMENT_ASSIGNMENT = {
    "NORMAL": "surveillance",
    "CIN1":   "surveillance",
    "CIN2":   "leep",
    "CIN3":   "leep",
}

# ── Post-Treatment / Post-Negative Re-entry Delays (days) ─────────────────────
POST_TREATMENT_DELAY_DAYS = {
    "cervical": 180,
    "lung":     365,
}

# ── Screening / Procedure Resource Capacities ─────────────────────────────────
CAPACITIES = {
    "cytology":    8,
    "hpv_alone":   8,
    "ldct":        4,
    "colposcopy":  8,
    "leep":        5,
    "cone_biopsy": 3,
}

# ── Unscreened Re-entry Delay (days) ──────────────────────────────────────────
RESCHEDULE_DELAY_DAYS = 90
