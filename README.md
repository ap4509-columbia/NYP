# NYP Women's Health Screening Simulation

A discrete-event simulation (DES) of a multi-cancer women's health screening program at NewYork-Presbyterian. The model simulates patient flow from provider arrival through screening, clinical follow-up, and system exit — quantifying drop-off at each step and supporting operational and financial planning.

---

## Project Context

**Scope:** screening → diagnosis (not treatment economics, except for foregone revenue analysis).

**Active cancers:** cervical and lung.

**Primary objectives:**
- Model patient attrition at every clinical decision node
- Identify missed screening opportunities and their revenue impact
- Compare **fragmented** (current state) vs. **coordinated** (future state) workflow scenarios
- Support ROI analysis for expanding the screening program

| Cancer | Status | Guideline |
|---|---|---|
| Cervical | ✅ Full pathway | USPSTF 2018 — cytology every 3 yrs (21–65) or HPV-alone every 5 yrs (30–65) |
| Lung | ✅ Full pathway | USPSTF 2021 — annual LDCT, age 50–80, ≥20 pack-years, current/quit ≤15 yrs |

---

## File Architecture

```
NYP/
│
├── README.md
├── LICENSE
│
├── src/                          # Core simulation modules
│   ├── config.py                 # All parameters, probabilities, and revenue rates
│   ├── patient.py                # Patient dataclass — shared data contract
│   ├── population.py             # Population sampler stub (replace with Yutong's code)
│   ├── screening.py              # Eligibility, test assignment, result draws
│   ├── followup.py               # Post-screening clinical pathways
│   ├── metrics.py                # Counters, rates, revenue analysis, reporting
│   └── scenarios.py              # Co-scheduling scenario definitions (future)
│
├── notebooks/                    # Jupyter notebooks
│   ├── 02_screening.ipynb        # Screening layer demo and tests
│   ├── 03_results_followup.ipynb # Follow-up pathway demo and tests
│   ├── 04_simulation_runner.ipynb# Full end-to-end orchestration
│   ├── 05_metrics_outputs.ipynb  # Analysis, funnels, revenue, plots
│   └── 06_scenario_analysis.ipynb# Co-scheduling comparison (future)
│
└── reference/                    # Source material — do not modify
    ├── initial_model_NYP_flow_simulation (1).ipynb  # Sophia's arrivals simulation
    └── Simulation_draft_Yutong.ipynb                # Yutong's draft (reference only)
```

**Design principle:** logic lives in `.py` modules — importable, testable, version-controlled cleanly. Notebooks are thin wrappers that call those modules, run scenarios, and show output. You never need to edit a notebook to change a clinical parameter.

---

## Simulation Flow

```
NYC Eligible Women Population
          │
          ▼
┌──────────────────────────────────────────────────────┐
│  STEP 1 — ARRIVAL  [Sophia's layer]                  │
│  Patient enters via PCP / Gynecologist / Specialist  │
│  Queue management, scheduling, capacity constraints  │
└────────────────────────┬─────────────────────────────┘
                         │  patient "seen" by provider
                         ▼
┌──────────────────────────────────────────────────────┐
│  STEP 2 — ELIGIBILITY CHECK  [screening.py]          │
│                                                      │
│  Cervical:  age 21–65, has cervix                    │
│  Lung:      age 50–80, ≥20 pack-years,               │
│             current smoker OR quit ≤15 years ago     │
│                                                      │
│  Not eligible / declines → UNSCREENED node:          │
│    50% willing to reschedule → re-enter queue        │
│    50% not willing         → EXIT (foregone revenue) │
└────────────────────────┬─────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────┐
│  STEP 3 — SCREENING  [screening.py]                  │
│                                                      │
│  Cervical (age-stratified, USPSTF 2018):             │
│    Age 21–29 → Cytology only, every 3 years          │
│    Age 30–65 → Cytology (3 yrs) or HPV-alone (5 yrs)│
│    Age 65+   → Stop screening (adequate prior hx)    │
│                                                      │
│  Lung (USPSTF 2021 / hospital flowchart):            │
│    LDCT order placed?      → LTFU if no              │
│    Patient schedules LDCT? → LTFU if no              │
│    LDCT completed → Lung-RADS v2022 result drawn     │
└────────────────────────┬─────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────┐
│  STEP 4 — RESULT ROUTING  [followup.py]              │
│                                                      │
│  Cervical cytology:                                  │
│    NORMAL              → routine surveillance        │
│    ASCUS/LSIL/ASC-H/HSIL → colposcopy (LTFU check)  │
│                                                      │
│  Cervical HPV-alone:                                 │
│    HPV_NEGATIVE        → routine surveillance        │
│    HPV_POSITIVE        → 40% 1-yr repeat / 60% colpo │
│                                                      │
│  Lung Lung-RADS v2022:                               │
│    RADS 0              → results communicated →      │
│                          repeat LDCT in 1–3 months   │
│    RADS 1 / 2          → results communicated →      │
│                          repeat LDCT in 12 months    │
│    RADS 3              → results communicated →      │
│                          repeat LDCT in 6 months     │
│    RADS 4A / 4B / 4X  → results communicated →      │
│                          biopsy pathway              │
│    Any tier, no communication → LTFU                 │
└────────────────────────┬─────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────┐
│  STEP 5 — CLINICAL FOLLOW-UP  [followup.py]          │
│                                                      │
│  Cervical colposcopy → CIN grade:                    │
│    NORMAL / CIN1 → surveillance                      │
│    CIN2 / CIN3   → LEEP or cone biopsy               │
│    LTFU check before treatment (10% drop)            │
│                                                      │
│  Lung biopsy chain (RADS 4A/4B/4X):                  │
│    Referral made? → scheduled? → completed?          │
│    → malignancy confirmed? → treatment given?        │
│    LTFU node at every step                           │
└────────────────────────┬─────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────┐
│  STEP 6 — EXIT / RE-ENTRY                            │
│  Treated      → surveillance loop                    │
│  Surveillance → returns at screening interval        │
│  LTFU / untreated → exits system                     │
└──────────────────────────────────────────────────────┘
```

---

## Module Reference

### `config.py` — Central Configuration
Every tunable value lives here. Change once, everything picks it up.

| Section | Contents |
|---|---|
| `ACTIVE_CANCERS` | Toggle which pathways run |
| `ELIGIBILITY` | Age ranges and clinical criteria per cancer |
| `SCREENING_TESTS` | Test modalities by cancer and age stratum |
| `SCREENING_INTERVALS_DAYS` | Recurrence interval per test |
| `CERVICAL_RESULT_PROBS` | Multinomial result probabilities (age + test stratified) |
| `LUNG_RADS_PROBS` | Lung-RADS v2022 category distribution |
| `LUNG_PATHWAY_PROBS` | Completion probability at each pre/post-LDCT step |
| `LUNG_RADS_REPEAT_INTERVALS` | Days until repeat LDCT by RADS category |
| `COLPOSCOPY_RESULT_PROBS` | CIN grade conditional on triggering result |
| `TREATMENT_ASSIGNMENT` | CIN grade → treatment modality |
| `LTFU_PROBS` | Drop-out rates at each clinical decision node |
| `PROCEDURE_REVENUE` | Revenue per procedure (CPT-referenced, PLACEHOLDER) |
| `CAPACITIES` | Daily slot counts per resource |

> ⚠️ All probability and revenue values are **PLACEHOLDERS**. Replace with NYP EHR-derived rates and finance data as they become available.

---

### `patient.py` — Patient Dataclass

| Field group | Fields |
|---|---|
| Core | `patient_id`, `day_created`, `patient_type`, `destination` |
| Demographics | `age`, `race`, `ethnicity`, `insurance` |
| Clinical flags | `has_cervix`, `smoker`, `pack_years`, `years_since_quit`, `bmi`, `hpv_positive`, `hpv_vaccinated`, `prior_abnormal_pap`, `prior_cin` |
| Simulation state | `active`, `current_stage`, `willing_to_reschedule` |
| Screening history | `last_cervical_screen_day`, `last_lung_screen_day` |
| Results | `cervical_result`, `lung_result` |
| Cervical follow-up | `colposcopy_result`, `treatment_type` |
| Lung follow-up | `lung_referral_placed`, `lung_ldct_scheduled`, `lung_biopsy_result` |
| Exit | `exit_reason` |
| Log | `event_log` — timestamped list of every event |

Helper methods: `p.log(day, event)`, `p.exit_system(day, reason)`, `p.print_history()`.

---

### `population.py` — Population Sampler (Stub)

Public interface — do not change the signature:
```python
sample_patient(patient_id, day_created, destination, patient_type) -> Patient
```
The stub draws demographics from NYC distributions so the simulation runs immediately. **Replace the body** with Yutong's code when it arrives — no other files need to change.

---

### `screening.py` — Steps 2–3

| Function | Purpose |
|---|---|
| `get_eligible_screenings(p)` | Returns cancer types patient qualifies for (filtered by `ACTIVE_CANCERS`) |
| `is_due_for_screening(p, cancer, day)` | Checks whether screening interval has elapsed |
| `get_cervical_age_stratum(age)` | Maps age → `"young"` / `"middle"` / `"older"` |
| `assign_screening_test(p, cancer)` | Picks test modality (age-stratified for cervical) |
| `draw_cervical_result(p, test)` | Multinomial result draw with risk-factor adjustments |
| `draw_lung_rads_result()` | Lung-RADS v2022 category draw |
| `run_lung_pre_ldct(p, day, metrics)` | Pre-LDCT pathway: referral → scheduling → LTFU nodes |
| `run_screening_step(p, cancer, day, metrics)` | Full screening event: eligibility → test → result |
| `handle_unscreened(p, day)` | Decision node: will patient reschedule? |

---

### `followup.py` — Steps 4–5

| Function | Purpose |
|---|---|
| `route_cervical_result(p, day, metrics)` | Routes cytology/HPV result to next step |
| `run_colposcopy(p, day, metrics)` | Draws CIN grade, updates patient |
| `run_treatment(p, day, metrics)` | LTFU check → surveillance or LEEP/cone |
| `run_cervical_followup(p, day, metrics)` | Full cervical pipeline orchestrator |
| `run_lung_followup(p, day, metrics)` | Full lung pipeline: RADS routing → biopsy chain |

LTFU is checked explicitly at every clinical decision node — not as a bulk end-of-pathway draw.

---

### `metrics.py` — Metrics and Revenue

| Function | Purpose |
|---|---|
| `initialize_metrics()` | Fresh metrics dict for one run |
| `record_screening(metrics, p, cancer, result)` | Log a completed screening |
| `record_exit(metrics, reason)` | Log a patient exit |
| `compute_rates(metrics)` | Screening rate, abnormal rate, LTFU rate, etc. |
| `compute_revenue(metrics)` | Realized + foregone revenue by procedure and LTFU node |
| `print_summary(metrics)` | Formatted clinical summary to stdout |
| `print_revenue_summary(metrics)` | Formatted revenue summary to stdout |
| `print_patient_trace(patients, n)` | Event logs for debugging |

#### Revenue analysis

`compute_revenue(metrics)` returns:
```python
{
    "realized_total":        float,   # revenue from completed procedures
    "foregone_total":        float,   # revenue lost to LTFU / unscreened
    "realized_by_procedure": dict,    # breakdown by procedure type
    "foregone_by_node": {
        "unscreened_cervical":          float,  # eligible but never screened
        "ltfu_post_abnormal_cervical":  float,  # abnormal but no colposcopy
        "ltfu_post_colposcopy":         float,  # colposcopy but no treatment
        "lung_no_ldct":                 float,  # eligible but no LDCT
        "lung_no_biopsy":               float,  # RADS 4 but no biopsy
    }
}
```

Revenue rates are set in `config.PROCEDURE_REVENUE` (CPT-referenced PLACEHOLDERs — replace with NYP contract rates).

---

### `scenarios.py` — Co-Scheduling Scenarios *(future)*

Four scenarios are defined — `baseline_fragmented`, `gyn_coordinated`, `coordinated_all`, `high_access_coordinated` — but not yet wired to the main simulation. Reserved for the co-scheduling improvement analysis phase.

---

## How to Run

**Option 1 — Jupyter Lab**
```bash
jupyter lab --notebook-dir=/path/to/NYP
```
Open notebooks from the `notebooks/` folder.

**Option 2 — Python directly**
```python
import sys
sys.path.insert(0, '/path/to/NYP/src')

import random
from population import sample_patient
from screening import get_eligible_screenings, run_screening_step
from followup import run_cervical_followup, run_lung_followup
from metrics import initialize_metrics, print_summary, print_revenue_summary

random.seed(42)
metrics = initialize_metrics()

for i in range(500):
    p = sample_patient(i, 0, 'gynecologist', 'outpatient')
    metrics['n_patients'] += 1
    for cancer in get_eligible_screenings(p):
        result = run_screening_step(p, cancer, 0, metrics)
        if result:
            if cancer == 'cervical':
                run_cervical_followup(p, 0, metrics)
            elif cancer == 'lung':
                run_lung_followup(p, 0, metrics)

print_summary(metrics)
print_revenue_summary(metrics)
```

---

## Replacing Placeholder Values

All placeholders are marked with `# PLACEHOLDER` in `config.py`. Priority order:

| Priority | Parameter | Source |
|---|---|---|
| 🔴 Now | `PROCEDURE_REVENUE` | NYP finance / contract rates |
| 🔴 Now | Population sampler | Yutong's code → drop into `population.py` |
| 🔴 Now | `CERVICAL_RESULT_PROBS` | NYP EHR abnormal Pap rates |
| 🟡 Soon | `COLPOSCOPY_RESULT_PROBS` | ASCCP risk table slides |
| 🟡 Soon | `LTFU_PROBS` | NYP EHR attrition analysis |
| 🟡 Soon | `LUNG_PATHWAY_PROBS` | NYP LDCT volume / referral data |
| 🟢 Future | `LUNG_RADS_PROBS` | NYP LDCT registry data |

---

## Next Steps

| Priority | Task |
|---|---|
| 🔴 Immediate | Build `runner.py` — `SimulationRunner` class with `run()`, `plot_all()`, `revenue_summary()` |
| 🔴 Immediate | Drop in Yutong's population sampling code |
| 🔴 Immediate | Replace all PLACEHOLDER values with NYP data |
| 🟡 Near-term | Wire `scenarios.py` into the runner for co-scheduling analysis |
| 🟡 Near-term | Build visualizations — cervical funnel, lung funnel, RADS distribution, revenue waterfall |
| 🟢 Future | Multi-replication runs + variance analysis (`NUM_REPS` in config) |

---

## Dependencies

```
matplotlib
```

Install: `pip install matplotlib`

All other dependencies (`random`, `dataclasses`, `collections`, `typing`) are Python standard library.
The simulation does **not** use SimPy — patient flow is modeled as a direct function call chain, not a coroutine-based event loop.
