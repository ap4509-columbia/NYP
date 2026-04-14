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

RANDOM_SEED = None   # None = non-deterministic (different result each run).
                     # Set to an integer (e.g. 42) to reproduce a specific run.

# ── Simulation Horizon ────────────────────────────────────────────────────────
SIM_YEARS      = 80                      # full 80-year longitudinal horizon
WARMUP_YEARS   = 10                      # years 0–9 are warmup; analysis starts at year 10
DAYS_PER_YEAR  = 365
SIM_DAYS       = SIM_YEARS * DAYS_PER_YEAR   # = 29,200 days
NUM_REPS       = 10       # number of replications for variance analysis

# Skip weekends — hospital screenings and appointments only occur Monday–Friday.
# Day 0 of the simulation is treated as Monday (day % 7: 0=Mon … 4=Fri, 5=Sat, 6=Sun).
# Source: AiP Parameters PDF — "skip_weekends: true"
SKIP_WEEKENDS  = True

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

DESTINATION_PROBS = {
    "pcp":          0.35,
    "gynecologist": 0.25,
    "specialist":   0.20,
    "er":           0.20,
}

# Outpatient routing: PCP vs GYN (Source: AiP Parameters PDF; NYP operational data)
DESTINATION_PROBS_OUTPATIENT = {
    "pcp":          0.852,   # Source: AiP Parameters PDF
    "gynecologist": 0.148,   # Source: AiP Parameters PDF
    "specialist":   0.00,    # Source: AiP Parameters PDF
    "er":           0.00,    # Source: AiP Parameters PDF
}
# Arrival type split (Source: NYP Facts & Figures — 2.5M outpatient + 620K ER annually; https://www.nyp.org/about/facts-and-figures)
ARRIVAL_TYPE_PROBS = {
    "outpatient": 0.80,
    "er":         0.20,
}

# ── Provider Daily Capacities ─────────────────────────────────────────────────
# Proportional distribution — patients are split to providers by these
# proportions.  There is no provider-level capacity constraint (no provider
# overflow).  The real bottleneck is at procedure slots (CAPACITIES above).
# PLACEHOLDER — replace with NYP capacity data.
PROVIDER_CAPACITY = {
    "pcp":          70,   # PLACEHOLDER — 35% of 200
    "gynecologist": 52,   # PLACEHOLDER — 26% of 200
    "specialist":   35,   # PLACEHOLDER — 17% of 200
    "er":           43,   # PLACEHOLDER — 22% of 200
}

# Scheduling lead time for new outpatient appointments (uniform lo–hi days ahead).
# PLACEHOLDER — replace with NYP scheduling data.
OUTPATIENT_LEAD_DAYS = {
    "pcp":          (1,  7),
    "gynecologist": (7,  21),
    "specialist":   (14, 28),
}

# Fraction of ER arrivals flagged as critical (return next day for follow-up).
ER_CRITICAL_PROB = 0.50         # PLACEHOLDER

# ── Age Strata ────────────────────────────────────────────────────────────────
AGE_STRATA = {
    "young":  (21, 29),
    "middle": (30, 65),
    "older":  (66, 99),
}

# ── Screening Eligibility Rules ───────────────────────────────────────────────
ELIGIBILITY = {
    "cervical": {"age_min": 21, "age_max": 65, "requires_cervix": True},
    # USPSTF 2021: age 50-80, ≥20 pack-years, current smoker OR quit within 15 years
    "lung":     {"age_min": 50, "age_max": 80, "min_pack_years": 20, "max_years_since_quit": 15},
}

# P(patient aged 50–80 meets USPSTF lung eligibility: ≥20 pack-years, current/quit <15yr)
# Source: CDC BRFSS NYC smoking data; Fedewa et al. 2022
# NYC smoking prevalence ~12% vs national ~14%; eligible subset ~40% of ever-smokers age 50–80
# Sensitivity range: 0.04–0.07
LUNG_ELIGIBLE_BASE_PROB = 0.055   # Source: CDC BRFSS; Fedewa et al. 2022; AiP Parameters PDF

# Screening initiation rates — hybrid per-visit probability model
# Formula: P(screen at visit k) = min(P_base + (k-1) * P_increment, P_cap)
# Source: AiP Parameters PDF, Section 2

# Base per-visit screening initiation probability by setting
# Cervical PCP: Kepka et al. Prev Med 2014; Stange et al. Ann Fam Med 2003; NYP Epic BPA adds ~10-15pp (Huy et al. 2013)
# Cervical GYN: Sawaya et al. Ann Intern Med 2019 — well-woman visit, cervical screening is integral
# Lung PCP: Fedewa et al. JAMA Intern Med 2022; Mazzone et al. Chest 2021 — SDM bottleneck
SCREENING_INITIATION_BASE = {
    "cervical_pcp": 0.55,   # Source: Kepka et al. Prev Med 2014; Stange et al. Ann Fam Med 2003; Huy et al. 2013; AiP Parameters PDF
    "cervical_gyn": 0.85,   # Source: Sawaya et al. Ann Intern Med 2019; AiP Parameters PDF
    "lung_pcp":     0.12,   # Source: Fedewa et al. JAMA Intern Med 2022; Mazzone et al. Chest 2021; AiP Parameters PDF
}
# Sensitivity ranges from AiP Parameters PDF:
#   cervical_pcp: 40–65%; cervical_gyn: 75–92%; lung_pcp: 8–18%

# Escalation per additional eligible visit where screening was not performed
# ASSUMPTION — no published per-visit escalation rate; chosen to reach ~calibration target over 1.5 visits (cervical)
SCREENING_INITIATION_INCREMENT = {
    "cervical": 0.05,   # ASSUMPTION
    "lung":     0.03,   # ASSUMPTION
}

# Maximum per-visit initiation probability (cap)
# ASSUMPTION — prevents unrealistic 100% eventual uptake
SCREENING_INITIATION_CAP = {
    "cervical": 0.95,   # ASSUMPTION
    "lung":     0.50,   # ASSUMPTION — SDM completes in only 30–50% of encounters (Mazzone et al. Chest 2021)
}

# "Never screener" fraction — patients who will never screen regardless of visit count
# Source: AiP Parameters PDF — "~10–15% for cervical, ~60–70% for lung"
NEVER_SCREENER_FRAC = {
    "cervical": 0.125,   # Source: AiP Parameters PDF (midpoint of 10–15%)
    "lung":     0.65,    # Source: AiP Parameters PDF (midpoint of 60–70%)
}

# Calibration targets from AiP Parameters PDF:
# Cervical: model should yield ~73–83% of women up-to-date over a 3-year interval (NHIS/BRFSS)
# Lung: annual rate ~20% (15–25%) at academic centers with dedicated programs (Fedewa et al. 2022)
# Visits before initiation: cervical ~1.5 (range 1–3); lung ~4 (range 2–6)
# Source: Triplette et al. JAMA Netw Open 2022 (lung visits before LDCT ordered)
SCREENING_VISITS_BEFORE_INITIATION = {
    "cervical": 1.5,   # Source: AiP Parameters PDF; Kepka et al. 2014
    "lung":     4.0,   # Source: AiP Parameters PDF; Triplette et al. JAMA Netw Open 2022
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

# P(cervical test completed | test ordered at visit) — test happens in-office during PCP/GYN visit
# Source: AiP Parameters PDF (Google Doc reference); note this is NOT the population-level uptake rate
# Population-level: 72.5% of eligible women complete cervical screening (PMC8390589)
# Per-visit: 97.1% complete once ordered — different denominator
CERVICAL_TEST_COMPLETION_PROB = 0.97108   # Source: AiP Parameters PDF (Google Doc)

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
    # Provider → primary screening scheduling delay
    "provider_to_screening": 10,    # PLACEHOLDER — days from provider visit to screening appointment

    # Lab / radiology processing times
    "cytology":               7,    # Pap cytology alone; Source: AiP Parameters PDF
    "co_test":               10,    # HPV co-testing; Source: AiP Parameters PDF
    "hpv_alone":              5,    # Primary HPV alone; Source: AiP Parameters PDF
    "ldct_notification":      5,    # LDCT result to patient notification; Source: AiP Parameters PDF
    "ldct_to_workup":        21,    # Positive LDCT result to diagnostic workup; Source: AiP Parameters PDF

    # Patient notification delays
    "notification_normal":   10,    # Total days to patient notification (normal); Source: AiP Parameters PDF
    "notification_abnormal": 14,    # Total days to patient notification (abnormal); Source: AiP Parameters PDF

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

# Malignancy rate by Lung-RADS category (among patients in that category)
# Source: Pinsky et al. 2015 (NLST/Lung-RADS); McKee et al. 2015; ACR Lung-RADS v1.1; Hammer et al. 2020
LUNG_RADS_MALIGNANCY_RATE = {
    "RADS_3":    0.03,   # Source: Pinsky et al. 2015; McKee et al. 2015
    "RADS_4A":   0.08,   # Source: ACR Lung-RADS v1.1; Hammer et al. 2020
    "RADS_4B_4X": 0.35,  # Source: Pinsky et al. 2015; ACR Lung-RADS v1.1
}

# ── Lung Pathway Clinical Probabilities ─────────────────────────────────────
# LTFU is handled exclusively by queue-based geometric waiting-time hazard
# (consistent with the cervical pathway). Only clinical outcome probabilities
# remain here — not per-node dropout rates.
LUNG_PATHWAY_PROBS = {
    "malignancy_confirmed":     0.25,    # P(malignant | biopsy completed); Source: Pinsky et al. 2015
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
    # small probability they abandon the queue (geometric/exponential model).
    # Median days in queue before abandoning ≈ ln(2) / daily_prob.
    #   primary:   0.002/day → median ~346 days  PLACEHOLDER
    #   secondary: 0.005/day → median ~139 days  PLACEHOLDER
    #   treatment: 0.003/day → median ~231 days  PLACEHOLDER
    "queue_primary_daily":   0.002,    # PLACEHOLDER — primary screening retry queue
    "queue_secondary_daily": 0.005,    # PLACEHOLDER — colposcopy / lung biopsy retry queue
    "queue_treatment_daily": 0.003,    # PLACEHOLDER — LEEP / cone biopsy retry queue

    # ── Reschedule probability ──────────────────────────────────────────────
    # When a patient's screening slot is full, this is the probability they
    # reschedule for the next available day (re-enter queue). Patients who
    # do not reschedule simply do not return for that appointment.
    # Applied at primary and secondary screening queue overflow.
    "reschedule_primary":    0.10,     # PLACEHOLDER — P(reschedule | primary screening slot full)
    "reschedule_secondary":  0.10,     # PLACEHOLDER — P(reschedule | secondary screening slot full)
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
# Colposcopy finding distribution (aggregate, all triggers combined)
# Source: AiP Parameters PDF
# Note: CIN2 and CIN3 split from combined high_grade (0.30) is ASSUMPTION — use 50/50
COLPOSCOPY_RESULT_PROBS_DEFAULT = {
    "NORMAL":       0.35,   # resolved_normal; Source: AiP Parameters PDF
    "CIN1":         0.28,   # low_grade; Source: AiP Parameters PDF
    "CIN2":         0.15,   # high_grade split (50% of 0.30); Source: AiP Parameters PDF; split ASSUMPTION
    "CIN3":         0.15,   # high_grade split (50% of 0.30); Source: AiP Parameters PDF; split ASSUMPTION
    "INSUFFICIENT": 0.07,   # insufficient sample; Source: AiP Parameters PDF
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

# CIN2/3 treatment outcome split
# Source: AiP Parameters PDF — "50% Treated, 50% Exit"
CIN23_TREATMENT_RATE = 0.50   # P(treated | CIN2/3 diagnosed); Source: AiP Parameters PDF

# CIN1 surveillance parameters
# Source: ASCCP 2019 (High confidence); Castle et al. 2009; Ostor 1993; ALTS trial; Cox et al. 2003
CIN1_SURVEILLANCE_INTERVAL_DAYS  = 365    # 12-month follow-up interval; Source: ASCCP 2019
CIN1_MAX_CLEAN_VISITS_BEFORE_ROUTINE = 2  # consecutive clean visits before returning to routine; Source: ASCCP 2019
CIN1_RESOLUTION_PROB_PER_VISIT    = 0.40  # Source: Castle et al. 2009; Ostor 1993
CIN1_ESCALATION_PROB_PER_VISIT    = 0.07  # escalation to CIN2/3; Source: ALTS trial; Cox et al. 2003
CIN1_PERSISTENCE_PROB_PER_VISIT   = 0.53  # calculated: 1 - 0.40 - 0.07; Source: AiP Parameters PDF

# ── Post-Treatment / Post-Negative Re-entry Delays (days) ─────────────────────
POST_TREATMENT_DELAY_DAYS = {
    "cervical": 180,
    "lung":     365,
}

# ── Screening / Procedure Resource Capacities (daily slots) ──────────────────
# Primary screenings compete for slots — PCP/GYN/Specialist patients get
# priority over ER walk-ins.  When capacity is exceeded, patients are
# rescheduled to the next workday (overflow).
# Secondary screenings and treatment follow FIFO — only patients with
# abnormal primary results enter these queues.
CAPACITIES = {
    # Primary screening slots
    "cytology":    8,     # PLACEHOLDER — replace with NYP lab throughput
    "hpv_alone":   8,     # PLACEHOLDER — replace with NYP lab throughput
    "co_test":     8,     # PLACEHOLDER — HPV + cytology combo; replace with NYP lab throughput
    "ldct":        4,     # PLACEHOLDER — replace with NYP radiology capacity
    # Secondary / diagnostic slots
    "colposcopy":  8,     # PLACEHOLDER — replace with NYP GYN procedure data
    "lung_biopsy": 2,     # PLACEHOLDER — replace with NYP IR capacity
    # Treatment slots
    "leep":        5,     # PLACEHOLDER — replace with NYP OR scheduling data
    "cone_biopsy": 3,     # PLACEHOLDER — replace with NYP OR scheduling data
}

# ── Unscreened Re-entry Delay (days) ──────────────────────────────────────────
# Reschedule delay by provider type
# Source: AiP Parameters PDF
RESCHEDULE_DELAY_DAYS = {
    "pcp":          28,   # Source: AiP Parameters PDF
    "gynecologist": 38,   # Source: AiP Parameters PDF
    "specialist":   30,   # PLACEHOLDER
    "er":            7,   # PLACEHOLDER
    "default":      30,   # PLACEHOLDER — fallback if provider not specified
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

# Time-to-colposcopy guidelines by result severity
# Source: 2019 ASCCP Risk-Based Management Consensus Guidelines (Perkins et al., J Low Genit Tract Dis 2020)
ABNORMAL_FOLLOWUP_DAYS = {
    "ASCUS_LSIL": 90,    # within 3 months; Source: ASCCP 2019 (Perkins et al. 2020)
    "HSIL_ASCH":  30,    # expedited, within 1 month; Source: ASCCP 2019 (Perkins et al. 2020)
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
# NYC eligible women ~1.5M → 1.5M / 100 = 15,000 simulated patients.
# All metrics scale by this factor when extrapolating to real-world counts.
# =============================================================================

# ── Population scale ─────────────────────────────────────────────────────────
POPULATION_SCALE_FACTOR = 100          # 1 sim patient = 100 NYC women

# ── Initial seed population ──────────────────────────────────────────────────
# Number of established patients to seed at day 0.  The pool then grows or
# shrinks organically as arrivals join and patients exit via mortality,
# attrition, LTFU, or ineligibility.  Set to 0 for a pure cold-start.
INITIAL_POOL_SIZE = 15_000

# ── Patient arrival sources ──────────────────────────────────────────────────
# Each source represents a distinct pathway into NYP's screening system.
# Patients from different sources have different age profiles and routing.
#
# Fields:
#   daily_rate  — mean Poisson arrivals/day for this source
#   age_range   — (min, max) age constraint; sampled within this range
#   routing     — "outpatient" | "er" | "census" (census = use ARRIVAL_TYPE_PROBS)
#
# The total arrival rate is the sum across all sources.  Pool size emerges
# from total arrivals vs. total exits (mortality + attrition + LTFU + ineligible).
#
# Sources:
#   aging_in       — women reaching screening eligibility age (turning 21)
#                    Source: ~4.3M US women turn 21/year; NYC share ~1.4%;
#                    NYP market share ~5% → ~3/day at scale factor 100
#                    PLACEHOLDER — calibrate to NYP panel acquisition data
#   new_mover      — women relocating to NYC or switching into NYP network
#                    Source: NYC net domestic migration + international inflow
#                    PLACEHOLDER — calibrate to NYP new-patient registration data
#   er_walkin      — unplanned ER visits where screening may be opportunistic
#                    Source: NYP ER volume ~620K/year, ~50% female, ~30% eligible age
#                    PLACEHOLDER — calibrate to NYP ER screening data
#   referral       — sent by external provider specifically for screening
#                    PLACEHOLDER — calibrate to NYP referral data
# Total Poisson arrival rate (λ) and routing split
TOTAL_DAILY_ARRIVALS = 1.6                # λ_total — mean Poisson arrivals/day
_OUTPATIENT_SHARE    = 0.80               # 80% outpatient (NYP Facts & Figures)
_ER_SHARE            = 0.20               # 20% drop-in ER

# Outpatient sub-source shares (must sum to 1.0 within outpatient)
_OP_AGING_IN  = 0.4141                    # aging_in  share of outpatient
_OP_NEW_MOVER = 0.3359                    # new_mover share of outpatient
_OP_REFERRAL  = 0.2500                    # referral  share of outpatient

ARRIVAL_SOURCES = {
    # Outpatient sources: λ = TOTAL_DAILY_ARRIVALS × _OUTPATIENT_SHARE × sub-share
    #   aging_in  → Poisson(1.6 × 0.80 × 0.4141) = Poisson(0.53)
    #   new_mover → Poisson(1.6 × 0.80 × 0.3359) = Poisson(0.43)
    #   referral  → Poisson(1.6 × 0.80 × 0.2500) = Poisson(0.32)
    # ER source:    λ = TOTAL_DAILY_ARRIVALS × _ER_SHARE
    #   er_walkin → Poisson(1.6 × 0.20)           = Poisson(0.32)
    "aging_in": {
        "daily_rate": TOTAL_DAILY_ARRIVALS * _OUTPATIENT_SHARE * _OP_AGING_IN,
        "age_range":  (21, 25),
        "routing":    "outpatient",
    },
    "new_mover": {
        "daily_rate": TOTAL_DAILY_ARRIVALS * _OUTPATIENT_SHARE * _OP_NEW_MOVER,
        "age_range":  (21, 85),     # Census age distribution within range
        "routing":    "outpatient",
    },
    "er_walkin": {
        "daily_rate": TOTAL_DAILY_ARRIVALS * _ER_SHARE,
        "age_range":  (21, 85),
        "routing":    "er",
    },
    "referral": {
        "daily_rate": TOTAL_DAILY_ARRIVALS * _OUTPATIENT_SHARE * _OP_REFERRAL,
        "age_range":  (30, 75),     # referred patients skew middle-aged
        "routing":    "outpatient",
    },
}

# ── Visit scheduling ──────────────────────────────────────────────────────────
ANNUAL_VISIT_INTERVAL  = 365           # days between established patient visits
# Number of years of outpatient appointments to pre-book for each established patient.
# At warmup, each patient is scheduled for ADVANCE_SCHEDULE_YEARS annual visits.
# After each visit, the far end of the window is extended by one year, so the
# patient always has approximately ADVANCE_SCHEDULE_YEARS future appointments booked.
ADVANCE_SCHEDULE_YEARS = 5
# Warmup period: spread initial cohort across multiple years so the screening
# intervals (3-year cytology, 5-year HPV) are staggered and Year 1 isn't a
# spike. Patients are evenly distributed across the warmup window; each gets
# ADVANCE_SCHEDULE_YEARS of pre-booked annual visits from their start date.
# A 3-year warmup (1095 days) ensures the first cytology cycle has completed
# and the system reaches near-steady-state by Year 4.
WARMUP_DAYS            = 1825          # 5-year warmup

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
# ── Disease-Specific Mortality ────────────────────────────────────────────────
# Annual excess mortality risk given a confirmed diagnosis.
# These are ADDITIVE to the baseline Gompertz mortality — they represent the
# additional risk of death attributable to the disease itself.
#
# PLACEHOLDER — set to None until NYP/SEER data is available.
# When populated, the runner should draw a disease-specific death day
# at diagnosis time and add it to the life-event queue.
DISEASE_MORTALITY = {
    # Cervical — by CIN grade at diagnosis
    "CIN1":  None,   # negligible excess mortality; placeholder
    "CIN2":  None,   # annual excess mortality rate (e.g., 0.001)
    "CIN3":  None,   # annual excess mortality rate (e.g., 0.005)

    # Lung — by biopsy result
    "lung_malignant": None,   # annual excess mortality rate (e.g., 0.15)

    # Combined (any confirmed cancer diagnosis — cervical or lung)
    "any_cancer":     None,   # overall annual cancer mortality if not using per-type
}

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
