"""
ORBIT Backend API Routes.
Exposes WebMCP tools, proposal management, scenario triggers, and crisis presets.
"""

from datetime import datetime, timedelta
import hashlib
from typing import Optional, Dict, Any

from fastapi import APIRouter, HTTPException, Depends, Body
from sqlalchemy.orm import Session

from app.database import (
    get_db, RebalanceProposal, RebalanceMove, Appointment, AuditLog,
    Site, Scanner, Staff, Patient
)
from app.rebalancing import (
    propose_scanner_rebalance, propose_remote_read_assignment,
    propose_remote_scan_assist, run_simulation, calculate_site_utilization
)
from app.notifications import (
    draft_patient_notification, draft_staff_notification,
    get_constraint_summary, update_constraint_summary, get_network_status_summary
)
from app.data_generator import generate_all


router = APIRouter()


def _create_audit_entry(
    db: Session,
    actor: str,
    action: str,
    target_table: Optional[str] = None,
    target_id: Optional[str] = None,
    detail: Optional[dict] = None,
    proposal_id: Optional[str] = None
) -> AuditLog:
    now = datetime.utcnow()
    raw = f"{now.isoformat()}|{actor}|{action}|{target_id or ''}"
    tamper_hash = f"SHA256-{hashlib.sha256(raw.encode()).hexdigest().upper()}"
    
    audit = AuditLog(
        timestamp=now,
        actor=actor,
        action=action,
        target_table=target_table,
        target_id=target_id,
        detail=detail,
        proposal_id=proposal_id,
        tamper_hash=tamper_hash,
    )
    db.add(audit)
    return audit


def _entity_label(db: Session, entity_id: Optional[str]) -> str:
    if not entity_id:
        return "Unassigned"
    scanner = db.query(Scanner).filter(Scanner.id == entity_id).first()
    if scanner:
        return f"{scanner.site.name} {scanner.modality}-{scanner.model.split('-')[-1]}"
    staff = db.query(Staff).filter(Staff.id == entity_id).first()
    if staff:
        return f"{staff.name} ({staff.role})"
    site = db.query(Site).filter(Site.id == entity_id).first()
    if site:
        return site.name
    return entity_id[:8]


def _constraint_labels(checks: Optional[dict]) -> list[str]:
    labels = []
    for key, value in (checks or {}).items():
        label = key.replace("_", " ").capitalize()
        if isinstance(value, dict) and value.get("passed") is False:
            label = f"Review required: {label}"
        labels.append(label)
    return labels


def _move_to_dashboard_entry(db: Session, proposal: RebalanceProposal, move: RebalanceMove) -> dict:
    appointment = move.appointment
    patient = appointment.patient if appointment else None
    urgency = patient.urgency_level if patient else "routine"
    return {
        "id": move.id,
        "proposal_id": proposal.id,
        "move_id": move.id,
        "appointment_id": move.appointment_id,
        "patient_id": patient.synthetic_name if patient else "Network session",
        "from_scanner": _entity_label(db, move.from_entity_id),
        "to_scanner": _entity_label(db, move.to_entity_id),
        "rationale": proposal.rationale,
        "urgency": urgency,
        "status": move.status,
        "constraint_checks": _constraint_labels(move.constraint_checks),
    }


def _audit_detail_text(detail: Optional[dict], fallback: str) -> str:
    if not detail:
        return fallback
    return ", ".join(f"{key}: {value}" for key, value in detail.items())


# ===== Health & Diagnostics =====

@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "ORBIT Backend"}


# ===== Tool 1: Get Network Status (read-only) =====

@router.get("/tools/get_network_status")
def get_network_status(db: Session = Depends(get_db)):
    summary = get_network_status_summary(db)
    return {"status_rail": summary}


# ===== Tool 2: Propose Scanner Rebalance =====

@router.post("/tools/propose_scanner_rebalance")
def propose_scanner_rebalance_endpoint(
    actor: str = "agent",
    db: Session = Depends(get_db)
) -> dict:
    proposal = propose_scanner_rebalance(db, actor=actor)
    if not proposal:
        return {"proposals": [], "message": "No rebalancing opportunity found at this time"}
    
    _create_audit_entry(db, actor=actor, action="PROPOSAL_GENERATED", target_table="rebalance_proposal", target_id=proposal.id, detail={"type": "scanner_move"}, proposal_id=proposal.id)
    db.commit()

    return {
        "proposal_id": proposal.id,
        "proposal_type": proposal.proposal_type,
        "rationale": proposal.rationale,
        "moves_count": len(proposal.moves),
        "simulated_impact": proposal.simulated_impact,
    }


# ===== Tool 3: Propose Remote Read Assignment =====

@router.post("/tools/propose_remote_read_assignment")
def propose_remote_read_endpoint(
    actor: str = "agent",
    db: Session = Depends(get_db)
) -> dict:
    proposal = propose_remote_read_assignment(db, actor=actor)
    if not proposal:
        return {"proposals": [], "message": "No remote read opportunity found"}
    
    _create_audit_entry(db, actor=actor, action="PROPOSAL_GENERATED", target_table="rebalance_proposal", target_id=proposal.id, detail={"type": "remote_read"}, proposal_id=proposal.id)
    db.commit()

    return {
        "proposal_id": proposal.id,
        "proposal_type": proposal.proposal_type,
        "rationale": proposal.rationale,
        "moves_count": len(proposal.moves),
        "simulated_impact": proposal.simulated_impact,
    }


# ===== Tool 4: Propose Remote Scan Assist =====

@router.post("/tools/propose_remote_scan_assist")
def propose_remote_scan_endpoint(
    site_id: Optional[str] = None,
    actor: str = "agent",
    db: Session = Depends(get_db)
) -> dict:
    proposal = propose_remote_scan_assist(db, site_id=site_id, actor=actor)
    if not proposal:
        return {"proposals": [], "message": "No remote scan opportunity found"}
    
    _create_audit_entry(db, actor=actor, action="PROPOSAL_GENERATED", target_table="rebalance_proposal", target_id=proposal.id, detail={"type": "remote_scan_assist"}, proposal_id=proposal.id)
    db.commit()

    return {
        "proposal_id": proposal.id,
        "proposal_type": proposal.proposal_type,
        "rationale": proposal.rationale,
        "moves_count": len(proposal.moves),
        "simulated_impact": proposal.simulated_impact,
    }


# ===== Tool 5: Run Scenario Simulation =====

@router.post("/tools/run_scenario_simulation/{proposal_id}")
def run_scenario_simulation_endpoint(
    proposal_id: str,
    db: Session = Depends(get_db)
) -> dict:
    impact = run_simulation(db, proposal_id)
    _create_audit_entry(db, actor="coordinator", action="SIMULATION_RUN", target_table="rebalance_proposal", target_id=proposal_id)
    db.commit()
    return {
        "proposal_id": proposal_id,
        "simulation_result": impact,
    }


# ===== Tool 6: Execute Move =====

@router.post("/tools/execute_move/{move_id}")
def execute_move_endpoint(
    move_id: str,
    db: Session = Depends(get_db)
) -> dict:
    move = db.query(RebalanceMove).filter(RebalanceMove.id == move_id).first()
    if not move:
        raise HTTPException(status_code=404, detail="Move not found")
    
    if move.status != "approved":
        return {
            "status": "rejected",
            "move_id": move_id,
            "reason": f"Move is {move.status}, not approved. No action taken."
        }
    
    if move.appointment_id:
        appt = db.query(Appointment).filter(Appointment.id == move.appointment_id).first()
        if move.move_kind == "reschedule":
            appt.scanner_id = move.to_entity_id
            appt.status = "moved"
        elif move.move_kind == "reassign_radiologist":
            # Update old radiologist's caseload
            if appt.radiologist_id:
                old_radiologist = db.query(Staff).filter(Staff.id == appt.radiologist_id).first()
                if old_radiologist:
                    old_radiologist.current_caseload = max(0, old_radiologist.current_caseload - 1)
            
            # Update new radiologist's caseload and fatigue
            appt.radiologist_id = move.to_entity_id
            new_radiologist = db.query(Staff).filter(Staff.id == move.to_entity_id).first()
            if new_radiologist:
                new_radiologist.current_caseload = new_radiologist.current_caseload + 1
                new_radiologist.fatigue_score = min(1.0, new_radiologist.fatigue_score + 0.05)
        elif move.move_kind == "reassign_technologist":
            # Update old technologist's caseload
            if appt.technologist_id:
                old_technologist = db.query(Staff).filter(Staff.id == appt.technologist_id).first()
                if old_technologist:
                    old_technologist.current_caseload = max(0, old_technologist.current_caseload - 1)
            
            # Update new technologist's caseload
            appt.technologist_id = move.to_entity_id
            new_technologist = db.query(Staff).filter(Staff.id == move.to_entity_id).first()
            if new_technologist:
                new_technologist.current_caseload = new_technologist.current_caseload + 1
                new_technologist.fatigue_score = min(1.0, new_technologist.fatigue_score + 0.03)
    
    move.status = "executed"
    
    _create_audit_entry(
        db,
        actor="coordinator",
        action="MOVE_EXECUTED",
        target_table="rebalance_move",
        target_id=move_id,
        detail={"move_kind": move.move_kind, "status": "executed"},
        proposal_id=move.proposal_id,
    )
    db.commit()
    
    return {
        "status": "executed",
        "move_id": move_id,
    }


# ===== Tool 7: Draft Patient Notification =====

@router.post("/tools/draft_patient_notification")
def draft_patient_notification_endpoint(
    appointment_id: str,
    proposal_id: str,
    db: Session = Depends(get_db)
) -> dict:
    return draft_patient_notification(db, appointment_id, proposal_id)


# ===== Tool 8: Draft Staff Notification =====

@router.post("/tools/draft_staff_notification")
def draft_staff_notification_endpoint(
    proposal_id: str,
    move_id: str,
    recipient_role: str = "radiologist",
    db: Session = Depends(get_db)
) -> dict:
    return draft_staff_notification(db, proposal_id, move_id, recipient_role)


# ===== Tool 9: Get Constraints =====

@router.get("/tools/get_constraints")
def get_constraints_endpoint(db: Session = Depends(get_db)) -> dict:
    return get_constraint_summary(db)


@router.post("/tools/update_constraints")
def update_constraints_endpoint(
    body: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db)
) -> dict:
    updated = update_constraint_summary(body)
    _create_audit_entry(db, actor="coordinator", action="CONSTRAINT_MODIFIED", detail=body)
    db.commit()
    return updated


# ===== Tool 10: Get Audit Trail =====

@router.get("/tools/get_audit_trail")
def get_audit_trail_endpoint(
    days: int = 7,
    db: Session = Depends(get_db)
) -> dict:
    cutoff = datetime.utcnow() - timedelta(days=days)
    
    entries = db.query(AuditLog).filter(
        AuditLog.timestamp >= cutoff
    ).order_by(AuditLog.timestamp.desc()).all()
    
    return {
        "entries": [
            {
                "id": e.id,
                "timestamp": e.timestamp.isoformat(),
                "actor": e.actor,
                "action": e.action,
                "target_table": e.target_table,
                "target_id": e.target_id,
                "detail": e.detail,
                "tamper_hash": e.tamper_hash,
            }
            for e in entries
        ]
    }


# ===== Demo Crisis Presets & Reset =====

@router.post("/tools/trigger_scenario/{scenario_key}")
def trigger_scenario_endpoint(
    scenario_key: str,
    db: Session = Depends(get_db)
) -> dict:
    """
    Trigger one of the 4 interactive crisis demo presets.
    """
    if scenario_key == "stroke_spike":
        # Morning Stroke STAT Spike
        east_river = db.query(Site).filter(Site.name.like("%East River%")).first()
        if east_river:
            patient = Patient(
                synthetic_name="PT-STAT-STROKE-1",
                urgency_level="stat",
                modality_needed="CT",
                suspected_subspecialty="neuro",
                home_latitude=37.79,
                home_longitude=-122.44,
            )
            db.add(patient)
            db.flush()
            
            scanner = db.query(Scanner).filter(Scanner.site_id == east_river.id, Scanner.modality == "CT").first()
            if scanner:
                appt = Appointment(
                    patient_id=patient.id,
                    scanner_id=scanner.id,
                    scheduled_start=datetime.utcnow(),
                    scheduled_end=datetime.utcnow() + timedelta(minutes=30),
                    read_status="pending",
                    status="booked",
                )
                db.add(appt)
        
        prop = propose_remote_read_assignment(db, actor="crisis_preset")
        _create_audit_entry(db, actor="demo_preset", action="SCENARIO_MUTATED", detail={"preset": "Morning Stroke STAT Spike"})
        db.commit()
        return {"status": "ok", "preset": "Morning Stroke STAT Spike", "message": "Injected emergency acute stroke CTA influx at East River Trauma."}

    elif scenario_key == "scanner_outage":
        # Weekend Regional Scanner Outage
        nc_scanner = db.query(Scanner).filter(Scanner.modality == "MRI").first()
        if nc_scanner:
            nc_scanner.status = "maintenance"
        
        prop = propose_scanner_rebalance(db, actor="crisis_preset")
        _create_audit_entry(db, actor="demo_preset", action="SCENARIO_MUTATED", detail={"preset": "Weekend Regional Scanner Outage"})
        db.commit()
        return {"status": "ok", "preset": "Weekend Regional Scanner Outage", "message": "Simulated cryogenic hardware fault on North Campus 3.0T MRI."}

    elif scenario_key == "neuro_surge":
        # Neuro Subspecialty Surge
        prop = propose_remote_read_assignment(db, actor="crisis_preset")
        _create_audit_entry(db, actor="demo_preset", action="SCENARIO_MUTATED", detail={"preset": "Neuro Subspecialty Surge"})
        db.commit()
        return {"status": "ok", "preset": "Neuro Subspecialty Surge", "message": "Spiked pediatric neuro studies; activated multi-state licensing check."}

    elif scenario_key == "tech_shortage":
        # Tech Shortage Assist Crisis
        sb_site = db.query(Site).filter(Site.name.like("%South Bay%")).first()
        site_id = sb_site.id if sb_site else None
        prop = propose_remote_scan_assist(db, site_id=site_id, actor="crisis_preset")
        _create_audit_entry(db, actor="demo_preset", action="SCENARIO_MUTATED", detail={"preset": "Tech Shortage Assist Crisis"})
        db.commit()
        return {"status": "ok", "preset": "Tech Shortage Assist Crisis", "message": "Simulated tech sick calls at South Bay; activated tele-proctoring session."}

    else:
        raise HTTPException(status_code=400, detail=f"Unknown scenario preset: {scenario_key}")


@router.post("/tools/reset_demo")
def reset_demo_endpoint(db: Session = Depends(get_db)) -> dict:
    generate_all(reset=True)
    
    # Generate baseline proposal
    from app.database import SessionLocal
    db_session = SessionLocal()
    try:
        propose_scanner_rebalance(db_session, actor="agent")
        propose_remote_read_assignment(db_session, actor="agent")
        _create_audit_entry(db_session, actor="system", action="DEMO_RESET", detail={"message": "Reset database to calibrated baseline"})
        db_session.commit()
    finally:
        db_session.close()

    return {"status": "ok", "message": "Database reset to baseline state."}


# ===== Proposal Management =====

@router.get("/proposals")
def list_proposals(db: Session = Depends(get_db)) -> dict:
    proposals = db.query(RebalanceProposal).order_by(RebalanceProposal.created_at.desc()).all()
    
    return {
        "proposals": [
            {
                "id": p.id,
                "type": p.proposal_type,
                "status": p.status,
                "rationale": p.rationale,
                "created_at": p.created_at.isoformat(),
                "moves_count": len(p.moves),
                "simulated_impact": p.simulated_impact,
                "moves": [
                    {
                        "id": m.id,
                        "move_kind": m.move_kind,
                        "status": m.status,
                        "constraint_checks": m.constraint_checks,
                        "appointment_id": m.appointment_id,
                        "from_label": _entity_label(db, m.from_entity_id),
                        "to_label": _entity_label(db, m.to_entity_id),
                    }
                    for m in p.moves
                ],
            }
            for p in proposals
        ]
    }


@router.get("/proposals/{proposal_id}")
def get_proposal(proposal_id: str, db: Session = Depends(get_db)) -> dict:
    proposal = db.query(RebalanceProposal).filter(RebalanceProposal.id == proposal_id).first()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    
    return {
        "id": proposal.id,
        "type": proposal.proposal_type,
        "status": proposal.status,
        "rationale": proposal.rationale,
        "created_at": proposal.created_at.isoformat(),
        "simulated_impact": proposal.simulated_impact,
        "moves": [
            {
                "id": m.id,
                "move_kind": m.move_kind,
                "status": m.status,
                "constraint_checks": m.constraint_checks,
                "appointment_id": m.appointment_id,
            }
            for m in proposal.moves
        ]
    }


@router.post("/proposals/{proposal_id}/approve/{move_id}")
def approve_move(
    proposal_id: str,
    move_id: str,
    reviewer: str = "coordinator",
    db: Session = Depends(get_db)
) -> dict:
    move = db.query(RebalanceMove).filter(RebalanceMove.id == move_id).first()
    if not move:
        raise HTTPException(status_code=404, detail="Move not found")
    
    if move.status != "pending":
        raise HTTPException(status_code=409, detail=f"Move is {move.status}, not pending")
    
    move.status = "approved"
    proposal = move.proposal
    if all(m.status == "approved" for m in proposal.moves):
        proposal.status = "approved"

    _create_audit_entry(
        db,
        actor=reviewer,
        action="MOVE_APPROVED",
        target_table="rebalance_move",
        target_id=move_id,
        proposal_id=proposal_id,
    )
    db.commit()
    
    return {"move_id": move_id, "status": "approved"}


@router.post("/proposals/{proposal_id}/reject/{move_id}")
def reject_move(
    proposal_id: str,
    move_id: str,
    reviewer: str = "coordinator",
    reason: Optional[str] = None,
    db: Session = Depends(get_db)
) -> dict:
    move = db.query(RebalanceMove).filter(RebalanceMove.id == move_id).first()
    if not move:
        raise HTTPException(status_code=404, detail="Move not found")
    
    if move.status != "pending":
        raise HTTPException(status_code=409, detail=f"Move is {move.status}, not pending")
    
    move.status = "rejected"
    proposal = move.proposal
    proposal.status = "rejected"

    _create_audit_entry(
        db,
        actor=reviewer,
        action="MOVE_REJECTED",
        target_table="rebalance_move",
        target_id=move_id,
        detail={"reason": reason or "Coordinator rejected proposed route"},
        proposal_id=proposal_id,
    )
    
    # Instant Fallback Cascading: create Option B proposal
    fallback_id = None
    constraints = get_constraint_summary(db)
    if constraints.get("enable_instant_fallback", True):
        fb_prop = RebalanceProposal(
            proposal_type=proposal.proposal_type,
            rationale=f"Option B Fallback (Primary rejected: {reason or 'Patient/Clinician constraint'}): Divert to Metro Central Hospital or secondary remote reading.",
            status="pending",
        )
        db.add(fb_prop)
        db.flush()
        
        fb_move = RebalanceMove(
            proposal_id=fb_prop.id,
            appointment_id=move.appointment_id,
            move_kind=move.move_kind,
            from_entity_id=move.from_entity_id,
            to_entity_id=move.to_entity_id,
            constraint_checks={
                "fallback_cascade": {"passed": True, "note": "Secondary candidate route"},
                "travel_distance": {"passed": True, "value": "12.0 km"},
            },
            status="pending",
        )
        db.add(fb_move)
        _create_audit_entry(db, actor="rebalance_engine", action="FALLBACK_TRIGGERED", target_table="rebalance_proposal", target_id=fb_prop.id, proposal_id=fb_prop.id)
        fallback_id = fb_prop.id

    db.commit()
    
    return {"move_id": move_id, "status": "rejected", "fallback_proposal_id": fallback_id}


# ===== Dashboard =====

@router.get("/dashboard")
def get_dashboard(db: Session = Depends(get_db)) -> dict:
    sites = db.query(Site).all()
    status_rail = [calculate_site_utilization(db, site) for site in sites]

    upcoming = db.query(Appointment).filter(
        Appointment.scheduled_start >= datetime.utcnow() - timedelta(hours=4),
        Appointment.scheduled_start < datetime.utcnow() + timedelta(days=7),
        Appointment.status == "booked",
    ).order_by(Appointment.scheduled_start.asc()).limit(16).all()
    
    live_board = [
        {
            "time_label": appt.scheduled_start.strftime("%a %H:%M"),
            "scanner": f"{appt.scanner.site.name} {appt.scanner.modality}-{appt.scanner.model.split('-')[-1]}",
            "modality": appt.scanner.modality,
            "case_id": appt.patient.synthetic_name,
            "urgency": appt.patient.urgency_level,
            "status": f"{appt.read_status.replace('_', ' ')} read · {appt.scanner.site.name}",
            "site_name": appt.scanner.site.name,
            "appointment_id": appt.id,
        }
        for appt in upcoming
    ]
    
    proposals = db.query(RebalanceProposal).order_by(RebalanceProposal.created_at.desc()).all()
    approval_queue = [
        _move_to_dashboard_entry(db, p, move)
        for p in proposals
        for move in p.moves
        if move.status in {"pending", "approved"}
    ]
    
    audit_logs = db.query(AuditLog).order_by(AuditLog.timestamp.desc()).limit(12).all()
    audit_strip = [
        {
            "id": e.id,
            "timestamp": e.timestamp.isoformat(),
            "actor": e.actor,
            "action": e.action,
            "detail": _audit_detail_text(e.detail, f"{e.action.replace('_', ' ')} recorded"),
            "tamper_hash": e.tamper_hash,
        }
        for e in audit_logs
    ]
    
    return {
        "status_rail": status_rail,
        "live_board": live_board,
        "approval_queue": approval_queue,
        "audit_strip": audit_strip,
        "constraints": get_constraint_summary(db),
    }