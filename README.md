# NYP Women's Health Screening Simulation — Digital Twin

A discrete-event simulation (DES) of a multi-cancer women's health screening program at NewYork-Presbyterian. The model simulates patient flow from provider arrival through screening, clinical follow-up, and system exit — quantifying drop-off at each step and supporting operational and financial planning.

---

## What Is This? The Digital Twin Concept

A **digital twin** is a dynamic software model that mirrors the behavior of a real-world system over time. This simulation is a digital twin of NYP's women's health screening program: it replicates the actual patient journey — arrivals, scheduling, capacity constraints, clinical decisions, loss-to-follow-up, and procedure revenue — as a virtual environment you can run, modify, and measure without touching the real system.

**Why build a digital twin for a screening program?**

In a fragmented healthcare system, patients are lost at every step: an eligible woman arrives at her PCP but cervical screening isn't offered; a lung-eligible smoker gets an LDCT order placed but never schedules; an abnormal Pap result triggers a colposcopy referral that goes unfollowed. Each gap is invisible in aggregate metrics but traceable in a simulation that tracks every patient individually.

This twin answers questions like:
- **How many eligible patients are we missing, and where do they fall off?**
- **What is the revenue impact of each LTFU node?**
- **If we co-schedule cervical and lung screening in one GYN encounter, how much does LTFU improve?**
- **What happens to wait times if we expand LDCT slots by 20%?**
- **Under current staffing, how many days until the colposcopy queue backs up?**

The simulation is built to be calibrated with NYP EHR data — all clinical probabilities and revenue rates are clearly marked as `# PLACEHOLDER` in `config.py`. Once calibrated, it becomes a planning tool: run scenarios, compare outcomes, quantify ROI.

---

## Project Scope

**Active cancers:** cervical and lung.

**Simulation window:** configurable; default 365 days (1 year), extendable to 30 years.

**Primary objectives:**
- Model patient attrition at every clinical decision node
- Identify missed screening opportunities and their revenue impact
- Compare **fragmented** (current state) vs. **coordinated** (future state) workflow scenarios
- Support ROI analysis for expanding the screening program

| Cancer | Status | Guideline |
|---|---|---|
| Cervical | Full pathway | USPSTF 2018 — cytology every 3 yrs (age 21–65) or HPV-alone every 5 yrs (age 30–65) |
| Lung | Full pathway | USPSTF 2021 — annual LDCT, age 50–80, ≥20 pack-years, current smoker or quit ≤15 yrs |

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
│   ├── population.py             # Population sampler (NYC demographics)
│   ├── screening.py              # Eligibility, test assignment, result draws
│   ├── followup.py               # Post-screening clinical pathways
│   ├── metrics.py                # Counters, rates, revenue analysis, reporting
│   ├── runner.py                 # SimulationRunner — day-by-day orchestration + queue engine
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

**Design principle:** all logic lives in `.py` modules — importable, testable, version-controlled cleanly. Notebooks are thin wrappers that call those modules, run scenarios, and display output. You never need to edit a notebook to change a clinical parameter.

---

## How the Simulation Works — End to End

### Time Model

The simulation runs as a **day-by-day tick loop**. The clock is an integer (`day = 0, 1, 2, …, n_days - 1`). Every day, the runner:

1. Processes all follow-up appointments that are due today (before new arrivals)
2. Generates new patient arrivals (Poisson draw)
3. Routes arrivals to provider queues (outpatient or drop-in)
4. Seats patients at each provider (respecting capacity)
5. Runs screening and result routing for each seen patient
6. Schedules future follow-up appointments for abnormal results

This order matters: follow-ups are processed first so that procedure slots (colposcopy, LEEP, biopsy) are consumed before the day's new patients arrive, accurately modeling finite capacity.

---

### The Full Patient Journey

```
NYC Eligible Women Population
          │
          ▼
┌──────────────────────────────────────────────────────┐
│  STEP 1 — ARRIVAL                                     │
│  Patient enters via PCP / Gynecologist / Specialist / ER│
│  70% outpatient (scheduled in advance)               │
│  30% drop-in (walk-in, seen same day if capacity)    │
│  ER patients are always drop-in                      │
└────────────────────────┬─────────────────────────────┘
                         │ patient "seen" by provider
                         ▼
┌──────────────────────────────────────────────────────┐
│  STEP 2 — ELIGIBILITY CHECK  [screening.py]          │
│                                                      │
│  Cervical:  age 21–65, has cervix                    │
│  Lung:      age 50–80, ≥20 pack-years,               │
│             current smoker OR quit ≤15 years ago     │
│                                                      │
│  Not eligible → three outcomes:                      │
│                                                      │
│  1. Not yet eligible (turning 21 soon, current       │
│     smoker approaching 20 pk-yrs, not yet 50)        │
│     → schedule return visit at eligibility date      │
│                                                      │
│  2. Permanently ineligible (no cervix, aged out,     │
│     never-smoker, quit window closed)                │
│     → EXIT silently (no revenue impact)              │
│                                                      │
│  3. Eligible but screening not offered [future]      │
│     → 50% reschedule / 50% EXIT (foregone revenue)  │
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
│  Lung (USPSTF 2021):                                 │
│    LDCT order placed?      → LTFU if no (28% drop)  │
│    Patient schedules LDCT? → LTFU if no (20% drop)  │
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

## The Queuing Engine — Detailed

The queue engine lives in `runner.py` and is the architectural core of the simulation. It manages three distinct queue types, each with different scheduling and capacity semantics.

### PatientQueues

```
PatientQueues
├── outpatient[provider][day]   → list[Patient]   Scheduled appointments
├── dropin[provider]            → list[Patient]   Today's walk-ins
├── followup[day]               → list[(Patient, context)]  Future procedures
└── daily_slots[day][procedure] → int             Remaining slots per procedure
```

---

### Queue Type 1: Outpatient Scheduling

Outpatients call ahead and book a future appointment. The scheduler finds the **earliest available day** on or after a lead-time window:

```python
def schedule_outpatient(self, p, provider, earliest_day) -> int:
    cap = _outpatient_cap(provider)   # e.g., 24 for GYN (80% of 30 slots)
    day = earliest_day
    while len(self.outpatient[provider][day]) >= cap:
        day += 1                      # push forward until a slot opens
    self.outpatient[provider][day].append(p)
    return day
```

**Key property:** The scheduler never overfills a day. Outpatient slots are always guaranteed — the scheduler will push the appointment as far forward as needed to find a free slot. This models a realistic booking system where patients get a confirmed date.

Lead-time windows (from `config.py`):
- PCP: 1–7 days
- Gynecologist: 7–28 days
- Specialist: 14–60 days

---

### Queue Type 2: Drop-In Processing

Drop-ins arrive and want to be seen today. On each tick, the runner processes them after seating outpatients:

```python
def process_day(self, provider, day):
    outpt_cap  = _outpatient_cap(provider)
    dropin_cap = _dropin_cap(provider)

    # 1. Seat all scheduled outpatients (guaranteed capacity)
    seen_outpts = self.outpatient[provider].pop(day, [])

    # 2. Drop-ins fill remaining capacity, including unused outpatient slots
    extra         = max(0, outpt_cap - len(seen_outpts))
    available     = dropin_cap + extra
    today_dropins = list(self.dropin.get(provider, []))
    seen_dropins  = today_dropins[:available]
    overflow      = today_dropins[available:]
    self.dropin[provider] = []        # clear queue regardless

    return seen_outpts + seen_dropins, overflow
```

**Key insight:** If fewer outpatients show up than their reserved cap (common in the early days of the sim before the schedule fills), those unused slots roll over to drop-ins. This maximizes utilization and matches how real clinic operations work.

**Overflow routing:**
- **PCP / GYN / Specialist overflow:** Patient is re-added to tomorrow's drop-in queue for the same provider. Wait time grows by 1 day.
- **ER overflow:** 70% retry the ER tomorrow. 30% are converted to a scheduled outpatient appointment at a random non-ER provider (PCP, GYN, or Specialist), earliest available slot.

Wait time is recorded as `day_seen - day_created` and logged per resource in `metrics["wait_times"]`.

---

### Queue Type 3: Follow-Up Scheduling

After a screening event produces an actionable result (abnormal Pap, RADS 4 lung), the patient is placed in a future-day follow-up queue:

```python
def schedule_followup(self, p, context, due_day) -> None:
    self.followup[due_day].append((p, context))
```

The `context` dict carries the clinical state: `{"cancer": "cervical", "step": "colposcopy"}` or `{"cancer": "lung", "step": "biopsy"}`.

On the due day, follow-ups are processed **before** new arrivals in `_tick()`:

```python
def _tick(self, day):
    # Step 1: Process follow-ups first (consumes procedure slots before walk-ins)
    for p, context in self._queues.get_due_followups(day):
        self._run_followup(p, context, day)

    # Step 2: Generate arrivals
    self._generate_arrivals(day)

    # Step 3: Process provider queues
    for provider in _ALL_PROVIDERS:
        seen, overflow = self._queues.process_day(provider, day)
        ...
```

---

### Procedure Slot Management

Each procedure type (colposcopy, LEEP, cone biopsy, lung biopsy, LDCT) has a finite daily capacity. Slots are initialized lazily from `config.CAPACITIES` on first access and decremented as patients consume them:

```python
def consume_slot(self, procedure, day) -> bool:
    if procedure not in self.daily_slots[day]:
        self.daily_slots[day][procedure] = cfg.CAPACITIES.get(procedure, 0)
    if self.daily_slots[day][procedure] > 0:
        self.daily_slots[day][procedure] -= 1
        return True
    return False   # fully booked today
```

**If no slot is available**, the patient is re-queued for tomorrow's same step:

```python
if not self._queues.consume_slot("colposcopy", day):
    self._queues.schedule_followup(
        p, {"cancer": "cervical", "step": "colposcopy"}, day + 1
    )
    return
```

This creates realistic queue buildup. If LDCT capacity is 4 slots/day and 10 RADS 4 patients all need biopsies on the same day, 6 of them wait — their biopsy delay is real time in the simulation.

Default capacities (from `config.CAPACITIES`, all PLACEHOLDER — replace with NYP actuals):

| Procedure | Slots/day |
|---|---|
| Cytology | 8 |
| HPV co-test | 6 |
| LDCT | 4 |
| Colposcopy | 3 |
| LEEP | 2 |
| Cone biopsy | 1 |
| Lung biopsy | 2 |

---

### How Cervical Follow-Up Flows Through the Queue

```
Day N:   Abnormal Pap result drawn during screening encounter
         → schedule_followup(p, {cancer: cervical, step: colposcopy}, day=N+30)

Day N+30: _run_followup() called
          → consume_slot("colposcopy", N+30)?
              NO  → re-queue for day N+31 (slot contention)
              YES → run_colposcopy() → CIN grade drawn
                    → run_treatment() → LTFU check (10% drop)
                        LTFU  → p.exit_reason = "lost_to_followup"
                        CIN1  → surveillance (no further steps)
                        CIN2+ → schedule_followup({step: leep}, day=N+30+14)

Day N+44: _run_followup() called
          → consume_slot("leep", N+44)?
              NO  → re-queue for day N+45
              YES → procedure complete; treatment revenue recorded
```

---

### How Lung Follow-Up Flows Through the Queue

```
Day N:   LDCT completed during screening encounter
         → run_lung_followup() called immediately (result communication + RADS routing)
             LTFU at communication? (10%) → exit
             RADS 0/1/2/3 → set next_ldct_day on patient; no queue entry
             RADS 4A/4B → schedule_followup({step: biopsy}, day=N+14)

Day N+14: _run_followup() called
          → consume_slot("lung_biopsy", N+14)?
              NO  → re-queue for day N+15
              YES → biopsy steps run:
                    referral placed? (80%) → LTFU if no
                    biopsy scheduled? (78%) → LTFU if no
                    biopsy completed? (88%) → LTFU if no
                    malignancy confirmed? (25%)
                      NO  → benign; resume surveillance
                      YES → treatment given? (92%) → LTFU if no
```

---

## LTFU — Loss to Follow-Up

LTFU is checked explicitly at every clinical decision node, not applied as a bulk end-of-pathway probability. This design means the simulation can tell you exactly *where* patients drop off and what the downstream revenue impact of each node is.

| LTFU Node | Default Drop Rate | Cancer |
|---|---|---|
| Unscreened eligible — won't reschedule | 50% | Both |
| Post-abnormal cervical result — no colposcopy | 20% | Cervical |
| Post-colposcopy — no treatment | 10% | Cervical |
| Pre-LDCT — no referral placed | 28% | Lung |
| Pre-LDCT — referral placed but not scheduled | 20% | Lung |
| RADS 4 — result not communicated | 10% | Lung |
| RADS 4 — biopsy referral not placed | 20% | Lung |
| RADS 4 — biopsy not scheduled | 22% | Lung |
| RADS 4 — biopsy not completed | 12% | Lung |
| Malignancy confirmed — no treatment | 8% | Lung |

> All values are PLACEHOLDERs. Replace with NYP EHR-derived attrition rates.

---

## Module Reference

### `config.py` — Central Configuration

Every tunable value lives here. Change once, everything picks it up.

| Section | Contents |
|---|---|
| `ACTIVE_CANCERS` | Toggle which pathways run |
| `DAILY_PATIENTS` | Expected arrivals per day |
| `DESTINATION_PROBS` | Provider routing weights |
| `PATIENT_TYPE_PROBS` | Outpatient vs. drop-in mix |
| `PROVIDER_CAPACITY` | Daily slot counts per provider |
| `OUTPATIENT_FRACTION` | Fraction of slots reserved for outpatients |
| `OUTPATIENT_LEAD_DAYS` | Scheduling lead-time range per provider |
| `ER_OVERFLOW_RETRY_PROB` | Probability ER overflow retries ER (vs. converts to outpatient) |
| `ELIGIBILITY` | Age ranges and clinical criteria per cancer |
| `SCREENING_INTERVALS_DAYS` | Recurrence interval per test |
| `CERVICAL_RESULT_PROBS` | Multinomial result probabilities (age + test stratified) — PLACEHOLDER |
| `LUNG_RADS_PROBS` | Lung-RADS v2022 category distribution — PLACEHOLDER |
| `LUNG_PATHWAY_PROBS` | Completion probability at each pre/post-LDCT step — PLACEHOLDER |
| `LUNG_RADS_REPEAT_INTERVALS` | Days until repeat LDCT by RADS category |
| `COLPOSCOPY_RESULT_PROBS` | CIN grade conditional on triggering result — PLACEHOLDER |
| `TREATMENT_ASSIGNMENT` | CIN grade → treatment modality |
| `LTFU_PROBS` | Drop-out rates at each clinical decision node — PLACEHOLDER |
| `PROCEDURE_REVENUE` | Revenue per procedure (CPT-referenced) — PLACEHOLDER |
| `CAPACITIES` | Daily slot counts per procedure type |
| `FOLLOWUP_DELAY_DAYS` | Days to follow-up appointment by type |

> ⚠️ All probability and revenue values are **PLACEHOLDERS**. Replace with NYP EHR-derived rates and finance data as they become available.

---

### `patient.py` — Patient Dataclass

A single `Patient` dataclass is the shared data contract across all modules. Every module reads from and writes to the same object — no copies, no translation layers.

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

### `population.py` — Population Sampler

Generates individual patients from NYC distributions:

```python
sample_patient(patient_id, day_created, destination, patient_type) -> Patient
```

Current stub draws from:
- **Age:** weighted brackets (18% age 21–29, 22% 30–39, 20% 40–49, etc.)
- **Race:** White 32%, Black 22%, Hispanic 28%, Asian 13%, Other 5%
- **Insurance:** Commercial 45%, Medicaid 30%, Medicare 15%, Uninsured 10%
- **Smoking:** 13% current; ~30% of non-smokers are former smokers with 5–40 pack-years
- **HPV:** 25% positive baseline, adjusted by vaccination by age cohort
- **Hysterectomy:** prevalence increasing with age (1% at 21–39 → 18% at 60–80)

**To replace:** drop Yutong's code into the function body. No other files change — the function signature is the stable interface.

---

### `screening.py` — Steps 2–3

| Function | Purpose |
|---|---|
| `get_eligible_screenings(p)` | Returns cancer types patient qualifies for (filtered by `ACTIVE_CANCERS`) |
| `days_until_eligible(p, cancer)` | Days until patient becomes eligible (`0` = now, `>0` = future date, `-1` = never) |
| `is_due_for_screening(p, cancer, day)` | Checks whether screening interval has elapsed |
| `get_cervical_age_stratum(age)` | Maps age → `"young"` / `"middle"` / `"older"` |
| `assign_screening_test(p, cancer)` | Picks test modality (age-stratified for cervical) |
| `draw_cervical_result(p, test)` | Multinomial result draw; applies 1.5–1.8× inflation for HPV+ or prior CIN |
| `draw_lung_rads_result()` | Lung-RADS v2022 category draw |
| `run_lung_pre_ldct(p, day, metrics)` | Pre-LDCT pathway: referral → scheduling → LTFU nodes |
| `run_screening_step(p, cancer, day, metrics)` | Full screening event: eligibility → test → result |
| `handle_unscreened(p, day)` | Reserved: decision node for eligible patients not offered screening (future use) |

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

#### Revenue Analysis

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

Revenue rates are set in `config.PROCEDURE_REVENUE` (CPT-referenced PLACEHOLDERs — replace with NYP contract rates):

| Procedure | CPT Reference | Placeholder Rate |
|---|---|---|
| Cytology | 88141 | $156 |
| HPV test | 87624 | $198 |
| LDCT | 71250 | $285 |
| Colposcopy | 57454 | $312 |
| LEEP | 57522 | $847 |
| Cone biopsy | 57520 | $1,250 |
| Lung biopsy | 32405 | $2,100 |

---

### `runner.py` — SimulationRunner

The orchestration layer. Owns the clock, all queues, and the daily tick.

```python
sim = SimulationRunner(n_days=365, seed=42)
metrics = sim.run()
sim.summary()           # clinical funnel summary
sim.revenue_summary()   # realized vs. foregone revenue
sim.plot_all()          # 2×2 chart: cervical funnel, lung funnel, RADS dist, revenue
sim.plot_queues()       # drop-in overflow by provider
```

**Key internal methods:**

| Method | Purpose |
|---|---|
| `_tick(day)` | One full simulation day: follow-ups → arrivals → provider queues |
| `_generate_arrivals(day)` | Poisson draw; route outpatients to scheduled slots, drop-ins to today |
| `_route_overflow(overflow, provider, day)` | Re-queue overflow (ER vs. non-ER logic) |
| `_screen_patient(p, day)` | Eligibility check → screening → result routing → follow-up scheduling |
| `_run_followup(p, context, day)` | Dispatch to cervical or lung follow-up step |
| `_cervical_followup_step(p, step, day)` | Consume slot → run colposcopy or treatment |
| `_lung_result_routing(p, day)` | RADS routing on LDCT day; schedule biopsy if RADS 4 |
| `_lung_biopsy_step(p, day)` | Consume biopsy slot; re-queue if full |

---

### `scenarios.py` — Co-Scheduling Scenarios *(future)*

Four scenarios defined — not yet wired to the main runner. Reserved for the co-scheduling improvement analysis phase:

| Scenario | Description |
|---|---|
| `baseline_fragmented` | Current state: each provider screens only their domain |
| `gyn_coordinated` | GYN visits also identify lung-eligible patients and place LDCT referrals |
| `coordinated_all` | All due screenings bundled into a single encounter |
| `high_access_coordinated` | Full co-scheduling + reduced scheduling friction |

Key levers: `cancer_map`, `co_schedule`, `ltfu_multiplier`, `scheduling_delay_days`, `capacity_multiplier`.

---

## How to Run

**Option 1 — Full simulation via runner**

```python
import sys
sys.path.insert(0, '/path/to/NYP/src')

from runner import SimulationRunner

sim = SimulationRunner(n_days=365, seed=42)
sim.run()
sim.summary()
sim.revenue_summary()
sim.plot_all()
```

**Option 2 — Manual patient-level loop (for unit testing)**

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

**Option 3 — Jupyter Lab**

```bash
jupyter lab --notebook-dir=/path/to/NYP
```

Open notebooks from the `notebooks/` folder. Start with `04_simulation_runner.ipynb` for the full end-to-end run.

---

## Replacing Placeholder Values

All placeholders are marked with `# PLACEHOLDER` in `config.py`.

| Priority | Parameter | Source |
|---|---|---|
| 🔴 Now | `PROCEDURE_REVENUE` | NYP finance / contract rates |
| 🔴 Now | Population sampler | Yutong's code → drop into `population.py` |
| 🔴 Now | `CERVICAL_RESULT_PROBS` | NYP EHR abnormal Pap rates |
| 🟡 Soon | `COLPOSCOPY_RESULT_PROBS` | ASCCP risk table slides |
| 🟡 Soon | `LTFU_PROBS` | NYP EHR attrition analysis |
| 🟡 Soon | `LUNG_PATHWAY_PROBS` | NYP LDCT volume / referral data |
| 🟢 Future | `LUNG_RADS_PROBS` | NYP LDCT registry data |
| 🟢 Future | `CAPACITIES` | NYP scheduling data |

---

## Next Steps

| Priority | Task |
|---|---|
| 🔴 Immediate | Drop in Yutong's population sampling code |
| 🔴 Immediate | Replace all PLACEHOLDER values with NYP EHR + finance data |
| 🟡 Near-term | Wire `scenarios.py` into the runner for co-scheduling analysis |
| 🟡 Near-term | Multi-replication runs + variance analysis (`NUM_REPS` in config) |
| 🟡 Near-term | Build visualizations — cervical funnel, lung funnel, RADS distribution, revenue waterfall |
| 🟢 Future | Add breast and colon cancer pathways |
| 🟢 Future | Calibration loop: compare sim output to NYP EHR aggregate statistics |

---

## Dependencies

```
matplotlib
```

Install: `pip install matplotlib`

All other dependencies (`random`, `dataclasses`, `collections`, `typing`) are Python standard library. The simulation does **not** use SimPy — patient flow is modeled as a direct day-by-day tick loop with explicit queue management, not a coroutine-based event loop.
