# =============================================================================
# model.py
# NYP Women's Health Screening Simulation — Core Model
# =============================================================================
#
# This file merges the four simulation-logic modules into a single file:
#   1. Population   — patient factory and life-event scheduling
#   2. Screening    — eligibility, test assignment, result draws
#   3. Follow-up    — post-screening clinical pathways (cervical + lung)
#   4. Metrics      — metric collection, aggregation, revenue analysis
#
# Backward-compatible shims in population.py, screening.py, followup.py,
# and metrics.py re-export every public name from this file, so existing
# imports continue to work.
# =============================================================================

import math
import random
from collections import defaultdict
from typing import List, Optional

import parameters as cfg
from patient import Patient

# Analysis warmup cutoff — metrics recorded before this day are excluded
_WARMUP_DAY = cfg.WARMUP_YEARS * cfg.DAYS_PER_YEAR


# #############################################################################
#                                                                             #
#   ═══════════════════════  POPULATION  ═══════════════════════               #
#   Patient factory — demographic sampling and life-event scheduling          #
#                                                                             #
# #############################################################################

# All population-attribute distributions now live in parameters.py under
# "POPULATION ATTRIBUTE DISTRIBUTIONS". They are read here as cfg.AGE_VALUES,
# cfg.SMOKER_RATE, cfg.HYSTERECTOMY_BY_GROUP, etc.


# ── Internal helpers ───────────────────────────────────────────────────────────

def _weighted_choice(dist: dict):
    """Draw one key from a {key: weight} dict using weighted random selection."""
    keys    = list(dist.keys())
    weights = list(dist.values())
    return random.choices(keys, weights=weights, k=1)[0]


def _sample_age(age_range: tuple = None, age_weights: dict = None) -> int:
    """
    Sample a patient age.

    If age_weights is provided — a {(lo, hi): weight} dict — draw a bucket
    by weight and then draw a uniform integer age within that bucket. This
    is how new arrivals are sampled (skew toward middle age).

    Otherwise, draw from the Census single-year-of-age empirical distribution
    (ages 21–85; the 85 bucket represents 85+ and is expanded to 85–100 via
    a clipped Normal(89, 4)). If age_range is provided as (lo, hi), the
    Census draw is constrained by rejection sampling. This path is used for
    the established pool, which should mirror the general NYC population.
    """
    if age_weights is not None:
        buckets = list(age_weights.keys())
        weights = list(age_weights.values())
        lo, hi = random.choices(buckets, weights=weights, k=1)[0]
        return random.randint(lo, hi)

    for _ in range(100):  # rejection sampling with safety limit
        age = random.choices(cfg.AGE_VALUES, weights=cfg.AGE_PROBS, k=1)[0]
        if age == 85:
            age = int(round(random.gauss(89.0, 4.0)))
            age = max(85, min(100, age))
        if age_range is None or (age_range[0] <= age <= age_range[1]):
            return age
    # Fallback: uniform within range
    if age_range:
        return random.randint(age_range[0], age_range[1])
    return age


def _sample_race_ethnicity() -> tuple:
    """
    Two-stage race/ethnicity draw matching Census methodology.

    Returns (race, ethnicity) where ethnicity is "Hispanic" or "Non-Hispanic"
    and race is one of the Census 6 race groups.
    """
    if random.random() < cfg.P_HISPANIC:
        ethnicity = "Hispanic"
        race = _weighted_choice(cfg.RACE_PROBS_HISP)
    else:
        ethnicity = "Non-Hispanic"
        race = _weighted_choice(cfg.RACE_PROBS_NON_HISP)
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
    for (lo, hi), prob in cfg.INSURANCE_PROBS.items():
        if lo <= age <= hi:
            p_insured = prob
            break
    return "Insured" if random.random() < p_insured else "Uninsured"


def _sample_bmi() -> float:
    """Draw BMI from the NYC-calibrated mixture model (27.6% obesity rate)."""
    if random.random() < cfg.BMI_OBESITY_RATE:
        bmi = random.gauss(cfg.BMI_OBESE_MU, cfg.BMI_OBESE_SIGMA)
        bmi = max(30.0, min(60.0, bmi))
    else:
        bmi = random.gauss(cfg.BMI_NONOBES_MU, cfg.BMI_NONOBES_SIGMA)
        bmi = max(16.0, min(29.9, bmi))
    return round(bmi, 1)


# ── Public interface ─────────────────────────────────────────────────────────

def sample_patient(
    patient_id:   int,
    day_created:  int,
    destination:  str,
    patient_type: str,
    age_range:    tuple = None,
    age_weights:  dict = None,
) -> Patient:
    """
    Draw one patient from the NYC eligible women population.

    Parameters
    ----------
    patient_id   : unique identifier
    day_created  : simulation day the patient enters the system
    destination  : first provider — "pcp"|"gynecologist"|"specialist"|"er"
    patient_type : "outpatient" | "drop_in"
    age_range    : optional (lo, hi) to constrain the Census age draw
    age_weights  : optional {(lo, hi): weight} for bucketed age sampling
                   (used for new arrivals; takes precedence over age_range)

    Returns
    -------
    Patient object with demographics and clinical flags set.
    """
    age              = _sample_age(age_range=age_range, age_weights=age_weights)
    race, ethnicity  = _sample_race_ethnicity()
    insurance        = _sample_insurance(age)

    smoker     = random.random() < cfg.SMOKER_RATE
    # ~30% of ever-smokers are former smokers; sample years since quitting (0–30 yrs)
    is_former  = (not smoker) and (random.random() < 0.30)
    pack_years = round(random.uniform(5, 40), 1) if (smoker or is_former) else 0.0
    years_since_quit = round(random.uniform(0, 30), 1) if is_former else 0.0

    bmi = _sample_bmi()

    hpv_vaccinated    = random.random() < _rate_for_age(cfg.HPV_VAX_RATE, age)
    hpv_rate          = _rate_for_age(cfg.HPV_POSITIVE_RATE, age, default=0.10)
    hpv_positive      = (not hpv_vaccinated) and (random.random() < hpv_rate)

    # Hysterectomy — age x race/ethnicity stratified (Yutong's code, BRFSS 2018)
    hyst_group        = _hysterectomy_group(race, ethnicity)
    hyst_table        = cfg.HYSTERECTOMY_BY_GROUP[hyst_group]
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
    """Shared Patient constructor."""
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
# Life-event scheduling — independent of visits
# =============================================================================
# All patient-attribute events (death, attrition, smoking cessation, HPV
# clearance) are drawn once at patient entry and placed into a life-event
# queue.  Each event fires on its scheduled day whether or not the patient
# has any upcoming clinical visits.  This eliminates batch sweeps and
# produces smooth, continuous event distributions.
# =============================================================================


def draw_death_day(p: "Patient", entry_day: int, extra_multiplier: float = 1.0) -> int:
    """
    Draw a death day from the Gompertz hazard conditional on current age.

    Gompertz survival:  S(t|x) = exp[ -(a/b) * e^(bx) * (e^(bt) - 1) ]
    where x = current age, t = additional years survived.

    We invert S(t|x) = U  (U ~ Uniform(0,1)) to get t, then convert to
    a simulation day.

    Risk multipliers scale `a` upward (earlier expected death) and stack
    multiplicatively — standard proportional-hazards composition:
      - Smoking status (current / former / never)
      - Obesity (BMI ≥ cfg.BMI_OBESE_THRESHOLD)
      - Optional `extra_multiplier` for disease-specific competing-risk
        draws (e.g. post-CIN2/3 LEEP).
    """
    a = cfg.GOMPERTZ_A * extra_multiplier
    b = cfg.GOMPERTZ_B

    # Smoking-adjusted baseline
    if p.smoker:
        a *= cfg.SMOKER_MORTALITY_MULTIPLIER
    elif getattr(p, "pack_years", 0) > 0:
        a *= cfg.FORMER_SMOKER_MORTALITY_MULTIPLIER

    # Obesity-adjusted baseline (stacks with smoking)
    if getattr(p, "bmi", 0.0) >= cfg.BMI_OBESE_THRESHOLD:
        a *= cfg.OBESE_MORTALITY_MULTIPLIER

    age = p.age
    # Hard cap
    max_remaining_years = max(0, cfg.MORTALITY_AGE_CAP - age)
    if max_remaining_years <= 0:
        return entry_day  # dies immediately

    # Inverse CDF:  t = (1/b) * ln(1 - (b * ln(U)) / (a * e^(b*x)))
    # where U ~ Uniform(0,1).  If the argument to ln is ≤ 0, the patient
    # "survives" beyond the Gompertz horizon — cap at max_remaining_years.
    u = random.random()
    # Avoid log(0)
    if u == 0.0:
        u = 1e-15

    inner = 1.0 - (b * math.log(u)) / (a * math.exp(b * age))
    if inner <= 0:
        remaining_years = max_remaining_years
    else:
        remaining_years = min((1.0 / b) * math.log(inner), max_remaining_years)

    remaining_days = int(remaining_years * 365)
    return entry_day + remaining_days


def draw_attrition_day(entry_day: int) -> tuple:
    """
    Draw an attrition day and sub-type from competing EXIT_SOURCES.

    Uses the combined rate (sum of sub-rates) for the time draw, then assigns
    the sub-type proportional to individual rates (standard competing risks).

    Returns (day: int, subtype: str).
    """
    rate = cfg.ANNUAL_ATTRITION_RATE
    if rate <= 0:
        return (entry_day + 999 * 365, "relocation")  # effectively never
    years = random.expovariate(rate)
    day = entry_day + int(years * 365)

    # Assign sub-type proportional to individual rates
    sources  = list(cfg.EXIT_SOURCES.keys())
    weights  = [cfg.EXIT_SOURCES[s]["annual_rate"] for s in sources]
    subtype  = random.choices(sources, weights=weights, k=1)[0]
    return (day, subtype)


def draw_cessation_day(entry_day: int) -> int:
    """
    Draw the day a current smoker quits, from Exponential(ANNUAL_SMOKING_CESSATION_PROB).
    Only called for patients who are smokers at entry.
    """
    rate = cfg.ANNUAL_SMOKING_CESSATION_PROB
    if rate <= 0:
        return entry_day + 999 * 365
    years = random.expovariate(rate)
    return entry_day + int(years * 365)


def draw_hpv_clearance_day(p: "Patient", entry_day: int) -> int:
    """
    Draw the day an HPV-positive patient clears the infection.

    Rate is age-stratified (ages 21–29 clear faster; 30+ persist longer)
    and picked once at entry using p.age. Exponential waiting-time draw.
    """
    rates = cfg.ANNUAL_HPV_CLEARANCE_PROB
    rate  = rates["young"] if p.age < 30 else rates["middle"]
    if rate <= 0:
        return entry_day + 999 * 365
    years = random.expovariate(rate)
    return entry_day + int(years * 365)


def _sample_established_destination() -> str:
    """
    Sample a primary-care provider for an established (cycling) patient.

    Established patients have a regular provider — PCP, gynecologist, or
    specialist. They are never assigned to the ER as a primary destination;
    ER visits are unplanned and handled via the drop-in flow. Redistributes
    ER's weight proportionally across the three non-ER providers.
    """
    # Use DESTINATION_PROBS_OUTPATIENT (specialist=0, er=0) — NOT the
    # legacy DESTINATION_PROBS which still has specialist at 20%.
    non_er = {k: v for k, v in cfg.DESTINATION_PROBS_OUTPATIENT.items()
              if k != "er" and v > 0}
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
    n         : number of established patients to generate (cfg.INITIAL_POOL_SIZE)
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


# #############################################################################
#                                                                             #
#   ═══════════════════════  SCREENING  ════════════════════════               #
#   Eligibility checks, test assignment, and result draws.                    #
#                                                                             #
# #############################################################################

# ─── Eligibility ──────────────────────────────────────────────────────────────

def is_eligible_cervical(p: Patient) -> bool:
    """
    Return True if the patient meets USPSTF cervical screening eligibility criteria.

    Criteria: age 21–65 AND anatomically has a cervix (no prior hysterectomy).
    Age bounds are read from config.ELIGIBILITY['cervical'] so they can be
    adjusted without touching this function.
    """
    e = cfg.ELIGIBILITY["cervical"]
    return e["age_min"] <= p.age <= e["age_max"] and p.has_cervix


def is_eligible_lung(p: Patient) -> bool:
    """
    USPSTF 2021 lung screening criteria — all three must be true:
      - Age 50–80
      - At least 20 pack-years of smoking history
      - Currently smoking OR quit within the last 15 years
    """
    e = cfg.ELIGIBILITY["lung"]
    age_ok     = e["age_min"] <= p.age <= e["age_max"]
    pack_ok    = p.pack_years >= e["min_pack_years"]
    # Current smoker counts; former smoker counts only if quit recently enough
    smoking_ok = p.smoker or (p.years_since_quit <= e["max_years_since_quit"])
    return age_ok and pack_ok and smoking_ok


# Maps each cancer name to its eligibility function — used to loop over all cancers
ELIGIBILITY_CHECKS = {
    "cervical": is_eligible_cervical,
    "lung":     is_eligible_lung,
}


def get_eligible_screenings(p: Patient) -> List[str]:
    """
    Return the list of cancer types this patient is eligible to be screened for today.

    Loops over ACTIVE_CANCERS (set in config) and runs each cancer's eligibility
    function. Returns only those where the patient passes. The result may be empty
    (patient is ineligible for everything today) or contain multiple cancers (patient
    is due for both cervical and lung on the same visit).
    """
    return [
        cancer for cancer, check in ELIGIBILITY_CHECKS.items()
        if cancer in cfg.ACTIVE_CANCERS and check(p)
    ]


# ─── Screening Interval Check ─────────────────────────────────────────────────

# Maps cancer name to the Patient attribute that stores the last screening date
_LAST_SCREEN_FIELD = {
    "cervical": "last_cervical_screen_day",
    "lung":     "last_lung_screen_day",
}


def is_due_for_screening(p: Patient, cancer: str, current_day: int) -> bool:
    """
    Return True if enough time has passed since this patient's last screen.

    A patient is always due if they have never been screened (last_day = -1).
    Otherwise, the interval depends on the test modality used at the last visit:
      cytology  → 3 years   HPV-alone → 5 years   LDCT → 1 year

    For cervical cancer the modality is read from p.last_cervical_screening_test
    (written by run_screening_step at the time of the actual screening).  This
    makes the check deterministic: using assign_screening_test() here would call
    random.choice() again and could return a different test than the one that was
    actually performed, giving different — and wrong — interval decisions across
    calls on the same day.

    Falls back to cytology (the shorter 3-year interval) if no test has been
    recorded yet, which guarantees we never delay a patient beyond their due date.
    """
    last_day = getattr(p, _LAST_SCREEN_FIELD[cancer], -1)
    if last_day < 0:
        return True  # never screened — always due

    if cancer == "cervical":
        # Use the test that was actually performed — not a fresh random draw.
        last_test = getattr(p, "last_cervical_screening_test", None) or "cytology"
        interval  = cfg.SCREENING_INTERVALS_DAYS.get(last_test, 365 * 3)
    else:
        test     = assign_screening_test(p, cancer)
        interval = cfg.SCREENING_INTERVALS_DAYS.get(test, 365)

    return (current_day - last_day) >= interval


# ─── Age Stratum ──────────────────────────────────────────────────────────────

def get_cervical_age_stratum(age: int) -> str:
    """
    Classify a patient's age into the USPSTF cervical screening stratum.

    The stratum controls both which tests are offered and which probability
    table is used when drawing a result:
      young  (21–29): cytology only, every 3 years
      middle (30–65): cytology every 3 years OR HPV-alone every 5 years
      older  (66+):   no routine screening if prior history is adequate
    """
    if 21 <= age <= 29:
        return "young"
    elif 30 <= age <= 65:
        return "middle"
    return "older"


# ─── Test Assignment ──────────────────────────────────────────────────────────

def assign_screening_test(p: Patient, cancer: str) -> str:
    """
    Pick the specific test modality for this patient and cancer type.
    For cervical, this is age-stratified (cytology vs. HPV-alone).
    For lung, it's always LDCT.
    Returns 'ineligible' if no test options exist for this patient.
    """
    if cancer == "cervical":
        stratum = get_cervical_age_stratum(p.age)
        options = cfg.SCREENING_TESTS["cervical"].get(stratum, [])
        if not options:
            return "ineligible"
        if stratum == "young":
            return "cytology"
        if stratum == "middle":
            # Age 30–65: weighted 3-way choice per academic center practice patterns
            # Source: AiP Parameters PDF — co-test 0.55, cytology 0.35, hpv_alone 0.10
            options = list(cfg.TEST_TYPE_PROBS_30_65.keys())
            weights = list(cfg.TEST_TYPE_PROBS_30_65.values())
            return random.choices(options, weights=weights, k=1)[0]
        # older stratum has no options (empty list handled above), so fallthrough returns ineligible
        return "ineligible"

    options = cfg.SCREENING_TESTS.get(cancer, [])
    return options[0] if options else "ineligible"


# ─── Result Draws ─────────────────────────────────────────────────────────────

def _adjust_probs(probs: dict, inflate_keys: list, factor: float) -> dict:
    """
    Apply a multiplicative risk adjustment to selected result categories, then renormalise.

    Multiplies each key in inflate_keys by factor, leaving the others unchanged.
    After inflation the whole dict is divided by its sum so all values still add to 1.
    This is how the model represents higher abnormal rates in higher-risk patients:
    HPV positivity inflates all abnormal cytology categories; prior CIN2/3 further
    inflates the high-grade categories specifically.
    """
    adjusted = {k: v * factor if k in inflate_keys else v for k, v in probs.items()}
    total    = sum(adjusted.values())
    return {k: v / total for k, v in adjusted.items()}


def draw_cervical_result(p: Patient, test: str) -> str:
    """
    Randomly draw a cervical screening result based on the test type and patient risk factors.

    Cytology returns one of: NORMAL | ASCUS | LSIL | ASC-H | HSIL
    HPV-alone returns one of: HPV_NEGATIVE | HPV_POSITIVE

    Risk adjustments:
      - HPV positive: inflates all abnormal cytology categories by 1.5×
      - Prior CIN2/CIN3: further inflates high-grade categories (ASC-H, HSIL) by 1.8×
    These multipliers are PLACEHOLDERS — calibrate against NYP data.
    """
    stratum = get_cervical_age_stratum(p.age)

    if test == "co_test":
        # Co-test: use middle-stratum cytology probabilities
        # In practice co-test returns both HPV and cytology; for routing we use the cytology result
        # Source: AiP Parameters PDF — co-test is dominant modality at academic centers for 30–65
        probs = dict(cfg.CERVICAL_RESULT_PROBS["middle_cytology"])
        if p.hpv_positive:
            probs = _adjust_probs(
                probs, inflate_keys=["ASCUS", "LSIL", "ASC-H", "HSIL"],
                factor=cfg.RISK_MULT_HPV_POSITIVE_CYTOLOGY,
            )
        if p.prior_cin in ("CIN2", "CIN3"):
            probs = _adjust_probs(
                probs, inflate_keys=["ASC-H", "HSIL"],
                factor=cfg.RISK_MULT_PRIOR_CIN_HIGHGRADE,
            )
        return random.choices(list(probs.keys()), weights=list(probs.values()), k=1)[0]

    if test == "hpv_alone":
        # HPV-alone test: binary result, no Pap categories
        probs = dict(cfg.CERVICAL_RESULT_PROBS["middle_hpv"])
        if p.hpv_positive:
            # Known HPV carrier → higher chance of testing positive
            probs = _adjust_probs(
                probs, inflate_keys=["HPV_POSITIVE"],
                factor=cfg.RISK_MULT_HPV_POSITIVE_HPV_TEST,
            )

    elif stratum == "young":
        # Age 21–29: cytology only (USPSTF does not recommend HPV testing under 30)
        probs = dict(cfg.CERVICAL_RESULT_PROBS["young"])
        if p.hpv_positive:
            probs = _adjust_probs(
                probs, inflate_keys=["ASCUS", "LSIL", "ASC-H", "HSIL"],
                factor=cfg.RISK_MULT_HPV_POSITIVE_CYTOLOGY,
            )
        if p.prior_cin in ("CIN2", "CIN3"):
            # History of high-grade CIN raises risk of HSIL recurrence
            probs = _adjust_probs(
                probs, inflate_keys=["ASC-H", "HSIL"],
                factor=cfg.RISK_MULT_PRIOR_CIN_HIGHGRADE,
            )

    else:
        # Age 30–65: cytology (middle stratum base rates)
        probs = dict(cfg.CERVICAL_RESULT_PROBS["middle_cytology"])
        if p.hpv_positive:
            probs = _adjust_probs(
                probs, inflate_keys=["ASCUS", "LSIL", "ASC-H", "HSIL"],
                factor=cfg.RISK_MULT_HPV_POSITIVE_CYTOLOGY,
            )
        if p.prior_cin in ("CIN2", "CIN3"):
            probs = _adjust_probs(
                probs, inflate_keys=["ASC-H", "HSIL"],
                factor=cfg.RISK_MULT_PRIOR_CIN_HIGHGRADE,
            )

    return random.choices(list(probs.keys()), weights=list(probs.values()), k=1)[0]


def draw_lung_rads_result() -> str:
    """
    Draw a Lung-RADS v2022 result category from the configured distribution.
    Categories: RADS_0 (incomplete) through RADS_4B_4X (very suspicious).
    PLACEHOLDER — calibrate to NYP LDCT volume data.
    """
    return random.choices(
        list(cfg.LUNG_RADS_PROBS.keys()),
        weights=list(cfg.LUNG_RADS_PROBS.values()),
        k=1,
    )[0]


def run_lung_pre_ldct(
    p: Patient, current_day: int, metrics: Optional[dict] = None
) -> bool:
    """
    Record administrative milestones before an LDCT scan proceeds.

    LTFU is handled exclusively by the queue-based geometric waiting-time
    hazard (in runner.py _check_queue_ltfu), consistent with the cervical
    pathway. This function only tracks metrics milestones — it always
    returns True so the scan proceeds to capacity/slot competition.
    """
    if metrics is not None and current_day >= _WARMUP_DAY:
        metrics["lung_eligible"] += 1

    p.lung_referral_placed = True
    p.log(current_day, "LUNG: LDCT order placed")
    if metrics is not None and current_day >= _WARMUP_DAY:
        metrics["lung_referral_placed"] += 1

    p.lung_ldct_scheduled = True
    p.log(current_day, "LUNG: LDCT scheduled")
    if metrics is not None and current_day >= _WARMUP_DAY:
        metrics["lung_ldct_scheduled"] += 1

    p.log(current_day, "LUNG: LDCT completed")
    if metrics is not None and current_day >= _WARMUP_DAY:
        metrics["lung_ldct_completed"] += 1

    return True


# ─── Main Screening Step ──────────────────────────────────────────────────────

# Maps cancer name to the Patient attribute where the result is stored
_RESULT_FIELD = {
    "cervical": "cervical_result",
    "lung":     "lung_result",
}


def run_screening_step(
    p: Patient, cancer: str, current_day: int,
    metrics: Optional[dict] = None,
    test_override: Optional[str] = None,
) -> Optional[str]:
    """
    Execute one full screening event for the given cancer type.

    Checks eligibility and interval, assigns the test, draws the result,
    and stores it on the patient. For lung, also runs the pre-LDCT pathway
    (referral + scheduling steps) before drawing a result.

    If test_override is provided, that test is used instead of drawing a new
    one via assign_screening_test(). This avoids a double random draw when
    the caller has already assigned the test (e.g. to check slot availability
    before committing to the screening).

    Returns the result string, or None if the patient was skipped or lost.
    """
    # Skip if not eligible (age, cervix, smoking history)
    if not ELIGIBILITY_CHECKS[cancer](p):
        p.log(current_day, f"SKIP {cancer} — not eligible (age={p.age})")
        return None

    # Skip if not enough time has passed since last screen
    if not is_due_for_screening(p, cancer, current_day):
        p.log(current_day, f"SKIP {cancer} — not yet due")
        return None

    test            = test_override or assign_screening_test(p, cancer)
    p.current_stage = "screening"
    p.log(current_day, f"SCREEN {cancer} via {test}")

    if cancer == "cervical":
        result                        = draw_cervical_result(p, test)
        p.cervical_result             = result
        p.last_cervical_screen_day    = current_day
        p.last_cervical_screening_test = test   # persist for deterministic interval check

    elif cancer == "lung":
        # Lung requires referral + scheduling before the scan can happen
        if not run_lung_pre_ldct(p, current_day, metrics):
            return None  # patient was lost before scan
        result                 = draw_lung_rads_result()
        p.lung_result          = result
        p.last_lung_screen_day = current_day
        if metrics is not None:
            metrics["lung_rads_distribution"][result] += 1

    else:
        result = "UNKNOWN"

    p.log(current_day, f"RESULT {cancer}: {result}")
    return result


# ─── Future Eligibility ───────────────────────────────────────────────────────

def days_until_eligible(p: Patient, cancer: str) -> int:
    """
    Return how many days until this patient becomes eligible for the given cancer screen.

    This drives the three-way eligibility routing in the runner:
      0   — eligible right now (call run_screening_step)
      > 0 — will become eligible in ~N days (schedule a return visit)
      -1  — permanently ineligible (no cervix, aged out, never smoked, etc.);
            no return visit should be scheduled — exit the patient silently

    For lung, the function also handles the case where a current smoker will eventually
    accumulate enough pack-years to qualify, returning the estimated wait time.
    """
    if cancer == "cervical":
        e = cfg.ELIGIBILITY["cervical"]
        if not p.has_cervix:
            return -1                                 # hysterectomy or no cervix — will never qualify
        if p.age > e["age_max"]:
            return -1                                 # over 65 — aged out of screening
        if p.age < e["age_min"]:
            return (e["age_min"] - p.age) * 365      # turns 21 in ~N days
        return 0                                      # eligible now

    if cancer == "lung":
        e = cfg.ELIGIBILITY["lung"]
        if p.age > e["age_max"]:
            return -1                                 # over 80 — aged out
        if not p.smoker and p.pack_years == 0:
            return -1                                 # never smoked — can never qualify
        if not p.smoker and p.years_since_quit > e["max_years_since_quit"]:
            return -1                                 # quit too long ago — window closed

        days_needed = 0
        if p.age < e["age_min"]:
            # Must wait until age 50
            days_needed = max(days_needed, (e["age_min"] - p.age) * 365)
        if p.pack_years < e["min_pack_years"]:
            if not p.smoker:
                return -1                             # not smoking now, can't accumulate more pack-years
            # Currently smoking — will reach 20 pack-years in N days (1 pack-year ≈ 365 days)
            packs_needed = e["min_pack_years"] - p.pack_years
            days_needed  = max(days_needed, int(packs_needed * 365))
        return days_needed if days_needed > 0 else 0

    return -1  # unknown cancer type


# #############################################################################
#                                                                             #
#   ═══════════════════════  FOLLOW-UP  ════════════════════════               #
#   Post-screening clinical follow-up pathways (cervical + lung).             #
#                                                                             #
# #############################################################################

# ─── Cervical: Result Routing ─────────────────────────────────────────────────

# Cytology results that always trigger a colposcopy referral
_CYTOLOGY_COLPOSCOPY_TRIGGERS = {"ASCUS", "LSIL", "ASC-H", "HSIL"}

# Results that mean "no abnormality found — return to routine schedule"
_NORMAL_RESULTS = {"NORMAL", "HPV_NEGATIVE"}


def route_cervical_result(
    p: Patient, current_day: int, metrics: Optional[dict] = None
) -> str:
    """
    Decide the clinical next step based on the patient's cervical screening result.

    Cytology (age 21–65):
      NORMAL              → routine surveillance (no follow-up needed)
      ASCUS / LSIL / ASC-H / HSIL → colposcopy referral, subject to LTFU check

    HPV-alone (age 30–65):
      HPV_NEGATIVE        → routine surveillance
      HPV_POSITIVE        → ASCCP triage: ~40% managed with 1-year repeat cytology
                            (low-risk path), ~60% referred directly to colposcopy
                            (PLACEHOLDER split — replace with NYP risk-table values)

    Returns one of:
      "colposcopy"         — patient should proceed to colposcopy
      "one_year_repeat"    — patient gets a 1-year follow-up cytology instead
      "routine_surveillance" — return to normal screening interval
      "exit"               — patient lost to follow-up
    """
    result = p.cervical_result

    # Normal results: no follow-up needed, return to routine schedule
    if result in _NORMAL_RESULTS:
        p.log(current_day, f"ROUTE cervical {result} → routine surveillance")
        return "routine_surveillance"

    # Abnormal cytology: all four categories go to colposcopy
    if result in _CYTOLOGY_COLPOSCOPY_TRIGGERS:
        p.log(current_day, f"ROUTE {result} → colposcopy")
        return "colposcopy"

    # HPV positive: use ASCCP triage split
    if result == "HPV_POSITIVE":
        # HPV+ triage: route to 1-year repeat cytology (lower-risk) vs colposcopy.
        # Split is controlled by config.HPV_POSITIVE_COLPOSCOPY_PROB (PLACEHOLDER).
        if random.random() >= cfg.HPV_POSITIVE_COLPOSCOPY_PROB:
            p.log(current_day, "ROUTE HPV_POSITIVE → 1-year repeat cytology (low-risk mgmt)")
            return "one_year_repeat"
        p.log(current_day, "ROUTE HPV_POSITIVE → colposcopy")
        return "colposcopy"

    # Fallback: unknown result category — treat as normal to avoid hard failures
    p.log(current_day, f"ROUTE unknown result '{result}' → routine surveillance")
    return "routine_surveillance"


# ─── Cervical: Colposcopy ─────────────────────────────────────────────────────

def draw_colposcopy_result(p: Patient) -> str:
    """
    Draw a CIN grade from colposcopy, conditional on the result that triggered it.
    The key format matches COLPOSCOPY_RESULT_PROBS in config (e.g. 'from_HSIL').

    Normal routing should only reach this function with an abnormal
    `p.cervical_result` (ASCUS / LSIL / ASC-H / HSIL / HPV_POSITIVE). If a
    queued colposcopy fires for a patient whose last cervical_result is
    NORMAL/HPV_NEGATIVE (e.g. stale queue entry after post-treatment
    surveillance reset), treat the trigger as "from_ASCUS" (the lowest-
    severity abnormal trigger) rather than crash. This preserves the sim's
    ability to complete while keeping clinical semantics reasonable.
    """
    key = f"from_{p.cervical_result}"
    if key not in cfg.COLPOSCOPY_RESULT_PROBS:
        p.log(
            getattr(p, "last_cervical_screen_day", -1),
            f"COLPOSCOPY fallback — no specific distribution for "
            f"cervical_result='{p.cervical_result}'; using from_ASCUS",
        )
        key = "from_ASCUS"
    probs = cfg.COLPOSCOPY_RESULT_PROBS[key]
    return random.choices(list(probs.keys()), weights=list(probs.values()), k=1)[0]


def run_colposcopy(
    p: Patient, current_day: int, metrics: Optional[dict] = None
) -> Optional[str]:
    """
    Perform a colposcopy procedure and record the CIN grade on the patient.
    Advances the patient's stage to 'followup'.

    CIN grades:
      NORMAL  — no dysplasia found; patient goes to surveillance
      CIN1    — low-grade; typically managed with surveillance
      CIN2/3  — high-grade; treated with excisional procedure (LEEP or cone)

    Returns the CIN grade string.
    """
    p.current_stage     = "followup"
    cin                 = draw_colposcopy_result(p)
    p.colposcopy_result = cin
    p.log(current_day, f"COLPOSCOPY → {cin}")

    # Elevate future-screening risk for confirmed high-grade dysplasia.
    # Patients with prior CIN2/CIN3 carry persistent higher risk for
    # ASC-H/HSIL on subsequent Pap tests, modelled via
    # RISK_MULT_PRIOR_CIN_HIGHGRADE in draw_cervical_result().
    if cin in ("CIN2", "CIN3"):
        p.prior_cin = cin

    if metrics is not None and current_day >= _WARMUP_DAY:
        metrics["n_colposcopy"] += 1
        metrics["colposcopy_results"][cin] += 1

    return cin


# ─── Cervical: Treatment ──────────────────────────────────────────────────────

def assign_treatment_type(cin_grade: str) -> str:
    """
    Map CIN grade to treatment modality using the config table.
    NORMAL/CIN1 → surveillance (watchful waiting)
    CIN2/CIN3   → leep (excisional treatment)
    """
    return cfg.TREATMENT_ASSIGNMENT.get(cin_grade, "surveillance")


def run_treatment(
    p: Patient, current_day: int, metrics: Optional[dict] = None
) -> str:
    """
    Execute the treatment step after colposcopy.

    If CIN grade maps to surveillance (NORMAL or CIN1):
      → patient enters 1-year surveillance schedule

    If CIN grade maps to an excisional procedure (CIN2 or CIN3):
      → patient is treated with LEEP; stage set to 'treated'

    Returns one of: "surveillance" | "treated"
    """
    treatment        = assign_treatment_type(p.colposcopy_result or "NORMAL")
    p.treatment_type = treatment

    if treatment == "surveillance":
        # Low-grade finding — no procedure needed, watch and repeat in 1 year
        p.current_stage = "surveillance"
        p.log(current_day, f"TREATMENT — {p.colposcopy_result}: 1-year surveillance")
        if metrics is not None and current_day >= _WARMUP_DAY:
            metrics["n_treatment"]["surveillance"] += 1
        return "surveillance"

    # High-grade finding — excisional treatment (LEEP or cold-knife cone)
    p.current_stage = "treated"
    p.log(current_day, f"TREATMENT — {treatment} (excisional) for {p.colposcopy_result}")
    if metrics is not None and current_day >= _WARMUP_DAY:
        metrics["n_treatment"][treatment] += 1
        metrics["n_treated"] += 1
    return "treated"


# ─── Cervical: Full Follow-Up Orchestrator ────────────────────────────────────

def run_cervical_followup(
    p: Patient, current_day: int, metrics: Optional[dict] = None
) -> str:
    """
    Main cervical follow-up pipeline. Chains the three steps in order:
      1. Route result → decide next clinical action
      2. Run colposcopy → get CIN grade
      3. Run treatment → excise or surveil

    Returns the final patient disposition:
      "routine_surveillance" — no abnormality / normal HPV → back to schedule
      "one_year_repeat"      — HPV+ low-risk path → 1-yr cytology repeat
      "surveillance"         — CIN1 / normal colposcopy → watchful waiting
      "treated"              — CIN2/3 → excisional treatment completed
      "exit"                 — patient lost to follow-up at some step
    """
    next_step = route_cervical_result(p, current_day, metrics)

    # These dispositions require no further action in this pipeline
    if next_step in ("routine_surveillance", "one_year_repeat", "exit"):
        return next_step

    if next_step == "colposcopy":
        cin = run_colposcopy(p, current_day, metrics)
        if cin is None:
            return "exit"
        # CIN grade known — proceed to treatment assignment
        return run_treatment(p, current_day, metrics)

    return "routine_surveillance"


# ─── Lung: Full Follow-Up Pathway (per clinical flowchart) ───────────────────
#
# Lung-RADS (v2022) post-LDCT disposition:
#
#   RADS 0            → communicate result → repeat LDCT in 1–3 months
#   RADS 1 / RADS 2   → communicate result → repeat LDCT in 12 months
#   RADS 3            → communicate result → repeat LDCT in 6 months
#   RADS 4A / 4B / 4X → communicate result → biopsy pathway → outcome
#
# At every node, communication failure or scheduling failure = LTFU.
# The biopsy pathway has five sequential LTFU nodes before treatment.

# RADS categories that lead to a repeat LDCT (no biopsy needed)
_LUNG_RADS_REPEAT = {"RADS_0", "RADS_1", "RADS_2", "RADS_3"}

# RADS categories that require a biopsy to rule out malignancy
_LUNG_RADS_BIOPSY = {"RADS_4A", "RADS_4B_4X"}


def _lung_result_communicated(p: Patient, current_day: int, metrics: Optional[dict]) -> bool:
    """
    Record that the LDCT result was communicated to the patient.

    Always returns True — LTFU is handled exclusively by the queue-based
    geometric waiting-time hazard, consistent with the cervical pathway.
    """
    if metrics is not None and current_day >= _WARMUP_DAY:
        metrics["lung_result_communicated"] += 1
    p.log(current_day, f"LUNG {p.lung_result}: results communicated to patient")
    return True


def run_lung_followup(
    p: Patient, current_day: int, metrics: Optional[dict] = None
) -> str:
    """
    Full lung follow-up pipeline per the clinical flowchart.

    For RADS 0/1/2/3 (repeat pathway):
      Communicate result → schedule repeat LDCT at the appropriate interval.

    For RADS 4A/4B/4X (biopsy pathway):
      Communicate result → biopsy referral → scheduling → completion
      → malignancy confirmation → treatment.
      LTFU is handled by queue-based geometric waiting-time hazard only
      (consistent with cervical pathway).

    Returns final disposition string:
      "repeat_ldct_12mo"    — RADS 1/2: routine annual repeat
      "repeat_ldct_6mo"     — RADS 3: 6-month repeat
      "repeat_ldct_1_3mo"   — RADS 0: incomplete scan, 1–3 month repeat
      "lung_treated"        — biopsy confirmed malignancy, treatment given
      "exit"                — guard (should not occur; LTFU via queue only)
    """
    rads = p.lung_result

    # ── RADS 0, 1, 2, 3 — communicate result then schedule repeat LDCT ────────
    if rads in _LUNG_RADS_REPEAT:
        if not _lung_result_communicated(p, current_day, metrics):
            return "exit"  # should not happen (always True), but guard

        repeat_days = cfg.LUNG_RADS_REPEAT_INTERVALS.get(rads, 365)
        if rads == "RADS_0":
            # Incomplete scan — must repeat sooner (1–3 months) to get a valid result
            p.log(current_day, f"LUNG {rads}: repeat LDCT in ~{repeat_days} days (1–3 months)")
            return "repeat_ldct_1_3mo"
        elif rads == "RADS_3":
            # Probably benign nodule — recheck in 6 months to confirm stability
            p.log(current_day, f"LUNG {rads}: repeat LDCT in {repeat_days} days (6 months)")
            return "repeat_ldct_6mo"
        else:
            # RADS 1/2: negative or clearly benign — routine annual surveillance
            p.log(current_day, f"LUNG {rads}: repeat LDCT in {repeat_days} days (12 months)")
            return "repeat_ldct_12mo"

    # ── RADS 4A / 4B / 4X — suspicious nodule, biopsy pathway ────────────────
    # LTFU is handled exclusively by queue-based geometric waiting-time hazard
    # (runner.py _check_queue_ltfu). No node-level Bernoulli LTFU here.
    if rads in _LUNG_RADS_BIOPSY:
        if not _lung_result_communicated(p, current_day, metrics):
            return "exit"  # should not happen (always True), but guard

        # Biopsy referral, scheduling, and completion — proceed to slot competition
        if metrics is not None and current_day >= _WARMUP_DAY:
            metrics["lung_biopsy_referral"] += 1
        p.log(current_day, f"LUNG {rads}: biopsy referral placed")

        if metrics is not None and current_day >= _WARMUP_DAY:
            metrics["lung_biopsy_scheduled"] += 1
        p.log(current_day, "LUNG biopsy: scheduled")

        if metrics is not None and current_day >= _WARMUP_DAY:
            metrics["lung_biopsy_completed"] += 1
        p.log(current_day, "LUNG biopsy: completed")

        # Malignancy confirmed by pathology? (clinical result, not LTFU)
        # Per-RADS-tier rate — RADS 4A ≈ 8%, RADS 4B/4X ≈ 35% (Pinsky 2015, ACR)
        malignancy_prob = cfg.LUNG_RADS_MALIGNANCY_RATE.get(rads, 0.25)
        if random.random() > malignancy_prob:
            # Biopsy came back benign — return to annual surveillance
            p.lung_biopsy_result = "benign"
            p.log(current_day, "LUNG biopsy: benign — return to surveillance")
            return "repeat_ldct_12mo"

        p.lung_biopsy_result = "malignant"
        if metrics is not None and current_day >= _WARMUP_DAY:
            metrics["lung_malignancy_confirmed"] += 1
        p.log(current_day, "LUNG biopsy: malignancy confirmed")

        # Treatment — proceeds to slot competition (LTFU via queue hazard only)
        p.current_stage = "treated"
        if metrics is not None and current_day >= _WARMUP_DAY:
            metrics["lung_treatment_given"] += 1
            metrics["n_treated"] += 1
        p.log(current_day, "LUNG: treatment given (surgery / radiation / med onc)")
        return "lung_treated"

    # Fallback — should never be reached if lung_result is always a valid RADS category
    p.log(current_day, f"LUNG: unknown RADS category '{rads}' — surveillance")
    return "repeat_ldct_12mo"


# #############################################################################
#                                                                             #
#   ═══════════════════════  METRICS  ══════════════════════════               #
#   Metric collection, aggregation, revenue analysis.                         #
#                                                                             #
# #############################################################################

def initialize_metrics() -> dict:
    """
    Create and return a fresh metrics dictionary for one simulation run.

    The dict is structured into logical groups:
      - Volume counters: how many patients were seen, eligible, or unscreened.
      - Screening counts: per-cancer screening totals, cervical result distributions.
      - Cervical follow-up: colposcopy counts, CIN grade distribution, treatment types.
      - Outcomes: treated, LTFU totals.
      - LTFU breakdown: how many patients were lost at each specific node.
      - Lung pathway funnel: step-by-step counts from referral through treatment.
      - Wait times: lists of days waited at each resource (for scheduling analysis).

    Call this at the start of each simulation replication so state doesn't carry
    over between runs.
    """
    return {
        # ── Volume ────────────────────────────────────────────────────────────
        "n_patients":     0,
        "n_eligible_any": 0,   # eligible for ≥1 cancer (any)
        "n_eligible":     defaultdict(int),  # cancer → eligible count (per-cancer)
        "n_unscreened":   0,
        "n_reschedule":   0,

        # ── Entry / Arrival breakdown ─────────────────────────────────────────
        # destination: "pcp" | "gynecologist" | "specialist" | "er"
        # patient_type: "outpatient" | "drop_in"
        "entries_by_destination": defaultdict(int),   # provider destination → count
        "entries_by_type":        defaultdict(int),   # patient_type → count

        # ── Exit / Retention breakdown ────────────────────────────────────────
        "exits_by_reason":    defaultdict(int),   # exit_reason string → count
        "days_in_system":     [],                 # list of ints (retention days per patient)
        "days_in_system_screened": [],             # same but only patients with visit_count > 0

        # ── Screenings ────────────────────────────────────────────────────────
        "n_screened":             defaultdict(int),                 # cancer → count (all patients)
        "n_screened_established": defaultdict(int),                # cancer → count (established pool only)
        "n_screened_by_test":     defaultdict(int),                # test modality → count (cytology / hpv_alone / ldct)

        # ── Cervical results ──────────────────────────────────────────────────
        "cervical_results": defaultdict(int),                      # result → count
        "cervical_results_by_test": defaultdict(                   # test → result → count
            lambda: defaultdict(int)                               # e.g. cervical_results_by_test["cytology"]["ASCUS"]
        ),
        "cervical_by_age_stratum": defaultdict(                    # stratum → result → count
            lambda: defaultdict(int)
        ),

        # ── Cervical follow-up ────────────────────────────────────────────────
        "n_colposcopy":       0,
        "colposcopy_results": defaultdict(int),                    # CIN grade → count
        "n_treatment":        defaultdict(int),                    # treatment type → count

        # ── Outcomes ──────────────────────────────────────────────────────────
        "n_treated":   0,
        "n_ltfu":      0,
        "n_exited":    0,

        # ── LTFU by node ──────────────────────────────────────────────────────
        "ltfu_unscreened":          0,
        "ltfu_queue_primary":       0,   # abandoned primary screening retry queue
        "ltfu_queue_secondary":     0,   # abandoned secondary (colposcopy/biopsy) retry queue
        "ltfu_queue_treatment":     0,   # abandoned treatment (LEEP/cone) retry queue

        # ── Lung pathway funnel ───────────────────────────────────────────────
        "lung_eligible":            0,
        "lung_referral_placed":     0,
        "lung_ldct_scheduled":      0,
        "lung_ldct_completed":      0,
        "lung_rads_distribution":   defaultdict(int),   # RADS category → count
        "lung_result_communicated": 0,
        "lung_biopsy_referral":     0,
        "lung_biopsy_scheduled":    0,
        "lung_biopsy_completed":    0,
        "lung_malignancy_confirmed": 0,
        "lung_treatment_given":     0,

        # ── Wait times (days, by resource) ────────────────────────────────────
        "wait_times": defaultdict(list),
        # Abandoned waits: patients who died/attrited while queued for a slot
        "wait_times_abandoned": defaultdict(list),

        # ── Daily screening demand vs capacity ────────────────────────────────
        # Each entry is one workday: (demand, supplied, overflow)
        # demand = patients who attempted a primary screening slot
        # supplied = slots consumed (screening happened)
        # overflow = patients rescheduled (no slot available)
        "daily_screening_demand": [],
        "daily_secondary_demand": [],    # colposcopy + lung_biopsy
        "daily_treatment_demand": [],    # leep + cone_biopsy

        # ── Stable population (only populated when use_stable_population=True) ─
        "mortality_count":    0,   # total patients removed by mortality events
        "pool_size_snapshot": [],  # (day, pool_size) snapshots for longitudinal plot

        # ── Intake queue (unserved demand tracking) ─────────────────────────
        "intake_queue_total":  0,   # cumulative patients added to intake queue
        "intake_queue_served": 0,   # cumulative patients pulled from queue → seen

        # ── Provider capacity ─────────────────────────────────────────────────
        "provider_demand":     0,   # total patients who tried to see a provider
        "provider_served":     0,   # patients seen (within daily cap)
        "provider_overflow":   0,   # patients turned away (rescheduled to next day)
        "daily_provider_demand": [],   # [(demand, served, overflow), ...] per workday

        # ── Multi-source tracking ─────────────────────────────────────────────
        "arrivals_by_source":  defaultdict(int),   # arrival source → count
        "exits_by_source":     defaultdict(int),   # exit source/subtype → count
        # ── Annual checkpoints (one dict per year, for longitudinal plots) ──────
        # Each entry: {year, day, pool_size, cum_cervical, cum_lung,
        #              cum_mortality, cum_colposcopy, cum_treated}
        "year_checkpoints": [],
    }


def record_screening(
    metrics: dict, p: Patient, cancer: str, result: str,
    test: str = "", current_day: int = 0
) -> None:
    """
    Record a completed screening event in the metrics dict.

    Events during the warmup period (day < WARMUP_YEARS * 365) are silently
    skipped — they still happen in the simulation but are excluded from
    analysis metrics.

    Increments the per-cancer screening counter and, for cervical screenings,
    also tallies the result category and the age-stratum breakdown. The stratum
    breakdown is used to verify that the simulation's result distribution matches
    expected rates for young vs. middle-aged women separately.

    The optional `test` parameter (e.g. "cytology", "hpv_alone", "ldct") is used
    to track first-stage screening volume by modality — the primary USPSTF metric.
    Falls back to the patient's last recorded test if not explicitly provided.
    """
    if current_day < _WARMUP_DAY:
        return
    # get_cervical_age_stratum is defined in the SCREENING section above — no import needed
    metrics["n_screened"][cancer] += 1
    if getattr(p, "is_established", False):
        metrics["n_screened_established"][cancer] += 1

    # Track by test modality — infer from patient if not supplied
    if not test and cancer == "cervical":
        test = getattr(p, "last_cervical_screening_test", "") or "cytology"
    elif not test and cancer == "lung":
        test = "ldct"
    if test:
        metrics["n_screened_by_test"][test] += 1

    if cancer == "cervical":
        metrics["cervical_results"][result] += 1
        metrics["cervical_results_by_test"][test][result] += 1
        stratum = get_cervical_age_stratum(p.age)
        metrics["cervical_by_age_stratum"][stratum][result] += 1


def record_exit(metrics: dict, reason: str, patient=None, current_day: int = 0) -> None:
    """
    Record a patient's exit from the system and classify it into an outcome bucket.

    Events during the warmup period (day < WARMUP_YEARS * 365) are silently
    skipped — they still happen in the simulation but are excluded from
    analysis metrics.

    Called whenever a patient's pathway ends, whether through successful treatment,
    voluntary departure without treatment, or LTFU. The reason string comes from
    patient.exit_reason (set by patient.exit_system()) and maps to one of three
    outcome counters: treated or lost_to_followup.

    Optional patient and current_day are used to record retention duration
    (days from patient creation to exit) in metrics["days_in_system"].
    """
    if current_day < _WARMUP_DAY:
        return
    metrics["n_exited"] += 1
    metrics["exits_by_reason"][reason] += 1

    if reason == "treated":
        metrics["n_treated"] += 1
    # n_ltfu is incremented directly in runner._check_queue_ltfu alongside
    # the per-queue breakdown counters — do NOT also increment here to
    # avoid double-counting.

    if patient is not None and current_day > 0:
        retention = current_day - getattr(patient, "day_created", current_day)
        if retention >= 0:
            metrics["days_in_system"].append(retention)
            if getattr(patient, "visit_count", 0) >= 2:
                metrics["days_in_system_screened"].append(retention)


def compute_rates(metrics: dict) -> dict:
    """
    Derive key percentage rates from the raw event counts in the metrics dict.

    Converts raw counts into the rates that appear in the summary report:
    screening rate, abnormal rate, colposcopy completion rate, treatment
    completion rate, and overall LTFU rate. Uses max(..., 1) denominators to
    avoid division-by-zero in runs where a particular event never occurred.

    Notes on correctness:
    - "Abnormal" for cervical means any result that triggers a follow-up action:
      ASCUS, LSIL, ASC-H, HSIL, and HPV_POSITIVE. HPV_NEGATIVE is excluded
      because it is a normal result (patient does not carry high-risk HPV).
    - treatment_completion_pct uses only cervical excisional treatments (LEEP /
      cone biopsy) as the numerator, NOT n_treated, because n_treated also
      accumulates lung malignancy treatments and would produce a rate > 100%
      against the cervical-only colposcopy denominator.
    """
    n     = max(metrics["n_patients"], 1)
    cerv  = max(metrics["n_screened"]["cervical"], 1)
    colpo = max(metrics["n_colposcopy"], 1)

    # Abnormal cervical result: any category that requires follow-up action.
    # HPV_NEGATIVE is explicitly excluded — it is a normal HPV-alone result.
    # NORMAL is excluded too. Everything else (ASCUS, LSIL, ASC-H, HSIL,
    # HPV_POSITIVE) triggers either colposcopy or a 1-year repeat.
    _NORMAL_CERVICAL = {"NORMAL", "HPV_NEGATIVE"}
    total_abnormal = sum(
        v for k, v in metrics["cervical_results"].items()
        if k not in _NORMAL_CERVICAL
    )

    # Cervical excisional treatment count (LEEP + cone biopsy).
    # Used as the numerator for treatment_completion_pct to keep the rate
    # within [0, 100%] against the colposcopy denominator.
    cerv_excisional = (
        metrics["n_treatment"].get("leep", 0)
        + metrics["n_treatment"].get("cone_biopsy", 0)
    )

    n_cytol    = metrics["n_screened_by_test"]["cytology"]
    n_hpv      = metrics["n_screened_by_test"]["hpv_alone"]
    n_ldct     = metrics["n_screened_by_test"]["ldct"]
    total_cerv = max(n_cytol + n_hpv, 1)

    return {
        # ── First-stage screening uptake (primary USPSTF metric) ───────────
        "n_cytology":                  n_cytol,
        "n_hpv_alone":                 n_hpv,
        "n_ldct":                      n_ldct,
        "cytology_pct_of_cerv":        100 * n_cytol / total_cerv,
        "hpv_alone_pct_of_cerv":       100 * n_hpv   / total_cerv,
        "screening_rate_cervical_pct": 100 * metrics["n_screened"]["cervical"] / n,
        "screening_rate_lung_pct":     100 * metrics["n_screened"]["lung"] / max(metrics["lung_eligible"], 1),
        # ── Downstream clinical rates (validation / secondary) ─────────────
        "unscreened_pct":              100 * metrics["n_unscreened"] / n,
        "reschedule_rate_pct":         100 * metrics["n_reschedule"] / max(metrics["n_unscreened"], 1),
        "abnormal_rate_cervical_pct":  100 * total_abnormal / cerv,
        "colposcopy_completion_pct":   100 * metrics["n_colposcopy"] / max(total_abnormal, 1),
        # Fraction of colposcopy patients who went on to excisional treatment.
        # Patients with CIN1/NORMAL colposcopy result are placed on surveillance
        # and are correctly excluded from this numerator.
        "treatment_completion_pct":    100 * cerv_excisional / max(colpo, 1),
        "ltfu_rate_pct":               100 * metrics["n_ltfu"] / n,
    }


def print_summary(metrics: dict) -> None:
    """
    Print a formatted summary of the simulation results to stdout.

    Covers all major pipeline sections in order: patient volumes, screening
    counts by cancer, cervical result distribution (with age-stratum breakdown),
    colposcopy and treatment counts, outcome totals, LTFU breakdown by node,
    and the full lung LDCT pathway funnel. Calls compute_rates() internally
    to derive the percentage columns.
    """
    rates = compute_rates(metrics)

    print("=" * 65)
    print("NYP WOMEN'S HEALTH SCREENING SIMULATION — RESULTS")
    print("=" * 65)

    print(f"\n{'Patients simulated:':<40} {metrics['n_patients']:>8,}")
    print(f"{'Eligible for ≥1 screening:':<40} {metrics['n_eligible_any']:>8,}")
    print(f"{'Unscreened (declined / no-show):':<40} {metrics['n_unscreened']:>8,}  "
          f"({rates['unscreened_pct']:.1f}%)")
    print(f"{'  ↳ agreed to reschedule:':<40} {metrics['n_reschedule']:>8,}  "
          f"({rates['reschedule_rate_pct']:.1f}% of unscreened)")

    print("\nScreenings completed by cancer type:")
    for cancer, count in sorted(metrics["n_screened"].items()):
        print(f"  {cancer:<22} {count:>8,}")

    if metrics["cervical_results"]:
        total_cerv = sum(metrics["cervical_results"].values())
        print(f"\nCervical result distribution  (n={total_cerv:,}):")
        for result, count in sorted(metrics["cervical_results"].items()):
            pct = 100 * count / max(total_cerv, 1)
            print(f"  {result:<30} {count:>6,}  ({pct:.1f}%)")
        print(f"  {'Abnormal rate:':<30} {rates['abnormal_rate_cervical_pct']:.1f}%")

    if metrics["cervical_by_age_stratum"]:
        print("\nCervical results by age stratum:")
        for stratum in ("young", "middle", "older"):
            sub = metrics["cervical_by_age_stratum"].get(stratum, {})
            if sub:
                total_s = sum(sub.values())
                print(f"  {stratum}  (n={total_s:,})")
                for result, count in sorted(sub.items()):
                    print(f"    {result:<28} {count:>6,}")

    print(f"\nColposcopies performed:           {metrics['n_colposcopy']:>8,}  "
          f"({rates['colposcopy_completion_pct']:.1f}% of abnormals)")
    if metrics["colposcopy_results"]:
        print("  CIN grade distribution:")
        for grade, count in sorted(metrics["colposcopy_results"].items()):
            print(f"    {grade:<12} {count:>6,}")

    if metrics["n_treatment"]:
        print("\nTreatments by type:")
        for ttype, count in sorted(metrics["n_treatment"].items()):
            print(f"  {ttype:<22} {count:>8,}")

    print(f"\nOutcomes:")
    print(f"  {'Treated:':<38} {metrics['n_treated']:>6,}  "
          f"({rates['treatment_completion_pct']:.1f}% of colposcopies)")
    print(f"  {'Lost to follow-up:':<38} {metrics['n_ltfu']:>6,}  "
          f"({rates['ltfu_rate_pct']:.1f}% of all patients)")

    print(f"\nLTFU breakdown:")
    print(f"  {'Declined screening:':<38} {metrics['ltfu_unscreened']:>6,}")
    print(f"  {'Queue — primary screening:':<38} {metrics['ltfu_queue_primary']:>6,}")
    print(f"  {'Queue — secondary (colpo/biopsy):':<38} {metrics['ltfu_queue_secondary']:>6,}")
    print(f"  {'Queue — treatment (LEEP/cone):':<38} {metrics['ltfu_queue_treatment']:>6,}")

    if metrics["lung_eligible"] > 0:
        print(f"\nLung LDCT pathway funnel:")
        steps = [
            ("Eligible (USPTF: age 50-80, ≥20 pk-yrs)", "lung_eligible"),
            ("LDCT order placed",                        "lung_referral_placed"),
            ("LDCT appointment scheduled",               "lung_ldct_scheduled"),
            ("LDCT completed",                           "lung_ldct_completed"),
            ("Results communicated to patient",          "lung_result_communicated"),
            ("Biopsy referral made (RADS 4)",            "lung_biopsy_referral"),
            ("Biopsy scheduled",                         "lung_biopsy_scheduled"),
            ("Biopsy completed",                         "lung_biopsy_completed"),
            ("Malignancy confirmed",                     "lung_malignancy_confirmed"),
            ("Treatment given",                          "lung_treatment_given"),
        ]
        prev = max(metrics["lung_eligible"], 1)
        for label, key in steps:
            val  = metrics[key]
            drop = f"  (↓{100*(1-val/prev):.0f}%)" if prev > 0 and key != "lung_eligible" else ""
            print(f"  {label:<45} {val:>6,}{drop}")
            prev = max(val, 1)

        if metrics["lung_rads_distribution"]:
            print(f"\n  Lung-RADS distribution (of completed LDCTs):")
            total_ldct = max(metrics["lung_ldct_completed"], 1)
            for rads in ["RADS_0","RADS_1","RADS_2","RADS_3","RADS_4A","RADS_4B_4X"]:
                cnt = metrics["lung_rads_distribution"].get(rads, 0)
                print(f"    {rads:<12} {cnt:>5,}  ({100*cnt/total_ldct:.1f}%)")


    print("=" * 65)


def print_patient_trace(patients: List[Patient], n: int = 5) -> None:
    """
    Print the full event log for the first n patients in the list.

    Each patient's log is a chronological list of (day, event_string) tuples
    recorded by patient.log() throughout the simulation. This is the primary
    tool for verifying that the clinical logic is flowing correctly — reading
    a trace makes it immediately obvious if a step fired out of order or a
    patient ended up in an unexpected state.
    """
    for p in patients[:n]:
        p.print_history()


# =============================================================================
# Revenue Analysis
# =============================================================================

def compute_revenue(metrics: dict) -> dict:
    """
    Calculate realized and foregone procedure revenue from a completed simulation run.

    Realized revenue  — billed for procedures that actually occurred.
    Foregone revenue  — lost because patients dropped out at a LTFU node.
                        Foregone amounts are the *minimum* revenue lost (screening
                        only); downstream cascade (e.g. missed LEEP after missed
                        colposcopy) is reported separately in foregone_cascade.

    All rates are PLACEHOLDERS — replace with NYP finance / contract data.
    Set individual values in config.PROCEDURE_REVENUE.

    UNIT: all dollar fields are scaled to REAL NYP DOLLARS by multiplying
    through POPULATION_SCALE_FACTOR (one simulated patient represents
    POPULATION_SCALE_FACTOR real women). Headcount and ratio fields
    (`*_count_sim`, `*_count_real`, `demand_capture_rate`, etc.) are
    not dollar-denominated and follow their own semantics.

    Returns
    -------
    dict with keys:
        realized_total        : float  (real USD)
        foregone_total        : float  (real USD)
        unserved_total        : float  (real USD)
        realized_by_procedure : dict[str, float]   (real USD per procedure)
        foregone_by_node      : dict[str, float]   (real USD per LTFU node)
    """
    rev = cfg.PROCEDURE_REVENUE

    # ── Realized revenue ──────────────────────────────────────────────────────
    realized = {
        # Cervical screenings — split by test type not tracked separately in
        # metrics, so use average of cytology + hpv_alone as a proxy.
        # For exact split, add per-test counter to initialize_metrics().
        "cervical_screening": (
            metrics["n_screened"].get("cervical", 0)
            * (rev["cytology"] + rev["hpv_alone"]) / 2
        ),
        "colposcopy":  metrics["n_colposcopy"] * rev["colposcopy"],
        "leep":        metrics["n_treatment"].get("leep", 0)        * rev["leep"],
        "cone_biopsy": metrics["n_treatment"].get("cone_biopsy", 0) * rev["cone_biopsy"],

        # Lung
        "ldct":           metrics["lung_ldct_completed"]       * rev["ldct"],
        "lung_biopsy":    metrics["lung_biopsy_completed"]     * rev["lung_biopsy"],
        "lung_treatment": metrics["lung_treatment_given"]      * rev["lung_treatment"],
    }
    realized_total = sum(realized.values())

    # ── Foregone revenue ──────────────────────────────────────────────────────
    # Each node: patients who dropped out × revenue of the missed procedure
    # (+ a conservative estimate of one downstream procedure where applicable).

    # Use per-cancer eligible count so lung-only-eligible patients do not
    # inflate the cervical denominator.  n_eligible_any would overstate foregone
    # cervical revenue whenever lung-only patients exist.
    cervical_eligible  = metrics["n_eligible"].get("cervical", 0)
    cervical_screened  = metrics["n_screened"].get("cervical", 0)
    avg_cerv_screen    = (rev["cytology"] + rev["hpv_alone"]) / 2

    # How many abnormal cervical results were there?
    total_abnormal = sum(
        v for k, v in metrics["cervical_results"].items()
        if k not in ("NORMAL", "HPV_NEGATIVE")
    )

    # Lung pathway clinical dropoffs
    lung_eligible      = metrics.get("lung_eligible", 0)
    lung_ldct_done     = metrics.get("lung_ldct_completed", 0)
    lung_biopsy_ref    = metrics.get("lung_biopsy_referral", 0)
    lung_biopsy_done   = metrics.get("lung_biopsy_completed", 0)

    foregone = {
        # Unscreened — eligible patients who declined / no-showed
        "unscreened": (
            metrics.get("ltfu_unscreened", 0) * avg_cerv_screen
        ),
        # Queue LTFU — primary screening queue
        "queue_ltfu_primary": (
            metrics["ltfu_queue_primary"] * avg_cerv_screen
        ),
        # Queue LTFU — diagnostic queue (colposcopy / biopsy)
        "queue_ltfu_secondary": (
            metrics["ltfu_queue_secondary"] * rev["colposcopy"]
        ),
        # Lung clinical LTFU — eligible but never completed LDCT
        "lung_screening_ltfu": (
            max(lung_eligible - lung_ldct_done, 0) * rev["ldct"]
        ),
        # Lung biopsy LTFU — referred for biopsy but never completed
        "lung_biopsy_ltfu": (
            max(lung_biopsy_ref - lung_biopsy_done, 0) * rev["lung_biopsy"]
        ),
    }
    foregone_total = sum(foregone.values())

    # ── Unserved demand revenue ─────────────────────────────────────────────
    # "Unserved demand" = patients who entered the intake queue but were
    # never screened over the full simulation.  These are real women who
    # sought care at NYP but remained in the queue at simulation end.
    #
    # Revenue estimate: each unserved patient represents one missed screening.
    # We use cervical screening revenue as the baseline (most common).

    intake_total  = metrics.get("intake_queue_total", 0)
    intake_served = metrics.get("intake_queue_served", 0)
    unserved_sim  = max(intake_total - intake_served, 0)
    unserved_real = unserved_sim * cfg.POPULATION_SCALE_FACTOR

    # Each unserved patient missed at least one screening visit
    unserved_total = unserved_sim * avg_cerv_screen

    # Capture rate: fraction of intake demand that was actually served
    demand_capture_rate = intake_served / max(intake_total, 1)

    # Scale all dollar outputs from simulated-cohort dollars to real NYP
    # dollars. 1 sim patient represents POPULATION_SCALE_FACTOR real women,
    # so every dollar amount (computed as sim_count × USD_rate) must be
    # multiplied by POPULATION_SCALE_FACTOR to reflect the true population.
    # Headcounts (*_count_*) and ratios (demand_capture_rate) are not
    # dollar-denominated and keep their own semantics.
    scale          = cfg.POPULATION_SCALE_FACTOR
    realized       = {k: v * scale for k, v in realized.items()}
    foregone       = {k: v * scale for k, v in foregone.items()}
    realized_total = realized_total * scale
    foregone_total = foregone_total * scale
    unserved_total = unserved_total * scale

    return {
        "realized_total":        realized_total,
        "foregone_total":        foregone_total,
        "realized_by_procedure": realized,
        "foregone_by_node":      foregone,
        # Unserved demand (intake queue)
        "unserved_total":        unserved_total,
        "unserved_count_sim":    unserved_sim,
        "unserved_count_real":   unserved_real,
        "intake_total_sim":      intake_total,
        "intake_served_sim":     intake_served,
        "demand_capture_rate":   demand_capture_rate,
    }


def print_revenue_summary(metrics: dict) -> None:
    """
    Print a formatted revenue summary showing realized vs. foregone procedure revenue.

    Calls compute_revenue() to translate procedure volume counts into dollar amounts
    using the CPT-based rates in config.PROCEDURE_REVENUE, then prints two sections:
      - Realized revenue: procedures that actually occurred (screening, colposcopy, LEEP, LDCT, etc.)
      - Foregone revenue: revenue lost because patients dropped out at LTFU nodes

    Also prints the revenue capture rate (realized / total addressable) so it is
    immediately clear what fraction of potential revenue was actually collected.
    All dollar amounts use PLACEHOLDER CPT rates — replace with NYP contract rates.
    """
    r = compute_revenue(metrics)

    print("\n" + "=" * 65)
    print("REVENUE ANALYSIS  (PLACEHOLDER CPT rates — replace with NYP data)")
    print("=" * 65)

    print("\nRealized revenue (procedures completed):")
    for proc, amt in r["realized_by_procedure"].items():
        if amt > 0:
            print(f"  {proc:<30} ${amt:>12,.0f}")
    print(f"  {'TOTAL':<30} ${r['realized_total']:>12,.0f}")

    print("\nForegone revenue (lost to LTFU / unscreened):")
    for node, amt in r["foregone_by_node"].items():
        if amt > 0:
            print(f"  {node:<30} ${amt:>12,.0f}")
    print(f"  {'TOTAL':<30} ${r['foregone_total']:>12,.0f}")

    total = r["realized_total"] + r["foregone_total"]
    if total > 0:
        pct_lost = 100 * r["foregone_total"] / total
        print(f"\n  Revenue capture rate: {100 - pct_lost:.1f}%  "
              f"({pct_lost:.1f}% foregone)")
    print("=" * 65)
