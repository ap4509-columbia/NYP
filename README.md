# NYP Women's Cancer Screening Simulation

An individual-level, open-population discrete-event simulation of the women's cancer screening pathway, modeled on the operational structure of NewYork-Presbyterian, a large academic medical center in the New York metropolitan area. The model represents cervical and lung cancer screening as a proof of concept for a broader multi-cancer women's screening panel, coupling clinical natural-history dynamics with an explicit operational layer (queueing, capacity, retention) so that operational interventions can be evaluated for their effects on both process outcomes (loss to follow-up, wait times) and clinical outcomes (cancer mortality).

## What this repository provides

- A discrete-event simulation engine that tracks every patient as a distinct entity through the screening pathway across an 80-year horizon (10-year warmup, 70-year measurement window).
- Two-layer parameter design: a clinical layer (natural history, screening-test performance, mortality) grounded in the published literature and shared across deployments; an operational layer (capacity, provider mix, LTFU hazards) configured per institution.
- A Monte Carlo runner that executes any scenario over 100 independent seeds in parallel isolated subprocesses.
- A pre-specified statistical change-analysis module (Welch's t, ANOVA + Tukey HSD, Wilcoxon rank-sum, Levene) that compares scenarios with Holm-Bonferroni multiplicity correction.
- Analysis notebooks that render publication-ready outputs for the base case and the four-scenario factorial design.

## Installation

The simulation is written in Python 3.11 and uses standard scientific libraries.

```bash
git clone git@github.com:ap4509-columbia/NYP.git
cd NYP
python3 -m venv .venv
source .venv/bin/activate
pip install numpy scipy statsmodels pandas matplotlib jupyter
```

Tested with Python 3.11.5, NumPy 2.3, SciPy 1.16, statsmodels 0.14, pandas 2.3, matplotlib 3.10.

## Quick start

### Run a single simulation

```python
import sys, os
sys.path.insert(0, "src")
sys.path.insert(0, "ModelParameters")

from mc_baseline import _run_one_baseline
result = _run_one_baseline(seed=42)
print(result["final_exits_by_source"])
```

### Run all four scenarios (Monte Carlo, 100 seeds each)

```python
from scenarios import run_all_scenarios
run_all_scenarios(n_seeds=100, seed_start=42)
```

Output CSVs land in `src/mc_scenario_data/` (per-seed, long-format).

### Run the statistical change analysis

```python
import change_analysis as ca

SCENARIOS = ["baseline_reference", "cotesting_only",
             "expanded_capacity", "cotesting_plus_expanded_capacity"]

results = ca.run_pairwise_analysis(SCENARIOS, baseline="baseline_reference", n_seeds=100)
ca.print_summary(results)

anova = ca.run_mortality_anova(SCENARIOS, n_seeds=100)
```

## Project structure

```
NYP/
├── src/                        # Simulation engine + analysis modules
│   ├── runner.py               # Main event loop; day-by-day dispatch
│   ├── patient.py              # Patient dataclass (attributes + state)
│   ├── model.py                # Patient sampling, ghost states, stage maps
│   ├── cotesting.py            # Same-day cervical-lung bundling logic
│   ├── db.py                   # SQLite persistence layer
│   ├── mc_baseline.py          # Single-seed sim driver + metric extractor
│   ├── mc_scenarios.py         # Parallel scenario Monte Carlo runner
│   ├── scenarios.py            # Scenario registry + parameter overrides
│   ├── stats_extract.py        # Per-run scalar metric computation
│   ├── change_analysis.py      # Pre-specified statistical comparisons
│   └── sensitivity.py          # Parameter sensitivity sweeps
├── ModelParameters/
│   └── parameters.py           # Every numeric parameter, cited inline
├── docs/                       # Architecture and glossary documentation
│   ├── README.md
│   ├── SIMULATION_ARCHITECTURE.md
│   └── GLOSSARY.md
├── spring-2026/                # Era-bound analysis notebooks + outputs
│   ├── notebooks/              # Base viz, scenario analysis, sensitivity MC
│   └── archive/                # Legacy notebooks (pre-refactor)
├── LICENSE
└── README.md
```

## Key design decisions

- **Individual-level, open-population**: the pool is emergent, arising from the balance of arrivals against exits, not a fixed cohort.
- **Ghost state paradigm**: each patient's underlying cancer trajectory is drawn once at creation from the screening-result probability tables; screening reveals it deterministically. Preserves the "one hidden truth per patient" invariant while permitting biology-driven re-evaluation on triggers (smoking initiation, HPV clearance / reacquisition).
- **Cancer as separately-progressing entity**: disease has its own stage and clock, running independently of the patient's visit schedule. Cervical (CIN1 → CIN2 → CIN3 → invasive) and lung (RADS_3 → RADS_4A → RADS_4B → invasive) progression via Bernoulli + Exponential mechanics with per-stage parameters from the literature.
- **Always-count rule**: mortality-family events (Gompertz, cancer_death, disease_mortality, cancer_progression) fire regardless of pool status. LTFU'd patients' biology runs to completion. Total mortality across a fixed population is therefore a biological invariant; screening interventions only reallocate deaths among causes.
- **Two-tier queueing**: separates provider intake capacity from per-procedure daily slot capacity. Over-capacity patients are re-queued for the next day with wait clock preserved; LTFU hazard applies per retry day.

See `docs/SIMULATION_ARCHITECTURE.md` for the full architectural walkthrough.

## Reproducibility

A given seed reproduces its run exactly provided patient-processing order is preserved. Monte Carlo replicates are executed in isolated subprocesses so scenario-specific parameter overrides never leak across runs. Every parameter in `ModelParameters/parameters.py` carries an inline citation or is explicitly flagged as a placeholder pending institutional calibration.

## Citation

If you use this simulation in academic work, please cite:

> Paiz Delgado A. NYP Women's Cancer Screening Simulation. GitHub, 2026. https://github.com/ap4509-columbia/NYP

A peer-reviewed publication is in preparation.

## License

MIT License. See [LICENSE](LICENSE).

## Contact

Alexandra Paiz Delgado — alexandrapaizdelgado@gmail.com

Columbia University, Department of Industrial Engineering and Operations Research.
