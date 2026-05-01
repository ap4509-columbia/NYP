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
    # ── Screening history (simulation day of last screen; -1 = never) ─────────
    # Compared against today's day in is_due_for_screening() to enforce intervals
    last_cervical_screen_day:     int           = -1
    last_lung_screen_day:         int           = -1
    # Test modality used at the last cervical screening visit.  Stored so that
    # is_due_for_screening() can apply the correct interval (cytology → 3 yrs,
    # hpv_alone → 5 yrs) without making a new random draw.  Without this field
    # the interval check is non-deterministic: two calls on the same day could
    # return different answers because assign_screening_test() uses random.choice.
    last_cervical_screening_test: Optional[str] = None  # "cytology" | "hpv_alone"

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

    # ── Stable population / longitudinal tracking ─────────────────────────────
    # These fields support the 70-year cycling model where established patients
    # re-visit annually and their demographics (age) update over time.
    #
    # is_established  — True for patients in the cycling pool; False for new
    #                   entrants / drop-ins who have not yet completed their
    #                   first visit.
    # age_at_entry    — snapshot of age when the patient joined the pool.
    #                   Used to compute current age via:
    #                       age = age_at_entry + (day - simulation_entry_day) // 365
    # simulation_entry_day — pool-entry day, used for age aging calculation.
    # visit_count     — incremented each time the patient is seen by a provider.
    # next_visit_day  — day the next annual appointment is scheduled; set by
    #                   _reschedule_established() after each visit.
    # exit_day        — day the patient left the system (mortality, LTFU, etc.).
    is_established:       bool         = False  # cycling patient vs new entrant
    age_at_entry:         int          = 0      # age on pool-entry day
    simulation_entry_day: int          = 0      # day patient was added to pool
    visit_count:          int          = 0      # total provider visits recorded
    next_visit_day:       Optional[int] = None  # next scheduled annual visit day

    # ── Scheduled life events (independent of visits) ────────────────────────
    # Drawn at patient entry and placed in the life-event priority queue.
    # Each fires on its scheduled day regardless of visit activity.
    scheduled_death_day:     Optional[int] = None  # Gompertz draw
    scheduled_attrition_day: Optional[int] = None  # Exponential draw (competing risks)
    attrition_subtype:       Optional[str] = None  # relocation | insurance_loss | provider_switch
    scheduled_cessation_day: Optional[int] = None  # Exponential draw (smokers only)
    scheduled_hpv_clear_day: Optional[int] = None  # Exponential draw (HPV+ only)

    # ── Latent (ghost) cancer state ──────────────────────────────────────────
    # Drawn ONCE at patient sampling from the same probability tables as the
    # screening tests — represents the patient's underlying disease state,
    # observable only through screening. Used to schedule cancer-mortality
    # events for patients whose ghost is abnormal. Treatment completion sets
    # the corresponding _cancelled flag so the death event no-ops when fired.
    true_cervical_state: Optional[str] = None  # NORMAL | ASCUS | LSIL | ASC-H | HSIL | HPV_NEGATIVE | HPV_POSITIVE
    true_lung_state:     Optional[str] = None  # RADS_0 | RADS_1 | RADS_2 | RADS_3 | RADS_4A | RADS_4B_4X
    cancer_death_cancelled_cervical: bool = False
    cancer_death_cancelled_lung:     bool = False

    # ── Exit state ────────────────────────────────────────────────────────────
    exit_day:    Optional[int] = None
    exit_reason: Optional[str] = None
    # treated | lost_to_followup | ineligible | mortality

    # ── Per-patient event log: [(day, event_string), ...] ─────────────────────
    # Each call to p.log() appends a (day, event_string) tuple here.
    # Used by print_patient_trace() to reconstruct the full patient journey.
    event_log: List[Tuple[int, str]] = field(default_factory=list)

    # ── Helpers ───────────────────────────────────────────────────────────────
    def log(self, day: int, event: str) -> None:
        """Append a (day, event) tuple to this patient's event log."""
        self.event_log.append((day, event))

    def exit_system(self, day: int, reason: str) -> None:
        """
        Mark patient as inactive, record exit reason and exit day, and log the event.

        The exit_day field is used by the stable-population model to sort
        patients into the flush buffer and to compute time-in-system statistics.
        Calling this method more than once is safe — subsequent calls are no-ops
        because active is already False.
        """
        if not self.active:
            return  # already exited — idempotent guard
        self.active        = False
        self.current_stage = "exited"
        self.exit_reason   = reason
        self.exit_day      = day
        self.log(day, f"EXIT — {reason}")

    def print_history(self) -> None:
        """Print a formatted timeline of all events for this patient."""
        print(f"\n── Patient {self.patient_id} | age={self.age} | "
              f"destination={self.destination} ──")
        for day, event in self.event_log:
            print(f"  Day {day:>5}: {event}")
