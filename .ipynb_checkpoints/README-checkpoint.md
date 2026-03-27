# NYP Women's Health Screening Simulation

A discrete-event simulation (DES) of a multi-cancer women's health screening program at NewYork-Presbyterian. The model simulates patient flow from provider arrival through screening, clinical follow-up, and system exit — quantifying drop-off at each step and supporting operational planning.

---

## Project Context

The simulation covers **screening → diagnosis**, not treatment economics. The primary objective is to:
- Model patient attrition through the screening pathway
- Identify missed screening opportunities
- Compare **fragmented** (current) vs. **coordinated** (future) workflow scenarios
- Support ROI analysis for expanding screening programs

Cancer pathways modelled:
| Cancer | Status |
|---|---|
| Cervical | ✅ Full pathway (ASCCP-aligned) |
| Lung | 🟡 Stub (LD CT → NEGATIVE/POSITIVE) |
| Breast | 🟡 Stub (mammogram → NEGATIVE/POSITIVE) |
| Colorectal | 🟡 Stub (colonoscopy/FIT → NEGATIVE/POSITIVE) |
| Osteoporosis | 🟡 Stub (DEXA → NEGATIVE/POSITIVE) |

Stubs are structured and ready to be filled in as clinical pathways are defined.

---

## File Architecture

```
NYP/
│
├── config.py                        # Central configuration — all parameters live here
├── patient.py                       # Shared Patient dataclass (data contract)
├── population.py                    # Population sampler stub — REPLACE with provided code
│
├── screening.py                     # Steps 2–3: eligibility, test assignment, results
├── followup.py                      # Steps 4–5: colposcopy, CIN grading, treatment
├── metrics.py                       # Metric collection, rate computation, reporting
│
├── 01_arrivals.ipynb                # Sophia's arrivals simulation (do not modify)
├── 02_screening.ipynb               # Demo + tests for screening layer
├── 03_results_followup.ipynb        # Demo + tests for follow-up pathways
├── 04_simulation_runner.ipynb       # Full end-to-end orchestration
└── 05_metrics_outputs.ipynb         # Analysis, funnel, scenario comparison
```

### Design Principle
Logic lives in **`.py` modules** — clean, importable, testable.
**Notebooks** are thin wrappers that import from the modules, demonstrate usage, and run experiments. This means you can test any piece in isolation without running the full simulation.

---

## Simulation Flow

```
Women Population Eligible in NYC
        │
        ▼
┌─────────────────────────────────────────────────────────┐
│  STEP 1 — ARRIVAL  [01_arrivals.ipynb — Sophia's layer] │
│                                                         │
│  Patient enters via:                                    │
│    PCP (35%)  │  Gynecologist (25%)                     │
│    Specialist (20%)  │  ER (20%)                        │
│                                                         │
│  Queue management: outpatient scheduling, drop-in       │
│  routing, ER critical returns, capacity constraints.    │
└──────────────────────────┬──────────────────────────────┘
                           │  patient "seen" by provider
                           ▼
┌─────────────────────────────────────────────────────────┐
│  STEP 2 — SCREENING DECISION  [screening.py]            │
│                                                         │
│  Is the patient eligible?                               │
│    Cervical:     age 21–65, has cervix                  │
│    Lung:         age 50–80, ≥20 pack-years              │
│    Breast:       age 40–80                              │
│    Colorectal:   age 45–80                              │
│    Osteoporosis: age 65+ or BMI < 19                    │
│                                                         │
│  Not eligible / declines → UNSCREENED node:             │
│    Will reschedule? (50%) → re-enter queue              │
│    Will not reschedule?   → EXIT SYSTEM                 │
└──────────────────────────┬──────────────────────────────┘
                           │  eligible
                           ▼
┌─────────────────────────────────────────────────────────┐
│  STEP 3 — SCREENING METHODS & RESULTS  [screening.py]   │
│                                                         │
│  Cervical test assigned by age (ASCCP guidelines):      │
│    Age 21–29 → Cytology (Pap)                          │
│    Age 30–65 → HPV-alone or Co-test                    │
│                                                         │
│  Cervical result (6 categories):                        │
│    NORMAL  │  ASCUS  │  LSIL                            │
│    ASC-H   │  HSIL   │  HPV_POS_NORMAL_CYTO             │
│                                                         │
│  Other cancers: NEGATIVE / POSITIVE (binary stub)       │
└──────────────────────────┬──────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  STEP 4 — RESULT ROUTING  [followup.py]                 │
│                                                         │
│  NORMAL          → routine surveillance interval        │
│  ASCUS / LSIL    → colposcopy (LTFU check: 20% drop)   │
│  ASC-H / HSIL    → colposcopy (expedited)              │
│  HPV+/normal cyto→ 1-year repeat (40%) or colposcopy   │
│                                                         │
│  Other cancers: POSITIVE → referral stub                │
└──────────────────────────┬──────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  STEP 5 — CLINICAL FOLLOW-UP  [followup.py]             │
│                                                         │
│  Colposcopy → CIN grade draw:                           │
│    CIN1 / NORMAL  → surveillance (1-year repeat)        │
│    CIN2 / CIN3    → excisional treatment                │
│                      LEEP (default) or Cold Knife Cone  │
│                                                         │
│  LTFU check before treatment: 10% drop-out             │
└──────────────────────────┬──────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  STEP 6 — EXIT / RE-ENTRY  [04_simulation_runner.ipynb] │
│                                                         │
│  Treated      → post-treatment delay → surveillance     │
│                 loop (re-enters system)                 │
│  Surveillance → returns at 1-year interval              │
│  Untreated    → exits system                            │
│  LTFU         → exits system                            │
└─────────────────────────────────────────────────────────┘
```

---

## Module Reference

### `config.py` — Central Configuration
All parameters in one place. Change values here; everything picks them up automatically.

Key sections:
- `ELIGIBILITY` — age and clinical rules per cancer type
- `SCREENING_TESTS` — test modalities by cancer and age stratum
- `SCREENING_INTERVALS_DAYS` — how often each test recurs
- `CERVICAL_RESULT_PROBS` — multinomial result probabilities (by age stratum + test)
- `COLPOSCOPY_RESULT_PROBS` — CIN grade probabilities conditional on triggering result
- `LTFU_PROBS` — loss-to-follow-up rates at each decision node
- `WORKFLOW_MODE` — `"fragmented"` (current state) or `"coordinated"` (future state)
- `CAPACITIES` — daily slot counts for each screening/procedure resource

> ⚠️ All probability values are **PLACEHOLDERS**. Replace with NYP EHR-derived rates and ASCCP risk table values as they become available.

---

### `patient.py` — Patient Dataclass
The **shared data contract** between all modules. Every patient object carries:

| Field group | Fields |
|---|---|
| Core (Sophia-compatible) | `patient_id`, `day_created`, `patient_type`, `destination`, `critical_status`, `scheduled_day`, `wait_days`, `return_count` |
| Demographics | `age`, `race`, `ethnicity`, `insurance` |
| Clinical flags | `has_cervix`, `smoker`, `pack_years`, `bmi`, `hpv_positive`, `hpv_vaccinated`, `prior_abnormal_pap`, `prior_cin` |
| Simulation state | `active`, `current_stage`, `willing_to_reschedule` |
| Screening history | `last_*_screen_day` (one per cancer type) |
| Results | `cervical_result`, `lung_result`, `breast_result`, `colorectal_result`, `osteo_result` |
| Follow-up state | `colposcopy_result`, `treatment_type` |
| Exit | `exit_reason` |
| Log | `event_log` — timestamped list of every event for this patient |

Helper methods: `p.log(day, event)`, `p.exit_system(day, reason)`, `p.print_history()`.

> **Backward compatibility**: Our `Patient` is a superset of Sophia's. All her queue-management functions work unchanged on these extended objects.

---

### `population.py` — Population Sampler (Stub)
Defines the interface contract:
```python
sample_patient(patient_id, day_created, destination, patient_type) -> Patient
```
The stub draws demographics from rough NYC distributions so the simulation runs end-to-end immediately.

> 🔄 **To swap in the provided population sampling code**: replace the body of `sample_patient()` only. The function signature must not change.

---

### `screening.py` — Steps 2–3
Key functions:

| Function | Purpose |
|---|---|
| `get_eligible_screenings(p)` | Returns list of cancer types patient qualifies for |
| `is_due_for_screening(p, cancer, day)` | Checks whether screening interval has elapsed |
| `get_cervical_age_stratum(age)` | Maps age → `"young"` / `"middle"` / `"older"` |
| `assign_screening_test(p, cancer)` | Picks test modality (age-stratified for cervical) |
| `draw_cervical_result(p, test)` | Multinomial result draw with risk-factor adjustments |
| `draw_other_cancer_result(cancer)` | Binary NEGATIVE/POSITIVE draw (stub) |
| `run_screening_step(p, cancer, day)` | Full screening event: eligibility → test → result → updates patient |
| `handle_unscreened(p, day)` | Decision node: will patient reschedule? |

---

### `followup.py` — Steps 4–5
Key functions:

| Function | Purpose |
|---|---|
| `route_cervical_result(p, day, metrics)` | Routes result to colposcopy / surveillance / exit |
| `run_colposcopy(p, day, metrics)` | Draws CIN grade, records on patient |
| `run_treatment(p, day, metrics)` | LTFU check → surveillance or excisional treatment |
| `run_cervical_followup(p, day, metrics)` | **Main orchestrator** — chains all cervical follow-up steps |
| `run_stub_followup(p, cancer, result, day, metrics)` | Simplified follow-up for non-cervical cancers |

Loss-to-follow-up is checked at two explicit nodes:
1. **Post-abnormal screen** → before colposcopy (20% LTFU, configurable)
2. **Post-colposcopy** → before treatment (10% LTFU, configurable)

---

### `metrics.py` — Metrics & Reporting
Key functions:

| Function | Purpose |
|---|---|
| `initialize_metrics()` | Creates a fresh metrics dict for one run |
| `record_screening(metrics, p, cancer, result)` | Log a completed screen |
| `record_exit(metrics, reason)` | Log a patient exit and classify outcome |
| `compute_rates(metrics)` | Derive key percentages (screening rate, LTFU rate, etc.) |
| `print_summary(metrics)` | Formatted report to stdout |
| `print_patient_trace(patients, n)` | Print event logs for debugging |

---

## Notebooks

### `01_arrivals.ipynb` — Sophia's Arrivals Simulation *(do not modify)*
Handles patient generation, provider queues, scheduling, and ER logic. The **handoff point** is when a patient is marked "seen" by a provider — that triggers Steps 2–6.

### `02_screening.ipynb` — Screening Layer Demo & Tests
- Eligibility edge-case verification
- Test assignment by age stratum
- Result distribution sanity check (observed vs. expected %)
- Smoke test: 30 patients through `run_screening_step`

### `03_results_followup.ipynb` — Follow-Up Pathway Demo & Tests
- Routing distribution per result category (stochastic, 200 trials each)
- Colposcopy CIN grade distribution vs. config expectations
- End-to-end trace: 10 patients from screening result → disposition
- Mini summary report

### `04_simulation_runner.ipynb` — Full Orchestration
Connects Sophia's arrival layer to Steps 2–6:
1. `%run`s Sophia's notebook to load her queue functions
2. Overrides patient creation with `sample_patient()` (enriched demographics)
3. Wraps her provider queue processor to trigger screening after each "seen" event
4. Runs the full SimPy simulation
5. Prints both arrival summary (Sophia's) and screening summary (Steps 2–6)

### `05_metrics_outputs.ipynb` — Analysis & Reporting
- Full summary report
- Key rate table
- Cervical results by age stratum
- **Pathway funnel** — shows count and % drop at every step
- **Workflow scenario comparison** — fragmented vs. coordinated side-by-side
- Plot placeholder (bar chart once matplotlib is confirmed)

---

## How to Run

**Option 1 — Jupyter Lab (recommended)**
```bash
jupyter lab --notebook-dir=/path/to/NYP
```
Then open notebooks in order: `02` → `03` → `04` → `05`.

**Option 2 — Run modules directly**
```python
import sys
sys.path.insert(0, '/path/to/NYP')

import random, config as cfg
from population import sample_patient
from screening import get_eligible_screenings, run_screening_step
from followup import run_cervical_followup
from metrics import initialize_metrics, print_summary

random.seed(42)
metrics = initialize_metrics()

for i in range(100):
    p = sample_patient(i, 0, 'gynecologist', 'outpatient')
    metrics['n_patients'] += 1
    for cancer in get_eligible_screenings(p):
        result = run_screening_step(p, cancer, 0)
        if result and cancer == 'cervical':
            run_cervical_followup(p, 0, metrics)

print_summary(metrics)
```

---

## Integrating the Provided Population Sampling Code

When the population sampling code is delivered, open `population.py` and replace everything below the `# ── Public interface ──` comment with the provided implementation. The function signature must remain:
```python
def sample_patient(patient_id: int, day_created: int,
                   destination: str, patient_type: str) -> Patient:
```
No other files need to change.

---

## Replacing Placeholder Parameters

All placeholder values are marked with `# PLACEHOLDER` comments in `config.py`. Priority order for replacement:

1. **`CERVICAL_RESULT_PROBS`** — from NYP EHR abnormal Pap rates (confirmed roughly consistent with national expectations per clinical team)
2. **`COLPOSCOPY_RESULT_PROBS`** — from ASCCP risk table slides (clinician to share)
3. **`LTFU_PROBS`** — from NYP EHR attrition analysis (Neil's logistic regression outputs)
4. **`POSITIVE_RATES`** (other cancers) — from literature / NYP data as each pathway is built out

---

## Next Steps

| Priority | Task |
|---|---|
| 🔴 Immediate | Swap in provided population sampling code into `population.py` |
| 🔴 Immediate | Replace `CERVICAL_RESULT_PROBS` with NYP EHR rates |
| 🔴 Immediate | Confirm procedure codes for LEEP, cone biopsy, colposcopy, CIN grading |
| 🟡 Near-term | Replace `COLPOSCOPY_RESULT_PROBS` with ASCCP risk table values |
| 🟡 Near-term | Replace `LTFU_PROBS` with EHR-derived attrition rates |
| 🟡 Near-term | Build out breast cancer pathway (next clinical priority) |
| 🟢 Later | Add coordinated-mode capacity assumptions to `config.py` |
| 🟢 Later | Build lung, colorectal, osteoporosis pathways |
| 🟢 Later | Multi-replication run + variance analysis (`NUM_REPS` in config) |

---

## Dependencies

```
simpy >= 4.1.1      # discrete-event simulation engine
```
Install: `pip install simpy`

Optional (for plots in `05_metrics_outputs.ipynb`):
```
matplotlib
```

All other dependencies (`random`, `dataclasses`, `collections`, `typing`) are Python standard library.
