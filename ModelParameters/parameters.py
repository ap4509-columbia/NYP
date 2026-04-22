# =============================================================================
# parameters.py
# NYP Women's Health Screening Simulation — Simulation Input Parameters
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
#   2. Arrivals (mirrors Sophia's parameters)
#   3. Provider capacities and scheduling lead times
#   4. Eligibility criteria (USPSTF guidelines)
#   5. Test modalities and screening intervals
#   6. Result probability tables (cervical cytology, HPV-alone, Lung-RADS)
#   7. Lung pathway step probabilities (referral, scheduling, biopsy, treatment)
#   8. Loss-to-follow-up probabilities
#   9. Colposcopy result probabilities (ASCCP risk tables)
#  10. Treatment assignment by CIN grade
#  11. Resource capacities (SimPy)
#  12. Procedure revenue (CPT-based placeholders)
#
# All values marked PLACEHOLDER must be replaced with NYP EHR-derived rates
# before the model is used for operational or planning decisions.
# =============================================================================

RANDOM_SEED = None   # None = non-deterministic (different result each run).
                     # Set to an integer (e.g. 42) to reproduce a specific run.

# ── Simulation Horizon ────────────────────────────────────────────────────────
# NOTE on two "warmup" concepts — they're different things, don't confuse them:
#   WARMUP_YEARS (below)      — ANALYSIS warmup. Metrics/vizzes exclude the
#                               first N years so we only measure steady state.
#   WARMUP_DAYS (later, under Visit Scheduling)
#                             — SCHEDULING warmup. The window over which the
#                               initial cohort's first visits are spread so
#                               providers don't sit idle on day 0.
# The scheduling warmup fits inside the analysis warmup.
SIM_YEARS      = 80                      # full 80-year longitudinal horizon
WARMUP_YEARS   = 10                      # ANALYSIS warmup — years 0–9 excluded from metrics
DAYS_PER_YEAR  = 365
SIM_DAYS       = SIM_YEARS * DAYS_PER_YEAR   # = 29,200 days
NUM_REPS       = 10       # number of replications for variance analysis

# Skip weekends — hospital screenings and appointments only occur Monday–Friday.
# Day 0 of the simulation is treated as Monday (day % 7: 0=Mon … 4=Fri, 5=Sat, 6=Sun).
# Source: AiP Parameters PDF — "skip_weekends: true"
SKIP_WEEKENDS  = True

# ── Active Cancer Pathways ────────────────────────────────────────────────────
ACTIVE_CANCERS = ["cervical", "lung"]

# ── Provider Throughput Cap ───────────────────────────────────────────────────
# Maximum patients seen by ALL providers per day (PCP + GYN + ER).
# This is the FIRST bottleneck: patients must see a provider before reaching
# screening procedure slots.  If more patients are scheduled than the cap,
# overflow patients are rescheduled to the next workday.
# PLACEHOLDER — replace with NYP scheduling / capacity data.
DAILY_PATIENTS = 2                        # PLACEHOLDER — sim scale (× POPULATION_SCALE_FACTOR = 200 real/day)

# Outpatient routing: PCP vs GYN (Source: AiP Parameters PDF; NYP operational data)
# Specialist = 0% (not used for primary screening routing).
# ER routing is handled separately via ARRIVAL_TYPE_PROBS (20% of arrivals).
DESTINATION_PROBS_OUTPATIENT = {
    "pcp":          0.852,   # Source: AiP Parameters PDF
    "gynecologist": 0.148,   # Source: AiP Parameters PDF
    "specialist":   0.00,    # Source: AiP Parameters PDF
}
# Arrival type split (Source: NYP Facts & Figures — 2.5M outpatient + 620K ER annually; https://www.nyp.org/about/facts-and-figures)
ARRIVAL_TYPE_PROBS = {
    "outpatient": 0.80,
    "er":         0.20,
}


# ── Screening Eligibility Rules ───────────────────────────────────────────────
ELIGIBILITY = {
    "cervical": {"age_min": 21, "age_max": 65, "requires_cervix": True},
    # USPSTF 2021: age 50-80, ≥20 pack-years, current smoker OR quit within 15 years
    "lung":     {"age_min": 50, "age_max": 80, "min_pack_years": 20, "max_years_since_quit": 15},
}


# Cervical test type selection for ages 30–65
# Co-test dominant at academic centers; primary HPV emerging post-2024 USPSTF draft
# Source: AiP Parameters PDF
TEST_TYPE_PROBS_30_65 = {
    "co_test":   0.55,   # HPV + cytology; dominant at academic centers for 30–65; Source: AiP Parameters PDF
    "cytology":  0.35,   # cytology alone; also used by some providers 30–65; Source: AiP Parameters PDF
    "hpv_alone": 0.10,   # primary HPV alone; emerging but minority practice; Source: AiP Parameters PDF
}

# ── Screening Test Modalities ─────────────────────────────────────────────────
# Cervical: age-stratified per USPSTF guidelines
SCREENING_TESTS = {
    "cervical": {
        "young":  ["cytology"],                          # age 21–29: cytology only (USPSTF)
        "middle": ["cytology", "hpv_alone", "co_test"],  # age 30–65: weighted by TEST_TYPE_PROBS_30_65
        "older":  [],                                     # age 65+ with adequate prior screening: do not screen
    },
    "lung": ["ldct"],
}

# ── Screening Intervals (days) ────────────────────────────────────────────────
SCREENING_INTERVALS_DAYS = {
    "cytology":  365 * 3,
    "hpv_alone": 365 * 5,
    "co_test":   365 * 3,   # co-test interval same as cytology per USPSTF; ASSUMPTION
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

# ── Lab/Result Turnaround Times ──────────────────────────────────────────────
# Lab/result turnaround times (calendar days)
# Source: AiP Parameters PDF
TURNAROUND_DAYS = {
    # Lab / radiology processing times (procedure → result available)
    "cytology":               7,    # Pap cytology alone; Source: AiP Parameters PDF
    "co_test":               10,    # HPV co-testing; Source: AiP Parameters PDF
    "hpv_alone":              5,    # Primary HPV alone; Source: AiP Parameters PDF
    "ldct_notification":     10,    # PLACEHOLDER — LDCT result to patient notification

    # Secondary screening → result turnaround
    "colposcopy_result":     10,    # PLACEHOLDER — days from colposcopy to pathology result
    "lung_biopsy_result":    10,    # PLACEHOLDER — days from lung biopsy to pathology result
}

# ── Lung-RADS Result Distribution (v2022) ────────────────────────────────────
# Lung-RADS result distribution (overall, across all LDCT scans)
# Overall: negative=83%, positive=17%; Source: PMC10331628; AiP Parameters PDF
# Among positive: RADS 3=60%, 4A=27%, 4B=13%; Source: Pinsky et al. 2015 (NLST/Lung-RADS)
# RADS 0 (incomplete): 0.01 — ASSUMPTION (unchanged from prior estimate)
# RADS 1/2 (negative/benign): 0.83 total, split 0.35/0.65 of negative — ASSUMPTION
LUNG_RADS_PROBS = {
    "RADS_0":    0.010,   # incomplete scan; ASSUMPTION (prior estimate retained)
    "RADS_1":    0.291,   # negative; Source: PMC10331628 (83% negative, split ASSUMPTION)
    "RADS_2":    0.529,   # benign appearance; Source: PMC10331628 (split ASSUMPTION)
    "RADS_3":    0.102,   # probably benign; Source: PMC10331628 + Pinsky et al. 2015 (0.17 * 0.60)
    "RADS_4A":   0.046,   # suspicious; Source: PMC10331628 + Pinsky et al. 2015 (0.17 * 0.27)
    "RADS_4B_4X": 0.022,  # very suspicious; Source: PMC10331628 + Pinsky et al. 2015 (0.17 * 0.13)
}

# ── Lung-RADS Malignancy Rate (per-tier) ─────────────────────────────────────
# P(malignant | biopsy completed) stratified by Lung-RADS category.
# Used in run_lung_followup() at the biopsy-pathology bifurcation for RADS 4
# patients. RADS_3 is included for completeness even though RADS 3 patients
# in the current flow go to 6-month repeat LDCT instead of biopsy.
# Source: Pinsky et al. 2015 (NLST/Lung-RADS); McKee et al. 2015;
# ACR Lung-RADS v1.1; Hammer et al. 2020
LUNG_RADS_MALIGNANCY_RATE = {
    "RADS_3":     0.03,   # Pinsky et al. 2015; McKee et al. 2015
    "RADS_4A":    0.08,   # ACR Lung-RADS v1.1; Hammer et al. 2020
    "RADS_4B_4X": 0.35,   # Pinsky et al. 2015; ACR Lung-RADS v1.1
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
LTFU_PROBS = {
    # ── Queue LTFU (daily hazard) ────────────────────────────────────────────
    # Each day a patient overflows procedure capacity and retries, there is a
    # probability they abandon the queue (geometric/exponential model).
    # Median days in queue before abandoning ≈ ln(2) / daily_prob.
    #   all queues: 0.10/day → median ~6.9 days
    "queue_primary_daily":   0.10,     # primary screening retry queue
    "queue_secondary_daily": 0.10,     # colposcopy / lung biopsy retry queue
    "queue_treatment_daily": 0.10,     # LEEP / cone biopsy retry queue

    # ── Reschedule probability ──────────────────────────────────────────────
    # P(patient cannot make it today and reschedules to the next day).
    # Checked BEFORE attempting to consume a procedure slot. If the patient
    # does NOT reschedule (90%), they proceed to their appointment normally.
    # This is NOT an LTFU mechanism — rescheduled patients simply shift by
    # one day. LTFU comes only from the geometric waiting-time hazard.
    "reschedule_primary":    0.10,     # PLACEHOLDER — P(patient reschedules | primary screening)
    "reschedule_secondary":  0.10,     # PLACEHOLDER — P(patient reschedules | secondary screening)
}

# ── HPV-Positive Triage Split (ASCCP) ────────────────────────────────────────
# When a cervical screening returns HPV_POSITIVE, the ASCCP risk table drives
# whether the patient is managed with a 1-year repeat cytology (lower-risk)
# or referred immediately to colposcopy (higher-risk).
#
# HOW TO CALIBRATE THIS:
# ────────────────────────────────────────────────────────────────────────────
# This single scalar is the AGGREGATE probability that an HPV-positive
# patient is sent to colposcopy. It collapses the following ASCCP 2019
# sub-populations into one weighted sum:
#
#     HPV_POSITIVE_COLPOSCOPY_PROB
#       ≈  P(HPV 16 or 18 | HPV+)                  [→ colposcopy]
#       +  P(other high-risk HPV+ with abnormal    [→ colposcopy]
#            reflex cytology | HPV+)
#       +  P(persistent HPV+ ≥12 months | HPV+)    [→ colposcopy]
#
# The remainder (1 − prob) — non-16/18 HPV+ with normal reflex cytology —
# goes to 1-year repeat cytology (watch-and-wait). We do NOT model
# HPV genotyping or reflex cytology as separate steps; this one number
# absorbs all three sub-paths.
#
# To replace the placeholder, pull NYP cervical EHR data filtered on
# HPV+ results and compute: N(colposcopy within 90 days) / N(total HPV+).
# ASCCP 2019 risk tables (Perkins et al., J Low Genit Tract Dis 2020)
# give literature anchors in the ~0.55–0.65 range for mixed HPV+ pools.
#
# PLACEHOLDER — replace with NYP risk-stratified HPV management data.
HPV_POSITIVE_COLPOSCOPY_PROB = 0.60   # aggregate P(HPV+ → immediate colposcopy)
                                       # (1 - this) → 1-year repeat cytology

# ── Risk Multipliers for Cervical Result Draws ────────────────────────────────
# Applied in screening.draw_cervical_result() via _adjust_probs().
# Multiplies the base-rate probability of abnormal categories for high-risk patients.
# PLACEHOLDER — calibrate against NYP cytology lab data.
RISK_MULT_HPV_POSITIVE_CYTOLOGY = 1.5   # inflate all abnormal cytology if HPV+
RISK_MULT_HPV_POSITIVE_HPV_TEST = 2.0   # inflate HPV_POSITIVE result if prior HPV+
RISK_MULT_PRIOR_CIN_HIGHGRADE   = 1.8   # inflate ASC-H / HSIL if prior CIN2/CIN3

# ── Colposcopy Result Probabilities (per triggering cytology result) ────────
# Given the abnormal Pap that triggered the colposcopy referral, what CIN
# grade does the biopsy return? Higher-severity triggers map to higher CIN
# grades (e.g. HSIL → mostly CIN2/3; ASCUS → mostly NORMAL/CIN1).
# PLACEHOLDER — replace with NYP pathology data or ASCCP risk tables.
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

# CIN1 surveillance parameters
# Source: ASCCP 2019 (High confidence); Castle et al. 2009; Ostor 1993; ALTS trial; Cox et al. 2003
CIN1_SURVEILLANCE_INTERVAL_DAYS  = 365    # 12-month follow-up interval; Source: ASCCP 2019
CIN1_MAX_CLEAN_VISITS_BEFORE_ROUTINE = 2  # consecutive clean visits before returning to routine; Source: ASCCP 2019
CIN1_RESOLUTION_PROB_PER_VISIT    = 0.40  # Source: Castle et al. 2009; Ostor 1993
CIN1_ESCALATION_PROB_PER_VISIT    = 0.07  # escalation to CIN2/3; Source: ALTS trial; Cox et al. 2003
CIN1_PERSISTENCE_PROB_PER_VISIT   = 0.53  # calculated: 1 - 0.40 - 0.07; Source: AiP Parameters PDF

# ── Screening / Procedure Resource Capacities (daily slots) ──────────────────
# Primary screenings compete for slots — PCP/GYN/Specialist patients get
# priority over ER walk-ins.  When capacity is exceeded, patients are
# rescheduled to the next workday (overflow).
# Secondary screenings and treatment follow FIFO — only patients with
# abnormal primary results enter these queues.
CAPACITIES = {
    # Primary screening slots
    "cytology":    4,     # PLACEHOLDER — replace with NYP lab throughput
    "hpv_alone":   4,     # PLACEHOLDER — replace with NYP lab throughput
    "co_test":     4,     # PLACEHOLDER — HPV + cytology combo; replace with NYP lab throughput
    "ldct":        4,     # PLACEHOLDER — replace with NYP radiology capacity
    # Secondary / diagnostic slots
    "colposcopy":  4,     # PLACEHOLDER — replace with NYP GYN procedure data
    "lung_biopsy": 4,     # PLACEHOLDER — replace with NYP IR capacity
    # Treatment slots
    "leep":        4,     # PLACEHOLDER — replace with NYP OR scheduling data
    "cone_biopsy": 4,     # PLACEHOLDER — replace with NYP OR scheduling data
}

# ── Inter-Step Scheduling Delays (days) ───────────────────────────────────────
# Time between referral and the next appointment at each step.
# These drive wait-time metrics and are a key lever in scenario analysis
# (coordinated care reduces these delays).
# These are scheduling delays, NOT queue wait times. Queue wait = additional
# days beyond this window caused by slot overflow and retry.
FOLLOWUP_DELAY_DAYS = {
    "colposcopy":        50,    # Source: AiP Parameters PDF
    "colposcopy_hsil":   32,    # HSIL-expedited colposcopy; Source: AiP Parameters PDF
    "leep":              14,    # colposcopy → LEEP; PLACEHOLDER
    "cone_biopsy":       21,    # colposcopy → cone biopsy; PLACEHOLDER
    "lung_biopsy":       21,    # RADS 4 → diagnostic workup; Source: AiP Parameters PDF
    "lung_treatment":    21,    # malignancy confirmed → treatment start; PLACEHOLDER
}

# =============================================================================
# POPULATION MODEL
# =============================================================================
#
# HOW IT WORKS
# ─────────────────────────────────────────────────────────────────────────────
# The population model seeds an initial cohort of INITIAL_POOL_SIZE patients
# who cycle through the provider system annually. The pool then evolves
# organically: new patients arrive via ARRIVAL_SOURCES (multi-source),
# and patients exit via mortality, attrition (EXIT_SOURCES), LTFU, or
# ineligibility. The pool size is EMERGENT — NOT maintained at a fixed target.
#
#   Established patients → visit once per ANNUAL_VISIT_INTERVAL days →
#       rescheduled immediately for next year → age updated at each visit
#
#   Mortality → at entry, a death day is drawn from the Gompertz hazard
#       conditional on age → event fires independently → patient exits
#
#   Arrivals → organic new patients arrive daily, join the cycling pool
#       after their first visit
#
#   Warmup → at day 0, INITIAL_POOL_SIZE patients are spread across the
#       warmup window so providers start near capacity from day 1
#
# SCALE FACTOR
# ─────────────────────────────────────────────────────────────────────────────
# 1 simulated patient represents POPULATION_SCALE_FACTOR real patients.
# All metrics scale by this factor when extrapolating to real-world counts.
# =============================================================================

# ── Population scale ─────────────────────────────────────────────────────────
POPULATION_SCALE_FACTOR = 100          # 1 sim patient = 100 NYC women

# ── Initial seed population ──────────────────────────────────────────────────
# Number of NYP established patients to seed at day 0.  This represents
# NYP's existing patient panel.
# The pool then grows or shrinks organically as arrivals join and patients
# exit via mortality, attrition, LTFU, or ineligibility.
#
# At POPULATION_SCALE_FACTOR=100:
#   1,500 sim patients = 150,000 real women ≈ 10% of 1.5M eligible
#
# PLACEHOLDER — replace with NYP's actual established patient panel size.
# Set to 0 for a pure cold-start.
INITIAL_POOL_SIZE = 1_500

# ── Patient arrival sources ──────────────────────────────────────────────────
# Each source is a Poisson process that generates eligible women seeking care.
# All arrivals enter the intake queue (FIFO).  Outpatient vs ER routing is
# determined AFTER arrival by ARRIVAL_TYPE_PROBS — NOT per-source.
#
# Fields:
#   daily_rate  — mean Poisson arrivals/day (sim scale) for this source
#   age_range   — (min, max) age constraint; sampled within this range
#
# The total arrival rate is the sum across all sources.  Pool size emerges
# from total arrivals vs. total exits (mortality + attrition + LTFU + aging out).
#
# Sources:
#   aging_in   — eligible women (21+) from the NYC population seeking care
#                at NYP for the first time.  Covers the full eligible age
#                range — not just women turning 21.
#                PLACEHOLDER — calibrate to NYP panel acquisition data
#   new_mover  — women relocating to NYC or switching into NYP network
#                Source: NYC net domestic migration + international inflow
#                PLACEHOLDER — calibrate to NYP new-patient registration data
#   referral   — sent by external provider specifically for screening
#                PLACEHOLDER — calibrate to NYP referral data
#
# NOTE: ER walk-ins are NOT a separate Poisson source.  Instead, each
# arriving patient is independently routed to outpatient (80%) or ER (20%)
# via ARRIVAL_TYPE_PROBS.  This ensures the ER fraction is a proportion of
# total arrivals, not an additive arrival stream.
#
TOTAL_DAILY_ARRIVALS = 3.2                # PLACEHOLDER — λ_total mean Poisson arrivals/day (sim scale)

# Sub-source shares (must sum to 1.0)
_SRC_AGING_IN  = 0.40                     # PLACEHOLDER — aging_in  share of total arrivals
_SRC_NEW_MOVER = 0.35                     # PLACEHOLDER — new_mover share of total arrivals
_SRC_REFERRAL  = 0.25                     # PLACEHOLDER — referral  share of total arrivals

ARRIVAL_SOURCES = {
    "aging_in": {
        "daily_rate": TOTAL_DAILY_ARRIVALS * _SRC_AGING_IN,   # Poisson(0.64)
        "age_range":  (21, 80),     # full eligible age range
    },
    "new_mover": {
        "daily_rate": TOTAL_DAILY_ARRIVALS * _SRC_NEW_MOVER,  # Poisson(0.56)
        "age_range":  (21, 80),     # full eligible age range
    },
    "referral": {
        "daily_rate": TOTAL_DAILY_ARRIVALS * _SRC_REFERRAL,   # Poisson(0.40)
        "age_range":  (30, 75),     # referred patients skew middle-aged
    },
}

# ── Visit scheduling ──────────────────────────────────────────────────────────
ANNUAL_VISIT_INTERVAL  = 365           # days between established patient visits
# Number of years of outpatient appointments to pre-book for each established patient.
# At warmup, each patient is scheduled for ADVANCE_SCHEDULE_YEARS annual visits.
# After each visit, the far end of the window is extended by one year, so the
# patient always has approximately ADVANCE_SCHEDULE_YEARS future appointments booked.
ADVANCE_SCHEDULE_YEARS = 5
# SCHEDULING warmup (distinct from the 10-year ANALYSIS warmup WARMUP_YEARS).
# Window over which the initial cohort of INITIAL_POOL_SIZE established
# patients has their FIRST visits spread, so providers don't sit idle on
# day 0 and don't get a visit spike at year 1.
#
# Why 5 years specifically:
#   Cervical screening intervals are 3 years (cytology) and 5 years (HPV-alone).
#   Spreading first visits over 5 years desynchronizes both screening cycles
#   at their natural frequency — no Year-4 or Year-6 re-screening spike.
#   After 5 years every patient has had at least one cycle, so the system is
#   in steady state by the time analysis begins at year 10.
WARMUP_DAYS            = 1825          # 5-year scheduling warmup

# ── Gompertz mortality parameters ─────────────────────────────────────────────
# Mortality is modeled as a Gompertz hazard: h(t) = a * exp(b * t), where t is
# age in years. At patient entry, a time-to-death is drawn from the conditional
# Gompertz survival function and scheduled as an independent event.
#
# Parameters fitted to US female life tables (Missov et al. 2015):
#   a (GOMPERTZ_A) — baseline hazard at age 0 (very small)
#   b (GOMPERTZ_B) — exponential growth rate of mortality with age
#
# The hazard doubles roughly every 8.5 years (= ln(2) / b ≈ 8.46).
GOMPERTZ_A = 0.0000592      # baseline hazard (per year)
GOMPERTZ_B = 0.0819          # exponential aging coefficient

# Mortality multiplier for current smokers (all-cause, both sexes).
# Source: Jha et al. 2013 (NEJM); CDC "Health Effects of Cigarette Smoking"
# Smokers have ~2.5x all-cause mortality vs never-smokers; former smokers ~1.4x.
# Applied multiplicatively to the Gompertz baseline `a` at draw time.
SMOKER_MORTALITY_MULTIPLIER        = 2.5    # current smokers
FORMER_SMOKER_MORTALITY_MULTIPLIER = 1.4    # quit but pack_years > 0

# Mortality multiplier for obese patients (BMI ≥ BMI_OBESE_THRESHOLD).
# Applied multiplicatively to the Gompertz baseline `a` at draw time,
# stacking with any smoker multiplier (standard proportional-hazards composition).
# PLACEHOLDER — value is a plausibility estimate, not from a verified source.
# Replace with a verified literature rate (e.g. from a vetted meta-analysis of
# BMI and all-cause mortality) or NYP / NYC-specific rates before use.
OBESE_MORTALITY_MULTIPLIER = 1.30   # current obese (BMI ≥ 30)  — PLACEHOLDER
BMI_OBESE_THRESHOLD        = 30.0   # CDC / WHO Class I obesity cutoff

# Hard cap — no patient survives past this age regardless of draw
MORTALITY_AGE_CAP = 100

# ── Annual life-event transition rates ────────────────────────────────────────
# At patient entry, each rate is converted to an exponentially-distributed
# event day and scheduled independently.  The event fires whether or not
# the patient has any upcoming visits.
ANNUAL_SMOKING_CESSATION_PROB = 0.05   # prob a current smoker quits in a given year
ANNUAL_HPV_CLEARANCE_PROB     = 0.30   # prob HPV-positive patient clears in a given year

# ── Exit sources (non-clinical churn) ─────────────────────────────────────────
# Mirrors ARRIVAL_SOURCES: each exit source has its own annual rate.
# At entry, a single attrition day is drawn from Exponential(sum of rates)
# (competing-risks model).  When the event fires, the sub-type is assigned
# proportional to the individual rates.
#
# Clinical exits (LTFU, treated, ineligible) are pathway outcomes
# and are NOT modelled as independent rates — they stay in the care pathway.
#
#   relocation      — patient moves out of NYC catchment area
#                     Source: ACS geographic mobility tables (~3% annual
#                     inter-county migration for NYC women); PLACEHOLDER
#   insurance_loss  — patient loses or switches insurance away from NYP
#                     Source: KFF uninsured rate churn estimates; PLACEHOLDER
#   provider_switch — patient voluntarily switches to a non-NYP provider
#                     Source: industry patient panel churn benchmarks; PLACEHOLDER
#
# Combined rate = sum of sub-rates ≈ 0.05
EXIT_SOURCES = {
    "relocation": {
        "annual_rate": 0.025,   # PLACEHOLDER — calibrate to NYC migration data
    },
    "insurance_loss": {
        "annual_rate": 0.015,   # PLACEHOLDER — calibrate to NYP payer-mix data
    },
    "provider_switch": {
        "annual_rate": 0.010,   # PLACEHOLDER — calibrate to NYP panel churn data
    },
}

# Derived: total annual attrition rate (sum of competing risks)
ANNUAL_ATTRITION_RATE = sum(s["annual_rate"] for s in EXIT_SOURCES.values())

# ── Database persistence ──────────────────────────────────────────────────────
DB_PATH           = "nyp_simulation.db"  # SQLite file path (relative to working dir)
DB_FLUSH_INTERVAL = 30                   # flush exited patients to SQLite every N days

# ── Post-Treatment Surveillance Schedules ────────────────────────────────────
# Cervical post-CIN2/3 treatment surveillance schedule
# Source: Katki et al. 2013, JNCI; AiP Parameters PDF
# Format: list of (max_year, interval_months) — apply in order
POST_TREATMENT_SURVEILLANCE_CERVICAL = [
    (2,  6),    # Years 1–2: every 6 months (4 visits); Source: Katki et al. 2013, JNCI
    (5,  12),   # Years 3–5: every 12 months (3 visits); Source: Katki et al. 2013
    (25, 36),   # Years 6–25: every 36 months (~7 visits); Source: Katki et al. 2013
]
POST_TREATMENT_ACTIVE_YEARS_CERVICAL = 10   # practical active duration; Source: Katki et al. 2013, JNCI

# Lung cancer post-treatment surveillance schedule
# Source: NCCN NSCLC Survivorship; ASCO (Schneider et al. 2020); AiP Parameters PDF
POST_TREATMENT_SURVEILLANCE_LUNG = [
    (2,  6),    # Years 1–2: every 6 months; Source: NCCN NSCLC Survivorship
    (5,  12),   # Years 3–5: every 12 months; Source: NCCN; ASCO Schneider et al. 2020
    (999, 12),  # Year 6+: every 12 months if fit; Source: NCCN
]
POST_TREATMENT_ACTIVE_YEARS_LUNG = 5   # standard of care; Source: AiP Parameters PDF

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


# =============================================================================
# POPULATION ATTRIBUTE DISTRIBUTIONS
# =============================================================================
# Every attribute a new patient carries (age, race, insurance, smoking, HPV,
# cervix status, BMI) is drawn from one of these distributions when
# sample_patient() is called. Originally these lived inside model.py as
# private module-level constants; they're parameters, not model logic, so
# they belong here where a scenario-runner can tune them.
# =============================================================================

# ── Age distribution ────────────────────────────────────────────────────────
# NY female, age 21+, single-year-of-age probabilities.
# Age-85 bucket represents 85+ in the Census source; sample_patient expands
# it to 85–100 via a clipped Normal(89, 4) draw.
# Source: Census SC-EST2024-AGESEX-CIV (civilian population by single year of age and sex)
AGE_VALUES = list(range(21, 86))  # 21–85; 85 represents 85+
AGE_PROBS = [
    0.015909975693027187, 0.015514818723877663, 0.016165837946892232,
    0.01689024862027892,  0.01699947013835047,  0.017212975882406,
    0.017355724578320054, 0.017534542152345977, 0.01772477427008793,
    0.01796727563118836,  0.017869788708665356, 0.017924952319437078,
    0.017984389970164453, 0.01800159302518125,  0.01741507874311268,
    0.01717177811225559,  0.016891975474882467, 0.016597230133555383,
    0.016439746732069248, 0.01589917571961944,  0.016007676438554838,
    0.015882613683578266, 0.01561910688061082,  0.015642815541554155,
    0.015150988805421506, 0.014800490746677025, 0.014795095607926827,
    0.01443569931312958,  0.01465963231536682,  0.014318527774542467,
    0.014495151642568842, 0.015190006179027459, 0.01629945044323262,
    0.016382292266177494, 0.015931279440770407, 0.015814107901489253,
    0.016029360010582517, 0.0164331408655305,   0.017043768053761793,
    0.017272201799168498, 0.017123462119641668, 0.01694025841256843,
    0.0168197626872114,   0.01658451968484241,  0.01618842115724186,
    0.015949102506540596, 0.015597560630433698, 0.014934859031132527,
    0.014587727036858528, 0.013939431938424342, 0.013283166298213077,
    0.012806339839006291, 0.012359378318188544, 0.011916549151198764,
    0.01150825888909235,  0.011151929991680755, 0.011483318856435978,
    0.008466537112776232, 0.008097424134650845, 0.007750418703502376,
    0.007755189543798091, 0.006740173239321621, 0.005973056389805381,
    0.005479500935361273, 0.03647309388037247,
]
# Normalise (source sums to ~1 but guard against float drift)
_age_total = sum(AGE_PROBS)
AGE_PROBS  = [p / _age_total for p in AGE_PROBS]
del _age_total

# ── Race / ethnicity (two-stage draw) ───────────────────────────────────────
# Source: Census SC-EST2024-SR11H (NY female by sex, race, Hispanic origin)
P_HISPANIC     = 2030943 / 10161956
P_NON_HISPANIC = 8131013 / 10161956

RACE_PROBS_NON_HISP = {
    "White":        5361976 / 8131013,
    "Black":        1504583 / 8131013,
    "AIAN":           30664 / 8131013,
    "Asian":        1023010 / 8131013,
    "NHPI":            5269 / 8131013,
    "Two or More":   205511 / 8131013,
}

RACE_PROBS_HISP = {
    "White":        1460416 / 2030943,
    "Black":         360471 / 2030943,
    "AIAN":           83440 / 2030943,
    "Asian":          23744 / 2030943,
    "NHPI":            9962 / 2030943,
    "Two or More":    92910 / 2030943,
}

# ── Insurance status by age band (female-only, age 21+) ──────────────────────
# Source: ACS B27001 (https://data.census.gov/table/ACSDT1Y2022.B27001)
INSURANCE_BY_AGE = {
    (21, 25): {"Insured": 13190608, "Uninsured": 1872271},
    (26, 34): {"Insured": 18118047, "Uninsured": 2411358},
    (35, 44): {"Insured": 20435569, "Uninsured": 2248259},
    (45, 54): {"Insured": 18622090, "Uninsured": 1805871},
    (55, 64): {"Insured": 19717410, "Uninsured": 1476065},
    (65, 74): {"Insured": 18473038, "Uninsured":  179909},
    (75, 99): {"Insured": 13985406, "Uninsured":   75089},
}
# Derived: P(insured) by age band
INSURANCE_PROBS = {
    _band: _vals["Insured"] / (_vals["Insured"] + _vals["Uninsured"])
    for _band, _vals in INSURANCE_BY_AGE.items()
}

# ── Smoking rate ─────────────────────────────────────────────────────────────
# Source: NYS BRFSS 2023
SMOKER_RATE = 0.109

# ── HPV status ───────────────────────────────────────────────────────────────
HPV_POSITIVE_RATE = 0.25    # among unvaccinated women with cervix — PLACEHOLDER

# Vaccination coverage by age cohort — PLACEHOLDER
HPV_VAX_RATE = {
    (21, 29): 0.60,
    (30, 39): 0.40,
    (40, 49): 0.20,
    (50, 99): 0.05,
}

# ── Hysterectomy prevalence (drives has_cervix) ──────────────────────────────
# Stratified by age band AND race/ethnicity group.
# Source: CDC/BRFSS 2018 (https://stacks.cdc.gov/view/cdc/113157/cdc_113157_DS1.pdf)
HYSTERECTOMY_BY_GROUP = {
    "Hispanic": {
        (21, 29): 0.004, (30, 39): 0.029, (40, 49): 0.109,
        (50, 59): 0.211, (60, 69): 0.295, (70, 99): 0.430,
    },
    "White": {
        (21, 29): 0.005, (30, 39): 0.054, (40, 49): 0.166,
        (50, 59): 0.268, (60, 69): 0.341, (70, 99): 0.456,
    },
    "Black": {
        (21, 29): 0.003, (30, 39): 0.038, (40, 49): 0.185,
        (50, 59): 0.337, (60, 69): 0.441, (70, 99): 0.521,
    },
    "Asian": {
        (21, 29): 0.004, (30, 39): 0.006, (40, 49): 0.078,
        (50, 59): 0.112, (60, 69): 0.149, (70, 99): 0.276,
    },
    "Other": {
        (21, 29): 0.004, (30, 39): 0.038, (40, 49): 0.143,
        (50, 59): 0.239, (60, 69): 0.310, (70, 99): 0.418,
    },
}

# ── BMI mixture model ────────────────────────────────────────────────────────
# Two-component Gaussian mixture: non-obese (BMI<30) and obese (BMI≥30).
# Overall obesity rate calibrated to NYC.
# Source: NYC Health obesity indicator
# (https://a816-dohbesp.nyc.gov/IndicatorPublic/data-explorer/overweight/)
BMI_OBESITY_RATE  = 0.276
BMI_NONOBES_MU    = 24.7
BMI_NONOBES_SIGMA = 3.2
BMI_OBESE_MU      = 34.8
BMI_OBESE_SIGMA   = 4.5
