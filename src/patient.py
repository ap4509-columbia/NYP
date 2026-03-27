# =============================================================================
# patient.py
# Shared Patient dataclass — the data contract between all simulation modules.
# =============================================================================
# This class is a superset of Sophia's Patient dataclass, so all of her
# queue-management functions work unchanged on these extended objects.
# =============================================================================

from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class Patient:
    # ── Core fields (Sophia-compatible, do not rename) ────────────────────────
    patient_id:     int
    day_created:    int
    patient_type:   str           # "outpatient" | "drop_in"
    destination:    str           # "pcp" | "gynecologist" | "specialist" | "er"
    critical_status: bool = False
    scheduled_day:  int   = 0
    wait_days:      int   = 0
    return_count:   int   = 0

    # ── Demographics (populated by population.py) ─────────────────────────────
    age:        int  = 0
    race:       str  = "unknown"
    ethnicity:  str  = "unknown"
    insurance:  str  = "unknown"

    # ── Clinical flags ────────────────────────────────────────────────────────
    has_cervix:        bool  = True
    smoker:            bool  = False
    pack_years:        float = 0.0
    bmi:               float = 25.0
    hpv_positive:      bool  = False
    hpv_vaccinated:    bool  = False
    prior_abnormal_pap: bool = False
    prior_cin: Optional[str] = None   # None | "CIN1" | "CIN2" | "CIN3"

    # ── Simulation state ──────────────────────────────────────────────────────
    active:               bool = True
    current_stage:        str  = "arrived"
    # stages: arrived → screening → followup → treatment → surveillance → exited
    willing_to_reschedule: bool = True

    # ── Screening history (simulation day of last screen; -1 = never) ─────────
    last_cervical_screen_day:   int = -1
    last_lung_screen_day:       int = -1
    last_breast_screen_day:     int = -1
    last_colorectal_screen_day: int = -1
    last_osteo_screen_day:      int = -1

    # ── Most recent test results ───────────────────────────────────────────────
    # Cervical: NORMAL | ASCUS | LSIL | ASC-H | HSIL | HPV_POS_NORMAL_CYTO
    # Lung:     RADS_0 | RADS_1 | RADS_2 | RADS_3 | RADS_4A | RADS_4B_4X
    # Others:   NEGATIVE | POSITIVE
    cervical_result:   Optional[str] = None
    lung_result:       Optional[str] = None
    breast_result:     Optional[str] = None
    colorectal_result: Optional[str] = None
    osteo_result:      Optional[str] = None

    # ── Cervical follow-up state ───────────────────────────────────────────────
    colposcopy_result: Optional[str] = None   # NORMAL | CIN1 | CIN2 | CIN3
    treatment_type:    Optional[str] = None   # surveillance | leep | cone_biopsy

    # ── Lung follow-up state (per flowchart) ──────────────────────────────────
    lung_referral_placed:  bool          = False  # LDCT order placed by provider
    lung_ldct_scheduled:   bool          = False  # patient scheduled for LDCT
    lung_biopsy_result:    Optional[str] = None   # "malignant" | "benign" | None

    # ── Exit state ────────────────────────────────────────────────────────────
    exit_reason: Optional[str] = None
    # treated | untreated | lost_to_followup | ineligible

    # ── Per-patient event log: [(day, event_string), ...] ─────────────────────
    event_log: List[Tuple[int, str]] = field(default_factory=list)

    # ── Helpers ───────────────────────────────────────────────────────────────
    def log(self, day: int, event: str) -> None:
        """Append a timestamped event to this patient's history."""
        self.event_log.append((day, event))

    def exit_system(self, day: int, reason: str) -> None:
        """Mark patient as inactive and record exit reason."""
        self.active        = False
        self.current_stage = "exited"
        self.exit_reason   = reason
        self.log(day, f"EXIT — {reason}")

    def print_history(self) -> None:
        """Pretty-print this patient's event log (for debugging)."""
        print(f"\n── Patient {self.patient_id} | age={self.age} | "
              f"destination={self.destination} ──")
        for day, event in self.event_log:
            print(f"  Day {day:>5}: {event}")
