# NYP Women's Health Screening Simulation — Digital Twin

A discrete-event simulation (DES) of a multi-cancer women's health screening program at NewYork-Presbyterian. The model simulates patient flow from provider arrival through screening, clinical follow-up, and system exit — across a **70-year longitudinal horizon** — quantifying drop-off at each step and supporting operational and financial planning.

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

**Simulation window:** 70 years (25,550 days) — the full longitudinal horizon over which a stable patient population is tracked from entry through eventual mortality or departure.

**Population scale:** 15,000 simulated patients represent approximately 1.5 million NYC eligible women (scale factor 1:100).

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
│   ├── population.py             # Population sampler (NYC demographics + mortality helpers)
│   ├── screening.py              # Eligibility, test assignment, result draws
│   ├── followup.py               # Post-screening clinical pathways
│   ├── metrics.py                # Counters, rates, revenue analysis, reporting
│   ├── db.py                     # SQLite persistence layer — patient + event log storage
│   ├── runner.py                 # SimulationRunner — day-by-day orchestration + queue engine
│   └── scenarios.py              # Co-scheduling scenario definitions (future)
│
├── notebooks/                    # Jupyter notebooks
│   ├── 02_screening.ipynb        # Screening layer demo and tests
│   ├── 03_results_followup.ipynb # Follow-up pathway demo and tests
│   ├── 04_simulation_runner.ipynb# Full end-to-end orchestration + 70-year run + visualizations
│   ├── 05_metrics_outputs.ipynb  # Analysis, funnels, revenue, plots
│   └── 06_scenario_analysis.ipynb# Co-scheduling comparison (future)
│
└── reference/                    # Source material — do not modify
    ├── initial_model_NYP_flow_simulation (1).ipynb  # Sophia's arrivals simulation
    └── Simulation_draft_Yutong.ipynb                # Yutong's draft (reference only)
```

**Design principle:** all logic lives in `.py` modules — importable, testable, version-controlled cleanly. Notebooks are thin wrappers that call those modules, run scenarios, and display output. You never need to edit a notebook to change a clinical parameter.

---

## The Open-System 70-Year Population Model

This is the conceptual core of the simulation. Understanding it is essential before reading any of the code.

### What "open system" means

The simulation does **not** run a fixed cohort of patients and watch them age to death. Instead, it models an **open system** — the way a real hospital actually works:

- Patients **enter** continuously: new women move to NYC, turn 21, switch providers, are referred by their PCP, or simply come in for the first time
- Patients **exit** continuously: some die, some reach the end of their screening-eligible years, some are permanently lost to follow-up
- A core group is **retained**: established patients who have an ongoing relationship with NYP return year after year for their annual visit and periodic screenings

Over 70 years, the composition of the patient pool shifts as generations of patients cycle through — but the total number of active patients remains approximately stable at 15,000 (representing ~1.5 million NYC women).

### The three patient flows

```
┌─────────────────────────────────────────────────────────────────────┐
│                    OPEN-SYSTEM PATIENT POOL                          │
│                      (target: 15,000 patients)                       │
│                                                                       │
│  INFLOW 1 — Organic new patients                                      │
│  10 new first-time patients/day arrive as drop-ins.                   │
│  After their first visit they join the established cycling pool.      │
│  These represent: women turning 21, new NYC residents,                │
│  patients switching providers, first-time visitors.                   │
│                                                                       │
│  INFLOW 2 — Mortality replacements                                    │
│  Up to 4 patients/day, spawned specifically to replace patients       │
│  removed by the monthly mortality sweep. Prevents pool shrinkage.     │
│  These enter directly as established cycling patients.                │
│                                                                       │
│  CYCLING CORE — Established patients                                  │
│  ~15,000 patients with an annual appointment at their primary         │
│  provider. Each visit triggers an immediate reschedule for the        │
│  next 5 annual visits (multi-year advance scheduling window).         │
│                                                                       │
│  OUTFLOW 1 — Mortality                                                │
│  Checked every 30 days. Age-specific Bernoulli draw per patient       │
│  (US life tables). Dead patients exit and are replaced.               │
│                                                                       │
│  OUTFLOW 2 — LTFU / ineligible                                        │
│  Patients who decline rescheduling, age permanently out of            │
│  eligibility, or are lost in the clinical follow-up pathway.          │
└─────────────────────────────────────────────────────────────────────┘
```

### The warmup period

On simulation day 0, all 15,000 established patients are created and their first annual visits are **spread evenly across year 1** (the warmup window). This prevents a cold-start artifact where providers are empty on day 1 and fill up slowly — instead, from day 1 onwards, providers operate near capacity as they would in a real steady-state clinic.

Each patient is also immediately pre-booked for their next **5 annual visits** (configurable via `ADVANCE_SCHEDULE_YEARS`), so the schedule book is filled years in advance from day 0.

### Multi-year advance scheduling

At any point in the simulation, every established patient has approximately 5 future annual appointments already booked. The mechanism:

- **Warmup:** each patient is scheduled for visits in years 1 through 5
- **After each visit:** the far edge of the window is extended by one year (year N+5 is booked). The near-end appointments (years 2 through 5) were already in the queue from the previous visit's rescheduling
- **Replacements:** new entrants are also immediately pre-booked for their first 5 annual visits

This means that at year 35, the schedule already contains confirmed appointments through year 40 for every active patient.

### Vacancy filling

When a patient dies, their future appointments remain in the scheduling queue as inactive records. Rather than letting those phantom bookings block new patients, `schedule_outpatient()` counts only **active** patients when checking slot capacity. This means a slot vacated by a deceased patient is immediately available to a replacement or organic new entrant — mirroring how a real scheduling desk would reassign a cancelled appointment.

### Age-based drop-in priority

> **NYP model assumption (revenue maximization):** When drop-in capacity is limited and some walk-in patients must be deferred to the next day, women aged 40+ receive priority over younger patients.

**Rationale:** The 40+ cohort is disproportionately associated with higher-revenue procedures — colposcopy, LEEP, cone biopsy, and LDCT — so prioritising them when the drop-in queue exceeds available slots maximises expected revenue per available slot. This applies **only** to drop-in queue ordering. All scheduled outpatients retain their guaranteed slot regardless of age.

### Scale interpretation

| Simulation unit | Real-world equivalent |
|---|---|
| 1 simulated patient | 100 NYC eligible women |
| 15,000 sim patients | ~1.5 million NYC eligible women |
| 1 cervical screen | 100 cervical screens in the real NYC population |

All volume outputs from the simulation should be multiplied by `POPULATION_SCALE_FACTOR = 100` when extrapolating to real-world planning figures.

### What the 70-year run produces

After a full 70-year run:

| Metric | Approximate value (sim scale) |
|---|---|
| Total provider contacts | ~1.5 million |
| Total cervical screens | ~310,000 |
| Total lung LDCT screens | ~18,000 |
| Total colposcopies | ~35,000 |
| Total LEEP / cone treatments | ~8,500 |
| Mortality exits | ~15,000 |
| Pool stability | 15,000 throughout |

---

## How the Simulation Works — End to End

### Time Model

The simulation runs as a **day-by-day tick loop**. The clock is an integer (`day = 0, 1, 2, …, 25549`). Every day, in this exact order:

1. **Mortality sweep** (every 30 days): age all patients; Bernoulli mortality draw per patient; remove the dead; queue replacements
2. **Spawn replacements**: create up to 4 new established patients to refill the pool
3. **Flush to database**: batch-write exited patients to SQLite (every 30 days)
4. **Annual checkpoint** (every 365 days): snapshot cumulative stats for longitudinal plots
5. **Follow-up appointments**: process all clinical follow-ups due today (colposcopy, LEEP, biopsy) before new arrivals
6. **New arrivals**: create 10 organic new patients; route to provider queues
7. **Provider queues**: seat patients (outpatients first, then age-prioritised drop-ins); screen seen patients; schedule follow-ups for abnormal results

This ordering matters: follow-ups consume procedure slots before new arrivals arrive, accurately modeling finite procedural capacity.

---

### The Full Patient Journey

```
NYC Eligible Women Population
          │
          ▼
┌──────────────────────────────────────────────────────┐
│  STEP 1 — ARRIVAL                                     │
│  Patient enters via PCP / Gynecologist / Specialist / ER│
│  70% outpatient (scheduled in advance, up to 5 yrs)  │
│  30% drop-in (walk-in, seen same day if capacity)    │
│  Drop-in priority: age 40+ seen before age <40       │
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
│  1. Not yet eligible → schedule return at future date│
│  2. Permanently ineligible → exit silently           │
│  3. Established patient: ineligible but retained     │
│     → reschedule annual visit (no screening)         │
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
│    RADS 0              → repeat LDCT in 1–3 months  │
│    RADS 1 / 2          → repeat LDCT in 12 months   │
│    RADS 3              → repeat LDCT in 6 months    │
│    RADS 4A / 4B / 4X  → biopsy pathway              │
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
│    Referral → scheduling → completion →              │
│    malignancy confirmed → treatment given            │
│    LTFU node at every step                           │
└────────────────────────┬─────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────┐
│  STEP 6 — EXIT OR RE-ENTRY                           │
│                                                      │
│  Treated established patient →                       │
│    re-activated; rescheduled for next annual visit   │
│                                                      │
│  Established patient LTFU from cancer pathway →      │
│    re-activated; still cycles annually               │
│    (LTFU from a specific pathway ≠ leaving the pool) │
│                                                      │
│  New patient LTFU / permanently ineligible → EXIT    │
│  Mortality → EXIT; slot freed; replacement spawned   │
└──────────────────────────────────────────────────────┘
```

---

## The Queuing Engine

The queue engine lives in `runner.py` and is the architectural core of the simulation. It manages three distinct queue types, each with different scheduling and capacity semantics.

### PatientQueues

```
PatientQueues
├── outpatient[provider][day]    → list[Patient]          Scheduled appointments
├── dropin[provider]             → list[Patient]          Today's walk-ins
├── followup[day]                → list[(Patient, context)] Future clinical procedures
└── daily_slots[day][procedure]  → int                    Remaining procedure slots
```

---

### Queue Type 1 — Outpatient Scheduling (advance, guaranteed)

Outpatients call ahead and book a future appointment. The scheduler finds the **earliest available day** on or after a lead-time window. Slot availability is determined by counting only **active** patients — a slot vacated by a deceased patient becomes available immediately to the next booking:

```python
def schedule_outpatient(self, p, provider, earliest_day) -> int:
    cap = _outpatient_cap(provider)        # e.g., 38 for PCP (75% of 70 slots = 52)
    day = earliest_day
    while sum(1 for q in self.outpatient[provider][day] if q.active) >= cap:
        day += 1                           # push forward until a slot opens
    self.outpatient[provider][day].append(p)
    return day
```

**Capacity guarantee:** The scheduler never overfills a day. Outpatient slots are always guaranteed — the scheduler will push the appointment as far forward as needed to find a free slot, but never double-books. A hard assertion fires immediately if this invariant is ever violated, preventing silent capacity bugs.

**Multi-year advance booking:** In stable-population mode, established patients are pre-booked for the next 5 annual visits at all times. After each visit, `_reschedule_established()` adds the appointment at the far end of the window (5 years out), so the schedule book stays filled:

```
Patient visit on day 1825 (year 5)
  → already has appointments booked for years 6, 7, 8, 9 (from previous rescheduling)
  → _reschedule_established() adds year 10 booking
  → patient now has years 6–10 confirmed in queue
```

Lead-time windows for new (non-established) outpatients:
- PCP: 1–7 days
- Gynecologist: 7–21 days
- Specialist: 14–28 days

---

### Queue Type 2 — Drop-In Processing (same-day, age-prioritised)

Drop-ins arrive and want to be seen today. Before applying the capacity cut, the drop-in list is sorted so women aged 40+ appear first (NYP revenue-maximisation assumption). Then the queue is sliced to available capacity:

```python
def process_day(self, provider, day):
    outpt_cap  = _outpatient_cap(provider)
    dropin_cap = _dropin_cap(provider)
    total_cap  = outpt_cap + dropin_cap        # = PROVIDER_CAPACITY[provider]

    # All scheduled outpatients are always seated
    seen_outpts = self.outpatient[provider].pop(day, [])

    # Sort drop-ins: age 40+ first, then under-40 (stable sort preserves arrival order)
    remaining     = max(0, total_cap - len(seen_outpts))
    today_dropins = sorted(dropin[provider], key=lambda p: (0 if p.age >= 40 else 1))
    seen_dropins  = today_dropins[:remaining]
    overflow      = today_dropins[remaining:]  # deferred to tomorrow

    return seen_outpts + seen_dropins, overflow
```

**Key insight:** If fewer outpatients show up than their reserved cap (e.g., due to some having died since booking), those unused slots roll over to drop-ins on the same day. The total capacity is never wasted.

**Overflow routing:**
- PCP / GYN / Specialist overflow: patient re-added to tomorrow's drop-in queue for the same provider; wait time grows by 1 day
- ER overflow: 70% retry ER tomorrow; 30% converted to a scheduled outpatient appointment at a random non-ER provider

---

### Queue Type 3 — Follow-Up Scheduling (future-dated procedures)

After a screening event produces an actionable result (abnormal Pap, RADS 4 lung finding), the patient is placed in a future-day follow-up queue:

```python
def schedule_followup(self, p, context, due_day) -> None:
    self.followup[due_day].append((p, context))
```

The `context` dict carries clinical state: `{"cancer": "cervical", "step": "colposcopy"}`.

On the due day, follow-ups are processed **before** new arrivals in `_tick()`, so procedure slots are consumed before any walk-ins are seen. This accurately models the real-world situation where scheduled procedures take priority over same-day demand.

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
    return False   # fully booked today — patient requeued for tomorrow
```

If no slot is available, the patient is re-queued for tomorrow's same step, creating realistic queue build-up under high demand.

---

### How a Full Patient Journey Flows Through the Queues

```
Day 0 (warmup):   Patient scheduled for annual visit on day 47
                  Pre-booked for days 412, 777, 1142, 1507 (years 2–5)

Day 47:           Patient seen at GYN
                  → cervical screen: HSIL result drawn
                  → schedule_followup({step: colposcopy}, due_day=77)
                  → _reschedule_established() books year-6 visit (day 1872)

Day 77:           _run_followup() called
                  → consume_slot("colposcopy", 77)?
                      NO  → re-queue for day 78 (slot contention)
                      YES → CIN3 drawn
                          → run_treatment(): LTFU check (10%)
                              PASS → schedule_followup({step: leep}, due_day=91)

Day 91:           → consume_slot("leep", 91)? YES
                  → LEEP completed; treatment revenue recorded
                  → patient re-activated; continues annual cycling
```

---

## The Database Layer

### Why a database?

A 70-year simulation with 15,000 cycling patients produces hundreds of thousands of patient records and millions of individual events. Keeping everything in memory throughout the run is wasteful and prevents post-run analysis. `db.py` adds a zero-infrastructure SQLite persistence layer that:

- Writes exited patient records to disk in **batch** (every 30 days) rather than row-by-row, keeping the simulation hot loop fast
- Enables **SQL queries after the run** — e.g., "show me all patients who had a CIN3 diagnosis and then died before treatment" — which is impossible from the metrics dict alone
- Provides **longitudinal tracking** across the 70-year horizon: patient IDs are stable, so a patient who entered in year 1 can be traced through all their visits until mortality in year 55
- Uses **WAL mode** (Write-Ahead Logging) so notebooks can run summary queries while the simulation is still writing

### Schema

```sql
-- One row per patient (written when the patient exits the system)
CREATE TABLE patients (
    patient_id                INTEGER PRIMARY KEY,
    age_at_entry              INTEGER,
    age_at_exit               INTEGER,
    race                      TEXT,
    insurance                 TEXT,
    is_established            INTEGER,   -- 1 = cycling pool patient; 0 = one-time visitor
    simulation_entry_day      INTEGER,
    exit_day                  INTEGER,
    exit_reason               TEXT,      -- mortality | lost_to_followup | untreated | ineligible
    visit_count               INTEGER,   -- total provider visits recorded
    has_cervix                INTEGER,
    smoker                    INTEGER,
    pack_years                REAL,
    cervical_result           TEXT,      -- last cervical screening result
    lung_result               TEXT,      -- last lung screening result
    colposcopy_result         TEXT,      -- last colposcopy result (if any)
    treatment_type            TEXT,      -- surveillance | leep | cone_biopsy (if any)
    last_cervical_screen_day  INTEGER,   -- simulation day of last cervical screen (-1 = never)
    last_lung_screen_day      INTEGER    -- simulation day of last lung screen (-1 = never)
);

-- Full timestamped event log for every patient
CREATE TABLE events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id  INTEGER,
    day         INTEGER,
    event       TEXT,
    FOREIGN KEY(patient_id) REFERENCES patients(patient_id)
);
```

Indexes are created on `exit_reason`, `is_established`, `race`, `last_cervical_screen_day`, `last_lung_screen_day`, `patient_id`, and `day` for fast post-run queries.

### Batch-write pattern

Patients are held in an in-memory flush buffer throughout the simulation and written to SQLite in bulk every `DB_FLUSH_INTERVAL = 30` days. A single `executemany` call handles the whole batch — one database transaction regardless of batch size. This gives ~100–1000× better write throughput than per-row commits.

All writes use `INSERT OR IGNORE` — if the simulation is restarted after a crash, re-flushing a patient already in the database is a silent no-op. This makes the database idempotent.

### Querying the database after a run

```python
sim = SimulationRunner(n_days=cfg.SIM_DAYS, use_stable_population=True,
                       db_path="nyp_70yr.db", reset_db=True)
metrics = sim.run()

# Built-in summary
sim.db_summary()

# Ad-hoc SQL via sim._db.query()
rows = sim._db.query("""
    SELECT race, AVG(age_at_exit) AS mean_age_exit, COUNT(*) AS n
    FROM patients
    WHERE exit_reason = 'mortality'
    GROUP BY race
    ORDER BY n DESC
""")

# Trace one patient's complete journey
history = sim._db.get_patient_history(patient_id=42)
for day, event in history:
    print(f"Day {day:>5}: {event}")

sim.close_db()   # always close explicitly after all queries
```

### `last_cervical_screen_day` and `last_lung_screen_day`

Every patient record in the database carries the simulation day of their most recent cervical screening and their most recent lung LDCT. These fields enable queries like:

```sql
-- Patients who were last screened more than 3 years before they died
SELECT COUNT(*) FROM patients
WHERE exit_reason = 'mortality'
  AND last_cervical_screen_day >= 0
  AND (exit_day - last_cervical_screen_day) > 1095;

-- Average days between last cervical screen and exit, by insurance type
SELECT insurance, AVG(exit_day - last_cervical_screen_day) AS avg_gap
FROM patients
WHERE last_cervical_screen_day >= 0
GROUP BY insurance;
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

**Why Poisson:** Patient arrivals at a clinic are the canonical Poisson process — a large number of independent individuals each with a small probability of showing up on any given day. For large `λ` (≥ ~30), `gauss(λ, √λ)` is a computationally simple approximation that avoids implementing a true Poisson sampler.

**Parameters:** `λ = 200` daily patients total across all providers (PLACEHOLDER). This produces a standard deviation of ~14 patients/day (`√200 ≈ 14.1`). Replace `DAILY_PATIENTS` with NYP throughput data.

---

### Destination Routing — Categorical (Weighted Discrete)

```python
DESTINATION_PROBS = {"pcp": 0.35, "gynecologist": 0.25, "specialist": 0.20, "er": 0.20}
dest = random.choices(destinations, weights=weights)[0]
```

**Distribution:** Categorical — one draw from a weighted discrete distribution.

**Parameters (all PLACEHOLDER):** PCP 35% / GYN 25% / Specialist 20% / ER 20%. Replace with actual visit-volume proportions from NYP scheduling data.

---

### Patient Type Mix — Bernoulli

```python
PATIENT_TYPE_PROBS = {"outpatient": 0.70, "drop_in": 0.30}
```

**Distribution:** Bernoulli — 70% outpatient (advance-scheduled), 30% drop-in (walk-in). ER is always drop-in regardless of this setting. Replace with NYP patient-type data per clinic.

---

### Outpatient Scheduling Lead Time — Uniform

```python
OUTPATIENT_LEAD_DAYS = {"pcp": (1, 7), "gynecologist": (7, 21), "specialist": (14, 28)}
earliest = day + random.randint(lo, hi)
```

**Distribution:** Discrete Uniform over `[lo, hi]` days. Chosen as the minimum-assumption model for scheduling wait times; replace with empirical distributions from NYP scheduling data when available.

---

### Capacity Split — Deterministic Fractions

```python
OUTPATIENT_FRACTION = {"pcp": 0.75, "gynecologist": 0.73, "specialist": 0.75, "er": 0.00}
outpatient_slots = int(total_capacity * fraction)
dropin_slots     = total_capacity - outpatient_slots
```

**Distribution:** Deterministic — the split between reserved outpatient slots and open drop-in slots is an operational policy decision, not a random variable. Unused outpatient slots roll over to drop-ins on the same day, maximising utilisation.

---

### Follow-Up Scheduling Delays — Fixed (Deterministic)

```python
FOLLOWUP_DELAY_DAYS = {
    "colposcopy":  30,   # abnormal result → colposcopy appointment
    "leep":        14,   # colposcopy → LEEP procedure
    "cone_biopsy": 21,   # colposcopy → cone biopsy
    "lung_biopsy": 14,   # RADS 4 → CT-guided biopsy
}
```

**Distribution:** Deterministic (fixed offset from the triggering event). These delays are a primary lever in co-scheduling scenario analysis — coordinated-care scenarios will reduce these values to model streamlined same-system care.

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

**Important for established patients:** LTFU from a specific cancer pathway does *not* remove an established patient from the provider pool. They are re-activated after the LTFU event and rescheduled for their next annual visit. They will be screened again at the next visit if they are still eligible. This models the real-world pattern where a patient misses a colposcopy but still shows up for their annual physical the following year.

---

## Source Files

### `src/config.py`
The single source of truth for every tunable value in the simulation. All clinical probabilities (result distributions, LTFU rates, pathway completion rates), revenue rates, provider capacities, scheduling parameters, eligibility criteria, stable-population parameters, and database settings live here. Changing a value in `config.py` propagates everywhere — no other file needs to be touched.

Key stable-population parameters:
- `SIMULATED_POPULATION = 15_000` — established cycling patient pool size
- `POPULATION_SCALE_FACTOR = 100` — 1 sim patient = 100 NYC women
- `ORGANIC_NEW_PATIENT_DAILY_RATE = 10` — first-time visitors entering daily
- `NEW_PATIENT_DAILY_RATE = 4` — mortality replacement rate (max per day)
- `ADVANCE_SCHEDULE_YEARS = 5` — years of appointments pre-booked per patient
- `ANNUAL_VISIT_INTERVAL = 365` — days between established patient visits
- `MORTALITY_CHECK_DAYS = 30` — mortality sweep frequency
- `AGE_PRIORITY_THRESHOLD = 40` — age above which drop-in patients get queue priority

---

### `src/patient.py`
Defines the `Patient` dataclass — the shared data contract between every module. A single `Patient` object is created on arrival and passed through eligibility, screening, follow-up, and exit without copying.

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
| Longitudinal tracking | `is_established`, `age_at_entry`, `simulation_entry_day`, `visit_count`, `next_visit_day` |
| Exit | `exit_day`, `exit_reason` |
| Log | `event_log` — timestamped list of every event |

Helper methods: `p.log(day, event)`, `p.exit_system(day, reason)`, `p.print_history()`.

---

### `src/population.py`
Generates individual `Patient` objects sampled from NYC demographic distributions. The public interface is `sample_patient(patient_id, day_created, destination, patient_type) → Patient`. Also provides:
- `generate_established_population(n, start_pid, entry_day)` — creates the initial 15,000-patient cohort for warmup
- `get_mortality_prob(age)` — age-specific annual death probability from US life tables
- `draw_mortality(p, sweep_days)` — Bernoulli draw scaled to sweep interval: `p_die ≈ annual_rate × (N / 365)`

**To replace the sampler:** drop new code into the body of `sample_patient()`. No other files change — the function signature is the stable interface.

---

### `src/screening.py`
Implements Steps 2–3 of the patient journey: eligibility determination, future eligibility estimation, test assignment, and result draws.

| Function | Purpose |
|---|---|
| `get_eligible_screenings(p)` | Returns cancer types patient qualifies for right now |
| `days_until_eligible(p, cancer)` | `0` = eligible now, `>0` = future date, `-1` = never |
| `is_due_for_screening(p, cancer, day)` | Whether screening interval has elapsed |
| `assign_screening_test(p, cancer)` | Picks test modality (age-stratified for cervical) |
| `draw_cervical_result(p, test)` | Multinomial result draw; 1.5–1.8× inflation for HPV+ or prior CIN |
| `run_lung_pre_ldct(p, day, metrics)` | Pre-LDCT nodes: referral placed → LDCT scheduled → LTFU |
| `run_screening_step(p, cancer, day, metrics)` | Full screening event for one cancer |

---

### `src/followup.py`
Implements Steps 4–5: result routing and clinical follow-up pathways for both cancers.

| Function | Purpose |
|---|---|
| `route_cervical_result(p, day, metrics)` | Routes cytology/HPV result to next step |
| `run_colposcopy(p, day, metrics)` | Draws CIN grade, updates patient |
| `run_treatment(p, day, metrics)` | LTFU check → surveillance or LEEP/cone |
| `run_cervical_followup(p, day, metrics)` | Full cervical pipeline orchestrator |
| `run_lung_followup(p, day, metrics)` | Full lung pipeline: RADS routing → biopsy chain |

---

### `src/metrics.py`
Collects and aggregates all simulation outputs. Key additions for longitudinal analysis:
- `year_checkpoints` — list of per-year snapshots (pool size, cumulative screens, LEEP, lung biopsy, LTFU, mortality, total patient contacts) for time-series visualisation
- `pool_size_snapshot` — monthly pool size for stability plots

`compute_revenue(metrics)` returns realized revenue from completed procedures and foregone revenue lost at each LTFU node, broken down by procedure type:

| Procedure | CPT Reference | Placeholder Rate |
|---|---|---|
| Cytology | 88141 | $156 |
| HPV test | 87624 | $198 |
| LDCT | 71250 | $285 |
| Colposcopy | 57454 | $312 |
| LEEP | 57522 | $847 |
| Cone biopsy | 57520 | $1,250 |
| Lung biopsy | 32405 | $2,100 |
| Lung treatment | composite | $18,500 |

---

### `src/db.py`
SQLite persistence layer. See the [Database Layer](#the-database-layer) section above for full details.

| Method | Purpose |
|---|---|
| `SimulationDB(db_path)` | Open or create the database; create schema if needed |
| `flush_patients(patients)` | Batch-insert exited patients into the `patients` table |
| `flush_events(patients)` | Batch-insert event logs into the `events` table |
| `get_patient_history(patient_id)` | Full chronological event log for one patient |
| `get_patient(patient_id)` | Demographic + outcome row for one patient |
| `summary_stats()` | Quick summary: total flushed, exit reason breakdown, mean age/visits |
| `count_by_exit_reason()` | `{exit_reason: count}` dict |
| `query(sql, params)` | Ad-hoc read-only SQL queries for notebook analysis |
| `reset()` | Drop and recreate all tables (use before each fresh run) |
| `close()` | Commit and close the connection |

---

### `src/runner.py`
The orchestration layer. Owns the clock, all queues, the stable-population pool, and the SQLite connection.

```python
sim = SimulationRunner(
    n_days               = cfg.SIM_DAYS,      # 25,550 days = 70 years
    seed                 = cfg.RANDOM_SEED,
    use_stable_population = True,
    db_path              = "nyp_70yr.db",
    reset_db             = True,
)
metrics = sim.run()
sim.summary()           # clinical funnel summary
sim.revenue_summary()   # realized vs. foregone revenue
sim.db_summary()        # database record counts and exit reason breakdown
sim.close_db()          # always close after all post-run queries
```

**Stable-population methods:**

| Method | Purpose |
|---|---|
| `_initialize_population()` | Create 15,000 established patients; spread across warmup; pre-book 5 years of visits |
| `_mortality_sweep(day)` | Age all patients; Bernoulli draw per patient; remove dead; queue replacements |
| `_reschedule_established(p, day)` | Extend advance-schedule window: book appointment at year N+5 from current day |
| `_spawn_replacement_entrants(day)` | Create up to 4 new established patients/day to replace mortality exits |
| `_flush_exited_patients(day, force)` | Batch-write exited patients to SQLite every 30 days |

---

### `src/scenarios.py`
Defines four co-scheduling scenarios for future comparative analysis — not yet wired to the main runner. Reserved for the ROI analysis phase once clinical probabilities are calibrated against NYP data.

| Scenario | Description |
|---|---|
| `baseline_fragmented` | Current state: each provider screens only their domain |
| `gyn_coordinated` | GYN visits identify lung-eligible patients and place LDCT referrals |
| `coordinated_all` | All due screenings bundled into one encounter |
| `high_access_coordinated` | Full co-scheduling + reduced scheduling friction |

---

## Notebooks

### `notebooks/04_simulation_runner.ipynb`
End-to-end simulation notebook. Contains:
1. **1-year quick test run** — confirms the module stack works end-to-end
2. **70-year single-patient trace** — follows one patient from age 21 to death or age 91, printing every clinical event; visualised as a colour-coded timeline
3. **Full 70-year population run** — runs `SimulationRunner` with `use_stable_population=True`; prints clinical and revenue summaries; calls `db_summary()`
4. **Six-panel comprehensive visualisation:**
   - Panel 1: Patient Journey Cascade (funnel from provider visit to treatment with LTFU annotated)
   - Panel 2: Cervical result distribution by age stratum (young vs. middle)
   - Panel 3: Colposcopy CIN grade distribution + treatment type breakdown
   - Panel 4: Annual procedure revenue by type over 70 years (+ LTFU lost revenue)
   - Panel 5: Lung LDCT pathway funnel (eligible → treated, with RADS distribution donut)
   - Panel 6: Population dynamics, annual screening throughput, and provider overflow

### `notebooks/02_screening.ipynb`
Tests and validates the screening layer in isolation. Run this to sanity-check `config.py` probability changes before a full simulation run.

### `notebooks/03_results_followup.ipynb`
Tests and demonstrates the follow-up pathways in isolation. Useful for verifying that LTFU rates and CIN grade distributions match clinical expectations.

### `notebooks/05_metrics_outputs.ipynb`
Deep-dive analytics on simulation output. Cervical result breakdowns by age stratum, pathway funnels, LTFU rates by node, wait-time distributions, and workflow comparison tables.

### `notebooks/06_scenario_analysis.ipynb`
Compares co-scheduling scenarios on a shared patient cohort. The eventual ROI deliverable — will be fully meaningful once `scenarios.py` is wired into the runner and LTFU multipliers are calibrated.

---

## How to Run

**Full 70-year population simulation**

```python
import sys
sys.path.insert(0, '/path/to/NYP/src')

import config as cfg
from runner import SimulationRunner

sim = SimulationRunner(
    n_days               = cfg.SIM_DAYS,        # 25,550 days = 70 years
    seed                 = cfg.RANDOM_SEED,
    use_stable_population = True,
    db_path              = "nyp_70yr.db",
    reset_db             = True,               # fresh DB each run
)
metrics = sim.run()

sim.summary()           # clinical funnel
sim.revenue_summary()   # realized vs. foregone revenue
sim.db_summary()        # database record counts

# Ad-hoc SQL query on the patient database
rows = sim._db.query("""
    SELECT exit_reason, COUNT(*) AS n, AVG(age_at_exit) AS mean_age
    FROM patients GROUP BY exit_reason
""")
for row in rows:
    print(row)

sim.close_db()
```

**Short run (1 year, no stable population)**

```python
sim = SimulationRunner(n_days=365, seed=42)
metrics = sim.run()
sim.summary()
```

**Jupyter Lab**

```bash
jupyter lab --notebook-dir=/path/to/NYP
```

Open `notebooks/04_simulation_runner.ipynb` for the full end-to-end run.

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
| 🟡 Soon | `PROVIDER_CAPACITY` | NYP scheduling data (currently set to sum to 200/day) |
| 🟢 Future | `LUNG_RADS_PROBS` | NYP LDCT registry data |
| 🟢 Future | `CAPACITIES` | NYP scheduling data |
| 🟢 Future | `ORGANIC_NEW_PATIENT_DAILY_RATE` | NYP patient acquisition data |
| 🟢 Future | `ANNUAL_MORTALITY_RATE` | NYC-specific mortality data |

---

## Next Steps

| Priority | Task |
|---|---|
| 🔴 Immediate | Drop in Yutong's population sampling code |
| 🔴 Immediate | Replace all PLACEHOLDER values with NYP EHR + finance data |
| 🟡 Near-term | Wire `scenarios.py` into the runner for co-scheduling analysis |
| 🟡 Near-term | Multi-replication runs + variance analysis (`NUM_REPS = 10` in config) |
| 🟡 Near-term | Calibration loop: compare sim output to NYP EHR aggregate statistics |
| 🟢 Future | Add breast and colon cancer pathways |
| 🟢 Future | Model insurance-stratified LTFU rates |

---

## Dependencies

```
matplotlib
sqlite3   (Python standard library — no install needed)
```

Install matplotlib: `pip install matplotlib`

All other dependencies (`random`, `dataclasses`, `collections`, `typing`) are Python standard library. The simulation does **not** use SimPy — patient flow is modeled as a direct day-by-day tick loop with explicit queue management, not a coroutine-based event loop.
