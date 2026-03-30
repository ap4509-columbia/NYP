# =============================================================================
# population.py
# Population sampler — STUB INTERFACE
# =============================================================================
#
# ROLE IN THE SIMULATION
# ─────────────────────────────────────────────────────────────────────────────
# This module is the patient factory. Whenever the simulation needs a new patient,
# it calls sample_patient(), which draws all demographic and clinical attributes
# from probability distributions calibrated to the NYC eligible-women population.
#
# The resulting Patient object carries the fields that screening.py and followup.py
# need to make decisions: age (for eligibility and stratum), has_cervix (cervical
# eligibility), pack_years / smoker (lung eligibility), hpv_positive and prior_cin
# (risk adjustment for result draws).
#
# STUB → REAL SAMPLER
# ─────────────────────────────────────────────────────────────────────────────
# This file is intentionally a thin stub. The real population sampling code will
# REPLACE the body of sample_patient() only — the function signature and return
# type must remain stable so no other file needs to change.
#
# All distribution values marked PLACEHOLDER must be replaced with NYC census /
# NYP EHR-derived rates before the model is used for planning.
# =============================================================================

import random
from patient import Patient
import config as cfg

# ── NYC placeholder distributions ─────────────────────────────────────────────
# PLACEHOLDER — replace with provided population sampling code

_AGE_BRACKETS = [
    ((21, 29), 0.18),
    ((30, 39), 0.22),
    ((40, 49), 0.20),
    ((50, 59), 0.18),
    ((60, 69), 0.13),
    ((70, 80), 0.09),
]

_RACE_DIST = {
    "White":                  0.32,
    "Black/African American": 0.22,
    "Hispanic/Latino":        0.28,
    "Asian":                  0.13,
    "Other":                  0.05,
}

_INSURANCE_DIST = {
    "Commercial": 0.45,
    "Medicaid":   0.30,
    "Medicare":   0.15,
    "Uninsured":  0.10,
}

_SMOKER_RATE       = 0.13    # NYC women, PLACEHOLDER
_HPV_POSITIVE_RATE = 0.25    # among unvaccinated women with cervix, PLACEHOLDER

_HPV_VAX_RATE = {            # vaccination coverage by age cohort, PLACEHOLDER
    (21, 29): 0.60,
    (30, 39): 0.40,
    (40, 49): 0.20,
    (50, 80): 0.05,
}

_HYSTERECTOMY_RATE = {       # prevalence by age group, PLACEHOLDER
    (21, 39): 0.01,
    (40, 49): 0.07,
    (50, 59): 0.12,
    (60, 80): 0.18,
}


# ── Internal helpers ───────────────────────────────────────────────────────────

def _weighted_choice(dist: dict):
    """Draw one key from a {key: weight} dict using weighted random selection."""
    keys    = list(dist.keys())
    weights = list(dist.values())
    return random.choices(keys, weights=weights, k=1)[0]


def _sample_age() -> int:
    """
    Sample a patient age from the NYC age bracket distribution.

    First picks a bracket ((lo, hi), weight) pair using the bracket weights,
    then draws a uniform integer within that bracket. This two-step approach
    gives a realistic piecewise-uniform age distribution rather than a smooth
    continuous one.
    """
    brackets, weights = zip(*_AGE_BRACKETS)
    (lo, hi) = random.choices(brackets, weights=weights, k=1)[0]
    return random.randint(lo, hi)


def _rate_for_age(table: dict, age: int, default: float = 0.05) -> float:
    """
    Look up the rate for a given age from a {(lo, hi): rate} bracket table.

    Iterates through the bracket keys until the patient's age falls within one.
    Returns default if no bracket matches (e.g. age is outside all defined ranges).
    Used for hysterectomy prevalence and HPV vaccination coverage lookups.
    """
    for (lo, hi), rate in table.items():
        if lo <= age <= hi:
            return rate
    return default


# ── Public interface (REPLACE body with provided code) ────────────────────────

def sample_patient(
    patient_id:   int,
    day_created:  int,
    destination:  str,
    patient_type: str,
) -> Patient:
    """
    Draw one patient from the NYC eligible women population.

    Parameters
    ----------
    patient_id   : unique identifier
    day_created  : simulation day the patient enters the system
    destination  : first provider — "pcp"|"gynecologist"|"specialist"|"er"
    patient_type : "outpatient" | "drop_in"

    Returns
    -------
    Patient object with demographics and clinical flags set.

    NOTE: This stub will be replaced by the provided population sampling code.
          Do not change the function signature.
    """
    age       = _sample_age()
    race      = _weighted_choice(_RACE_DIST)
    insurance = _weighted_choice(_INSURANCE_DIST)

    smoker     = random.random() < _SMOKER_RATE
    # ~30% of ever-smokers are former smokers; sample years since quitting (0–30 yrs)
    is_former  = (not smoker) and (random.random() < 0.30)
    pack_years = round(random.uniform(5, 40), 1) if (smoker or is_former) else 0.0
    years_since_quit = round(random.uniform(0, 30), 1) if is_former else 0.0

    bmi = round(random.gauss(27.5, 5.0), 1)
    bmi = max(15.0, min(60.0, bmi))

    hpv_vaccinated    = random.random() < _rate_for_age(_HPV_VAX_RATE, age)
    hpv_positive      = (not hpv_vaccinated) and (random.random() < _HPV_POSITIVE_RATE)
    hysterectomy_prob = _rate_for_age(_HYSTERECTOMY_RATE, age)
    has_cervix        = random.random() > hysterectomy_prob

    prior_abnormal_pap = has_cervix and (random.random() < 0.12)
    prior_cin = None
    if prior_abnormal_pap and random.random() < 0.30:
        prior_cin = random.choice(["CIN1", "CIN2"])

    return Patient(
        patient_id           = patient_id,
        day_created          = day_created,
        patient_type         = patient_type,
        destination          = destination,
        age                  = age,
        race                 = race,
        insurance            = insurance,
        smoker               = smoker,
        pack_years           = pack_years,
        years_since_quit     = years_since_quit,
        bmi                  = bmi,
        has_cervix           = has_cervix,
        hpv_positive         = hpv_positive,
        hpv_vaccinated       = hpv_vaccinated,
        prior_abnormal_pap   = prior_abnormal_pap,
        prior_cin            = prior_cin,
        age_at_entry         = age,
        simulation_entry_day = day_created,
    )


# =============================================================================
# Stable population: mortality helpers
# =============================================================================

def get_mortality_prob(age: int) -> float:
    """
    Return the annual probability of death for a woman of the given age.

    Values come from cfg.ANNUAL_MORTALITY_RATE, which is keyed on (lo, hi)
    inclusive age brackets calibrated to US life tables. Returns 0.03 as a
    conservative default for ages outside all defined brackets (e.g. age > 80).

    This probability is used per-patient per year in the mortality sweep.
    The sweep scales it down to the sweep interval:
        p_death_this_sweep = annual_prob × (MORTALITY_CHECK_DAYS / 365)
    """
    for (lo, hi), rate in cfg.ANNUAL_MORTALITY_RATE.items():
        if lo <= age <= hi:
            return rate
    return 0.03   # PLACEHOLDER fallback for very old patients (>80)


def draw_mortality(p: "Patient", sweep_days: int = cfg.MORTALITY_CHECK_DAYS) -> bool:
    """
    Bernoulli draw: does this patient die during the current sweep interval?

    Scales the annual mortality rate to the sweep interval length so that the
    expected number of deaths per year is correct regardless of sweep frequency:

        p(die in N days) ≈ annual_rate × (N / 365)

    This approximation is valid when annual_rate << 1, which holds for all
    age groups in the simulation.

    Parameters
    ----------
    p           : Patient object (must have a current .age attribute)
    sweep_days  : number of days since last mortality check (default from config)

    Returns
    -------
    True  → patient dies; caller should call p.exit_system(day, "mortality")
    False → patient survives this interval
    """
    annual_prob   = get_mortality_prob(p.age)
    interval_prob = annual_prob * (sweep_days / 365.0)
    return random.random() < interval_prob


def _sample_established_destination() -> str:
    """
    Sample a primary-care provider for an established (cycling) patient.

    Established patients have a regular provider — PCP, gynecologist, or
    specialist. They are never assigned to the ER as a primary destination;
    ER visits are unplanned and handled via the drop-in flow. Redistributes
    ER's weight proportionally across the three non-ER providers.
    """
    non_er = {k: v for k, v in cfg.DESTINATION_PROBS.items() if k != "er"}
    keys    = list(non_er.keys())
    weights = list(non_er.values())
    return random.choices(keys, weights=weights, k=1)[0]


def generate_established_population(
    n: int,
    start_pid: int = 0,
    entry_day: int = 0,
) -> list:
    """
    Generate the initial stable-population cohort of n established patients.

    Each patient is drawn from the same NYC demographic distribution as new
    arrivals (same age, race, smoking, cervix, HPV distributions) but is
    flagged as an established cycling patient:

        p.is_established = True

    Patients are given a destination drawn from DESTINATION_PROBS; they will
    be spread across providers during warmup scheduling in the runner.

    Parameters
    ----------
    n         : number of established patients to generate (cfg.SIMULATED_POPULATION)
    start_pid : starting patient_id counter (runner passes its current _pid)
    entry_day : simulation day to stamp as simulation_entry_day (usually 0)

    Returns
    -------
    list[Patient] — length n, all with is_established=True
    """
    pool = []
    for i in range(n):
        dest = _sample_established_destination()
        p    = sample_patient(
            patient_id   = start_pid + i,
            day_created  = entry_day,
            destination  = dest,
            patient_type = "outpatient",   # established patients are always outpatient
        )
        p.is_established       = True
        p.age_at_entry         = p.age
        p.simulation_entry_day = entry_day
        pool.append(p)
    return pool
