from datetime import datetime, timezone
from itertools import count
from uuid import uuid4

from app.models import AuditEvent, BoardAssignment, ConstraintProfile, DashboardPayload, ProposalEntry, UtilizationSummary

_STATUS_RAIL = [
    UtilizationSummary(
        site="North Campus",
        modality="MRI",
        utilization_percent=94,
        queue_depth=18,
        idle_hours=0.5,
        coverage="Radiologist constrained",
    ),
    UtilizationSummary(
        site="Riverside",
        modality="MRI",
        utilization_percent=61,
        queue_depth=6,
        idle_hours=4.0,
        coverage="Balanced",
    ),
    UtilizationSummary(
        site="West Annex",
        modality="CT",
        utilization_percent=73,
        queue_depth=9,
        idle_hours=2.0,
        coverage="Technologist constrained",
    ),
]

_LIVE_BOARD = [
    BoardAssignment(
        time_label="08:00",
        scanner="MRI-02",
        modality="MRI",
        case_id="PT-1042",
        urgency="urgent",
        status="Booked at North Campus",
    ),
    BoardAssignment(
        time_label="10:30",
        scanner="MRI-07",
        modality="MRI",
        case_id="PT-1180",
        urgency="routine",
        status="Capacity open at Riverside",
    ),
    BoardAssignment(
        time_label="12:15",
        scanner="CT-03",
        modality="CT",
        case_id="PT-1225",
        urgency="stat",
        status="Protected slot",
    ),
]

_CONSTRAINTS = ConstraintProfile(
    max_travel_km=25,
    protected_shifts=["STAT hold 12:00-14:00", "Pediatric MRI 15:00-17:00"],
    remote_reading_enabled=True,
)

# In-memory demo state (synthetic data only — resets on server restart).
_proposals: dict[str, ProposalEntry] = {
    "prop-001": ProposalEntry(
        id="prop-001",
        patient_id="PT-1042",
        from_scanner="North Campus MRI-02",
        to_scanner="Riverside MRI-07",
        rationale="Moves a non-STAT scan into open Thursday capacity without exceeding the patient's travel policy.",
        urgency="urgent",
        constraint_checks=[
            "Travel distance within 25 km limit",
            "No protected shift conflict",
            "Destination scanner has open capacity",
        ],
    ),
    "prop-002": ProposalEntry(
        id="prop-002",
        patient_id="PT-1154",
        from_scanner="North Campus MRI-02",
        to_scanner="Riverside MRI-08",
        rationale="Reduces queue depth at the overloaded site while keeping the technologist coverage unchanged.",
        urgency="routine",
        constraint_checks=[
            "Travel distance within 25 km limit",
            "Technologist coverage unchanged at both sites",
            "No protected shift conflict",
        ],
    ),
}

_audit_log: list[AuditEvent] = [
    AuditEvent(
        id="audit-001",
        actor="agent",
        action="Drafted proposal",
        timestamp="2026-09-03T08:09:00Z",
        detail="2 MRI moves staged for human review.",
    ),
]

_audit_seq = count(2)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M")


def _log(actor: str, action: str, detail: str) -> None:
    _audit_log.insert(
        0,
        AuditEvent(
            id=f"audit-{next(_audit_seq):03d}-{uuid4().hex[:6]}",
            actor=actor,
            action=action,
            timestamp=_timestamp(),
            detail=detail,
        ),
    )


def get_dashboard_payload() -> DashboardPayload:
    return DashboardPayload(
        status_rail=_STATUS_RAIL,
        live_board=_LIVE_BOARD,
        approval_queue=list(_proposals.values()),
        audit_strip=_audit_log,
        constraints=_CONSTRAINTS,
    )


def _get_proposal(proposal_id: str) -> ProposalEntry:
    proposal = _proposals.get(proposal_id)
    if proposal is None:
        raise LookupError(f"Proposal {proposal_id} not found")
    return proposal


def approve_proposal(proposal_id: str, reviewer: str = "coordinator") -> ProposalEntry:
    proposal = _get_proposal(proposal_id)
    if proposal.status != "pending":
        raise ValueError(f"Proposal {proposal_id} is {proposal.status}, not pending. No action taken.")
    proposal.status = "approved"
    proposal.reviewed_by = reviewer
    _log(reviewer, "Approved move", f"{proposal_id} approved after constraint review.")
    return proposal


def reject_proposal(proposal_id: str, reviewer: str = "coordinator") -> ProposalEntry:
    proposal = _get_proposal(proposal_id)
    if proposal.status != "pending":
        raise ValueError(f"Proposal {proposal_id} is {proposal.status}, not pending. No action taken.")
    proposal.status = "rejected"
    proposal.reviewed_by = reviewer
    _log(reviewer, "Rejected move", f"{proposal_id} rejected by coordinator.")
    return proposal


def execute_proposal(proposal_id: str, actor: str = "agent") -> ProposalEntry:
    proposal = _get_proposal(proposal_id)
    if proposal.status != "approved":
        raise ValueError(f"Move {proposal_id} is not approved. No action taken.")
    proposal.status = "executed"
    _log(actor, "Executed move", f"{proposal_id} executed, authorized by {proposal.reviewed_by or 'unknown reviewer'}.")
    return proposal