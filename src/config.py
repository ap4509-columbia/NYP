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
SIM_YEARS      = 70                      # full 70-year longitudinal horizon
DAYS_PER_YEAR  = 365
SIM_DAYS       = SIM_YEARS * DAYS_PER_YEAR   # = 25,550 days
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

PATIENT_TYPE_PROBS = {"outpatient": 0.70, "drop_in": 0.30}

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

OUTPATIENT_SHOW_PROB = 1.00   # raise to model no-shows

# No-show rates by provider type
# PCP: Kheirkhah et al. (2016, BMC Health Services Research)
# GYN: Percac-Lima et al. (2010); Engelstad et al. (2001)
# Specialist/ER: ASSUMPTION — no published NYC-specific data
NO_SHOW_PROB = {
    "pcp":          0.23,   # Kheirkhah et al. 2016, BMC Health Services Research
    "gynecologist": 0.26,   # Percac-Lima et al. 2010; Engelstad et al. 2001
    "specialist":   0.20,   # ASSUMPTION — placeholder
    "er":           0.00,   # ER has no scheduled appointments
}

# Post-no-show disposition probabilities: what happens to a patient who misses their appointment
# Source: Hwang et al. (2015, J Health Care Poor Underserved); AiP Parameters PDF
NOT_SEEN_PROBS = {
    "pcp": {
        "reschedule": 0.50,   # Source: AiP Parameters PDF (Hwang et al. 2015)
        "exit":       0.15,   # Source: AiP Parameters PDF
        "wait":       0.35,   # Source: AiP Parameters PDF
    },
    "gynecologist": {
        "reschedule": 0.40,   # Source: AiP Parameters PDF (Hwang et al. 2015)
        "exit":       0.20,   # Source: AiP Parameters PDF
        "wait":       0.40,   # Source: AiP Parameters PDF
    },
}

# Cumulative exit probability by number of missed appointments (no-shows)
# Source: AiP Parameters PDF — "15% exit after 1st no-show, 30% after 2nd, 60–80% after 3rd, rest after 4th"
NOSHOW_CUMULATIVE_EXIT_PROB = [0.15, 0.30, 0.70, 1.00]  # after 1st, 2nd, 3rd, 4th no-show

# Outreach scenario: with active navigation vs without
# Source: AiP Parameters PDF — Cochrane reviews; Everett et al. 2011
OUTREACH_PROBS = {
    "with_navigation":    {"reschedule": 0.60, "exit": 0.10, "wait": 0.30},
    "without_outreach":   {"reschedule": 0.35, "exit": 0.25, "wait": 0.40},
}

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

# P(LDCT completed | ordered)
# Source: ASCO abstracts/presentations/197367; AiP Parameters PDF
# Absolute completion rate = 71.9% after follow-up (same source)
LUNG_TEST_COMPLETION_PROB    = 0.612         # Source: ASCO abstracts/197367; AiP Parameters PDF
LUNG_TEST_COMPLETION_ABSOLUTE = 0.719        # absolute completion after follow-up; same source

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
    "cytology":               7,    # Pap cytology alone; Source: AiP Parameters PDF
    "co_test":               10,    # HPV co-testing; Source: AiP Parameters PDF
    "hpv_alone":              5,    # Primary HPV alone; Source: AiP Parameters PDF
    "notification_normal":   10,    # Total days to patient notification (normal); Source: AiP Parameters PDF
    "notification_abnormal": 14,    # Total days to patient notification (abnormal); Source: AiP Parameters PDF
    "ldct_notification":      5,    # LDCT result to patient notification; Source: AiP Parameters PDF
    "ldct_to_workup":        21,    # Positive LDCT result to diagnostic workup; Source: AiP Parameters PDF
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

# Follow-up adherence by Lung-RADS category
# Source: ASCO JCO 2021 (ascopubs.org/doi/10.1200/JCO.2021.39.15_suppl.10540)
LUNG_RADS_ADHERENCE = {
    "RADS_3":    0.671,   # Source: ASCO JCO 2021.39.15_suppl.10540
    "RADS_4A":   0.664,   # Source: ASCO JCO 2021.39.15_suppl.10540
    "RADS_4B_4X": 0.782,  # Source: ASCO JCO 2021.39.15_suppl.10540
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

# P(follow-up completed | positive LDCT) treated by NYP: ~50%
# Source: AiP Parameters PDF (Yiye Zhang, Weill Cornell)
LUNG_NYP_TREATMENT_RATE = 0.50   # Source: AiP Parameters PDF

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
    # Cervical
    # P(colposcopy completed | abnormal result): 75.3% within 12 months; Source: PMC9808794
    # Note: marked "Most likely irrelevant to NYP" in AiP PDF — use with caution
    "post_abnormal_to_colposcopy":  0.247,   # 1 - 0.753; Source: PMC9808794 (pmc.ncbi.nlm.nih.gov/articles/PMC9808794/)

    # CIN2/3: 50% exit post-colposcopy before treatment; Source: AiP Parameters PDF
    "post_colposcopy_to_treatment": 0.50,    # Source: AiP Parameters PDF ("50% Exit")

    # Unscreened re-engagement: yes_schedule default = 0.40
    # Source: AiP Parameters PDF; Cochrane reviews; Everett et al. 2011
    # Outreach modality ranges: letter ~15–20%, phone ~30–40%, in-person ~40–55%
    "unscreened_will_reschedule":   0.40,    # Source: AiP Parameters PDF (Everett et al. 2011)
}

# Fate of patients who decline outreach / don't reschedule
# Source: NHIS/BRFSS longitudinal data; AiP Parameters PDF
# "40–60% eventually screen within 5 years due to life events"
UNSCREENED_FATE = {
    "may_return_later": 0.60,   # Source: NHIS/BRFSS; AiP Parameters PDF
    "exit_system":      0.40,   # Source: AiP Parameters PDF
}

# Scenario overrides for outreach testing
# Source: AiP Parameters PDF
UNSCREENED_REENTRY_SCENARIOS = {
    "robust_navigation": 0.55,   # yes_schedule; Source: AiP Parameters PDF
    "minimal_outreach":  0.25,   # yes_schedule; Source: AiP Parameters PDF
}

# Re-entry delay after outreach acceptance
REENTRY_DELAY_DAYS = 30   # Source: AiP Parameters PDF

# Overdue grace period before patient is flagged as non-adherent
# Source: AiP Parameters PDF
# Rationale: EHR BPAs typically fire at 1–3 months past due; CMS quality measure uses 12-month interval
OVERDUE_GRACE_DAYS = {
    "cervical_3yr": 90,    # Source: AiP Parameters PDF (EHR BPA fire window)
    "cervical_5yr": 180,   # Source: AiP Parameters PDF (longer interval tolerates drift)
    "lung":         90,    # Source: AiP Parameters PDF (CMS quality measure)
}

# Daily probability of spontaneous re-entry from overdue pool
# Source: AiP Parameters PDF; NHIS/BRFSS longitudinal data
# Implied median return: cervical ~18 months, lung ~12 months
# Sensitivity: cervical 0.0007–0.0025; lung 0.0010–0.0035
DAILY_REENTRY_PROB = {
    "cervical": 0.0013,   # Source: AiP Parameters PDF
    "lung":     0.0019,   # Source: AiP Parameters PDF
}

# Maximum days overdue before patient permanently exits (without active re-entry)
# Source: AiP Parameters PDF
MAX_OVERDUE_DAYS = {
    "cervical_3yr": 1095,   # 3 years; Source: AiP Parameters PDF
    "cervical_5yr": 1825,   # 5 years; Source: AiP Parameters PDF
    "lung":          730,   # 2 years; Source: AiP Parameters PDF
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
# Reschedule delay by provider type
# Source: AiP Parameters PDF
RESCHEDULE_DELAY_DAYS = {
    "pcp":          28,   # Source: AiP Parameters PDF
    "gynecologist": 38,   # Source: AiP Parameters PDF
    "specialist":   30,   # ASSUMPTION
    "er":            7,   # ASSUMPTION
    "default":      30,   # ASSUMPTION — fallback if provider not specified
}

# ── Inter-Step Scheduling Delays (days) ───────────────────────────────────────
# Time between referral and the next appointment at each step.
# These drive SimPy timeouts and are a key lever in scenario analysis
# (coordinated care reduces these delays).
# PLACEHOLDER — replace with NYP scheduling data.
FOLLOWUP_DELAY_DAYS = {
    "colposcopy":        50,    # default: abnormal result → colposcopy; Source: AiP Parameters PDF (was 30 — ASSUMPTION)
    "colposcopy_hsil":   32,    # HSIL-expedited colposcopy; Source: AiP Parameters PDF
    "leep":              14,    # colposcopy → LEEP; ASSUMPTION
    "cone_biopsy":       21,    # colposcopy → cone biopsy; ASSUMPTION
    "lung_biopsy":       21,    # RADS 4 → diagnostic workup; Source: AiP Parameters PDF
    "lung_treatment":    21,    # malignancy confirmed → treatment start; ASSUMPTION
}

# Time-to-colposcopy guidelines by result severity
# Source: 2019 ASCCP Risk-Based Management Consensus Guidelines (Perkins et al., J Low Genit Tract Dis 2020)
ABNORMAL_FOLLOWUP_DAYS = {
    "ASCUS_LSIL": 90,    # within 3 months; Source: ASCCP 2019 (Perkins et al. 2020)
    "HSIL_ASCH":  30,    # expedited, within 1 month; Source: ASCCP 2019 (Perkins et al. 2020)
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

# Untreated patient re-engagement probabilities
# Source: AiP Parameters PDF
UNTREATED_REENGAGEMENT = {
    "cervical_cin23": {
        "reengage":        0.70,   # Source: AiP Parameters PDF; sensitivity 0.55–0.85
        "remain_overdue":  0.20,   # Source: AiP Parameters PDF; sensitivity 0.10–0.25
        "exit":            0.10,   # Source: AiP Parameters PDF; sensitivity 0.05–0.20
    },
    "lung": {
        "reengage":        0.55,   # Source: AiP Parameters PDF; sensitivity 0.35–0.70
        "remain_overdue":  0.15,   # Source: AiP Parameters PDF; sensitivity 0.05–0.20
        "exit":            0.30,   # Source: AiP Parameters PDF; sensitivity 0.15–0.40
    },
}

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
