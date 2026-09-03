from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


UrgencyLevel = Literal["routine", "urgent", "stat"]
Modality = Literal["CT", "MRI", "XRAY", "US"]
ProposalState = Literal["pending", "approved", "rejected", "edited", "executed"]


class UtilizationSummary(BaseModel):
    site: str
    modality: Modality
    utilization_percent: int
    queue_depth: int
    idle_hours: float
    coverage: str


class BoardAssignment(BaseModel):
    time_label: str
    scanner: str
    modality: Modality
    case_id: str
    urgency: UrgencyLevel
    status: str


class ProposalEntry(BaseModel):
    id: str
    patient_id: str
    from_scanner: str
    to_scanner: str
    rationale: str
    urgency: UrgencyLevel
    constraint_checks: list[str] = []
    status: ProposalState = "pending"
    reviewed_by: str | None = None


class AuditEvent(BaseModel):
    id: str
    actor: str
    action: str
    timestamp: str
    detail: str


class ConstraintProfile(BaseModel):
    max_travel_km: int
    protected_shifts: list[str]
    remote_reading_enabled: bool


class DashboardPayload(BaseModel):
    status_rail: list[UtilizationSummary]
    live_board: list[BoardAssignment]
    approval_queue: list[ProposalEntry]
    audit_strip: list[AuditEvent]
    constraints: ConstraintProfile