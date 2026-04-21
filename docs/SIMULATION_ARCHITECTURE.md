# NYP Women's Health Screening Simulation — Complete Architecture

A discrete-event simulation (DES) of a multi-cancer women's health screening program at NewYork-Presbyterian. The model simulates patient flow from provider arrival through screening, clinical follow-up, and system exit — across an **80-year longitudinal horizon** (with a 10-year warmup period) — quantifying drop-off at each step and supporting operational and financial planning.

---

## Table of Contents

1. [Project Scope](#project-scope)
2. [File Architecture](#file-architecture)
3. [Population Generation](#1-population-generation)
4. [Life Event Scheduling](#2-life-event-scheduling)
5. [The Patient Pool & Warmup](#3-the-patient-pool--warmup)
6. [Daily Simulation Loop](#4-daily-simulation-loop)
7. [Arrivals & Provider Routing](#5-arrivals--provider-routing)
8. [Eligibility & Screening Intervals](#6-eligibility--screening-intervals)
9. [Screening Initiation Model](#7-screening-initiation-model)
10. [Test Assignment & Result Draws](#8-test-assignment--result-draws)
11. [Procedure Slot Capacity & Queue Overflow](#9-procedure-slot-capacity--queue-overflow)
12. [Cervical Follow-Up Pathway](#10-cervical-follow-up-pathway)
13. [Lung Follow-Up Pathway](#11-lung-follow-up-pathway)
14. [Surveillance & Re-Entry](#12-surveillance--re-entry)
15. [Loss-to-Follow-Up (LTFU)](#13-loss-to-follow-up-ltfu)
16. [Mortality, Attrition & Life Events](#14-mortality-attrition--life-events)
17. [Metrics & Revenue](#15-metrics--revenue)
18. [Database Persistence](#16-database-persistence)
19. [Visualizations](#17-visualizations)
20. [Turnaround & Scheduling Delays](#turnaround--scheduling-delays)
21. [Scale Interpretation](#scale-interpretation)

---

## Project Scope

**Active cancers:** cervical and lung (`ACTIVE_CANCERS = ["cervical", "lung"]`).

**Simulation window:** 80 years (`SIM_YEARS = 80`) = 29,200 days (`SIM_DAYS`). The first 10 years (`WARMUP_YEARS = 10`) are warmup; all metrics are collected from day 3,650 onward (70 years of measured output).

**Population scale:** 15,000 simulated patients (`INITIAL_POOL_SIZE`) represent approximately 1.5 million NYC eligible women (scale factor `POPULATION_SCALE_FACTOR = 100`).

**Replications:** `NUM_REPS = 10` independent runs for variance analysis.

**Random seed:** `RANDOM_SEED = None` (non-deterministic by default; set to an integer for reproducibility).

| Cancer | Status | Guideline |
|---|---|---|
| Cervical | Full pathway | USPSTF 2018 — cytology every 3 yrs (age 21–65) or HPV-alone every 5 yrs (age 30–65) |
| Lung | Full pathway | USPSTF 2021 — annual LDCT, age 50–80, ≥20 pack-years, current smoker or quit ≤15 yrs |

---

## File Architecture

```
NYP/
├── ModelParameters/
│   ├── parameters.py      # All simulation inputs (probabilities, capacities, revenue, population distributions)
│   └── validation.py      # Cross-validation targets + literature benchmarks (NOT simulation inputs)
│
├── src/
│   ├── patient.py         # Patient dataclass — shared data contract between all modules
│   ├── model.py           # Population sampler + screening + follow-up + metrics (merged)
│   ├── db.py              # SQLite persistence — patient + event log storage
│   └── runner.py          # SimulationRunner — day-by-day orchestration + queue engine
│
├── notebooks/
│   ├── simulation.ipynb          # Main notebook: 80-year run + all visualizations
│   └── Base Visualizations/      # 30+ saved PNGs from latest run
│
├── archive/                      # Reference notebooks (not required to run)
└── docs/
    ├── README.md                 # Project overview
    ├── SIMULATION_ARCHITECTURE.md # This file
    └── GLOSSARY.md               # Medical terms + CPT codes
```

**Design principle:** all logic lives in `.py` modules — importable, testable, version-controlled. Notebooks are thin wrappers that call those modules, run scenarios, and display output. No probability, capacity, or interval value is hard-coded anywhere except `parameters.py`.

---

## 1. Population Generation

**Module:** `population.py` → `sample_patient()`

Each patient is drawn from NYC Census and health survey distributions at entry. The function signature:

```python
sample_patient(patient_id, day_created, destination, patient_type, age_range=None) → Patient
```

### Demographics

| Field | Distribution | Parameters / Source |
|---|---|---|
| `age` | Empirical categorical from Census single-year-of-age (21–85). Age-85 bucket expanded to 85–100 via `Normal(89, 4)` clipped [85, 100]. If `age_range` is provided, rejection sampling constrains the draw. | `_AGE_VALUES`, `_AGE_PROBS` — Census SC-EST2024-AGESEX-CIV |
| `race` | Two-stage: (1) Hispanic vs Non-Hispanic by `_P_HISPANIC = 2030943/10161956 ≈ 0.1997`, (2) 6-category Census race within ethnicity group | `_RACE_PROBS_NON_HISP`, `_RACE_PROBS_HISP` — Census SC-EST2024-SR11H |
| `ethnicity` | "Hispanic" or "Non-Hispanic" from stage 1 | Census SC-EST2024-SR11H |
| `insurance` | Bernoulli by age band from `_INSURANCE_PROBS` (7 bands: 21–25, 26–34, 35–44, 45–54, 55–64, 65–74, 75–99). Lookup: `_INSURANCE_BY_AGE[band]["Insured"] / total` | ACS B27001 |
| `bmi` | Mixture model: Bernoulli(`_BMI_OBESITY_RATE = 0.276`) selects component; Obese: `Normal(34.8, 4.5)` clipped [30, 60]; Non-obese: `Normal(24.7, 3.2)` clipped [16, 29.9] | NYC Health obesity indicator |

### Clinical Attributes

| Field | Distribution | Parameters |
|---|---|---|
| `smoker` | Bernoulli(`_SMOKER_RATE = 0.109`) | NYS BRFSS 2023 |
| Former smoker | If not smoker: Bernoulli(0.30) → `is_former = True` | ~30% of ever-smokers |
| `pack_years` | Uniform(5, 40) if smoker or former; 0 otherwise | — |
| `years_since_quit` | Uniform(0, 30) if former smoker; 0 otherwise | — |
| `has_cervix` | `1 - hysterectomy_prob` where prob looked up by `(age_band, race/ethnicity_group)` from `_HYSTERECTOMY_BY_GROUP` | CDC/BRFSS 2018 |
| `hpv_vaccinated` | Bernoulli by age band from `_HPV_VAX_RATE`: {21–29: 0.60, 30–39: 0.40, 40–49: 0.20, 50+: 0.05} | PLACEHOLDER |
| `hpv_positive` | Bernoulli(`_HPV_POSITIVE_RATE = 0.25`) if not vaccinated and has cervix | PLACEHOLDER |
| `prior_abnormal_pap` | Bernoulli(0.12) if has cervix | Fixed prevalence |
| `prior_cin` | If prior_abnormal_pap: Bernoulli(0.30) → random choice of "CIN1" or "CIN2" | — |

### Hysterectomy Prevalence Table

Stratified by race/ethnicity group (`_HYSTERECTOMY_BY_GROUP`):

| Group | 21–29 | 30–39 | 40–49 | 50–59 | 60–69 | 70–99 |
|---|---|---|---|---|---|---|
| Hispanic | 0.004 | 0.029 | 0.109 | 0.211 | 0.295 | 0.430 |
| White | 0.005 | 0.054 | 0.166 | 0.268 | 0.341 | 0.456 |
| Black | 0.003 | 0.038 | 0.185 | 0.337 | 0.441 | 0.521 |
| Asian | 0.004 | 0.006 | 0.078 | 0.112 | 0.149 | 0.276 |
| Other | 0.004 | 0.038 | 0.143 | 0.239 | 0.310 | 0.418 |

Source: CDC/BRFSS 2018

### Established Population

`generate_established_population(n, start_pid, entry_day)` creates the initial `INITIAL_POOL_SIZE` (15,000) patient cohort. All patients are assigned a non-ER provider destination via `_sample_established_destination()` (redistributes ER weight across PCP/GYN/Specialist proportionally), flagged `is_established=True`.

---

## 2. Life Event Scheduling

**Module:** `population.py` → called from `runner._schedule_life_events()`

At patient entry, four independent life events are pre-drawn and scheduled on fixed future days. No periodic sweeps — events fire on their pre-drawn day.

### Mortality — Gompertz Survival

**Function:** `draw_death_day(p, entry_day)`

Conditional Gompertz survival function:

```
S(t|x) = exp[ -(a/b) × e^(b×x) × (e^(b×t) - 1) ]
```

Inverse CDF to draw remaining years:

```
t = (1/b) × ln(1 - (b × ln(U)) / (a × e^(b×x)))    where U ~ Uniform(0,1)
```

If the argument to `ln()` is ≤ 0, the patient "survives" beyond the Gompertz horizon and is capped at `max_remaining_years`. The result is converted to a simulation day: `entry_day + int(remaining_years × 365)`.

| Parameter | Config Name | Value | Source |
|---|---|---|---|
| Baseline hazard (a) | `GOMPERTZ_A` | 0.0000592 | Missov et al. 2015 (US female life tables) |
| Aging coefficient (b) | `GOMPERTZ_B` | 0.0819 | Missov et al. 2015 |
| Smoker multiplier on a | `SMOKER_MORTALITY_MULTIPLIER` | 2.5× | Jha et al. 2013 (NEJM) |
| Former smoker multiplier on a | `FORMER_SMOKER_MORTALITY_MULTIPLIER` | 1.4× | Jha et al. 2013 |
| Hard age cap | `MORTALITY_AGE_CAP` | 100 | — |

The hazard doubles roughly every 8.5 years (= ln(2) / b ≈ 8.46).

**Smoking adjustment:** For current smokers, `a` is multiplied by `SMOKER_MORTALITY_MULTIPLIER` (2.5). For former smokers (pack_years > 0 but smoker=False), `a` is multiplied by `FORMER_SMOKER_MORTALITY_MULTIPLIER` (1.4). This shifts the hazard curve left (earlier expected death).

### Attrition — Exponential with Competing Risks

**Function:** `draw_attrition_day(entry_day)` → `(day, subtype)`

```
years ~ Exponential(rate = ANNUAL_ATTRITION_RATE = 0.050)
day = entry_day + int(years × 365)
```

Subtype drawn proportional to individual source rates via `random.choices()`:

| Source | Config Path | Annual Rate |
|---|---|---|
| Relocation | `EXIT_SOURCES["relocation"]["annual_rate"]` | 0.025 |
| Insurance loss | `EXIT_SOURCES["insurance_loss"]["annual_rate"]` | 0.015 |
| Provider switch | `EXIT_SOURCES["provider_switch"]["annual_rate"]` | 0.010 |
| **Total** | `ANNUAL_ATTRITION_RATE` | **0.050** |

Attrition is suppressed in `_schedule_life_events()` if it would occur after the drawn death day (`att_day < death_day` check).

### Smoking Cessation — Exponential

**Function:** `draw_cessation_day(entry_day)` (smokers only)

```
years ~ Exponential(rate = ANNUAL_SMOKING_CESSATION_PROB = 0.05)
day = entry_day + int(years × 365)
```

Suppressed if after death or attrition day (`cess_day < min(death_day, att_day)` check). When the event fires: `p.pack_years` is accumulated up to the quit day, `p.smoker` is set to False, `p.years_since_quit` is set to 0.

### HPV Clearance — Exponential

**Function:** `draw_hpv_clearance_day(entry_day)` (HPV+ patients only)

```
years ~ Exponential(rate = ANNUAL_HPV_CLEARANCE_PROB = 0.30)
day = entry_day + int(years × 365)
```

Suppressed if after death or attrition day. When the event fires: `p.hpv_positive` is set to False.

---

## 3. The Patient Pool & Warmup

### Open-System Model

The simulation models an **open system** — patients enter continuously (organic arrivals) and exit continuously (mortality, attrition, ineligibility, LTFU). There are **no replacement entrants** tied to exits; pool size is **emergent** from the balance of arrivals vs. exits and gradually decreases over time due to survivorship dynamics (the initial cohort ages out faster than new arrivals replenish it).

### Pool Initialization

On day 0, `_initialize_population()`:
1. Creates `INITIAL_POOL_SIZE` (15,000) established patients via `generate_established_population()`
2. Spreads their first visits evenly across `WARMUP_DAYS` (1,825 days = 5 years): `target_day = int(i × warmup / len(pool))`
3. Pre-books `ADVANCE_SCHEDULE_YEARS` (5) annual visits per patient, spaced `ANNUAL_VISIT_INTERVAL` (365) days apart
4. Calls `_schedule_life_events()` for each patient (mortality, attrition, cessation, clearance)

### Warmup Period

`WARMUP_YEARS = 10` years (days 0–3,649). **All metrics, counters, wait times, and revenue calculations exclude the warmup period.** Data collection begins on day 3,650 (`_WARMUP_DAY = WARMUP_YEARS × DAYS_PER_YEAR`). This ensures:
- Screening intervals (3-year cytology, 5-year HPV) have completed at least one full cycle
- The system reaches near-steady-state behavior
- Year-10+ metrics are comparable to each other

### Pool Size Snapshots

Every 30 days, `_process_life_events()` appends `(day, len(self._established_pool))` to `metrics["pool_size_snapshot"]` for longitudinal plotting.

---

## 4. Daily Simulation Loop

**Module:** `runner.py` → `SimulationRunner.run()` and `_tick(day)`

Every day (0 to `n_days - 1`), in this exact order:

### Background (runs every day including weekends):
1. **Life events** — `_process_life_events(day)`: fire all pre-scheduled mortality, attrition, smoking cessation, and HPV clearance events due today. Exited patients are collected in `_pool_removals`; every 30 days a single O(n) filter pass purges them from `_established_pool`.
2. **Database flush** — `_flush_exited_patients(day)`: batch-write exited patients to SQLite every `DB_FLUSH_INTERVAL` (30) days.

### Annual checkpoint (every 365 days):
- Snapshot: pool size, cumulative screenings (by cancer, by test), cumulative LTFU (by queue stage), procedure queue depth, procedure overflow counts, due-for-screening counts, mortality, arrivals, exits by reason.

### Clinical steps (weekdays only — skipped if `SKIP_WEEKENDS = True` and `day % 7 >= 5`):
3. **Follow-ups** — process all clinical follow-ups due today (colposcopy, LEEP, biopsy, surveillance, repeat LDCT, result routing, screening retries) via `_run_followup()`. Follow-ups consume procedure slots **before** new arrivals.
4. **New arrivals** — `_generate_arrivals(day)`: create new patients from `ARRIVAL_SOURCES` via Poisson draws.
5. **Provider queues** — for each provider (non-ER first, then ER):
   - Fetch scheduled outpatients + drop-ins via `process_day(provider, day)`
   - Apply provider-to-screening delay: if `TURNAROUND_DAYS["provider_to_screening"]` > 0, schedule a `provider_screening` follow-up; otherwise screen immediately
   - Screen each patient via `_screen_patient(p, day)`
6. **Daily demand recording** (post-warmup only): append `(demand, supplied, overflow)` tuples to `daily_screening_demand`, `daily_secondary_demand`, `daily_treatment_demand`.

---

## 5. Arrivals & Provider Routing

### Arrival Sources

Arrivals come from `ARRIVAL_SOURCES`, each with its own daily Poisson rate, age range, and routing:

| Source | Config Key | Daily Rate (Poisson λ) | Formula | Age Range | Routing |
|---|---|---|---|---|---|
| Aging in (turning 21) | `ARRIVAL_SOURCES["aging_in"]` | ~0.53 | `TOTAL_DAILY_ARRIVALS × _OUTPATIENT_SHARE × _OP_AGING_IN` = 1.6 × 0.80 × 0.4141 | 21–25 | Outpatient |
| New movers | `ARRIVAL_SOURCES["new_mover"]` | ~0.43 | 1.6 × 0.80 × 0.3359 | 21–85 | Outpatient |
| ER walk-ins | `ARRIVAL_SOURCES["er_walkin"]` | ~0.32 | 1.6 × 0.20 | 21–85 | ER (drop-in) |
| Referrals | `ARRIVAL_SOURCES["referral"]` | ~0.32 | 1.6 × 0.80 × 0.2500 | 30–75 | Outpatient |

**Total:** `TOTAL_DAILY_ARRIVALS = 1.6` patients/day. Split: 80% outpatient (`_OUTPATIENT_SHARE`), 20% ER (`_ER_SHARE`).

**Poisson sampling:** `_poisson(lam)` uses a Normal approximation: `max(0, round(random.gauss(lam, sqrt(lam))))`.

### Provider Destination

Outpatient arrivals are routed to a provider by weighted draw from `DESTINATION_PROBS_OUTPATIENT`:

| Provider | Weight | Config Key |
|---|---|---|
| PCP | 0.852 | `DESTINATION_PROBS_OUTPATIENT["pcp"]` |
| Gynecologist | 0.148 | `DESTINATION_PROBS_OUTPATIENT["gynecologist"]` |

Provider throughput is enforced as a single daily cap (`DAILY_PATIENTS`), not per-provider. Each workday, up to `DAILY_PATIENTS` patients from the intake queue are seated across all providers; overflow remains in the FIFO queue for the next workday. Provider *routing* (PCP vs GYN) is proportional and does not itself cap capacity — the real bottleneck is at the procedure slots (Section 9).

### Scheduling Lead Time

Outpatients enter the intake FIFO on their arrival day. They get seen by a provider on the next available workday within the `DAILY_PATIENTS` cap — no explicit per-provider lead-time draw. ER patients skip the outpatient intake and enter `intake_er` as drop-ins, served after established and outpatient priority.

### New-to-Established Conversion

After a first visit, non-established patients join the cycling pool:
1. `p.is_established = True`, `p.age_at_entry = p.age`, `p.simulation_entry_day = day`
2. If ER patient: reassigned to a non-ER provider via `_sample_established_destination()`
3. Life events scheduled via `_schedule_life_events(p, entry_day=day)`
4. Pre-booked for `ADVANCE_SCHEDULE_YEARS` (5) annual visits starting 365 days from today
5. Added to `_established_pool`

---

## 6. Eligibility & Screening Intervals

### Eligibility Criteria

| Cancer | Function | Criteria | Config Path |
|---|---|---|---|
| Cervical | `is_eligible_cervical(p)` | Age 21–65 AND `has_cervix=True` | `ELIGIBILITY["cervical"]`: `age_min=21, age_max=65, requires_cervix=True` |
| Lung | `is_eligible_lung(p)` | Age 50–80 AND `pack_years ≥ 20` AND (`smoker=True` OR `years_since_quit ≤ 15`) | `ELIGIBILITY["lung"]`: `age_min=50, age_max=80, min_pack_years=20, max_years_since_quit=15` |

### Lung Eligibility Base Probability

Not all age/smoking-eligible patients are actually lung-eligible in the population: `LUNG_ELIGIBLE_BASE_PROB = 0.055` (5.5% of women aged 50–80 meet all USPSTF criteria). Source: CDC BRFSS; Fedewa et al. 2022.

### Future Eligibility Routing

`days_until_eligible(p, cancer)` computes how many days until a not-yet-eligible patient becomes eligible:
- `> 0` — schedule a return visit at that future day
- `0` — eligible right now
- `-1` — **permanently ineligible** (no cervix, aged out, never smoked, quit window closed)

Permanently ineligible established patients exit the pool with `exit_reason = "ineligible"`. Non-established patients exit silently.

### Screening Intervals

`is_due_for_screening(p, cancer, day)` checks if enough time has passed since the last screen. The interval depends on the **test modality used at the last visit** (stored in `p.last_cervical_screening_test`), not a fresh random draw — this makes the check deterministic:

| Test Modality | Interval | Config Key |
|---|---|---|
| Cytology | 3 years (1,095 days) | `SCREENING_INTERVALS_DAYS["cytology"]` |
| HPV-alone | 5 years (1,825 days) | `SCREENING_INTERVALS_DAYS["hpv_alone"]` |
| Co-test | 3 years (1,095 days) | `SCREENING_INTERVALS_DAYS["co_test"]` |
| LDCT | 1 year (365 days) | `SCREENING_INTERVALS_DAYS["ldct"]` |

First screen ever (`last_day = -1`): always due. Falls back to cytology (shorter 3-year interval) if no test recorded.

### Age Strata

`get_cervical_age_stratum(age)`:
- **young** (21–29): cytology only, every 3 years
- **middle** (30–65): cytology every 3 yrs OR HPV-alone every 5 yrs OR co-test every 3 yrs
- **older** (66+): no routine screening

---

## 7. Calibration Targets

Once a patient is eligible and due, the simulation initiates screening deterministically. No per-visit "decision to order" probability applies; we do not model test-completion-given-ordered either.

External benchmarks in `validation.py` — the simulation OUTPUT should fall in these ranges when calibrated against NYP data:

| Metric | Target | Source |
|---|---|---|
| Cervical up-to-date (3-yr interval) | 73–83% | NHIS/BRFSS |
| Lung annual rate (academic center) | 15–25% | Fedewa et al. 2022 |
| Visits before cervical initiation | ~1.5 (range 1–3) | Kepka et al. 2014 |
| Visits before lung initiation | ~4 (range 2–6) | Triplette et al. 2022 |

---

## 8. Test Assignment & Result Draws

### Cervical Test Assignment

`assign_screening_test(p, cancer)`:

| Age Stratum | Modality | Selection Logic |
|---|---|---|
| 21–29 (young) | Cytology only | Deterministic (`return "cytology"`) |
| 30–65 (middle) | Co-test / Cytology / HPV-alone | Weighted draw from `TEST_TYPE_PROBS_30_65`: co_test **0.55**, cytology **0.35**, hpv_alone **0.10** |
| 66+ (older) | None | Returns `"ineligible"` |

Lung: always LDCT.

### Cervical Result Draws

`draw_cervical_result(p, test)` — multinomial draw from age-stratified probability tables.

**Co-test** uses `middle_cytology` probabilities (returns both HPV and cytology; cytology result drives routing).

**Young (21–29), cytology** — `CERVICAL_RESULT_PROBS["young"]`:

| Result | Probability |
|---|---|
| NORMAL | 0.890 |
| ASCUS | 0.040 |
| LSIL | 0.045 |
| ASC-H | 0.015 |
| HSIL | 0.010 |

**Middle (30–65), cytology** — `CERVICAL_RESULT_PROBS["middle_cytology"]`:

| Result | Probability |
|---|---|
| NORMAL | 0.910 |
| ASCUS | 0.035 |
| LSIL | 0.030 |
| ASC-H | 0.015 |
| HSIL | 0.010 |

**Middle (30–65), HPV-alone** — `CERVICAL_RESULT_PROBS["middle_hpv"]`:

| Result | Probability |
|---|---|
| HPV_NEGATIVE | 0.880 |
| HPV_POSITIVE | 0.120 |

### Risk Adjustments

Applied via `_adjust_probs()`: multiply selected result categories by the factor, then renormalize the entire distribution to sum to 1:

| Condition | Affected Results | Multiplier | Config Key |
|---|---|---|---|
| HPV+ patient on cytology | ASCUS, LSIL, ASC-H, HSIL | 1.5× | `RISK_MULT_HPV_POSITIVE_CYTOLOGY` |
| HPV+ patient on HPV test | HPV_POSITIVE | 2.0× | `RISK_MULT_HPV_POSITIVE_HPV_TEST` |
| Prior CIN2/3 on cytology | ASC-H, HSIL | 1.8× | `RISK_MULT_PRIOR_CIN_HIGHGRADE` |

Multiple adjustments stack: an HPV+ patient with prior CIN3 gets both the 1.5× and 1.8× adjustments (applied sequentially, each re-normalizing).

### Lung Result Draws

`draw_lung_rads_result()` — multinomial from `LUNG_RADS_PROBS`:

| Lung-RADS | Probability | Interpretation | Source |
|---|---|---|---|
| RADS_0 | 0.010 | Incomplete — repeat in 1–3 months | ASSUMPTION |
| RADS_1 | 0.291 | Negative — annual follow-up | PMC10331628 |
| RADS_2 | 0.529 | Benign — annual follow-up | PMC10331628 |
| RADS_3 | 0.102 | Probably benign — 6-month repeat | PMC10331628 + Pinsky et al. 2015 |
| RADS_4A | 0.046 | Suspicious — biopsy pathway | PMC10331628 + Pinsky et al. 2015 |
| RADS_4B_4X | 0.022 | Very suspicious — biopsy pathway | PMC10331628 + Pinsky et al. 2015 |

No patient-level risk adjustments for lung results.

### Lung-RADS Malignancy Rates (Among Patients in Each Category)

| Category | Rate | Config Key | Source |
|---|---|---|---|
| RADS_3 | 0.03 | `LUNG_RADS_MALIGNANCY_RATE["RADS_3"]` | Pinsky et al. 2015 |
| RADS_4A | 0.08 | `LUNG_RADS_MALIGNANCY_RATE["RADS_4A"]` | ACR Lung-RADS v1.1 |
| RADS_4B_4X | 0.35 | `LUNG_RADS_MALIGNANCY_RATE["RADS_4B_4X"]` | Pinsky et al. 2015 |

---

## 9. Procedure Slot Capacity & Queue Overflow

### Daily Procedure Slots

`PatientQueues.consume_slot(procedure, day)` enforces daily capacity limits from `CAPACITIES`. Slots are initialized lazily from config on first access per day.

| Stage | Procedure | Daily Slots | Config Key |
|---|---|---|---|
| Primary | Cytology | 8 | `CAPACITIES["cytology"]` |
| Primary | HPV-alone | 8 | `CAPACITIES["hpv_alone"]` |
| Primary | Co-test | 8 | `CAPACITIES["co_test"]` |
| Primary | LDCT | 4 | `CAPACITIES["ldct"]` |
| Secondary | Colposcopy | 8 | `CAPACITIES["colposcopy"]` |
| Secondary | Lung biopsy | 2 | `CAPACITIES["lung_biopsy"]` |
| Treatment | LEEP | 5 | `CAPACITIES["leep"]` |
| Treatment | Cone biopsy | 3 | `CAPACITIES["cone_biopsy"]` |

### Priority Order

Primary screenings: outpatients (PCP/GYN/Specialist) are processed **before** ER drop-ins in the daily loop, so scheduled patients get priority on slots.

Follow-ups (colposcopy, biopsy, LEEP) are processed **before** new arrivals on each day — existing patients in the pipeline get priority.

### Overflow Handling

When `consume_slot()` returns False (capacity full):
1. Patient is **rescheduled to the next workday** via `schedule_followup()` with `step="screening_retry"` (for primary) or the same step name (for secondary/treatment)
2. Tracked as `procedure_overflow` in `PatientQueues.procedure_overflow`
3. On each retry day, the patient faces a daily queue LTFU hazard (see [LTFU section](#13-loss-to-follow-up-ltfu))
4. `referral_day` is preserved across retries so wait time = total days from original referral
5. Wait time = `day_performed - referral_day - scheduled_delay` (pure queue delay, excluding the configured scheduling window)

---

## 10. Cervical Follow-Up Pathway

### Result Routing

`_route_cervical_followup(p, result, day)` dispatches based on screening result:

| Result | Action | Scheduling Delay | Config Key |
|---|---|---|---|
| NORMAL, HPV_NEGATIVE | Return to routine screening interval | — | — |
| HPV_POSITIVE | **ASCCP triage**: Bernoulli(`HPV_POSITIVE_COLPOSCOPY_PROB = 0.60`) → colposcopy; else → 1-year repeat cytology | Colposcopy: 50 days; Repeat: 365 days | `HPV_POSITIVE_COLPOSCOPY_PROB`, `FOLLOWUP_DELAY_DAYS["colposcopy"]` |
| ASCUS, LSIL | Colposcopy referral | 50 days | `FOLLOWUP_DELAY_DAYS["colposcopy"]` |
| ASC-H, HSIL | Expedited colposcopy | 32 days | `FOLLOWUP_DELAY_DAYS["colposcopy_hsil"]` |

Note: Result routing happens **after lab turnaround delay** (e.g., `TURNAROUND_DAYS["cytology"] = 7` days for cytology, `TURNAROUND_DAYS["co_test"] = 10` for co-test, `TURNAROUND_DAYS["hpv_alone"] = 5` for HPV-alone).

### One-Year Repeat Cytology (HPV+ Low-Risk Path)

When HPV+ is triaged to 1-year repeat:
1. Scheduled 365 days out
2. On due day: consume a cytology slot, force cytology (not HPV-alone)
3. Draw new cervical result
4. Re-route the new result: normal → done; abnormal → colposcopy

### Colposcopy

`run_colposcopy(p, day, metrics)` draws CIN grade conditional on triggering result from `COLPOSCOPY_RESULT_PROBS`:

| Trigger (`from_` key) | NORMAL | CIN1 | CIN2 | CIN3 |
|---|---|---|---|---|
| ASCUS | 0.60 | 0.25 | 0.10 | 0.05 |
| LSIL | 0.40 | 0.35 | 0.15 | 0.10 |
| ASC-H | 0.25 | 0.20 | 0.30 | 0.25 |
| HSIL | 0.10 | 0.10 | 0.30 | 0.50 |
| HPV_POSITIVE | 0.50 | 0.30 | 0.15 | 0.05 |

Every colposcopy trigger (ASCUS / LSIL / ASC-H / HSIL / HPV_POSITIVE) has a specific `from_X` distribution — no fallback is needed.

After colposcopy, a pathology result delay of `TURNAROUND_DAYS["colposcopy_result"] = 10` days elapses before treatment routing. If the biopsy returns CIN2 or CIN3, the patient's `prior_cin` attribute is updated so subsequent cytology draws correctly apply `RISK_MULT_PRIOR_CIN_HIGHGRADE`.

### Treatment Assignment

From `TREATMENT_ASSIGNMENT`:

| CIN Grade | Treatment | Next Step |
|---|---|---|
| NORMAL | Surveillance | CIN1 surveillance pathway |
| CIN1 | Surveillance | CIN1 surveillance pathway |
| CIN2 | LEEP | Schedule LEEP procedure |
| CIN3 | LEEP | Schedule LEEP procedure |

LEEP scheduling delay: `FOLLOWUP_DELAY_DAYS["leep"] = 14` days.
Cone biopsy scheduling delay: `FOLLOWUP_DELAY_DAYS["cone_biopsy"] = 21` days.

After LEEP/cone biopsy: post-treatment surveillance is scheduled per ASCCP guidelines.

---

## 11. Lung Follow-Up Pathway

### Pre-LDCT Milestones

`run_lung_pre_ldct(p, day, metrics)` records three administrative milestones (referral placed → scan scheduled → scan completed). **No Bernoulli LTFU fires at these nodes** — LTFU is handled exclusively by the queue-based geometric waiting-time hazard (consistent with the cervical pathway). Dropouts happen only if the patient abandons the LDCT retry queue after repeated capacity overflow.

### Post-LDCT Result Routing

`run_lung_followup(p, day, metrics)` — RADS category drives disposition:

| RADS Category | Action | Repeat Interval | Config Key |
|---|---|---|---|
| RADS_0 | Repeat LDCT | 60 days | `LUNG_RADS_REPEAT_INTERVALS["RADS_0"]` |
| RADS_1, RADS_2 | Repeat LDCT (routine annual) | 365 days | `LUNG_RADS_REPEAT_INTERVALS["RADS_1"]` / `["RADS_2"]` |
| RADS_3 | Repeat LDCT | 180 days | `LUNG_RADS_REPEAT_INTERVALS["RADS_3"]` |
| RADS_4A | Biopsy pathway | — | — |
| RADS_4B_4X | Biopsy pathway | — | — |

### Biopsy Pathway (RADS 4A/4B/4X)

Biopsy referral → scheduling → completion → pathology result. Administrative steps are recorded but do not fire Bernoulli LTFU (again, queue hazard only).

The pathology result is the one clinical probability in this pathway — it determines whether the biopsy returns malignant or benign. Rates are stratified by Lung-RADS tier:

| RADS Category | P(malignant \| biopsy completed) | Config Key |
|---|---|---|
| RADS_3 | 0.03 | `LUNG_RADS_MALIGNANCY_RATE["RADS_3"]` (not reached — RADS 3 goes to repeat LDCT) |
| RADS_4A | 0.08 | `LUNG_RADS_MALIGNANCY_RATE["RADS_4A"]` |
| RADS_4B_4X | 0.35 | `LUNG_RADS_MALIGNANCY_RATE["RADS_4B_4X"]` |

Sources: Pinsky et al. 2015 (NLST/Lung-RADS); McKee et al. 2015; ACR Lung-RADS v1.1; Hammer et al. 2020.

If benign: `p.lung_biopsy_result = "benign"`, return to annual surveillance.
If malignant: `p.lung_biopsy_result = "malignant"`, proceed to treatment slot competition.

Scheduling delays: `FOLLOWUP_DELAY_DAYS["lung_biopsy"] = 21` days, `FOLLOWUP_DELAY_DAYS["lung_treatment"] = 21` days.

### Repeat LDCT Processing

`_lung_repeat_ldct_step(p, day, referral_day, scheduled_delay)`:
1. Consume an LDCT slot (overflow → retry next day)
2. Draw new Lung-RADS result
3. Re-route: may escalate to biopsy (RADS 4A/4B) or schedule another repeat

---

## 12. Surveillance & Re-Entry

### CIN1 Surveillance

**Function:** `_cin1_surveillance_step(p, day, referral_day, clean_count)` — annual cytology with three possible outcomes per visit:

| Outcome | Probability | Config Key | Action |
|---|---|---|---|
| Resolution | 0.40 | `CIN1_RESOLUTION_PROB_PER_VISIT` | Increment `clean_count`; if ≥ `CIN1_MAX_CLEAN_VISITS_BEFORE_ROUTINE` (2), return to routine |
| Escalation to CIN2/3 | 0.07 | `CIN1_ESCALATION_PROB_PER_VISIT` | Route to colposcopy/treatment (random CIN2 or CIN3) |
| Persistence | 0.53 | `CIN1_PERSISTENCE_PROB_PER_VISIT` (= 1 - 0.40 - 0.07) | Reschedule in `CIN1_SURVEILLANCE_INTERVAL_DAYS` (365) days, reset `clean_count = 0` |

Sources: Resolution — Castle et al. 2009, Ostor 1993; Escalation — ALTS trial, Cox et al. 2003; Schedule — ASCCP 2019.

### Cervical Post-Treatment Surveillance (ASCCP)

**Function:** `_post_treatment_surveillance_step(p, day, treatment_day, visit_number)`

Schedule from `POST_TREATMENT_SURVEILLANCE_CERVICAL`:

| Years Since Treatment | Interval | Config Entry |
|---|---|---|
| 1–2 | Every 6 months (4 visits) | `(2, 6)` |
| 3–5 | Every 12 months (3 visits) | `(5, 12)` |
| 6–25 | Every 36 months (~7 visits) | `(25, 36)` |

Active surveillance duration: `POST_TREATMENT_ACTIVE_YEARS_CERVICAL = 10` years, then return to routine provider visits.

At each visit: consume a cytology slot, draw a recurrence result (recurrence probability = 0.07, PLACEHOLDER). If recurrence: draw ASCUS or HSIL, refer to colposcopy. If normal: schedule next surveillance visit.

Source: Katki et al. 2013, JNCI.

### Lung Post-Treatment Surveillance (NCCN)

**Function:** `_lung_post_treatment_surveillance_step(p, day, treatment_day, visit_number)`

Schedule from `POST_TREATMENT_SURVEILLANCE_LUNG`:

| Years Since Treatment | Interval | Config Entry |
|---|---|---|
| 1–2 | Every 6 months | `(2, 6)` |
| 3–5 | Every 12 months | `(5, 12)` |
| 6+ | Every 12 months (if fit) | `(999, 12)` |

Active surveillance duration: `POST_TREATMENT_ACTIVE_YEARS_LUNG = 5` years.

At each visit: consume an LDCT slot, draw a new Lung-RADS result. If RADS 4A/4B_4X: escalate to biopsy pathway. Otherwise: schedule next surveillance per schedule.

Source: NCCN NSCLC Survivorship; ASCO (Schneider et al. 2020).

### Re-Entry After Normal/Negative Results

After screening completes (including normal results), `_reschedule_established(p, day)` extends the advance-schedule window by one year at the far end. The patient's `next_visit_day` is set to `day + ANNUAL_VISIT_INTERVAL`. Established patients continuously cycle through annual visits with periodic screening based on their interval.

### Re-Entry After Pathway LTFU

Established patients who hit LTFU from a clinical pathway (e.g., lung referral not placed) are **re-activated**: `p.active=True`, `p.exit_reason=None`, `p.exit_day=None`, then `_reschedule_established(p, day)` is called. LTFU from a cancer pathway does **not** mean leaving the system — it means missing that specific follow-up step. The patient continues their annual provider visits.

Non-established patients who hit LTFU exit permanently.

---

## 13. Loss-to-Follow-Up (LTFU)

### Queue LTFU (Geometric Waiting-Time Model)

**Function:** `_check_queue_ltfu(p, day, referral_day, queue_type, procedure)` — called each day a patient is retrying for a procedure slot.

When a patient is waiting in a procedure queue (overflow, retrying daily), each day an independent **Bernoulli trial** is drawn:

```python
random.random() < LTFU_PROBS[f"queue_{queue_type}_daily"]
```

The number of days until abandonment therefore follows a **geometric waiting-time distribution**, Geometric(p) — the discrete analog of the Exponential. Because the Geometric distribution is **memoryless**, the probability of abandoning on any given day does not depend on how long the patient has already waited.

| Queue Type | Daily Hazard (*p*) | Config Key | Mean (1/*p*) | Median ⌈ln 0.5 / ln(1−*p*)⌉ |
|---|---|---|---|---|
| Primary screening | 0.002 | `LTFU_PROBS["queue_primary_daily"]` | 500 days | ~346 days |
| Secondary (colposcopy/biopsy) | 0.005 | `LTFU_PROBS["queue_secondary_daily"]` | 200 days | ~139 days |
| Treatment (LEEP/cone) | 0.003 | `LTFU_PROBS["queue_treatment_daily"]` | 333 days | ~231 days |

Queue LTFU is the **sole dropout mechanism** from procedure queues. There is no single up-front draw — it is a geometric waiting-time process: a daily coin flip each time `_check_queue_ltfu()` is called during the retry loop.

When queue LTFU fires:
1. Wait time recorded in `metrics["wait_times_abandoned"][procedure]`
2. Patient exits: `p.exit_system(day, f"ltfu_queue_{queue_type}")`
3. `record_exit()` called with `reason="lost_to_followup"`
4. `metrics["ltfu_queue_{queue_type}"]` incremented
5. Established patients added to flush buffer + pool removals

### Clinical Pathway LTFU (Lung)

The lung pathway has explicit LTFU nodes at each step (see [Lung Follow-Up Pathway](#11-lung-follow-up-pathway)). Each node is a Bernoulli draw — failure = `p.exit_system(day, "lost_to_followup")` for non-established patients; re-activation for established patients.

### Unscreened LTFU

`handle_unscreened(p, day)`: eligible patients not offered screening:
- `LTFU_PROBS["unscreened_will_reschedule"] = 0.40` probability of re-engaging → "reschedule"
- 60% exit the system → `p.exit_system(day, "lost_to_followup")`

---

## 14. Mortality, Attrition & Life Events

All life events are **pre-drawn at patient entry** and fire on their scheduled day. No periodic sweeps.

### Daily Processing

`_process_life_events(day)` fires all events due today via `_queues.get_due_life_events(day)`:

| Event | Guard | Action |
|---|---|---|
| `mortality` | `p.active` | `p.exit_system(day, "mortality")`, `record_exit(metrics, "mortality")`, increment `mortality_count`, add to flush buffer + pool removals |
| `attrition` | `p.active` | `p.exit_system(day, f"attrition:{subtype}")`, `record_exit(metrics, "attrition")`, increment `exits_by_source[subtype]`, add to flush buffer + pool removals |
| `smoking_cessation` | `p.smoker` | `p.pack_years += years_smoked_since_entry`, `p.smoker = False`, `p.years_since_quit = 0.0` |
| `hpv_clearance` | `p.hpv_positive` | `p.hpv_positive = False` |

Age is updated arithmetically before processing: `p.age = p.age_at_entry + (day - p.simulation_entry_day) // 365`.

### Attribute Evolution

| Attribute | How It Changes | Eligibility Impact |
|---|---|---|
| `age` | Arithmetic: `age_at_entry + (day - simulation_entry_day) // 365` | Cervical ends at 65; lung starts at 50, ends at 80 |
| `pack_years` | +accumulated years while `smoker=True` (added at cessation event) | Crosses 20 pk-yr lung threshold mid-simulation |
| `smoker` | Pre-drawn cessation day from Exponential(0.05) | Former smokers enter 15-yr quit window for lung eligibility |
| `years_since_quit` | Computed dynamically from cessation day | Closes lung eligibility after 15 years |
| `hpv_positive` | Pre-drawn clearance day from Exponential(0.30) | Lowers abnormal cervical result risk (removes multiplier) |

### Batch Pool Purge

Exited patients are **not** removed individually (O(n) per removal). Instead:
- `_pool_removals` collects `id(p)` of exited patients
- Every 30 days: `self._established_pool = [p for p in self._established_pool if id(p) not in self._pool_removals]`
- Then `_pool_removals.clear()`

---

## 15. Metrics & Revenue

### Metrics Initialization

**Module:** `metrics.py` → `initialize_metrics()` returns a fresh dict for each run.

All metrics are gated by warmup: events before day `_WARMUP_DAY = WARMUP_YEARS × DAYS_PER_YEAR` are not recorded.

### Recording Functions

| Function | Called When | What It Records |
|---|---|---|
| `record_screening(metrics, p, cancer, result)` | After each successful screening | `n_screened[cancer]`, `n_screened_established[cancer]`, `n_screened_by_test[test]`, `cervical_results[result]`, `cervical_results_by_test[test][result]`, `cervical_by_age_stratum[stratum][result]` |
| `record_exit(metrics, reason, patient, day)` | When a patient exits the system | `n_exited`, `exits_by_reason[reason]`, `n_treated` (if treated), `n_ltfu` (if LTFU), `days_in_system`, `days_in_system_screened` (if `visit_count ≥ 2`) |

### Key Metrics Tracked

| Category | Metrics |
|---|---|
| Volume | `n_patients`, `n_eligible_any`, `n_eligible[cancer]`, `n_unscreened`, `n_reschedule` |
| Entry/Exit | `entries_by_destination[provider]`, `entries_by_type[type]`, `exits_by_reason[reason]`, `arrivals_by_source[source]`, `exits_by_source[subtype]` |
| Retention | `days_in_system` (all patients), `days_in_system_screened` (visit_count ≥ 2 only) |
| Screenings | `n_screened[cancer]`, `n_screened_established[cancer]`, `n_screened_by_test[test]` |
| Cervical Results | `cervical_results[result]`, `cervical_results_by_test[test][result]`, `cervical_by_age_stratum[stratum][result]` |
| Follow-Up | `n_colposcopy`, `colposcopy_results[grade]`, `n_treatment[type]` |
| Lung Funnel | `lung_eligible` → `lung_referral_placed` → `lung_ldct_scheduled` → `lung_ldct_completed` → `lung_result_communicated` → `lung_biopsy_referral` → `lung_biopsy_scheduled` → `lung_biopsy_completed` → `lung_malignancy_confirmed` → `lung_treatment_given` |
| Outcomes | `n_treated`, `n_ltfu`, `n_exited` |
| LTFU Breakdown | `ltfu_unscreened`, `ltfu_queue_primary`, `ltfu_queue_secondary`, `ltfu_queue_treatment` |
| Wait Times | `wait_times[procedure]` (list of days completed), `wait_times_abandoned[procedure]` (list of days before abandoning) |
| Demand/Capacity | `daily_screening_demand`, `daily_secondary_demand`, `daily_treatment_demand` — each: list of `(demand, supplied, overflow)` tuples per workday |
| Population | `mortality_count`, `pool_size_snapshot` (sampled every 30 days), `year_checkpoints` |

### Revenue Analysis

`compute_revenue(metrics)`:

**Realized revenue** = procedures completed × CPT rate from `PROCEDURE_REVENUE`:

| Procedure | Rate (USD) | Config Key |
|---|---|---|
| Cytology | $156 | `PROCEDURE_REVENUE["cytology"]` |
| HPV-alone | $198 | `PROCEDURE_REVENUE["hpv_alone"]` |
| Colposcopy | $312 | `PROCEDURE_REVENUE["colposcopy"]` |
| LEEP | $847 | `PROCEDURE_REVENUE["leep"]` |
| Cone biopsy | $1,240 | `PROCEDURE_REVENUE["cone_biopsy"]` |
| LDCT | $285 | `PROCEDURE_REVENUE["ldct"]` |
| Lung biopsy | $2,100 | `PROCEDURE_REVENUE["lung_biopsy"]` |
| Lung treatment | $18,500 | `PROCEDURE_REVENUE["lung_treatment"]` |
| Surveillance | $0 | `PROCEDURE_REVENUE["surveillance"]` |

**Foregone revenue** = patients lost at LTFU nodes × missed procedure cost:

| LTFU Source | Missed Procedure | Calculation |
|---|---|---|
| Unscreened (declined) | Avg cervical screening | `ltfu_unscreened × avg(cytology_rate, hpv_alone_rate)` |
| Queue LTFU — primary | Avg cervical screening | `ltfu_queue_primary × avg(cytology_rate, hpv_alone_rate)` |
| Queue LTFU — secondary | Colposcopy | `ltfu_queue_secondary × colposcopy_rate` |
| Lung screening LTFU | LDCT | `max(lung_eligible - lung_ldct_completed, 0) × ldct_rate` |
| Lung biopsy LTFU | Lung biopsy | `max(lung_biopsy_referral - lung_biopsy_completed, 0) × lung_biopsy_rate` |

### Computed Rates

`compute_rates(metrics)` derives:
- `screening_rate_cervical_pct`: cervical screened / total patients
- `screening_rate_lung_pct`: lung screened / lung eligible
- `abnormal_rate_cervical_pct`: abnormal cervical results / cervical screened
- `colposcopy_completion_pct`: colposcopies / abnormal results
- `treatment_completion_pct`: cervical excisional treatments (LEEP + cone) / colposcopies
- `ltfu_rate_pct`: total LTFU / total patients

---

## 16. Database Persistence

**Module:** `db.py` → `SimulationDB`

SQLite database (`DB_PATH = "nyp_simulation.db"`) with WAL mode and `PRAGMA synchronous=NORMAL` for performance.

### Schema

**`patients` table** — one row per exited patient:
`patient_id (PK), age_at_entry, age_at_exit, race, insurance, is_established, simulation_entry_day, exit_day, exit_reason, visit_count, has_cervix, smoker, pack_years, cervical_result, lung_result, colposcopy_result, treatment_type, last_cervical_screen_day, last_lung_screen_day`

**`events` table** — full event log:
`id (PK, auto), patient_id (FK), day, event` with `UNIQUE(patient_id, day, event)` constraint.

### Indexes

- `idx_patients_exit_reason`, `idx_patients_is_established`, `idx_patients_race`
- `idx_events_patient_id`, `idx_events_day`
- `idx_patients_last_cervical_screen`, `idx_patients_last_lung_screen`

### Write Strategy

- Batch flush every `DB_FLUSH_INTERVAL = 30` days via `_flush_exited_patients()`
- `flush_patients()` — `executemany` with `INSERT OR IGNORE` (idempotent)
- `flush_events()` — same pattern for the event log
- Final forced flush at simulation end

### Query Methods

- `get_patient_history(patient_id)` → chronological event log
- `get_patient(patient_id)` → demographic/outcome row
- `count_by_exit_reason()` → `{reason: count}`
- `count_established_vs_new()` → `{established: N, new_entrant: M}`
- `summary_stats()` → total, by_exit_reason, mean_visit_count, mean_age_at_exit
- `query(sql, params)` → arbitrary read-only SQL

---

## 17. Visualizations

The notebook (`simulation.ipynb`) produces 30+ visualizations organized by category:

| Category | Visualizations |
|---|---|
| Population | Pool size over time, entry density, exit breakdown, retention distribution (all + returning patients with visit_count ≥ 2), mortality & pool dynamics |
| Entries & Exits | Annual entries & exits by reason (stacked area chart: green entries above zero; stacked exits below zero color-coded by mortality, attrition, ineligible, LTFU, queue LTFU secondary, queue LTFU treatment; net flow dashed line) |
| Capacity & Queues | Procedure capacity utilization (horizontal bars with capacity overlay), queue wait time distribution, follow-up scheduling delays, screening queue length, demand vs. capacity (primary / secondary / treatment), turnaround delays |
| Screening Volume | Annual screening volume, cervical uptake, lung uptake, first-stage screenings by modality, cervical uptake rate, lung uptake rate |
| Clinical Cascade | Cervical cascade (funnel), cervical follow-up pathway, lung cascade, lung follow-up pathway |
| Revenue | Annual cumulative revenue, foregone revenue by LTFU source (3-year rolling average, 4 sources: unscreened, queue LTFU primary, queue LTFU secondary, lung screening LTFU + lung biopsy LTFU) |

All visualizations respect the warmup filter — only post-warmup data is plotted.

---

## Turnaround & Scheduling Delays

These are **configured scheduling windows**, not simulation outputs. Queue wait time is the *additional* delay beyond these windows when capacity is insufficient.

| Step | Delay (days) | Config Key |
|---|---|---|
| Provider visit → Screening | 10 | `TURNAROUND_DAYS["provider_to_screening"]` |
| Cytology → Result | 7 | `TURNAROUND_DAYS["cytology"]` |
| Co-test → Result | 10 | `TURNAROUND_DAYS["co_test"]` |
| HPV-alone → Result | 5 | `TURNAROUND_DAYS["hpv_alone"]` |
| LDCT → Result notification | 5 | `TURNAROUND_DAYS["ldct_notification"]` |
| Positive LDCT → Diagnostic workup | 21 | `TURNAROUND_DAYS["ldct_to_workup"]` |
| Normal result → Patient notification | 10 | `TURNAROUND_DAYS["notification_normal"]` |
| Abnormal result → Patient notification | 14 | `TURNAROUND_DAYS["notification_abnormal"]` |
| Colposcopy → Pathology result | 10 | `TURNAROUND_DAYS["colposcopy_result"]` |
| Lung biopsy → Pathology result | 10 | `TURNAROUND_DAYS["lung_biopsy_result"]` |
| Abnormal cytology → Colposcopy appt | 50 | `FOLLOWUP_DELAY_DAYS["colposcopy"]` |
| HSIL → Expedited colposcopy | 32 | `FOLLOWUP_DELAY_DAYS["colposcopy_hsil"]` |
| CIN2/3 → LEEP | 14 | `FOLLOWUP_DELAY_DAYS["leep"]` |
| CIN2/3 → Cone biopsy | 21 | `FOLLOWUP_DELAY_DAYS["cone_biopsy"]` |
| RADS 4 → Lung biopsy | 21 | `FOLLOWUP_DELAY_DAYS["lung_biopsy"]` |
| Malignancy → Lung treatment | 21 | `FOLLOWUP_DELAY_DAYS["lung_treatment"]` |

### ASCCP Time-to-Colposcopy Guidelines

| Result Severity | Target Window | Config Key |
|---|---|---|
| ASCUS / LSIL | Within 3 months (90 days) | `ABNORMAL_FOLLOWUP_DAYS["ASCUS_LSIL"]` |
| HSIL / ASC-H | Within 1 month (30 days) | `ABNORMAL_FOLLOWUP_DAYS["HSIL_ASCH"]` |

Source: 2019 ASCCP Risk-Based Management Consensus Guidelines (Perkins et al., J Low Genit Tract Dis 2020). `ABNORMAL_FOLLOWUP_DAYS` lives in `validation.py` as a literature benchmark; it is not currently consumed by the scheduler, which uses the simpler `FOLLOWUP_DELAY_DAYS["colposcopy"] = 50` / `FOLLOWUP_DELAY_DAYS["colposcopy_hsil"] = 32` instead.

---

## Scale Interpretation

| Simulation Unit | Real-World Equivalent |
|---|---|
| 1 simulated patient | 100 NYC eligible women (`POPULATION_SCALE_FACTOR = 100`) |
| 15,000 sim patients | ~1.5 million NYC eligible women |
| 1 cervical screen | 100 cervical screens in the real population |
| 1 LDCT | 100 LDCTs in the real population |

All volume outputs should be multiplied by `POPULATION_SCALE_FACTOR = 100` when extrapolating to real-world planning figures.

---

*All clinical probabilities and revenue rates are PLACEHOLDERS unless otherwise sourced — replace with NYP EHR/finance data before operational use. Parameters marked PLACEHOLDER in `parameters.py` are clearly annotated.*
