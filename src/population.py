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

# ── NYC census-calibrated distributions ───────────────────────────────────────
# Yutong's code — census-sourced parameters replacing prior placeholders.
# See notebooks/archive/generator.ipynb for full sourcing and methodology.

# Age distribution: NY female, 21+, single-year-of-age probabilities
# Source: Census SC-EST2024-AGESEX-CIV (civilian population by single year of age and sex)
_AGE_VALUES = list(range(21, 86))  # 21–85; bucket 85 represents 85+
_AGE_PROBS = [
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
# Normalise (source already sums to ~1, but guard against float drift)
_age_total = sum(_AGE_PROBS)
_AGE_PROBS = [p / _age_total for p in _AGE_PROBS]

# Race / ethnicity — two-stage draw matching Census methodology
# Source: Census SC-EST2024-SR11H (NY female by sex, race, Hispanic origin)
_P_HISPANIC     = 2030943 / 10161956
_P_NON_HISPANIC = 8131013 / 10161956

_RACE_PROBS_NON_HISP = {
    "White":        5361976 / 8131013,
    "Black":        1504583 / 8131013,
    "AIAN":           30664 / 8131013,
    "Asian":        1023010 / 8131013,
    "NHPI":            5269 / 8131013,
    "Two or More":   205511 / 8131013,
}

_RACE_PROBS_HISP = {
    "White":        1460416 / 2030943,
    "Black":         360471 / 2030943,
    "AIAN":           83440 / 2030943,
    "Asian":          23744 / 2030943,
    "NHPI":            9962 / 2030943,
    "Two or More":    92910 / 2030943,
}

# Insurance status by age band — female-only, age 21+
# Source: ACS B27001 (https://data.census.gov/table/ACSDT1Y2022.B27001)
_INSURANCE_BY_AGE = {
    (21, 25): {"Insured": 13190608, "Uninsured": 1872271},
    (26, 34): {"Insured": 18118047, "Uninsured": 2411358},
    (35, 44): {"Insured": 20435569, "Uninsured": 2248259},
    (45, 54): {"Insured": 18622090, "Uninsured": 1805871},
    (55, 64): {"Insured": 19717410, "Uninsured": 1476065},
    (65, 74): {"Insured": 18473038, "Uninsured":  179909},
    (75, 99): {"Insured": 13985406, "Uninsured":   75089},
}

_INSURANCE_PROBS = {}
for _band, _vals in _INSURANCE_BY_AGE.items():
    _total = _vals["Insured"] + _vals["Uninsured"]
    _INSURANCE_PROBS[_band] = _vals["Insured"] / _total

# Smoking rate — NYS BRFSS 2023
_SMOKER_RATE       = 0.109

_HPV_POSITIVE_RATE = 0.25    # among unvaccinated women with cervix, PLACEHOLDER

_HPV_VAX_RATE = {            # vaccination coverage by age cohort, PLACEHOLDER
    (21, 29): 0.60,
    (30, 39): 0.40,
    (40, 49): 0.20,
    (50, 99): 0.05,
}

# Hysterectomy prevalence by age band AND race/ethnicity group
# Source: CDC/BRFSS 2018 (https://stacks.cdc.gov/view/cdc/113157/cdc_113157_DS1.pdf)
_HYSTERECTOMY_BY_GROUP = {
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

# BMI mixture model — calibrated to NYC 27.6% obesity rate
# Source: NYC Health obesity indicator (https://a816-dohbesp.nyc.gov/IndicatorPublic/data-explorer/overweight/)
_BMI_OBESITY_RATE    = 0.276
_BMI_NONOBES_MU      = 24.7
_BMI_NONOBES_SIGMA   = 3.2
_BMI_OBESE_MU        = 34.8
_BMI_OBESE_SIGMA     = 4.5

# Replacement-patient age distribution — younger-skewing inflow
# ─────────────────────────────────────────────────────────────────────────────
# When a patient exits the established pool (mortality, aged out), their
# replacement represents a NEW patient entering the health system: a woman
# turning 21, a new mover, someone newly establishing care. In real life this
# inflow skews younger than the Census stock distribution (mean ~51). Using
# the stock distribution for replacements causes the pool to age over time
# because replacements enter too old to offset survivors aging in place.
#
# This bracket distribution (mean ~33) maintains demographic equilibrium
# across the 70-year simulation horizon.
_REPLACEMENT_AGE_BRACKETS = [
    ((21, 29), 0.40),   # women aging into eligibility, new movers
    ((30, 39), 0.30),   # establishing care, post-pregnancy re-engagement
    ((40, 49), 0.15),   # mid-career provider switches
    ((50, 59), 0.10),   # later-life re-engagement
    ((60, 69), 0.04),   # Medicare transitions
    ((70, 85), 0.01),   # rare late-life new patients
]


# ── Internal helpers ───────────────────────────────────────────────────────────

def _weighted_choice(dist: dict):
    """Draw one key from a {key: weight} dict using weighted random selection."""
    keys    = list(dist.keys())
    weights = list(dist.values())
    return random.choices(keys, weights=weights, k=1)[0]


def _sample_age() -> int:
    """
    Sample a patient age from the Census single-year-of-age distribution.

    Draws from the empirical categorical distribution (ages 21–85).
    The age-85 bucket represents 85+ in the Census source; patients who
    draw 85 are expanded to 85–100 using a clipped Normal(89, 4).
    """
    age = random.choices(_AGE_VALUES, weights=_AGE_PROBS, k=1)[0]
    if age == 85:
        # Expand 85+ bucket — Yutong's code: clipped Normal(89, 4) → 85-100
        age = int(round(random.gauss(89.0, 4.0)))
        age = max(85, min(100, age))
    return age


def _sample_replacement_age() -> int:
    """
    Sample an age for a replacement patient entering the established pool.

    Uses a younger-skewing distribution (mean ~33) rather than the full
    Census stock distribution (mean ~51) to maintain demographic equilibrium.
    """
    brackets, weights = zip(*_REPLACEMENT_AGE_BRACKETS)
    (lo, hi) = random.choices(brackets, weights=weights, k=1)[0]
    return random.randint(lo, hi)


def _sample_race_ethnicity() -> tuple:
    """
    Two-stage race/ethnicity draw matching Census methodology.

    Returns (race, ethnicity) where ethnicity is "Hispanic" or "Non-Hispanic"
    and race is one of the Census 6 race groups.
    """
    if random.random() < _P_HISPANIC:
        ethnicity = "Hispanic"
        race = _weighted_choice(_RACE_PROBS_HISP)
    else:
        ethnicity = "Non-Hispanic"
        race = _weighted_choice(_RACE_PROBS_NON_HISP)
    return race, ethnicity


def _hysterectomy_group(race: str, ethnicity: str) -> str:
    """Map race/ethnicity to the hysterectomy lookup group."""
    if ethnicity == "Hispanic":
        return "Hispanic"
    if race in ("White", "Black", "Asian"):
        return race
    return "Other"


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


def _sample_insurance(age: int) -> str:
    """Draw Insured/Uninsured from the ACS age-band distribution."""
    p_insured = 0.95  # fallback
    for (lo, hi), prob in _INSURANCE_PROBS.items():
        if lo <= age <= hi:
            p_insured = prob
            break
    return "Insured" if random.random() < p_insured else "Uninsured"


def _sample_bmi() -> float:
    """Draw BMI from the NYC-calibrated mixture model (27.6% obesity rate)."""
    if random.random() < _BMI_OBESITY_RATE:
        bmi = random.gauss(_BMI_OBESE_MU, _BMI_OBESE_SIGMA)
        bmi = max(30.0, min(60.0, bmi))
    else:
        bmi = random.gauss(_BMI_NONOBES_MU, _BMI_NONOBES_SIGMA)
        bmi = max(16.0, min(29.9, bmi))
    return round(bmi, 1)


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
    age              = _sample_age()
    race, ethnicity  = _sample_race_ethnicity()
    insurance        = _sample_insurance(age)

    smoker     = random.random() < _SMOKER_RATE
    # ~30% of ever-smokers are former smokers; sample years since quitting (0–30 yrs)
    is_former  = (not smoker) and (random.random() < 0.30)
    pack_years = round(random.uniform(5, 40), 1) if (smoker or is_former) else 0.0
    years_since_quit = round(random.uniform(0, 30), 1) if is_former else 0.0

    bmi = _sample_bmi()

    hpv_vaccinated    = random.random() < _rate_for_age(_HPV_VAX_RATE, age)
    hpv_positive      = (not hpv_vaccinated) and (random.random() < _HPV_POSITIVE_RATE)

    # Hysterectomy — age x race/ethnicity stratified (Yutong's code, BRFSS 2018)
    hyst_group        = _hysterectomy_group(race, ethnicity)
    hyst_table        = _HYSTERECTOMY_BY_GROUP[hyst_group]
    hysterectomy_prob = _rate_for_age(hyst_table, age, default=0.35)
    has_cervix        = random.random() > hysterectomy_prob

    prior_abnormal_pap = has_cervix and (random.random() < 0.12)
    prior_cin = None
    if prior_abnormal_pap and random.random() < 0.30:
        prior_cin = random.choice(["CIN1", "CIN2"])

    return _build_patient(patient_id, day_created, patient_type, destination,
                          age, race, ethnicity, insurance, smoker, pack_years,
                          years_since_quit, bmi, has_cervix, hpv_positive,
                          hpv_vaccinated, prior_abnormal_pap, prior_cin)


def sample_replacement_patient(
    patient_id:   int,
    day_created:  int,
    destination:  str,
    patient_type: str,
) -> Patient:
    """
    Draw a replacement patient using a younger-skewing age distribution.

    Identical to sample_patient except age is drawn from
    _REPLACEMENT_AGE_BRACKETS (mean ~33) instead of the Census stock
    distribution (mean ~51). This prevents the established pool from
    aging over the 70-year simulation horizon.
    """
    age              = _sample_replacement_age()
    race, ethnicity  = _sample_race_ethnicity()
    insurance        = _sample_insurance(age)

    smoker     = random.random() < _SMOKER_RATE
    is_former  = (not smoker) and (random.random() < 0.30)
    pack_years = round(random.uniform(5, 40), 1) if (smoker or is_former) else 0.0
    years_since_quit = round(random.uniform(0, 30), 1) if is_former else 0.0

    bmi = _sample_bmi()

    hpv_vaccinated    = random.random() < _rate_for_age(_HPV_VAX_RATE, age)
    hpv_positive      = (not hpv_vaccinated) and (random.random() < _HPV_POSITIVE_RATE)

    hyst_group        = _hysterectomy_group(race, ethnicity)
    hyst_table        = _HYSTERECTOMY_BY_GROUP[hyst_group]
    hysterectomy_prob = _rate_for_age(hyst_table, age, default=0.35)
    has_cervix        = random.random() > hysterectomy_prob

    prior_abnormal_pap = has_cervix and (random.random() < 0.12)
    prior_cin = None
    if prior_abnormal_pap and random.random() < 0.30:
        prior_cin = random.choice(["CIN1", "CIN2"])

    return _build_patient(patient_id, day_created, patient_type, destination,
                          age, race, ethnicity, insurance, smoker, pack_years,
                          years_since_quit, bmi, has_cervix, hpv_positive,
                          hpv_vaccinated, prior_abnormal_pap, prior_cin)


def _build_patient(patient_id, day_created, patient_type, destination,
                   age, race, ethnicity, insurance, smoker, pack_years,
                   years_since_quit, bmi, has_cervix, hpv_positive,
                   hpv_vaccinated, prior_abnormal_pap, prior_cin) -> Patient:
    """Shared Patient constructor for sample_patient and sample_replacement_patient."""
    return Patient(
        patient_id           = patient_id,
        day_created          = day_created,
        patient_type         = patient_type,
        destination          = destination,
        age                  = age,
        race                 = race,
        ethnicity            = ethnicity,
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
    Return the BASE annual probability of death for a woman of the given age.

    Values come from cfg.ANNUAL_MORTALITY_RATE, which is keyed on (lo, hi)
    inclusive age brackets calibrated to NCHS Life Tables 2020. Returns 0.40
    for ages above the highest bracket (≥100 is handled by a hard cap in
    draw_mortality, so this fallback only applies to ages 100+).

    This is the non-smoking base rate. Smoking adjustment is applied in
    draw_mortality() using cfg.SMOKER_MORTALITY_MULTIPLIER.
    """
    for (lo, hi), rate in cfg.ANNUAL_MORTALITY_RATE.items():
        if lo <= age <= hi:
            return rate
    return 0.40   # extreme elderly fallback (ages above table ceiling)


def draw_mortality(p: "Patient", sweep_days: int = cfg.MORTALITY_CHECK_DAYS) -> bool:
    """
    Bernoulli draw: does this patient die during the current sweep interval?

    Scales the annual mortality rate to the sweep interval length so that the
    expected number of deaths per year is correct regardless of sweep frequency:

        p(die in N days) ≈ annual_rate × (N / 365)

    Smoking adjustment: current smokers get cfg.SMOKER_MORTALITY_MULTIPLIER
    (default 2.5x); former smokers with pack-year history get
    cfg.FORMER_SMOKER_MORTALITY_MULTIPLIER (default 1.4x). This models the
    well-documented excess all-cause mortality among smokers (Jha et al. 2013).

    Parameters
    ----------
    p           : Patient object (must have .age, .smoker, .pack_years attributes)
    sweep_days  : number of days since last mortality check (default from config)

    Returns
    -------
    True  → patient dies; caller should call p.exit_system(day, "mortality")
    False → patient survives this interval
    """
    if p.age >= 100:
        return True   # hard cap — no patient survives past age 100

    annual_prob = get_mortality_prob(p.age)

    # Smoking-adjusted mortality
    if p.smoker:
        annual_prob *= cfg.SMOKER_MORTALITY_MULTIPLIER
    elif getattr(p, "pack_years", 0) > 0:
        annual_prob *= cfg.FORMER_SMOKER_MORTALITY_MULTIPLIER

    # Cap at 1.0 (can happen for very old smokers)
    annual_prob = min(annual_prob, 1.0)

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
