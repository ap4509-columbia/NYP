# =============================================================================
# patient.py
# Shared Patient dataclass — the data contract between all simulation modules.
# =============================================================================

from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class Patient:
    # ── Core fields ───────────────────────────────────────────────────────────
    # These four are required at construction time; all others have defaults
    # so population.py can fill them in after the object is created.
    patient_id:   int
    day_created:  int
    patient_type: str           # "outpatient" | "drop_in"
    destination:  str           # "pcp" | "gynecologist" | "specialist" | "er"
    scheduled_day: int  = 0     # simulation day this patient is scheduled to be seen
    wait_days:     int  = 0     # cumulative days spent waiting across all visits
    return_count:  int  = 0     # how many times this patient has re-entered the system

    # ── Demographics (populated by population.py) ─────────────────────────────
    # Defaults are placeholders; population.py overwrites these with sampled values
    age:       int = 0
    race:      str = "unknown"
    ethnicity: str = "unknown"
    insurance: str = "unknown"

    # ── Clinical flags ────────────────────────────────────────────────────────
    # Drive eligibility checks and risk-adjusted result probabilities
    has_cervix:         bool  = True
    smoker:             bool  = False   # True = current smoker
    pack_years:         float = 0.0     # cumulative pack-years of smoking history
    years_since_quit:   float = 0.0     # 0 if current smoker; years ago they quit if former
    bmi:                float = 25.0
    hpv_positive:       bool  = False   # known hrHPV carrier (raises abnormal cytology risk)
    hpv_vaccinated:     bool  = False   # prior HPV vaccination (not yet used in result draws)
    prior_abnormal_pap: bool  = False   # any prior abnormal Pap result on record
    prior_cin: Optional[str]  = None    # None | "CIN1" | "CIN2" | "CIN3" (prior diagnosis)

    # ── Simulation state ──────────────────────────────────────────────────────
    active:                bool = True       # False once the patient exits the system
    current_stage:         str  = "arrived"  # tracks where in the pathway the patient is
    willing_to_reschedule: bool = True       # used by handle_unscreened to decide LTFU vs. retry

    # ── Screening history (simulation day of last screen; -1 = never) ─────────
    # Compared against today's day in is_due_for_screening() to enforce intervals
    last_cervical_screen_day: int = -1
    last_lung_screen_day:     int = -1

    # ── Most recent test results ───────────────────────────────────────────────
    # Cytology:  NORMAL | ASCUS | LSIL | ASC-H | HSIL
    # HPV-alone: HPV_NEGATIVE | HPV_POSITIVE
    # Lung:      RADS_0 | RADS_1 | RADS_2 | RADS_3 | RADS_4A | RADS_4B_4X
    cervical_result: Optional[str] = None
    lung_result:     Optional[str] = None

    # ── Cervical follow-up state ───────────────────────────────────────────────
    # Written by run_colposcopy() and run_treatment() in followup.py
    colposcopy_result: Optional[str] = None   # NORMAL | CIN1 | CIN2 | CIN3
    treatment_type:    Optional[str] = None   # surveillance | leep | cone_biopsy

    # ── Lung follow-up state ───────────────────────────────────────────────────
    # Written step-by-step as the patient clears each node in run_lung_pre_ldct()
    lung_referral_placed: bool         = False  # LDCT order placed by provider
    lung_ldct_scheduled:  bool         = False  # patient scheduled for LDCT
    lung_biopsy_result:   Optional[str] = None  # "malignant" | "benign" | None

    # ── ER triage flag ────────────────────────────────────────────────────────
    critical_status: bool = False  # True = critical ER patient (returns next day)

    # ── Exit state ────────────────────────────────────────────────────────────
    exit_reason: Optional[str] = None
    # treated | untreated | lost_to_followup | ineligible

    # ── Per-patient event log: [(day, event_string), ...] ─────────────────────
    # Each call to p.log() appends a (day, event_string) tuple here.
    # Used by print_patient_trace() to reconstruct the full patient journey.
    event_log: List[Tuple[int, str]] = field(default_factory=list)

    # ── Helpers ───────────────────────────────────────────────────────────────
    def log(self, day: int, event: str) -> None:
        """Append a (day, event) tuple to this patient's event log."""
        self.event_log.append((day, event))

    def exit_system(self, day: int, reason: str) -> None:
        """Mark patient as inactive, record exit reason, and log the exit event."""
        self.active        = False
        self.current_stage = "exited"
        self.exit_reason   = reason
        self.log(day, f"EXIT — {reason}")

    def print_history(self) -> None:
        """Print a formatted timeline of all events for this patient."""
        print(f"\n── Patient {self.patient_id} | age={self.age} | "
              f"destination={self.destination} ──")
        for day, event in self.event_log:
            print(f"  Day {day:>5}: {event}")
