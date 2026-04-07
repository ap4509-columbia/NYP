# =============================================================================
# config.py
# NYP Women's Health Screening Simulation — Central Configuration
# =============================================================================
#
# DESIGN PHILOSOPHY
# ─────────────────────────────────────────────────────────────────────────────
# This file is the single source of truth for every numeric parameter in the
# simulation. No probability, capacity, or interval value is hard-coded
# anywhere else in the codebase — they all come from here.
#
# This means that to run a scenario analysis (e.g. "what if we doubled
# colposcopy capacity?" or "what if LTFU post-abnormal dropped from 20% to 10%?"),
# you change exactly one number in this file and re-run.
#
# SECTIONS
# ─────────────────────────────────────────────────────────────────────────────
#   1. Simulation horizon & replication settings
#   2. Workflow mode toggle (fragmented vs. coordinated care)
#   3. Arrivals (mirrors Sophia's parameters)
#   4. Provider capacities and scheduling lead times
#   5. Eligibility criteria (USPSTF guidelines)
#   6. Test modalities and screening intervals
#   7. Result probability tables (cervical cytology, HPV-alone, Lung-RADS)
#   8. Lung pathway step probabilities (referral, scheduling, biopsy, treatment)
#   9. Loss-to-follow-up probabilities
#  10. Colposcopy result probabilities (ASCCP risk tables)
#  11. Treatment assignment by CIN grade
#  12. Resource capacities (SimPy)
#  13. Procedure revenue (CPT-based placeholders)
#
# All values marked PLACEHOLDER must be replaced with NYP EHR-derived rates
# before the model is used for operational or planning decisions.
# =============================================================================

RANDOM_SEED = 42

# ── Simulation Horizon ────────────────────────────────────────────────────────
SIM_YEARS      = 70                      # full 70-year longitudinal horizon
DAYS_PER_YEAR  = 365
SIM_DAYS       = SIM_YEARS * DAYS_PER_YEAR   # = 25,550 days
NUM_REPS       = 10       # number of replications for variance analysis

# ── Workflow Mode ─────────────────────────────────────────────────────────────
# "fragmented"  = current state (separate appointments per specialty)
# "coordinated" = future state (bundled multi-screening program)
WORKFLOW_MODE = "fragmented"

# ── Active Cancer Pathways ────────────────────────────────────────────────────
ACTIVE_CANCERS = ["cervical", "lung"]

# ── Arrivals (mirrors Sophia's parameters) ────────────────────────────────────
# Total daily screening capacity = 200 patients across all providers.
# PLACEHOLDER — replace with NYP scheduling / capacity data.
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
# Scaled so total = DAILY_PATIENTS = 200. Distribution is proportional to
# DESTINATION_PROBS. PLACEHOLDER — replace with NYP capacity data.
PROVIDER_CAPACITY = {
    "pcp":          70,   # 35% of 200
    "gynecologist": 52,   # 26% of 200
    "specialist":   35,   # 17% of 200
    "er":           43,   # 22% of 200
}

# Fraction of each provider's daily capacity reserved for scheduled outpatients.
# Remainder is available to drop-ins. ER = 0 (drop-in only).
OUTPATIENT_FRACTION = {
    "pcp":          0.75,   # 30 outpatient slots, 10 drop-in slots
    "gynecologist": 0.73,   # 22 outpatient slots,  8 drop-in slots
    "specialist":   0.75,   # 15 outpatient slots,  5 drop-in slots
    "er":           0.00,   # ER is entirely drop-in
}

# Scheduling lead time for new outpatient appointments (uniform lo–hi days ahead).
# PLACEHOLDER — replace with NYP scheduling data.
OUTPATIENT_LEAD_DAYS = {
    "pcp":          (1,  7),
    "gynecologist": (7,  21),
    "specialist":   (14, 28),
}

# ER overflow routing: fraction of patients who retry ER tomorrow vs.
# convert to an outpatient appointment at PCP / Gynecologist / Specialist.
ER_OVERFLOW_RETRY_PROB = 0.70   # PLACEHOLDER

# Fraction of ER arrivals flagged as critical (return next day for follow-up).
ER_CRITICAL_PROB = 0.50         # PLACEHOLDER

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
# PLACEHOLDER — replace with NYP EHR rates / ASCCP risk table values
#
# Cytology result categories (Pap smear): NORMAL | ASCUS | LSIL | ASC-H | HSIL
#   - HPV status is NOT reported by cytology alone → no HPV result in these tables
#   - USPSTF: cytology is the only test for ages 21–29; HPV testing not recommended <30
#
# HPV-alone result categories (hrHPV test): HPV_NEGATIVE | HPV_POSITIVE
#   - No cytology categories in this table; HPV test does not return Pap results
#   - USPSTF: HPV-alone every 5 years is an option only for ages 30–65
CERVICAL_RESULT_PROBS = {
    "young": {               # age 21–29 — cytology only (USPSTF Grade A)
        "NORMAL": 0.890,
        "ASCUS":  0.040,
        "LSIL":   0.045,
        "ASC-H":  0.015,
        "HSIL":   0.010,
    },
    "middle_cytology": {     # age 30–65 — cytology every 3 years (USPSTF Grade A)
        "NORMAL": 0.910,
        "ASCUS":  0.035,
        "LSIL":   0.030,
        "ASC-H":  0.015,
        "HSIL":   0.010,
    },
    "middle_hpv": {          # age 30–65 — hrHPV-alone every 5 years (USPSTF Grade A)
        "HPV_NEGATIVE": 0.880,
        "HPV_POSITIVE": 0.120,
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

# ── HPV-Positive Triage Split (ASCCP) ────────────────────────────────────────
# When a cervical screening returns HPV_POSITIVE, the ASCCP risk table drives
# whether the patient is managed with a 1-year repeat cytology (lower-risk) or
# referred immediately to colposcopy (higher-risk).
# PLACEHOLDER — replace with NYP risk-stratified HPV management data.
HPV_POSITIVE_COLPOSCOPY_PROB = 0.60   # probability HPV+ → immediate colposcopy
                                       # (1 - this) → 1-year repeat cytology

# ── Risk Multipliers for Cervical Result Draws ────────────────────────────────
# Applied in screening.draw_cervical_result() via _adjust_probs().
# Multiplies the base-rate probability of abnormal categories for high-risk patients.
# PLACEHOLDER — calibrate against NYP cytology lab data.
RISK_MULT_HPV_POSITIVE_CYTOLOGY = 1.5   # inflate all abnormal cytology if HPV+
RISK_MULT_HPV_POSITIVE_HPV_TEST = 2.0   # inflate HPV_POSITIVE result if prior HPV+
RISK_MULT_PRIOR_CIN_HIGHGRADE   = 1.8   # inflate ASC-H / HSIL if prior CIN2/CIN3

# ── Colposcopy Result Fallback Distribution ───────────────────────────────────
# Used when the triggering cytology result does not match any key in
# COLPOSCOPY_RESULT_PROBS (e.g. unexpected result category).
# PLACEHOLDER — replace with ASCCP risk table values.
COLPOSCOPY_RESULT_PROBS_DEFAULT = {
    "NORMAL": 0.50, "CIN1": 0.25, "CIN2": 0.15, "CIN3": 0.10,
}

# ── Colposcopy Result Probabilities ───────────────────────────────────────────
# PLACEHOLDER — to be powered by ASCCP risk tables
COLPOSCOPY_RESULT_PROBS = {
    "from_ASCUS":        {"NORMAL": 0.60, "CIN1": 0.25, "CIN2": 0.10, "CIN3": 0.05},
    "from_LSIL":         {"NORMAL": 0.40, "CIN1": 0.35, "CIN2": 0.15, "CIN3": 0.10},
    "from_ASC-H":        {"NORMAL": 0.25, "CIN1": 0.20, "CIN2": 0.30, "CIN3": 0.25},
    "from_HSIL":         {"NORMAL": 0.10, "CIN1": 0.10, "CIN2": 0.30, "CIN3": 0.50},
    "from_HPV_POSITIVE": {"NORMAL": 0.50, "CIN1": 0.30, "CIN2": 0.15, "CIN3": 0.05},
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

# ── Inter-Step Scheduling Delays (days) ───────────────────────────────────────
# Time between referral and the next appointment at each step.
# These drive SimPy timeouts and are a key lever in scenario analysis
# (coordinated care reduces these delays).
# PLACEHOLDER — replace with NYP scheduling data.
FOLLOWUP_DELAY_DAYS = {
    "colposcopy":     30,    # abnormal result → colposcopy appointment
    "leep":           14,    # colposcopy → LEEP procedure
    "cone_biopsy":    21,    # colposcopy → cone biopsy procedure
    "lung_biopsy":    14,    # RADS 4 → CT-guided biopsy
    "lung_treatment": 21,    # malignancy confirmed → treatment start
}

# =============================================================================
# STABLE POPULATION MODEL
# =============================================================================
#
# HOW IT WORKS
# ─────────────────────────────────────────────────────────────────────────────
# The stable-population model maintains a fixed cohort of ~SIMULATED_POPULATION
# established patients who cycle through the provider system annually. Each
# year, some patients are removed (mortality, permanent LTFU) and replaced by
# an equal number of new entrants (drop-ins), keeping the total population
# roughly constant across the 70-year simulation horizon.
#
#   Established patients → visit once per ANNUAL_VISIT_INTERVAL days →
#       rescheduled immediately for next year → age updated each sweep
#
#   Mortality sweep → runs every MORTALITY_CHECK_DAYS → Bernoulli draw per
#       patient with age-adjusted annual probability →  dead patients exit,
#       are flushed to SQLite, and replaced by new entrants
#
#   Warmup → at day 0, SIMULATED_POPULATION established patients are spread
#       evenly across days 0…ANNUAL_VISIT_INTERVAL so providers start near
#       capacity from day 1 (no cold-start bias in year-1 metrics)
#
# SCALE FACTOR
# ─────────────────────────────────────────────────────────────────────────────
# 1 simulated patient represents POPULATION_SCALE_FACTOR real patients.
# NYC eligible women ~1.5M → 1.5M / 100 = 15,000 simulated patients.
# All metrics scale by this factor when extrapolating to real-world counts.
# =============================================================================

# ── Stable population size and scale ─────────────────────────────────────────
POPULATION_SCALE_FACTOR = 100          # 1 sim patient = 100 NYC women
SIMULATED_POPULATION    = 15_000       # established cycling patients in pool

# ── Patient flow into the system (open-loop model) ───────────────────────────
#
# There are TWO distinct flows of new patients. The simulation is NOT a closed
# system — patients continuously enter throughout all 70 years.
#
# 1. ORGANIC NEW ENTRANTS (ORGANIC_NEW_PATIENT_DAILY_RATE)
#    Women who are genuinely new to NYP: first-time visitors, recent movers,
#    women turning 21, patients switching providers. They arrive as drop-ins,
#    get screened at their first visit, then join the established cycling pool
#    for annual follow-ups (up to SIMULATED_POPULATION cap).
#    PLACEHOLDER — calibrate to NYP patient acquisition data.
#
# 2. MORTALITY REPLACEMENTS (NEW_PATIENT_DAILY_RATE)
#    Patients spawned specifically to replace mortality exits and keep the pool
#    at exactly SIMULATED_POPULATION. Separate from organic flow — they enter
#    directly as established cycling patients, not as drop-ins.
#    Rate-limited to spread replacements across days rather than spawning all
#    deaths at once on the mortality-sweep day.

ORGANIC_NEW_PATIENT_DAILY_RATE = 10   # ~10 new first-time patients/day ≈ 3,650/year
NEW_PATIENT_DAILY_RATE         = 4    # mortality-replacement rate (max per day)

# ── NYP MODEL ASSUMPTION: Age-based drop-in queue priority ───────────────────
# Hospital preference for revenue maximization: when drop-in capacity is limited
# and some walk-in patients must be deferred to the next day, women aged 40+
# receive priority and are seen before younger patients.
#
# RATIONALE: The 40+ cohort is disproportionately associated with higher-revenue
# procedures — colposcopy, LEEP, cone biopsy, and LDCT — so prioritizing them
# maximises expected procedure revenue per available drop-in slot.
#
# This applies ONLY to drop-in queue ordering. All scheduled outpatients retain
# their guaranteed slot regardless of age (capacity contract is unchanged).
AGE_PRIORITY_THRESHOLD = 40           # women aged >= this receive drop-in priority

# ── Visit scheduling ──────────────────────────────────────────────────────────
ANNUAL_VISIT_INTERVAL  = 365           # days between established patient visits
# Number of years of outpatient appointments to pre-book for each established patient.
# At warmup, each patient is scheduled for ADVANCE_SCHEDULE_YEARS annual visits.
# After each visit, the far end of the window is extended by one year, so the
# patient always has approximately ADVANCE_SCHEDULE_YEARS future appointments booked.
ADVANCE_SCHEDULE_YEARS = 5
WARMUP_DAYS            = 365           # spread initial cohort across first full year

# ── Mortality sweep cadence ───────────────────────────────────────────────────
MORTALITY_CHECK_DAYS   = 30            # run mortality Bernoulli draws every N days

# ── Annual patient attribute transition rates (PLACEHOLDER) ──────────────────
# Applied each mortality sweep (scaled to sweep interval).
# All values are annual probabilities — replace with NYP / literature values.
ANNUAL_SMOKING_CESSATION_PROB = 0.05   # prob a current smoker quits in a given year
ANNUAL_HPV_CLEARANCE_PROB     = 0.30   # prob HPV-positive patient clears in a given year

# ── Age-specific annual mortality rates for US women (PLACEHOLDER) ────────────
# Source: CDC WONDER / NCHS Life Tables (women, all causes, approximate).
# PLACEHOLDER — replace with NYC-specific mortality data if available.
# Keys: (age_lo, age_hi) inclusive; value: annual probability of death.
ANNUAL_MORTALITY_RATE = {
    (21,  29): 0.0006,
    (30,  39): 0.0009,
    (40,  49): 0.0020,
    (50,  59): 0.0045,
    (60,  69): 0.0100,
    (70,  80): 0.0230,
}

# ── Database persistence ──────────────────────────────────────────────────────
DB_PATH           = "nyp_simulation.db"  # SQLite file path (relative to working dir)
DB_FLUSH_INTERVAL = 30                   # flush exited patients to SQLite every N days

# ── Procedure Revenue (per event, USD) ────────────────────────────────────────
# PLACEHOLDER — replace with NYP finance / contract rates.
# CPT references provided for calibration.
PROCEDURE_REVENUE = {
    # Cervical screening
    "cytology":       156,    # CPT 88175 (liquid-based cytology)
    "hpv_alone":      198,    # CPT 87624 (hrHPV nucleic acid)

    # Cervical follow-up
    "colposcopy":     312,    # CPT 57454 (colposcopy w/ biopsy)
    "leep":           847,    # CPT 57461 (LEEP excision)
    "cone_biopsy":   1240,    # CPT 57520 (cold-knife cone)
    "surveillance":     0,    # watchful waiting — no billable procedure

    # Lung screening
    "ldct":           285,    # CPT 71271 (low-dose CT thorax)

    # Lung follow-up
    "lung_biopsy":   2100,    # CPT 32405 (CT-guided needle biopsy)
    "lung_treatment": 18500,  # surgery / radiation / med onc — rough composite
}
