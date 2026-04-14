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
├── notebooks/                    # Jupyter notebooks (live model — run these)
│   ├── simulation.ipynb          # 70-year run, patient trace, visualizations, 1-year run
│   ├── metrics_outputs.ipynb     # Analysis, funnels, revenue, LTFU plots
│   └── scenario_analysis.ipynb   # Co-scheduling comparison (FUTURE — not yet implemented)
│
├── archive/                      # Archived and reference notebooks — not required to run
│   ├── 02_screening.ipynb
│   ├── 03_results_followup.ipynb
│   ├── initial_model_NYP_flow_simulation (1).ipynb
│   └── Simulation_draft_Yutong.ipynb
│
└── docs/
    └── GLOSSARY.md               # Definitions for all medical terms, acronyms, and CPT codes
```

**Design principle:** all logic lives in `.py` modules — importable, testable, version-controlled cleanly. Notebooks are thin wrappers that call those modules, run scenarios, and display output. You never need to edit a notebook to change a clinical parameter.

---

## The Open-System 70-Year Population Model

This is the conceptual core of the simulation. Understanding it is essential before reading any of the code.

### What "open system" means

The simulation does **not** run a fixed cohort of patients and watch them age to death. Instead, it models an **open system** — the way a real hospital actually works:

- Patients **enter** continuously: new women move to NYC, turn 21, switch providers, or are referred for the first time
- Patients **exit** continuously: some die, some age permanently out of all screening eligibility, some are lost to follow-up
- A core group is **retained**: established patients with an ongoing relationship with NYP return year after year for their annual visit and periodic screenings

Over 70 years, the composition of the patient pool shifts as generations of patients cycle through — but the total number of active patients remains approximately stable at 15,000 (representing ~1.5 million NYC women).

### The three patient flows

```
┌─────────────────────────────────────────────────────────────────────┐
│                    OPEN-SYSTEM PATIENT POOL                          │
│                      (target: 15,000 patients)                       │
│                                                                       │
│  INFLOW 1 — Organic new patients                                      │
│  ~10 new first-time patients/day arrive as drop-ins.                 │
│  After their first visit they join the established cycling pool.     │
│  Represents: women turning 21, new NYC residents,                    │
│  patients switching providers, first-time visitors.                  │
│                                                                       │
│  INFLOW 2 — Replacement entrants                                      │
│  Up to 4 patients/day, spawned to replace patients removed           │
│  by mortality or permanent ineligibility exits. Enter directly       │
│  as established cycling patients, sampled from the same NYC          │
│  demographic distribution. Prevents pool shrinkage.                  │
│                                                                       │
│  CYCLING CORE — Established patients                                  │
│  ~15,000 patients with an annual appointment at their primary        │
│  provider. Each visit triggers an immediate reschedule for the       │
│  next 5 annual visits (multi-year advance scheduling window).        │
│  Patient attributes evolve each year: pack-years accumulate,        │
│  some smokers quit, HPV can clear — all affecting eligibility.       │
│                                                                       │
│  OUTFLOW 1 — Mortality                                                │
│  Checked every 30 days. Age-specific Bernoulli draw per patient      │
│  (US life tables). Hard cap: no patient survives past age 100.       │
│  Dead patients exit and trigger a replacement entrant.               │
│                                                                       │
│  OUTFLOW 2 — Permanent ineligibility                                  │
│  Established patients who age out of ALL active screening            │
│  pathways (cervical ends at 65, lung window closes after 15 yrs     │
│  quit) exit the pool and trigger a replacement younger entrant.      │
│  This continuously refreshes the pool with newly eligible women.    │
│                                                                       │
│  OUTFLOW 3 — LTFU / declined                                          │
│  Non-established patients who decline rescheduling or are            │
│  permanently lost in the clinical follow-up pathway exit silently.  │
│  Established patients who miss a follow-up step are re-activated     │
│  and continue cycling — LTFU from a cancer pathway ≠ leaving NYP.  │
└─────────────────────────────────────────────────────────────────────┘
```

### Patient attribute evolution over time

Each established patient's clinical profile changes during the simulation's periodic mortality sweeps (every 30 days). These changes directly affect screening eligibility:

| Attribute | What changes | Eligibility impact |
|---|---|---|
| `age` | +1 year per year | Cervical eligibility ends at 65; lung ends at 80 |
| `pack_years` | +1/year while `smoker=True` | Patient crosses 20 pk-yr lung threshold mid-simulation |
| `smoker` | 5% annual cessation chance (PLACEHOLDER) | Former smokers enter the 15-yr quit window |
| `years_since_quit` | +1/year for former smokers | Closes lung eligibility window after 15 years |
| `hpv_positive` | 30% annual clearance (PLACEHOLDER) | Clears → lower abnormal cervical result risk |

This means a patient who enters at age 45 with 18 pack-years and is still smoking will **become lung-eligible mid-simulation** as her pack-years accumulate past 20. A smoker who quits during the simulation will remain lung-eligible for another 15 years before losing that eligibility. These dynamics are applied proportionally to the 30-day sweep interval so they are correct at any sweep frequency.

### The warmup period

On simulation day 0, all 15,000 established patients are created and their first annual visits are **spread evenly across year 1** (the warmup window). This prevents a cold-start artifact where providers are empty on day 1 and fill up slowly — instead, from day 1 onwards, providers operate near capacity as they would in a real steady-state clinic.

Each patient is also immediately pre-booked for their next **5 annual visits** (configurable via `ADVANCE_SCHEDULE_YEARS`), so the schedule book is filled years in advance from day 0.

### Multi-year advance scheduling

At any point in the simulation, every established patient has approximately 5 future annual appointments already booked. After each visit, `_reschedule_established()` adds the appointment at the far end of the window (year N+5), so the schedule book stays filled. Replacement entrants are immediately pre-booked for their first 5 visits too.

### Vacancy filling

When a patient exits (mortality or ineligibility), their future appointments remain in the scheduling queue as inactive records. Rather than letting those phantom bookings block new patients, `schedule_outpatient()` counts only **active** patients when checking slot capacity — a slot vacated by an exited patient is immediately available to a replacement or organic new entrant.

### Age-based drop-in priority

> **NYP model assumption (revenue maximization):** When drop-in capacity is limited, women aged 40+ are seen before younger patients.

**Rationale:** The 40+ cohort is disproportionately associated with higher-revenue procedures — colposcopy, LEEP, cone biopsy, and LDCT — so prioritising them when the drop-in queue exceeds available slots maximises expected revenue per available slot. This applies **only** to drop-in queue ordering. All scheduled outpatients retain their guaranteed slot regardless of age.

### Scale interpretation

| Simulation unit | Real-world equivalent |
|---|---|
| 1 simulated patient | 100 NYC eligible women |
| 15,000 sim patients | ~1.5 million NYC eligible women |
| 1 cervical screen | 100 cervical screens in the real NYC population |

All volume outputs should be multiplied by `POPULATION_SCALE_FACTOR = 100` when extrapolating to real-world planning figures.

---

## How the Simulation Works — End to End

### Time Model

The simulation runs as a **day-by-day tick loop**. Every day, in this exact order:

1. **Mortality sweep** (every 30 days): age all patients; update lifecycle attributes; Bernoulli mortality draw per patient; remove the dead; queue replacements
2. **Spawn replacements**: create up to 4 new established patients to refill the pool
3. **Flush to database**: batch-write exited patients to SQLite (every 30 days)
4. **Annual checkpoint** (every 365 days): snapshot cumulative stats for longitudinal plots
5. **Follow-up appointments**: process all clinical follow-ups due today (colposcopy, LEEP, biopsy) before new arrivals
6. **New arrivals**: create ~10 organic new patients; route to provider queues
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
│  1. Not yet eligible (e.g., age 48, almost 50)       │
│     → schedule return visit at future eligibility date│
│  2. New patient permanently ineligible               │
│     → exit silently (no screening was due)           │
│  3. Established patient permanently ineligible       │
│     → EXIT pool; trigger replacement younger entrant │
└────────────────────────┬─────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────┐
│  STEP 3 — SCREENING  [screening.py]                  │
│                                                      │
│  Cervical (age-stratified, USPSTF 2018):             │
│    Age 21–29 → Cytology only, every 3 years          │
│    Age 30–65 → Cytology (3 yrs) or HPV-alone (5 yrs)│
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
│  Mortality or permanent ineligibility → EXIT         │
│    → slot freed; replacement entrant spawned         │
└──────────────────────────────────────────────────────┘
```

---

## The Queuing Engine

The queue engine lives in `runner.py` and manages three distinct queue types, each with different scheduling and capacity semantics. All three must work together correctly for the simulation to produce realistic throughput, wait times, and procedure slot utilization.

### PatientQueues — data structure

```
PatientQueues
├── outpatient[provider][day]     → list[Patient]            Advance-scheduled appointments
├── dropin[provider]              → list[Patient]            Today's walk-ins
├── followup[day]                 → list[(Patient, context)] Future clinical procedures
└── daily_slots[day][procedure]   → int                      Remaining procedure slots
```

Each of the four fields is a separate queue with separate rules — they are not interchangeable.

---

### Queue Type 1 — Outpatient (advance-scheduled, guaranteed)

Outpatients call ahead and book a confirmed future appointment. The scheduler scans forward day by day until it finds a day where the **active** outpatient count is below the cap, then books that day.

```python
def schedule_outpatient(self, p, provider, earliest_day) -> int:
    cap = _outpatient_cap(provider)        # e.g., 52 for GYN (73% of 71 daily slots)
    day = earliest_day
    while sum(1 for q in self.outpatient[provider][day] if q.active) >= cap:
        day += 1                           # push forward until a free slot is found
    self.outpatient[provider][day].append(p)
    assert active_count <= cap             # hard post-condition — fires immediately on any bug
    return day
```

**Key properties:**

- **Guaranteed slot.** Once booked, the outpatient cannot be turned away on the day of their appointment. The scheduler will push the appointment as far forward as needed, but never double-books.
- **Active-only counting.** Only active (living, non-exited) patients count toward the daily cap. A slot vacated by a deceased patient becomes immediately available to replacement entrants or organic new arrivals — no manual cleanup needed.
- **Hard capacity assertion.** After every booking, the code asserts the cap was not violated. Any bug that allows two code paths to race on the same slot surfaces immediately rather than silently corrupting the schedule.
- **Lead-time windows** for new (non-established) outpatients:

| Provider | Lead time |
|---|---|
| PCP | 1–7 days |
| Gynecologist | 7–21 days |
| Specialist | 14–28 days |
| ER | No outpatient slots — always drop-in |

**Multi-year advance booking for established patients:**

In stable-population mode, established patients are pre-booked for the next 5 annual visits at all times. After each visit, `_reschedule_established()` adds the year-N+5 appointment, keeping the rolling window constant:

```
Patient seen on day 1825 (year 5):
  → already has appointments booked for years 6, 7, 8, 9
  → _reschedule_established() adds year 10 booking
  → patient now has years 6–10 confirmed in queue
```

This means at year 35, the schedule already contains confirmed appointments through year 40 for every active patient.

---

### Queue Type 2 — Drop-In (same-day, age-prioritised, overflow-capable)

Drop-ins arrive and want to be seen today. On each provider's daily tick, the queue is processed in this order:

```python
def process_day(self, provider, day):
    outpt_cap  = _outpatient_cap(provider)
    dropin_cap = _dropin_cap(provider)
    total_cap  = outpt_cap + dropin_cap         # = PROVIDER_CAPACITY[provider]

    # 1. Seat ALL scheduled outpatients unconditionally
    seen_outpts = self.outpatient[provider].pop(day, [])

    # 2. Remaining capacity for drop-ins = total - actual outpatients seen today
    #    (uses actual headcount, not config cap, so capacity reductions apply correctly)
    remaining = max(0, total_cap - len(seen_outpts))

    # 3. Age-based priority: women aged 40+ go to the front of the queue
    today_dropins = sorted(dropin[provider], key=lambda p: (0 if p.age >= 40 else 1))

    # 4. Seat up to remaining capacity; rest overflow
    seen_dropins = today_dropins[:remaining]
    overflow     = today_dropins[remaining:]    # deferred or re-routed

    return seen_outpts + seen_dropins, overflow
```

**Key properties:**

- **Outpatients always come first.** Their count is subtracted from total capacity before any drop-in is admitted.
- **Unused outpatient slots roll to drop-ins.** If scheduled outpatients are fewer than their reserved cap (e.g., some died since booking), those unused slots become available to drop-ins on the same day. Total capacity is never wasted.
- **Age priority (40+).** When the drop-in pool exceeds available slots, women aged 40+ are seen first. Within each age group, arrival order is preserved (stable sort). This is a revenue-maximisation assumption — the 40+ cohort drives higher-revenue procedures.
- **Overflow routing by provider type:**

| Provider | Overflow handling |
|---|---|
| PCP / GYN / Specialist | Re-added to tomorrow's drop-in queue for the same provider; wait time grows by 1 day |
| ER (critical patient) | Returns to ER tomorrow |
| ER (non-critical) | 30% chance of being converted to a scheduled outpatient appointment at a non-ER provider |

---

### Queue Type 3 — Follow-Up (future-dated clinical procedures)

After a screening event produces an actionable result (abnormal Pap, RADS 4 lung finding), the patient is placed in a future-day follow-up queue:

```python
def schedule_followup(self, p, context, due_day) -> None:
    self.followup[due_day].append((p, context))
```

The `context` dict carries clinical state: `{"cancer": "cervical", "step": "colposcopy"}`.

On the due day, follow-ups are processed **before** new arrivals in `_tick()`. This is a deliberate design choice: scheduled procedures take priority over same-day walk-in demand, accurately modeling how a real clinical schedule works.

---

### Procedure Slot Management

Each procedure type (colposcopy, LEEP, cone biopsy, lung biopsy, LDCT) has a finite daily capacity tracked separately from provider capacity. Slots are initialized lazily from `config.CAPACITIES` on first access and decremented as patients consume them:

```python
def consume_slot(self, procedure, day) -> bool:
    if procedure not in self.daily_slots[day]:
        self.daily_slots[day][procedure] = cfg.CAPACITIES.get(procedure, 0)
    if self.daily_slots[day][procedure] > 0:
        self.daily_slots[day][procedure] -= 1
        return True
    return False   # fully booked today — patient re-queued for tomorrow
```

If no slot is available, the patient is re-queued for tomorrow's same step, creating realistic queue build-up under high demand.

---

### How a Full Patient Journey Flows Through the Queues

```
Day 0 (warmup):   Patient scheduled for annual visit on day 47
                  Pre-booked for days 412, 777, 1142, 1507 (years 2–5)

Day 47:           Patient seen at GYN (drop-in, age 42 → gets queue priority)
                  → cervical screen: HSIL result drawn
                  → schedule_followup({step: colposcopy}, due_day=77)
                  → _reschedule_established() books year-6 visit (day 1872)

Day 77:           _run_followup() called before new arrivals
                  → consume_slot("colposcopy", 77)?
                      NO  → re-queue for day 78 (slot contention)
                      YES → CIN3 drawn
                          → run_treatment(): LTFU check (10%)
                              LTFU fires  → patient re-activated; continues cycling
                              LTFU passes → schedule_followup({step: leep}, due_day=91)

Day 78:           consume_slot("colposcopy", 78)? YES
                  (re-queued patient from day 77 is seen)

Day 91:           → consume_slot("leep", 91)? YES
                  → LEEP completed; revenue recorded
                  → patient re-activated; continues annual cycling

Day 30-sweep:     Mortality sweep runs
                  → p.age updated; pack_years += year_fraction (if smoker)
                  → smoker cessation draw (5% annual)
                  → HPV clearance draw (30% annual)
                  → Bernoulli mortality draw
                  → Dead patients removed; _pending_new_entries += death_count

Day 412:          Patient seen at GYN (pre-booked year-2 visit)
                  → now age 43, smoker, pack_years have grown since year 1
                  → lung eligibility checked against updated attributes
```

---

## The Database Layer

### Why a database?

A 70-year simulation with 15,000 cycling patients produces hundreds of thousands of patient records. `db.py` adds a zero-infrastructure SQLite persistence layer that:

- Writes exited patient records in **batch** (every 30 days) via a single `executemany` — ~100–1000× better throughput than per-row commits
- Enables **SQL queries after the run** — e.g., "show me all patients who had a CIN3 diagnosis and then died before treatment"
- Provides **longitudinal tracking** across 70 years: patient IDs are stable, so a patient who entered in year 1 can be traced through all their visits until mortality in year 55
- Uses **WAL mode** (Write-Ahead Logging) so notebooks can run summary queries while the simulation is still writing

### Schema

```sql
CREATE TABLE patients (
    patient_id                INTEGER PRIMARY KEY,
    age_at_entry              INTEGER,
    age_at_exit               INTEGER,
    race                      TEXT,
    insurance                 TEXT,
    is_established            INTEGER,   -- 1 = cycling pool; 0 = one-time visitor
    simulation_entry_day      INTEGER,
    exit_day                  INTEGER,
    exit_reason               TEXT,      -- mortality | lost_to_followup | untreated | ineligible
    visit_count               INTEGER,
    has_cervix                INTEGER,
    smoker                    INTEGER,
    pack_years                REAL,
    cervical_result           TEXT,
    lung_result               TEXT,
    colposcopy_result         TEXT,
    treatment_type            TEXT,
    last_cervical_screen_day  INTEGER,
    last_lung_screen_day      INTEGER
);

CREATE TABLE events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id  INTEGER,
    day         INTEGER,
    event       TEXT,
    FOREIGN KEY(patient_id) REFERENCES patients(patient_id)
);
```

All writes use `INSERT OR IGNORE` — re-flushing a patient already in the database after a crash is a silent no-op.

### Querying the database after a run

```python
sim = SimulationRunner(n_days=cfg.SIM_DAYS, use_stable_population=True,
                       db_path="nyp_70yr.db", reset_db=True)
metrics = sim.run()
sim.db_summary()

rows = sim._db.query("""
    SELECT race, AVG(age_at_exit) AS mean_age_exit, COUNT(*) AS n
    FROM patients
    WHERE exit_reason = 'mortality'
    GROUP BY race ORDER BY n DESC
""")

history = sim._db.get_patient_history(patient_id=42)
for day, event in history:
    print(f"Day {day:>5}: {event}")

sim.close_db()
```

---

## LTFU — Loss to Follow-Up

LTFU is checked explicitly at every clinical decision node, not applied as a bulk end-of-pathway probability. The simulation can tell you exactly *where* patients drop off and what the downstream revenue impact of each node is.

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

**Important for established patients:** LTFU from a specific cancer pathway does *not* remove an established patient from the provider pool. They are re-activated after the LTFU event and rescheduled for their next annual visit. They will be screened again at the next visit if still eligible. This models the real-world pattern where a patient misses a colposcopy but still shows up for their annual physical the following year.

---

## Source Files

### `src/config.py`
The single source of truth for every tunable value. All clinical probabilities, revenue rates, provider capacities, scheduling parameters, eligibility criteria, stable-population parameters, lifecycle transition rates, and database settings live here. Changing a value in `config.py` propagates everywhere.

Key stable-population parameters:
- `SIMULATED_POPULATION = 15_000` — established cycling patient pool size
- `POPULATION_SCALE_FACTOR = 100` — 1 sim patient = 100 NYC women
- `ORGANIC_NEW_PATIENT_DAILY_RATE = 10` — first-time visitors entering daily
- `NEW_PATIENT_DAILY_RATE = 4` — replacement entrant rate (max per day)
- `ADVANCE_SCHEDULE_YEARS = 5` — years of appointments pre-booked per patient
- `ANNUAL_VISIT_INTERVAL = 365` — days between established patient visits
- `MORTALITY_CHECK_DAYS = 30` — mortality and attribute-update sweep frequency
- `AGE_PRIORITY_THRESHOLD = 40` — age above which drop-in patients get queue priority

Key lifecycle transition rates (PLACEHOLDER):
- `ANNUAL_SMOKING_CESSATION_PROB = 0.05` — probability a current smoker quits per year
- `ANNUAL_HPV_CLEARANCE_PROB = 0.30` — probability an HPV+ patient clears per year

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
- `draw_mortality(p, sweep_days)` — Bernoulli draw scaled to sweep interval; returns `True` unconditionally if `p.age >= 100`

---

### `src/screening.py`
Implements eligibility determination, future eligibility estimation, test assignment, and result draws.

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
Implements result routing and clinical follow-up pathways for both cancers.

| Function | Purpose |
|---|---|
| `route_cervical_result(p, day, metrics)` | Routes cytology/HPV result to next step |
| `run_colposcopy(p, day, metrics)` | Draws CIN grade, updates patient |
| `run_treatment(p, day, metrics)` | LTFU check → surveillance or LEEP/cone |
| `run_cervical_followup(p, day, metrics)` | Full cervical pipeline orchestrator |
| `run_lung_followup(p, day, metrics)` | Full lung pipeline: RADS routing → biopsy chain |

---

### `src/metrics.py`
Collects and aggregates all simulation outputs. `year_checkpoints` stores per-year snapshots of:
- `cum_cervical`, `cum_lung` — cumulative screens by cancer
- `cum_colposcopy`, `cum_leep` — cumulative procedures
- `cum_lung_biopsy`, `cum_lung_treatment` — lung pathway procedures
- `cum_ltfu`, `cum_mortality` — exits by reason
- `pool_size` — active established patient count

`compute_revenue(metrics)` returns realized revenue from completed procedures and foregone revenue lost at each LTFU node:

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

### `src/runner.py`
The orchestration layer. Owns the clock, all queues, the stable-population pool, and the SQLite connection.

```python
sim = SimulationRunner(
    n_days                = cfg.SIM_DAYS,      # 25,550 days = 70 years
    seed                  = cfg.RANDOM_SEED,
    use_stable_population = True,
    db_path               = "nyp_70yr.db",
    reset_db              = True,
)
metrics = sim.run()
sim.summary()
sim.db_summary()
sim.close_db()
```

**Stable-population methods:**

| Method | Purpose |
|---|---|
| `_initialize_population()` | Create 15,000 established patients; spread across warmup; pre-book 5 years of visits |
| `_mortality_sweep(day)` | Age all patients; update lifecycle attributes; Bernoulli draw; remove dead and aged-out; queue replacements |
| `_reschedule_established(p, day)` | Extend advance-schedule window: book appointment at year N+5 |
| `_spawn_replacement_entrants(day)` | Create up to 4 new established patients/day to replace all exits |
| `_flush_exited_patients(day, force)` | Batch-write exited patients to SQLite every 30 days |

---

### `src/scenarios.py`
Defines four co-scheduling scenarios for future comparative analysis — not yet wired to the main runner.

| Scenario | Description |
|---|---|
| `baseline_fragmented` | Current state: each provider screens only their domain |
| `gyn_coordinated` | GYN visits identify lung-eligible patients and place LDCT referrals |
| `coordinated_all` | All due screenings bundled into one encounter |
| `high_access_coordinated` | Full co-scheduling + reduced scheduling friction |

---

## Notebooks

### `notebooks/simulation.ipynb` ← start here

Priority order within the notebook:

1. **70-Year Longitudinal Simulation** — runs `SimulationRunner` with `use_stable_population=True`; prints clinical and revenue summaries
2. **Single Patient Trace** — a randomly selected patient followed from entry to death or end of simulation, printing every clinical event year by year; visualised as a colour-coded age timeline
3. **Longitudinal Visualizations** — six time-series panels covering screening volume, clinical rates, revenue, patient cascade, lung pathway, and population dynamics over 70 years
4. **1-Year Simulation** — short standard-mode run for quick verification; screening funnel and lung pathway charts
5. **Step-by-Step Breakdown** — technical walkthrough of the daily simulation engine (Sophia's integration layer, SimPy loop, patient trace details)

### `notebooks/metrics_outputs.ipynb`
Deep-dive analytics on simulation output, in order of clinical priority:
1. Foregone revenue and screening capacity
2. Loss-to-follow-up by node
3. Cervical screening pipeline funnel
4. Lung cancer pathway funnel
5. Cervical results by age group (observed vs. expected)
6. CIN grade distribution by triggering result

### `notebooks/scenario_analysis.ipynb`
> ⚠️ **FUTURE — Not implemented in the current stage.** Will compare co-scheduling scenarios once `scenarios.py` is wired into the runner and LTFU multipliers are calibrated.

---

## How to Run

**Full 70-year population simulation**

```python
import sys
sys.path.insert(0, '/path/to/NYP/src')

import config as cfg
from runner import SimulationRunner

sim = SimulationRunner(
    n_days                = cfg.SIM_DAYS,        # 25,550 days = 70 years
    seed                  = cfg.RANDOM_SEED,
    use_stable_population = True,
    db_path               = "nyp_70yr.db",
    reset_db              = True,
)
metrics = sim.run()
sim.summary()
sim.db_summary()
sim.close_db()
```

**Short run (1 year, standard mode)**

```python
sim = SimulationRunner(n_days=365, seed=42)
metrics = sim.run()
sim.summary()
```

**Jupyter Lab**

```bash
jupyter lab --notebook-dir=/path/to/NYP
# Open notebooks/simulation.ipynb
```

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
| 🟡 Soon | `PROVIDER_CAPACITY` | NYP scheduling data |
| 🟡 Soon | `ANNUAL_SMOKING_CESSATION_PROB` | Literature / NYP patient data |
| 🟡 Soon | `ANNUAL_HPV_CLEARANCE_PROB` | Literature (natural history of HPV) |
| 🟢 Future | `LUNG_RADS_PROBS` | NYP LDCT registry data |
| 🟢 Future | `ANNUAL_MORTALITY_RATE` | NYC-specific mortality data |

---

## Next Steps

| Priority | Task |
|---|---|
| 🔴 Immediate | Drop in Yutong's population sampling code |
| 🔴 Immediate | Replace all PLACEHOLDER values with NYP EHR + finance data |
| 🟡 Near-term | Wire `scenarios.py` into the runner for co-scheduling analysis |
| 🟡 Near-term | Multi-replication runs + variance analysis |
| 🟡 Near-term | Calibration loop: compare sim output to NYP EHR aggregate statistics |
| 🟢 Future | Add breast and colon cancer pathways |
| 🟢 Future | Model insurance-stratified LTFU rates |

---

## Dependencies

```
matplotlib
sqlite3   (Python standard library — no install needed)
```

Install: `pip install matplotlib`

All other dependencies (`random`, `dataclasses`, `collections`, `typing`, `math`) are Python standard library. The simulation does **not** use SimPy — patient flow is modeled as a direct day-by-day tick loop with explicit queue management, not a coroutine-based event loop.
