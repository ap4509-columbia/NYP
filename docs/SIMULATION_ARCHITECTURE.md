# NYP Women's Health Screening Simulation — Architecture & Design

## Overview

This is a **discrete-event simulation (DES)** of a women's cancer screening program at NewYork-Presbyterian Hospital, built in Python. It models cervical and lung cancer screening pathways across a 70-year horizon for a stable cycling population of 15,000 simulated patients, each representing a cohort of real NYC women.

All numeric parameters live in a single file (`src/config.py`) and are never hard-coded elsewhere. To run a scenario analysis — doubling colposcopy capacity, changing LTFU rates, switching workflow modes — you change one number in config and re-run.

---

## 1. The Clock

The simulation runs as an **integer day-counter loop** from day 0 to day 25,550 (70 years × 365 days/year). There is no floating-point SimPy time — every iteration of the loop represents one full calendar day, processed by a function called `_tick(day)`.

**Day 0 is treated as Monday.** When `SKIP_WEEKENDS = True`, any day where `day % 7 ≥ 5` is a weekend. On those days, all clinical steps are suppressed — no arrivals, no provider queues, no screenings. Background processes (mortality sweeps, database flushes, annual metric checkpoints) still run on weekends, because they model biological and administrative continuity that is not bounded by clinic hours.

---

## 2. The Population

The simulation maintains a **stable cycling population** of 15,000 established patients. These are not 15,000 unique individuals followed once — they are 15,000 simulation agents, each representing a cohort of real NYC women via a population scale factor. The pool collectively represents the eligible screening population of the NYC metro area.

### Warmup

At day 0, all 15,000 patients are created and spread evenly across the first 365 days. Patient `i` receives their first appointment on day `i × 365 / 15,000`. This prevents a cold-start bias where year-1 metrics look artificially low because the clinic is empty.

Each patient is also given several years of pre-booked annual appointments from the start. After each visit, the far end of that window is extended by one year, so every established patient always has multiple future appointments on the books.

### Patient Attributes

Each patient is assigned at creation (from `population.py`):

- **Age** — drawn to match NYC demographic distributions for women aged 21–80
- `has_cervix` — anatomical eligibility for cervical screening
- `smoker` / `pack_years` / `years_since_quit` — lung screening eligibility attributes
- `hpv_positive` — HPV carrier status (modifies cervical result probabilities)
- `prior_cin` — prior CIN grade history (modifies high-grade result probabilities)
- `destination` — which provider type they are routed to (PCP, GYN, Specialist, ER)
- `noshow_count` — consecutive missed appointments since last attendance; resets to 0 on attendance
- `overdue_since_day` — simulation day the patient entered the overdue pool; `None` when current

---

## 3. Daily New Arrivals

Every weekday, a draw from a **Poisson distribution** (approximated by a Normal distribution for computational efficiency) determines how many new first-time patients arrive as drop-ins. These are women who are genuinely new to NYP: first-time visitors, women turning 21, recent movers, patients switching providers. After their first visit, they join the established cycling pool.

For each new arrival, two independent **categorical draws** happen:

1. **Destination** — which provider type they go to (PCP, GYN, Specialist, ER), drawn from configured routing weights
2. **Patient type** — whether they have a scheduled appointment (outpatient) or walk in (drop-in), drawn from configured type weights

Outpatient arrivals going to a non-ER provider receive a **scheduling lead time** drawn from a **discrete uniform distribution** over a provider-specific range of days. They are placed into the outpatient queue for that future day. Drop-ins and ER arrivals go directly into today's queue.

---

## 4. Queue Processing

Each weekday, for each of the four provider types (PCP, GYN, Specialist, ER):

### Outpatients
Placed in the outpatient queue by the scheduler, which guarantees their slot was never overfilled when it was booked. Total daily capacity is split between outpatient and drop-in slots via a configured fraction per provider type. ER has no outpatient fraction — it is entirely drop-in.

Before an outpatient is processed, a **no-show gate** fires (see Section 4a). Drop-ins are walk-ins who have already arrived and are not subject to this gate.

### Drop-ins
Fill whatever capacity remains after outpatients are seated. Available drop-in capacity = the drop-in cap plus any unused outpatient slots from today (e.g. if some booked patients have since died or aged out). When there are more drop-ins than available slots, **women aged 40 and above are sorted to the front of the queue** before the capacity slice is applied. This is a hospital operations assumption: the 40+ cohort is disproportionately associated with higher-complexity procedures.

### Overflow Routing
Drop-ins who cannot be seen:
- **PCP / GYN / Specialist** — placed into tomorrow's drop-in queue for the same provider
- **ER** — a **Bernoulli draw** determines whether they retry the ER tomorrow or are converted to a scheduled outpatient appointment at a non-ER provider with a new lead-time draw

---

## 4a. No-Show Gate

For every **scheduled outpatient** at a non-ER provider, a **Bernoulli draw** is taken before `_screen_patient` is called. The probability is provider-specific (`NO_SHOW_PROB` in config). If the patient does not show, `_handle_noshow` runs instead of screening.

### No-Show Cascade (`_handle_noshow`)

The chain has three sequential stages:

**Stage 1 — Cumulative exit draw.**
The patient's `noshow_count` is incremented. A **Bernoulli draw** is made against the cumulative exit probability for that streak position (`NOSHOW_CUMULATIVE_EXIT_PROB`). Probability rises with each consecutive miss. If the draw fires, the patient exits immediately as `lost_to_followup`. On a successful attendance (no-show did not trigger), `noshow_count` is reset to 0.

**Stage 2 — Post-no-show disposition.**
If the cascade draw did not exit the patient, a **weighted categorical draw** from `NOT_SEEN_PROBS` for that provider produces one of three fates:
- `reschedule` — a new appointment is booked after a provider-specific delay (`RESCHEDULE_DELAY_DAYS`)
- `exit` — patient exits immediately as `lost_to_followup`
- `wait` — outreach is attempted (Stage 3)

**Stage 3 — Outreach and final fate.**
If the patient is "waiting," `_apply_outreach` is called (see Section 4b). If outreach also returns `"wait"`, a final **weighted categorical draw** from `UNSCREENED_FATE` determines long-term disposition:
- `may_return_later` — patient's `overdue_since_day` is set; they enter the overdue pool monitored by the monthly sweep (Section 15)
- `exit_system` — permanent `lost_to_followup` exit

---

## 4b. Outreach (`_apply_outreach`)

A single outreach contact attempt. The probability distribution over outcomes is read from `OUTREACH_PROBS` keyed by `WORKFLOW_MODE`:
- `"coordinated"` → `"with_navigation"` (higher reschedule probability; reflects active care navigation)
- any other value → `"without_outreach"` (lower reschedule probability; reflects fragmented care)

A **weighted categorical draw** produces one of three outcomes:
- `reschedule` — books an appointment after `REENTRY_DELAY_DAYS`, clears `overdue_since_day`, resets `noshow_count`
- `exit` — exits as `lost_to_followup`
- `wait` — no action; returns `"wait"` to caller

This function is called from both `_handle_noshow` (Stage 3 above) and the monthly overdue sweep (Section 15). The `WORKFLOW_MODE` toggle means the coordinated-care scenario automatically produces higher outreach success rates than the fragmented-care scenario without any other code changes.

---

## 5. Screening Eligibility

Once a patient clears the no-show gate and is seen, two sequential checks determine whether screening occurs:

### Check 1 — Clinical Eligibility
- **Cervical:** age within the USPSTF-specified range AND anatomically has a cervix
- **Lung:** age within the USPSTF-specified range AND cumulative pack-years above the threshold AND (currently smoking OR quit within the allowed window)

Patients ineligible for all active cancer types who will never become eligible (aged out, no cervix, smoking window permanently closed) exit the cycling pool and trigger a replacement entrant.

### Check 2 — Interval Check
Is enough time elapsed since the patient's last screen? The interval is read from the **test type that was actually performed** at the previous visit, not a new random draw. This is critical: re-randomizing the test type at the interval check would apply the wrong interval (e.g. assigning a 5-year HPV-alone interval to a patient who actually received cytology last time).

### Never-Screeners
A fraction of eligible patients — assigned at population creation — will never initiate screening regardless of how many eligible visits they have. This fraction differs between cervical and lung and is drawn at patient initialization as a permanent attribute.

### Screening Initiation Model
For patients who are not never-screeners and who are both eligible and due, the probability of initiating screening on a given visit follows a **hybrid per-visit escalation model**:

```
P(screen at visit k) = min(base + (k − 1) × increment, cap)
```

The base, increment, and cap are configured separately by cancer type and provider type (e.g. a GYN visit has a higher cervical screening base rate than a PCP visit). This models the empirical finding that screening is more likely to be initiated on a second or third eligible visit than on the first.

---

## 6. Test Assignment

### Cervical, Age 21–29
Always cytology (Pap smear). USPSTF does not recommend HPV testing under age 30. No randomness — deterministic by age stratum.

### Cervical, Age 30–65
A **weighted categorical draw** over three modalities: co-test (HPV + cytology), cytology alone, and HPV-alone. Weights reflect practice patterns at large academic medical centers and are configurable.

### Lung
Always LDCT. No randomness in test assignment, but a multi-step attrition process runs before the scan occurs (see Section 7).

---

## 7. Pre-Scan Attrition (Lung Only)

Before any LDCT result is generated, the patient must clear three sequential gates. Each is an independent **Bernoulli draw** against a configured probability. If any gate fails, the patient is classified as lost-to-follow-up and no scan result is produced:

1. **Provider places a referral order** — a substantial fraction of eligible patients are never referred, representing the biggest real-world attrition point for lung screening
2. **Patient schedules the scan** — of those referred, some never book an appointment
3. **Patient completes the scan** — of those scheduled, some do not show up

All three probabilities are sourced from the literature and configurable in `config.py`.

---

## 8. Cervical Result Draw

After a cervical test, the result is drawn using **`random.choices`** (a weighted categorical draw) over a result probability table. The table used depends on the age stratum and test type:

- **Age 21–29 cytology:** draws from the young-stratum table with categories NORMAL, ASCUS, LSIL, ASC-H, HSIL
- **Age 30–65 cytology or co-test:** draws from the middle-stratum cytology table with the same categories
- **Age 30–65 HPV-alone:** draws from the HPV binary table with categories HPV_NEGATIVE, HPV_POSITIVE

### Risk Adjustment
Before the draw, the probability table is modified based on patient risk factors using a **multiplicative inflation + renormalization** procedure:

1. If `hpv_positive = True`: all abnormal cytology categories are multiplied by a configured risk multiplier, then the entire table is renormalized to sum to 1
2. If `prior_cin` is a high-grade history: the high-grade categories (ASC-H, HSIL) are additionally multiplied by a second configured multiplier, then renormalized again

These adjustments are applied sequentially (HPV inflation first, then CIN inflation), so a patient with both risk factors receives compounded inflation on the high-grade categories. All multipliers are configurable in `config.py`.

---

## 9. Lung-RADS Result Draw

After the three pre-scan gates clear, a single **weighted categorical draw** from the Lung-RADS v2022 distribution produces one of six result categories:

| Category | Meaning |
|---|---|
| RADS 0 | Incomplete scan — must repeat |
| RADS 1 | No nodules — clearly negative |
| RADS 2 | Nodule with benign appearance |
| RADS 3 | Probably benign — short-interval follow-up |
| RADS 4A | Suspicious — biopsy indicated |
| RADS 4B / 4X | Very suspicious — urgent biopsy |

Probabilities are sourced from published LDCT screening studies and are configurable in `config.py`.

---

## 10. Cervical Follow-Up Routing

After a result is drawn, the patient is routed based on result category:

- **NORMAL or HPV_NEGATIVE** → return to routine screening interval; no follow-up needed
- **Any abnormal cytology (ASCUS, LSIL, ASC-H, HSIL)** → LTFU check #1, then colposcopy referral if not lost
- **HPV_POSITIVE** → LTFU check #1, then ASCCP triage: a **Bernoulli draw** determines whether the patient goes to immediate colposcopy (higher-risk management) or a 1-year repeat cytology (lower-risk management)

### LTFU Check #1 — Post-Abnormal Result
A **Bernoulli draw** against the configured `post_abnormal_to_colposcopy` probability determines whether the patient fails to follow up after receiving an abnormal result. If lost, the patient exits the clinical pathway as `lost_to_followup` and returns to the annual cycling schedule.

### Scheduling Delay to Colposcopy
The colposcopy appointment is placed a fixed number of days in the future, read from `config.FOLLOWUP_DELAY_DAYS`. This is a fixed offset, not a distribution, because empirical scheduling data from NYP has not yet been integrated. Once NYP scheduling data is available, this should be replaced with an empirical wait-time distribution.

---

## 11. Colposcopy — CIN Grade Draw

On the day of the colposcopy appointment, a **conditional weighted categorical draw** produces a CIN grade. The probability table used is conditioned on the result that triggered the referral (e.g. a colposcopy triggered by HSIL uses a different distribution than one triggered by ASCUS). If the triggering result is not found in the config table, a default aggregate distribution is used as a fallback.

Possible outcomes: NORMAL (no dysplasia found), CIN1 (low-grade), CIN2 (high-grade), CIN3 (high-grade), INSUFFICIENT (biopsy sample inadequate — patient loops back for a repeat colposcopy).

---

## 12. Treatment Assignment

### CIN Grade → Procedure
Treatment assignment is deterministic by CIN grade:
- NORMAL colposcopy or CIN1 → surveillance (watchful waiting, 1-year follow-up cytology)
- CIN2 or CIN3 → LEEP (excisional procedure)

### LTFU Check #2 — Post-Colposcopy
Before treatment is assigned, a **Bernoulli draw** against the configured `post_colposcopy_to_treatment` probability determines whether the patient drops out after colposcopy before completing treatment. If lost, the patient exits as `untreated` and returns to the annual cycling schedule.

### CIN1 Surveillance
Patients in CIN1 surveillance return at each annual follow-up visit for a **three-outcome categorical draw**:

| Outcome | Meaning |
|---|---|
| Resolution | Lesion reverts to normal; patient returns to routine screening |
| Escalation | Lesion progresses to CIN2/3; patient enters the treatment pathway |
| Persistence | Lesion remains CIN1; patient continues surveillance for another year |

Probabilities are sourced from ASCCP guidelines and long-term CIN1 cohort studies and are configurable in `config.py`.

---

## 13. Lung Follow-Up

### RADS 0 / 1 / 2 / 3 — Repeat LDCT Pathway

1. **Result communication check** — **Bernoulli draw**: was the result successfully communicated to the patient? If not → LTFU.
2. **Adherence check (RADS 3 only)** — **Bernoulli draw** using the RADS-3-specific adherence rate from the literature. RADS 1/2 have no separate adherence check (negative results have higher natural adherence).
3. Schedule repeat LDCT at the appropriate interval per Lung-RADS guidelines (RADS 0: 1–3 months, RADS 3: 6 months, RADS 1/2: 12 months).

### RADS 4A / 4B / 4X — Biopsy Pathway

Five sequential **Bernoulli draws**, each representing a real-world attrition node:

1. Result successfully communicated to patient
2. Patient adheres to the biopsy recommendation (adherence rate is RADS-category-specific, sourced from published literature)
3. Biopsy referral placed by provider
4. Patient schedules the biopsy
5. Patient completes the biopsy

Then two conditional outcomes:

6. **Malignancy confirmed by pathology** — **Bernoulli draw**; if benign → return to annual surveillance
7. **Treatment given** — **Bernoulli draw**; if not → patient exits as `untreated`

Each node failure exits the patient as `lost_to_followup`. All node probabilities are configurable in `config.py`.

---

## 14. Patient Lifecycle and Re-Scheduling

After every provider visit — regardless of whether screening occurred, or whether a result was abnormal — established patients are immediately rescheduled for their next annual visit approximately one year later. The scheduler finds the next available outpatient slot on or after that target day.

### All Ways to Leave the Cycling Pool

| Exit Reason | Mechanism |
|---|---|
| **Mortality** | Bernoulli draw in monthly sweep, age-stratified annual rate from US life tables |
| **Permanent ineligibility** | Aged out of all cancers, no cervix, smoking window permanently closed — deterministic |
| **No-show cascade** | Cumulative Bernoulli exit draw after consecutive missed appointments |
| **No-show disposition exit** | Categorical draw from `NOT_SEEN_PROBS` returns `"exit"` after a single miss |
| **Outreach failure** | Outreach categorical draw returns `"exit"` during no-show or overdue handling |
| **UNSCREENED_FATE exit** | After both no-show and outreach return `"wait"`, final categorical draw returns `"exit_system"` |
| **Overdue pool timeout** | Patient in overdue pool exceeds `MAX_OVERDUE_DAYS` without re-engaging |
| **Clinical LTFU — post-abnormal** | Bernoulli draw after abnormal cervical result; patient exits that clinical episode |
| **Clinical LTFU — post-colposcopy** | Bernoulli draw before treatment; CIN2/3 patient exits without completing LEEP |
| **Lung pathway LTFU** | Any of the sequential Bernoulli gates in the lung referral or biopsy chain |
| **Untreated exit** | Malignancy confirmed but treatment Bernoulli draw fails |

For **established patients**, clinical LTFU exits (post-abnormal, post-colposcopy, lung pathway) are **soft exits** — the patient exits the current clinical episode but is immediately re-activated and rescheduled for next year's annual visit. They continue cycling. All other exits above are **hard exits** that remove the patient from the pool and trigger a replacement entrant.

For **non-established patients** (first-time drop-ins who have not yet completed their first visit), all exits are permanent.

---

## 15. Mortality Sweep and Overdue Pool

Every 30 days, a sweep runs over all established patients in the pool. It handles both mortality and the overdue pool in the same pass.

### Mortality

1. **Age is updated** — `age = age_at_entry + floor((current_day − entry_day) / 365)` — integer years only
2. **Smoking dynamics** — current smokers accumulate pack-years; a **Bernoulli draw** determines annual cessation; former smokers advance their years-since-quit counter (which eventually closes the USPSTF eligibility window)
3. **HPV clearance** — HPV-positive patients draw a **Bernoulli** against the configured annual clearance rate; cleared patients become HPV-negative, reducing their future result probabilities
4. **Mortality draw** — **Bernoulli(annual_rate × sweep_fraction)** where the annual rate comes from the age-stratified US life table lookup and `sweep_fraction = sweep_days / 365`

Dead patients are removed from the pool, buffered for database write, and counted toward a replacement quota.

### Overdue Pool

For each patient who survives the mortality draw and has `overdue_since_day` set:

1. Compute `days_overdue = current_day − overdue_since_day`
2. If `days_overdue > MAX_OVERDUE_DAYS`: the patient exits as `lost_to_followup` (`n_overdue_exit`) and triggers a replacement entrant
3. Otherwise: `_apply_outreach` is called (one monthly outreach attempt). If outreach returns `"wait"`, a **Bernoulli spontaneous re-entry draw** is taken, scaled from the daily re-entry probability to the 30-day sweep interval via `1 − (1 − p_daily)^sweep_days`. If it fires, `overdue_since_day` is cleared and an appointment is booked after `REENTRY_DELAY_DAYS` (`n_spontaneous_reentry`)

### Replacement Accounting

Non-mortality exits (no-show cascades, overdue timeouts, outreach failures) are accumulated in a counter between sweeps. At the end of each sweep this counter is folded into the main `_pending_new_entries` quota and reset, so replacement entrants trickle in to refill all exits — not just deaths — and the pool stays near its target size.

---

## 16. Metrics and Annual Checkpoints

Every 365 simulation days, a checkpoint snapshot is appended to `metrics["year_checkpoints"]` with all cumulative counts: cervical screens by modality, lung LDCTs, colposcopies, LEEPs, LTFU events, mortality count, pool size. The 70 checkpoint snapshots are the primary input to all notebook visualizations.

The metrics dictionary is the single output object of a simulation run. All rates and percentages (uptake %, LTFU %, completion %) are computed in the notebook by dividing event counts by the appropriate denominators from the same metrics object.

Key no-show and overdue counters tracked:
- `n_noshow` — total missed appointment events
- `n_noshow_exit` — exits triggered by the cumulative cascade
- `n_overdue_exit` — exits triggered by exceeding the maximum overdue window
- `n_spontaneous_reentry` — patients who re-engaged from the overdue pool without active outreach
- `ltfu_unscreened` — combined LTFU sub-bucket for all appointment-miss-driven exits

---

## Summary of Distributions Used

| Simulation Step | Distribution |
|---|---|
| Daily new arrivals | Poisson (Normal approximation) |
| Provider destination assignment | Categorical (weighted) |
| Patient type (outpatient / drop-in) | Categorical (weighted) |
| Outpatient scheduling lead time | Discrete Uniform [lo, hi] |
| **No-show draw (per outpatient)** | **Bernoulli (provider-specific rate)** |
| **Cumulative no-show exit** | **Bernoulli (probability indexed by streak length)** |
| **Post-no-show disposition** | **Categorical (weighted 3-way: reschedule / exit / wait)** |
| **Outreach outcome** | **Categorical (weighted 3-way, keyed by WORKFLOW_MODE)** |
| **Unscreened fate** | **Categorical (weighted 2-way: may_return_later / exit_system)** |
| **Spontaneous overdue re-entry** | **Bernoulli (daily rate scaled to sweep interval)** |
| Cervical test type (age 30–65) | Categorical (weighted, 3-way) |
| Cervical result draw | Categorical (weighted, with multiplicative risk adjustment and renormalization) |
| Lung-RADS result draw | Categorical (weighted, 6-way) |
| All clinical LTFU checks | Bernoulli |
| All lung pathway attrition gates | Bernoulli (sequential, independent) |
| HPV+ triage split | Bernoulli |
| Colposcopy CIN grade draw | Categorical (weighted, conditioned on triggering result) |
| CIN1 surveillance outcome | Categorical (weighted, 3-way: resolve / escalate / persist) |
| ER overflow routing | Bernoulli |
| Mortality draw | Bernoulli (age-stratified rate × time fraction) |
| HPV clearance | Bernoulli |
| Smoking cessation | Bernoulli |
| Age-based drop-in priority | Deterministic sort — no randomness |

---

## Key Design Principles

- **Single source of truth:** every probability, capacity, and interval is defined once in `config.py`. No magic numbers anywhere else in the codebase.
- **Scenario toggles:** changing `WORKFLOW_MODE` between `"fragmented"` and `"coordinated"` adjusts outreach success rates, capacity, and LTFU assumptions simultaneously for scenario comparison.
- **Parameter provenance:** every parameter in config is tagged with its source citation or labeled `# ASSUMPTION` if not yet grounded in data.
- **Stable population:** the cycling pool design avoids the ramp-up bias of open-queue models while keeping the age distribution realistic over 70 years through continuous mortality and non-mortality replacement.
- **Weekday-only clinical activity:** all screening and provider activity is suppressed on weekends; background biological and administrative processes are not.
- **Layered exit system:** exits are structured as a cascade — each layer (no-show, outreach, UNSCREENED_FATE, overdue timeout) gives the patient one more chance to re-engage before permanent removal, matching the real-world pattern of repeated missed contacts before a patient is truly lost.
