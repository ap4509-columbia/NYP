# =============================================================================
# validation.py
# NYP Women's Health Screening Simulation — Cross-Validation Targets
# =============================================================================
#
# THESE VALUES ARE NOT SIMULATION INPUTS.
#
# They are external benchmarks and calibration targets from the literature.
# Compare simulation OUTPUTS against these to validate the model.
# If the model does not produce outputs within these ranges, tune the
# PARAMETERS (in parameters.py), not these targets.
#
# Do NOT import this file inside model.py or runner.py for use in
# simulation logic — that would make the model circular.
# =============================================================================

# ── Lung Eligibility Benchmark ──────────────────────────────────────────────
# P(patient aged 50–80 meets USPSTF lung eligibility: ≥20 pack-years,
# current/quit <15yr).  This should EMERGE from the patient attribute
# distributions (smoker rate, pack_years, years_since_quit) — NOT be
# set as an input.
# Source: CDC BRFSS NYC smoking data; Fedewa et al. 2022
# NYC smoking prevalence ~12% vs national ~14%; eligible subset ~40%
# of ever-smokers age 50–80.  Sensitivity range: 4–7%.
LUNG_ELIGIBLE_BASE_PROB = 0.055

# ── Expected Visits Before Screening Initiation ────────────────────────────
# Average number of provider visits before a screening is first ordered.
# External benchmark for cross-validation — not currently consumed by the model.
# Source: AiP Parameters PDF; Kepka et al. 2014; Triplette et al. 2022
SCREENING_VISITS_BEFORE_INITIATION = {
    "cervical": 1.5,   # Source: AiP Parameters PDF; Kepka et al. 2014
    "lung":     4.0,   # Source: AiP Parameters PDF; Triplette et al. JAMA Netw Open 2022
}

# ── External Population Benchmark ──────────────────────────────────────────
# Total eligible women in NYC metro area (real-world estimate).
# NOT a simulation input — used only for population capture rate
# visualizations and foregone revenue calculations.
# Source: ACS 2020 5-Year Estimates — NYC women aged 21–80 ≈ 1.5M
# PLACEHOLDER — replace with refined NYC metro catchment estimate
NYC_ELIGIBLE_POPULATION = 1_500_000

# ── Calibration Targets ────────────────────────────────────────────────────
# Structured version of literature benchmarks. Compare simulation outputs
# against these ranges.  If outside range, tune parameters.py — not these.
CALIBRATION_TARGETS = {
    # Cervical: % of eligible women up-to-date over a 3-year interval
    # Source: NHIS/BRFSS national estimates
    "cervical_up_to_date_pct":  (73, 83),

    # Lung: annual LDCT screening rate at academic centers with programs
    # Source: Fedewa et al. JAMA Intern Med 2022
    "lung_annual_rate_pct":     (15, 25),

    # Average visits before first screening initiation
    # Source: AiP Parameters PDF; Kepka et al. 2014; Triplette et al. 2022
    "cervical_visits_before":   (1, 3),
    "lung_visits_before":       (2, 6),
}

# ── Literature Benchmarks (defined but not consumed by the current model) ────
# These parameters are cited from published sources and preserved here as
# reference ranges. They are NOT read by model.py or runner.py. Keeping them
# documented so future model iterations can wire them in.

# Per-visit screening initiation probability at first eligible visit.
# Formula (when implemented): P(screen at visit k) = min(BASE + (k-1)×INC, CAP)
# Cervical PCP: Kepka et al. Prev Med 2014; Stange et al. Ann Fam Med 2003; Huy et al. 2013
# Cervical GYN: Sawaya et al. Ann Intern Med 2019 — well-woman visit, cervical screening integral
# Lung PCP:    Fedewa et al. JAMA Intern Med 2022; Mazzone et al. Chest 2021 — SDM bottleneck
# Sensitivity ranges: cervical_pcp 40–65%; cervical_gyn 75–92%; lung_pcp 8–18%
SCREENING_INITIATION_BASE = {
    "cervical_pcp": 0.55,
    "cervical_gyn": 0.85,
    "lung_pcp":     0.12,
}

# Time-to-colposcopy guidelines by cytology result severity (days).
# Source: 2019 ASCCP Risk-Based Management Consensus Guidelines (Perkins et al.,
# J Low Genit Tract Dis 2020)
ABNORMAL_FOLLOWUP_DAYS = {
    "ASCUS_LSIL": 90,    # within 3 months
    "HSIL_ASCH":  30,    # expedited, within 1 month
}

