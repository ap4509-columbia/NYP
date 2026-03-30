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

## Source Files

### `src/config.py`
The single source of truth for every tunable value in the simulation. All clinical probabilities (result distributions, LTFU rates, pathway completion rates), revenue rates, provider capacities, scheduling parameters, and eligibility criteria live here. Changing a value in `config.py` propagates everywhere — no other file needs to be touched. All probability and revenue values are currently marked `# PLACEHOLDER` and must be replaced with NYP EHR and finance data before the simulation produces calibrated output.

### `src/patient.py`
Defines the `Patient` dataclass — the shared data contract between every module. A single `Patient` object is created on arrival and passed through eligibility, screening, follow-up, and exit without copying. It holds all demographic fields (age, race, insurance), clinical flags (has_cervix, pack_years, hpv_positive, prior_cin), simulation state (active, current_stage), screening history (last screen day per cancer), results (cervical_result, lung_result), follow-up state (colposcopy_result, treatment_type, lung biopsy chain flags), exit reason, and a timestamped event log. Helper methods `p.log()`, `p.exit_system()`, and `p.print_history()` are used throughout.

### `src/population.py`
Generates individual `Patient` objects sampled from NYC demographic distributions. The public interface is one function: `sample_patient(patient_id, day_created, destination, patient_type) → Patient`. The current stub draws age, race/ethnicity, insurance, smoking history, HPV status, hysterectomy status, and BMI from population-weighted distributions based on NYC data. This is the intended integration point for Yutong's population code — replace the function body without changing the signature and no other file needs to change.

### `src/screening.py`
Implements Steps 2–3 of the patient journey: eligibility determination, future eligibility estimation, test assignment, and result draws. Key functions: `get_eligible_screenings()` returns which cancers a patient qualifies for right now; `days_until_eligible()` returns how many days until a patient will become eligible (used to schedule return visits for patients who are close but not yet eligible, vs. permanently ineligible patients who exit silently); `assign_screening_test()` picks the test modality (age-stratified for cervical per USPSTF 2018); `draw_cervical_result()` draws a multinomial result with risk-factor inflation for HPV+ and prior CIN history; `run_lung_pre_ldct()` models the two pre-LDCT LTFU nodes (referral placed, LDCT scheduled); `run_screening_step()` orchestrates the full screening event for one cancer.

### `src/followup.py`
Implements Steps 4–5: result routing and clinical follow-up pathways for both cancers. For cervical, `route_cervical_result()` branches on result category (normal → surveillance; abnormal cytology → colposcopy; HPV+ → 40% 1-year repeat / 60% colposcopy per ASCCP triage); `run_colposcopy()` draws a CIN grade from result-conditional distributions; `run_treatment()` applies the treatment decision rule (CIN1 → surveillance, CIN2/3 → LEEP) with a 10% LTFU check before the procedure. For lung, `run_lung_followup()` handles result communication (10% LTFU), RADS category routing (repeat LDCT intervals for RADS 0–3), and the full biopsy chain for RADS 4 (referral → scheduling → completion → malignancy confirmation → treatment), with an explicit LTFU check at every step.

### `src/metrics.py`
Collects and aggregates all simulation outputs into a single metrics dictionary. `initialize_metrics()` returns a fresh dict at the start of each run. `record_screening()` and `record_exit()` are called by the runner as events occur. `compute_rates()` derives percentages (screening rate, abnormal rate, colposcopy completion, LTFU rate). `compute_revenue()` calculates realized revenue from completed procedures and foregone revenue lost at each LTFU node, broken down by procedure type and drop-off point. `print_summary()` and `print_revenue_summary()` produce formatted clinical and financial summaries to stdout.

### `src/runner.py`
The orchestration layer that owns the clock, all queues, and the daily tick. `SimulationRunner` runs the full simulation (`run()`), exposes `summary()`, `revenue_summary()`, `plot_all()`, and `plot_queues()` after the run completes. Internally, `PatientQueues` manages three queue types: outpatient (advance-scheduled, never overfills), drop-in (walk-ins, fills remaining capacity), and follow-up (future-dated procedures with per-procedure slot capacity). Each day, follow-ups are processed before new arrivals so procedure slots are consumed before walk-ins are seen. See the [Queuing Engine](#the-queuing-engine--detailed) section for the full mechanics.

### `src/scenarios.py`
Defines four co-scheduling scenarios for future comparative analysis: `baseline_fragmented` (current state — each provider screens only their domain), `gyn_coordinated` (GYN visits also identify lung-eligible patients and place LDCT referrals), `coordinated_all` (all due screenings bundled into one encounter), and `high_access_coordinated` (full co-scheduling plus reduced scheduling friction). Each scenario is a config dict with keys for `cancer_map`, `co_schedule`, `ltfu_multiplier`, `scheduling_delay_days`, and `capacity_multiplier`. Not yet wired to the main runner — reserved for the scenario analysis phase once clinical probabilities are calibrated.

---

## Notebooks

### `notebooks/02_screening.ipynb`
Tests and validates the screening layer (`screening.py`) in isolation. Verifies eligibility logic across age groups and clinical profiles, confirms test modality assignment follows USPSTF age stratification, and runs Monte Carlo sampling (5,000 trials) to validate that result probability distributions match the configured values. A smoke test runs 30 patients through `run_screening_step()` and prints their event logs. Use this notebook to sanity-check `config.py` probability changes before running the full simulation.

### `notebooks/03_results_followup.ipynb`
Tests and demonstrates the follow-up pathways (`followup.py`) in isolation. Shows stochastic LTFU branching at each decision node (e.g., ASCUS → colposcopy referral with 20% LTFU), draws CIN grade distributions from result-conditional probabilities, and traces complete patient journeys from screening result through colposcopy and treatment. Useful for verifying that LTFU rates and CIN grade distributions match clinical expectations before integrating with the full runner.

### `notebooks/04_simulation_runner.ipynb`
End-to-end simulation notebook. Runs `SimulationRunner` for a configurable number of days, prints the clinical summary and revenue summary, and calls `plot_all()` to produce the four-panel chart (cervical funnel, lung funnel, Lung-RADS distribution, realized vs. foregone revenue). The primary notebook for running the simulation and viewing top-line results. Start here for a full run.

### `notebooks/05_metrics_outputs.ipynb`
Deep-dive analytics on simulation output. Loads results from a completed run and produces: cervical result breakdowns by age stratum (young/middle/older), full pathway funnels with drop-off percentages at each step, LTFU rates by node, wait-time distributions by resource, and a workflow comparison table (fragmented vs. coordinated). Also contains a matplotlib bar chart of the cervical result distribution. Use this notebook to diagnose where patients are dropping off and to compare scenarios once `scenarios.py` is wired in.

### `notebooks/06_scenario_analysis.ipynb`
Compares all four co-scheduling scenarios on a shared, fixed patient cohort to isolate the effect of workflow changes from population sampling noise. Identifies age-clustering opportunities (patients in the 40–50 range eligible for multiple screenings simultaneously), runs all four scenarios on deep copies of the same patients, and produces side-by-side comparison tables for screening rate, LTFU rate, colposcopy completion, and encounter savings. Includes per-scenario cervical funnels and a multi-bar matplotlib plot comparing key rates across scenarios. This notebook is the eventual deliverable for the ROI analysis — it will be fully meaningful once LTFU multipliers and capacity adjustments in `scenarios.py` are calibrated against NYP data.

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

## Arrival & Queue Distributions — Design Rationale

Every stochastic element in the arrival and queuing layer was chosen for a specific reason. This section documents each distribution, why it was selected, and what its parameters represent.

---

### Daily Arrivals — Poisson (Normal Approximation)

```python
def _poisson(lam: float) -> int:
    return max(0, round(random.gauss(lam, math.sqrt(lam))))
```

**Distribution:** Normal approximation to Poisson(`λ = 200`).

**Why Poisson:** Patient arrivals at a clinic are the canonical Poisson process — a large number of independent individuals each with a small probability of showing up on any given day. The Poisson distribution is the standard model for count data of this type (unscheduled, memoryless, non-negative integer). For large `λ` (≥ ~30), the Poisson and Normal distributions are nearly identical, so `gauss(λ, √λ)` is used as a computationally simple approximation that avoids implementing a true Poisson sampler.

**Parameters:** `λ = 200` daily patients (PLACEHOLDER — mirrors Sophia's arrival model). This produces a standard deviation of ~14 patients/day (`√200 ≈ 14.1`), meaning on a typical day you'd see anywhere from ~172 to ~228 patients (±2 SD). Replace `DAILY_PATIENTS` with NYP throughput data for the target clinics.

---

### Destination Routing — Categorical (Weighted Discrete)

```python
DESTINATION_PROBS = {"pcp": 0.35, "gynecologist": 0.25, "specialist": 0.20, "er": 0.20}
dest = random.choices(destinations, weights=weights)[0]
```

**Distribution:** Categorical (single draw from a weighted discrete distribution).

**Why Categorical:** Each patient goes to exactly one provider destination. A categorical draw is the natural model — one outcome from a mutually exclusive, exhaustive set of options. The weights encode the relative volume at each entry point.

**Parameters (all PLACEHOLDER):**
- **PCP 35%:** Primary care is the most common entry point; PCPs see the broadest patient mix and are the most likely to identify screening-eligible patients incidentally during general visits.
- **Gynecologist 25%:** Second-highest volume; GYN visits are the natural home for cervical screening and the primary co-scheduling target for the coordinated-care scenarios.
- **Specialist 20%:** Pulmonology, oncology, internal medicine — providers likely to encounter lung-eligible patients (older, smoking history). Lower volume than PCP/GYN.
- **ER 20%:** Emergency visits; screening is rarely the primary reason but ER patients may be the hardest-to-reach population (uninsured, lack of PCP). High LTFU expected from this channel.

Replace with actual visit-volume proportions from NYP scheduling data.

---

### Patient Type Mix — Bernoulli

```python
PATIENT_TYPE_PROBS = {"outpatient": 0.70, "drop_in": 0.30}
ptype = random.choices(types, weights=weights)[0]
```

**Distribution:** Bernoulli (binary categorical — two outcomes).

**Why Bernoulli:** Each patient is either scheduled in advance or a walk-in. There is no middle ground. A weighted binary draw is the simplest correct model.

**Parameters (PLACEHOLDER):** 70% outpatient / 30% drop-in. This ratio reflects the fact that most clinic visits at an academic medical center are pre-scheduled, but a meaningful fraction are same-day or walk-in (urgent care, ER, patients calling for a same-day slot). The ER is always drop-in regardless of this setting. Replace with NYP patient-type data per clinic.

---

### Outpatient Scheduling Lead Time — Uniform

```python
OUTPATIENT_LEAD_DAYS = {"pcp": (1, 7), "gynecologist": (7, 21), "specialist": (14, 28)}
earliest = day + random.randint(lo, hi)
```

**Distribution:** Discrete Uniform over `[lo, hi]` days.

**Why Uniform:** Lead time depends on scheduling availability, which varies day-to-day based on cancellations and slot releases. Without detailed scheduling data, a uniform distribution over a plausible range is the minimum-assumption model — it says "we know the earliest and latest typical wait, but have no reason to prefer any particular day within that window." It is a deliberate placeholder for a more calibrated distribution (e.g., empirical from NYP scheduling data, or a shifted Geometric if next-available-slot logic were modeled explicitly).

**Parameters (all PLACEHOLDER):**
- **PCP 1–7 days:** PCPs have high throughput and relatively short waits; patients can often get in within the week.
- **Gynecologist 7–21 days:** GYN scheduling is tighter; 1–3 weeks is typical for a non-urgent visit.
- **Specialist 14–28 days:** Specialty appointments have the longest lead times; 2–4 weeks reflects the reality of limited specialist slots.

Replace with NYP scheduling data. If the distribution turns out to be skewed (many short waits, occasional very long waits), a log-normal or empirical distribution would be more appropriate.

---

### Capacity Split — Deterministic Fractions

```python
OUTPATIENT_FRACTION = {"pcp": 0.75, "gynecologist": 0.73, "specialist": 0.75, "er": 0.00}
outpatient_slots = int(total_capacity * fraction)
dropin_slots     = total_capacity - outpatient_slots
```

**Distribution:** Deterministic (fixed fraction of total daily capacity).

**Why deterministic:** The split between reserved outpatient slots and open drop-in slots is an operational policy decision, not a random variable. A clinic sets this ratio in advance (e.g., "we hold 30 of 40 PCP slots for scheduled patients"). Using a fixed fraction accurately models that policy and makes it easy to test the effect of changing the ratio in scenario analysis.

**Parameters (PLACEHOLDER):**
- **PCP:** 75% outpatient (30 slots), 25% drop-in (10 slots) — high throughput; enough drop-in capacity for urgent same-day needs.
- **GYN:** ~73% outpatient (22 slots), ~27% drop-in (8 slots) — slightly higher drop-in share than PCP; GYN sees more urgent gynecologic complaints.
- **Specialist:** 75% outpatient (15 slots), 25% drop-in (5 slots) — small absolute drop-in capacity reflects that specialist walk-ins are uncommon.
- **ER:** 0% outpatient — ER is entirely drop-in by design; no advance scheduling.

An important queue property: unused outpatient slots roll over to drop-ins on the same day (a patient who cancels their GYN slot frees it for a walk-in). This is implemented in `process_day()` via `extra = max(0, outpt_cap - len(seen_outpts))`.

---

### ER Overflow Routing — Bernoulli

```python
ER_OVERFLOW_RETRY_PROB = 0.70
if random.random() < ER_OVERFLOW_RETRY_PROB:
    re-queue for ER tomorrow
else:
    schedule outpatient at random non-ER provider
```

**Distribution:** Bernoulli(`p = 0.70`).

**Why Bernoulli:** When an ER patient cannot be seen today (capacity full), they face a binary decision: return to the ER tomorrow, or accept a scheduled outpatient appointment elsewhere. A Bernoulli draw captures this binary choice. The probability `p` encodes the relative likelihood of each path.

**Parameters (PLACEHOLDER):** 70% retry ER / 30% convert to outpatient. This reflects the intuition that most ER overflow patients have urgent or undifferentiated complaints that keep them in the ED pathway, while a minority are willing and able to wait for a scheduled appointment at a less acute setting. The 30% conversion rate is a key lever in scenario analysis — if it increases, more patients enter the scheduled system with better continuity-of-care, which is favorable for screening follow-through.

**Non-ER overflow** (PCP, GYN, Specialist) is deterministic: every overflow patient re-queues for the same provider tomorrow. There is no Bernoulli draw because these patients have an established relationship with a specific provider and a scheduled appointment — leaving that queue for a different provider would break continuity.

---

### Follow-Up Scheduling Delays — Fixed (Deterministic)

```python
FOLLOWUP_DELAY_DAYS = {
    "colposcopy":  30,   # abnormal result → colposcopy appointment
    "leep":        14,   # colposcopy → LEEP procedure
    "cone_biopsy": 21,   # colposcopy → cone biopsy
    "lung_biopsy": 14,   # RADS 4 → biopsy
}
due_day = current_day + FOLLOWUP_DELAY_DAYS[step]
```

**Distribution:** Deterministic (fixed offset from the triggering event).

**Why deterministic:** These delays represent standard scheduling targets — the number of days a provider aims for between an abnormal result and the next procedure. Using a fixed value rather than a random draw makes the model easier to interpret and to calibrate: you can directly compare the simulated delay to NYP's actual scheduling data and adjust the single number. If scheduling data shows high variance (e.g., colposcopy wait times range 14–60 days depending on clinic load), this can be replaced with a Uniform or log-normal draw.

**Parameters (all PLACEHOLDER):**
- **Colposcopy 30 days:** ASCCP guidance suggests colposcopy within 4 weeks of an abnormal result; 30 days is the modeled target.
- **LEEP 14 days:** Treatment follows colposcopy quickly for CIN2/3; 2 weeks is the standard scheduling target post-diagnosis.
- **Cone biopsy 21 days:** Slightly longer than LEEP due to OR scheduling complexity.
- **Lung biopsy 14 days:** ACR and NCCN guidelines recommend timely workup for RADS 4 findings; 14 days is the modeled target.

These delays are a primary lever in co-scheduling scenario analysis — the `gyn_coordinated` and `coordinated_all` scenarios will reduce these values to model the effect of streamlined same-system care.

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
