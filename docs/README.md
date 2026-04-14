# NYP Women's Health Screening Simulation — Digital Twin

A discrete-event simulation of NYP's multi-cancer women's health screening program. Tracks individual patients from provider arrival through screening, follow-up, and exit across an **80-year horizon** (10-year warmup + 70 years measured), quantifying drop-off at every step.

---

## Quick Start

```python
import sys
sys.path.insert(0, '/path/to/NYP/src')

import config as cfg
from runner import SimulationRunner

sim = SimulationRunner(
    n_days                = cfg.SIM_DAYS,        # 29,200 days = 80 years
    seed                  = cfg.RANDOM_SEED,
    use_stable_population = True,
    db_path               = "nyp_simulation.db",
    reset_db              = True,
)
metrics = sim.run()
sim.summary()
sim.revenue_summary()
sim.db_summary()
sim.close_db()
```

Or open `notebooks/simulation.ipynb` in Jupyter and run all cells.

---

## Scope

| | |
|---|---|
| **Cancers** | Cervical (USPSTF 2018) + Lung (USPSTF 2021) |
| **Horizon** | 80 years total; first 10 years warmup, 70 years measured |
| **Population** | 15,000 simulated patients ≈ 1.5M NYC women (scale factor 100) |
| **Workflow** | `"fragmented"` (current) or `"coordinated"` (future) |

---

## How It Works

### Population Model

**Open system** — no fixed cohort. Patients enter continuously via 4 arrival sources (aging in, new movers, ER walk-ins, referrals; total ~1.6/day Poisson) and exit via mortality, attrition, ineligibility, or LTFU. Pool size is emergent, not maintained at a target.

At entry, each patient is sampled from NYC Census distributions (age, race, insurance, smoking, BMI, HPV status, hysterectomy) and four life events are pre-drawn: **mortality** (Gompertz survival), **attrition** (exponential competing risks), **smoking cessation** (exponential), **HPV clearance** (exponential). Events fire on their scheduled day — no periodic sweeps.

### Daily Loop

Every day, in order:
1. **Life events** fire (mortality, attrition, cessation, clearance) — runs on weekends too
2. **DB flush** — batch-write exited patients to SQLite every 30 days
3. **Follow-ups** processed (colposcopy, LEEP, biopsy, surveillance, repeat LDCT) — consume slots before new arrivals
4. **New arrivals** generated from arrival sources
5. **Provider queues** — outpatients first, then ER; each patient screened for eligible cancers

Weekends (Sat/Sun) skip clinical steps 3–5.

### Screening

Patients are checked for eligibility (cervical: age 21–65 + cervix; lung: age 50–80 + ≥20 pack-years + current/recent smoker), then screening interval (`is_due_for_screening`). Test assignment is age-stratified for cervical (cytology only for 21–29; co-test 55% / cytology 35% / HPV-alone 10% for 30–65). Results are drawn from probability tables with risk adjustments for HPV+ and prior CIN.

### Procedure Capacity

Daily slot limits enforced per procedure (e.g., cytology 8/day, LDCT 4/day, colposcopy 8/day). When full, patient is rescheduled to the next workday with a daily queue LTFU hazard. Wait time = days beyond scheduled appointment caused by overflow.

### Follow-Up Pathways

**Cervical:** Normal → routine. Abnormal cytology → colposcopy (50-day delay). HPV+ → 60% colposcopy / 40% 1-year repeat. Colposcopy draws CIN grade → NORMAL/CIN1 go to surveillance; CIN2/3 go to LEEP. Post-treatment surveillance per ASCCP (q6mo yr 1–2, q12mo yr 3–5, q36mo yr 6+).

**Lung:** Pre-LDCT funnel (referral → scheduling → completion, ~35% survive). Lung-RADS routing: RADS 0/1/2/3 → repeat LDCT at intervals; RADS 4A/4B → biopsy pathway (5 sequential LTFU nodes). Post-treatment surveillance per NCCN.

### Re-Entry

Established patients who hit pathway LTFU are **re-activated** and continue annual visits — missing a colposcopy doesn't mean leaving NYP. Non-established patients who hit LTFU exit permanently.

---

## File Structure

```
NYP/
├── src/
│   ├── config.py       # Single source of truth for all parameters
│   ├── patient.py      # Patient dataclass
│   ├── population.py   # NYC demographic sampler + life event draws
│   ├── screening.py    # Eligibility, test assignment, result draws
│   ├── followup.py     # Cervical + lung follow-up pathways
│   ├── metrics.py      # Counters, rates, revenue analysis
│   ├── db.py           # SQLite persistence (patients + events tables)
│   └── runner.py       # SimulationRunner — day loop + queue engine
│
├── notebooks/
│   ├── simulation.ipynb        # Main: 80-year run + 30+ visualizations
│   └── metrics_outputs.ipynb   # Deep-dive analytics
│
├── docs/
│   ├── README.md                    # This file
│   ├── SIMULATION_ARCHITECTURE.md   # Full technical architecture (all distributions, all parameters)
│   └── GLOSSARY.md                  # Medical terms + CPT codes
│
└── archive/            # Reference notebooks
```

All logic in `.py` modules; notebooks are thin wrappers. Change any parameter in `config.py` and re-run.

---

## Key Outputs

- **Clinical funnel:** eligible → screened → abnormal → colposcopy → treated, with drop rates at each step
- **Revenue analysis:** realized (procedures completed × CPT rate) vs. foregone (LTFU × missed procedure cost)
- **Wait times:** days beyond scheduled appointment per procedure type
- **Population dynamics:** pool size, entries/exits by reason, mortality, retention distributions
- **30+ visualizations:** capacity utilization, demand vs. capacity, screening uptake, clinical cascades, revenue trends

---

## Placeholder Parameters

All clinical probabilities and revenue rates in `config.py` are marked `# PLACEHOLDER` — replace with NYP EHR/finance data before operational use.

| Priority | What to Replace |
|---|---|
| High | `PROCEDURE_REVENUE` — NYP contract rates |
| High | `CERVICAL_RESULT_PROBS` — NYP lab abnormal rates |
| High | `LTFU_PROBS` — NYP EHR attrition data |
| Medium | `LUNG_PATHWAY_PROBS` — NYP LDCT referral/completion data |
| Medium | `CAPACITIES` — NYP procedure slot throughput |
| Medium | `COLPOSCOPY_RESULT_PROBS` — ASCCP risk tables |

---

## Documentation

For the complete technical architecture — every distribution, every parameter name, every probability table, the exact daily loop order, queue mechanics, and re-entry logic — see **[`docs/SIMULATION_ARCHITECTURE.md`](SIMULATION_ARCHITECTURE.md)**.

---

## Dependencies

```
simpy
matplotlib
sqlite3  (standard library)
```

Install: `pip install simpy matplotlib`
