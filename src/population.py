# =============================================================================
# population.py
# Population sampler — STUB INTERFACE
# =============================================================================
# This file defines the contract for patient generation.
# The provided population sampling code will REPLACE the body of
# `sample_patient()` below. The function signature must remain stable.
#
# Stub uses rough NYC demographic distributions so Steps 2–6 run
# end-to-end before the real sampler arrives.
# =============================================================================

import random
from patient import Patient

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
    keys    = list(dist.keys())
    weights = list(dist.values())
    return random.choices(keys, weights=weights, k=1)[0]


def _sample_age() -> int:
    brackets, weights = zip(*_AGE_BRACKETS)
    (lo, hi) = random.choices(brackets, weights=weights, k=1)[0]
    return random.randint(lo, hi)


def _rate_for_age(table: dict, age: int, default: float = 0.05) -> float:
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
    pack_years = round(random.uniform(5, 40), 1) if smoker else 0.0

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
        patient_id         = patient_id,
        day_created        = day_created,
        patient_type       = patient_type,
        destination        = destination,
        age                = age,
        race               = race,
        insurance          = insurance,
        smoker             = smoker,
        pack_years         = pack_years,
        bmi                = bmi,
        has_cervix         = has_cervix,
        hpv_positive       = hpv_positive,
        hpv_vaccinated     = hpv_vaccinated,
        prior_abnormal_pap = prior_abnormal_pap,
        prior_cin          = prior_cin,
    )
