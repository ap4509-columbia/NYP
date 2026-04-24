# =============================================================================
# cotesting.py
# Bundle cervical + lung procedures that naturally occur on the same day.
#
# Controlled by cfg.COTESTING["enabled"]. When disabled, both helpers are
# pass-through — no behavior change relative to the baseline simulation.
#
# Two symmetric bundling points:
#
#   Primary   — maybe_bundle_primary(): called inside _screen_patient when a
#               patient is due+eligible for both cervical and lung today.
#               Atomic slot check on (assigned cervical modality) + ldct.
#
#   Secondary — maybe_bundle_followup(): called inside the daily follow-up
#               dispatch when a patient has BOTH a colposcopy context and a
#               lung biopsy context due today. Atomic slot check on
#               colposcopy + lung_biopsy.
#
# If either slot is unavailable at the time of the atomic peek, neither is
# consumed and the caller falls back to the existing sequential path — so any
# procedure that does have capacity still proceeds through its normal flow.
#
# A successful bundle marks each bundled context/test with a preconsumed flag.
# The caller consults the flag to skip its own reschedule check and
# consume_slot call (both have already been done atomically here), but
# otherwise runs the exact same per-procedure logic.
# =============================================================================

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

import parameters as cfg


@dataclass
class BundleResult:
    """Output of maybe_bundle_primary.

    cancers_to_run        — pass-through of the eligible list so the caller's
                            loop shape is unchanged
    assigned_tests        — cervical modality (and "ldct" for lung) that was
                            already drawn inside the bundler. Caller uses this
                            to avoid a second weighted draw for cervical.
    preconsumed_by_cancer — populated only on successful bundle; keys are
                            cancer names ("cervical", "lung") and values are
                            the procedure slot already consumed. Caller uses
                            the presence of a key to skip reschedule + consume.
    cotested              — True iff an atomic bundle succeeded.
    """
    cancers_to_run: List[str]
    assigned_tests: Dict[str, str] = field(default_factory=dict)
    preconsumed_by_cancer: Dict[str, str] = field(default_factory=dict)
    cotested: bool = False


def is_enabled() -> bool:
    cot = getattr(cfg, "COTESTING", None)
    if not isinstance(cot, dict):
        return False
    return bool(cot.get("enabled", False))


def maybe_bundle_primary(
    patient,
    eligible: List[str],
    day: int,
    queues,
    metrics: Dict[str, Any],
    is_due_fn: Callable,
    assign_test_fn: Callable,
) -> BundleResult:
    """Attempt to bundle a cervical + lung primary screening on the same visit.

    Triggers only when ALL of these hold:
      • cfg.COTESTING["enabled"] is True
      • `eligible` contains both "cervical" and "lung"
      • patient is due for both today
      • the cervical modality slot AND the ldct slot both have capacity today

    On success both slots are consumed atomically here, metrics['n_cotest_primary']
    is incremented, and the returned BundleResult tells the caller to skip its
    own reschedule + consume_slot for those cancers. Screening execution
    (run_screening_step) still runs in the caller exactly as before.
    """
    if not is_enabled():
        return BundleResult(cancers_to_run=list(eligible))

    if "cervical" not in eligible or "lung" not in eligible:
        return BundleResult(cancers_to_run=list(eligible))

    if not (is_due_fn(patient, "cervical", day) and is_due_fn(patient, "lung", day)):
        return BundleResult(cancers_to_run=list(eligible))

    cervical_test = assign_test_fn(patient, "cervical")
    if not cervical_test:
        return BundleResult(cancers_to_run=list(eligible))

    assigned = {"cervical": cervical_test, "lung": "ldct"}

    # Atomic peek — both must have capacity, else no bundle (but we still
    # return the already-drawn cervical modality so the caller's fallback
    # doesn't redraw it).
    if not (queues.peek_slot(cervical_test, day) and queues.peek_slot("ldct", day)):
        return BundleResult(cancers_to_run=list(eligible), assigned_tests=assigned)

    # Atomic consume.
    queues.consume_slot(cervical_test, day)
    queues.consume_slot("ldct", day)
    metrics["n_cotest_primary"] = metrics.get("n_cotest_primary", 0) + 1

    return BundleResult(
        cancers_to_run=list(eligible),
        assigned_tests=assigned,
        preconsumed_by_cancer={"cervical": cervical_test, "lung": "ldct"},
        cotested=True,
    )


def maybe_bundle_followup(
    patient,
    due_ctxs: List[Dict[str, Any]],
    day: int,
    queues,
    metrics: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Attempt to bundle a colposcopy + lung biopsy on the same day.

    Triggers only when ALL of these hold:
      • cfg.COTESTING["enabled"] is True
      • due_ctxs contains a ("cervical", "colposcopy") ctx AND a ("lung", "biopsy") ctx
      • colposcopy AND lung_biopsy both have capacity today

    On success both slots are consumed atomically, each ctx is tagged with
    "cotest_preconsumed": True, and metrics['n_cotest_secondary'] is incremented.
    The returned list is the same due_ctxs (in place) — the caller dispatches
    each ctx through _run_followup as before; the per-step handlers read the
    flag and skip their own reschedule + consume_slot.
    """
    if not is_enabled():
        return due_ctxs

    cervix_ctx = next((c for c in due_ctxs
                       if c.get("cancer") == "cervical" and c.get("step") == "colposcopy"),
                      None)
    lung_ctx = next((c for c in due_ctxs
                     if c.get("cancer") == "lung" and c.get("step") == "biopsy"),
                    None)
    if cervix_ctx is None or lung_ctx is None:
        return due_ctxs

    if not (queues.peek_slot("colposcopy", day) and queues.peek_slot("lung_biopsy", day)):
        return due_ctxs

    queues.consume_slot("colposcopy", day)
    queues.consume_slot("lung_biopsy", day)
    cervix_ctx["cotest_preconsumed"] = True
    lung_ctx["cotest_preconsumed"] = True
    metrics["n_cotest_secondary"] = metrics.get("n_cotest_secondary", 0) + 1

    return due_ctxs
