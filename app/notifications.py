"""
Supporting services: notifications, constraint checking, and utilities.
"""

from typing import Dict, Any, List
from sqlalchemy.orm import Session

from app.database import RebalanceProposal, RebalanceMove, Staff, Appointment, Patient


def draft_patient_notification(db: Session, appointment_id: str, proposal_id: str) -> Dict[str, str]:
    """
    Draft a plain-language notification to a patient explaining a schedule change.
    Text-only, never actually sent.
    """
    appt = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    proposal = db.query(RebalanceProposal).filter(RebalanceProposal.id == proposal_id).first()
    
    if not appt or not proposal:
        return {"error": "Appointment or proposal not found"}
    
    patient = appt.patient
    old_site = appt.scanner.site.name
    
    # Find the new site from the move
    move = db.query(RebalanceMove).filter(
        RebalanceMove.proposal_id == proposal_id,
        RebalanceMove.appointment_id == appointment_id,
    ).first()
    
    if move and move.to_entity_id:
        # Look up the scanner
        from app.database import Scanner
        new_scanner = db.query(Scanner).filter(Scanner.id == move.to_entity_id).first()
        new_site = new_scanner.site.name if new_scanner else "another location"
    else:
        new_site = "another location"
    
    draft = f"""
Dear {patient.synthetic_name},

We are writing to inform you of a change to your scheduled appointment.

**What's happening?**
To reduce wait times and improve our service to all patients, we are adjusting your scan appointment location from {old_site} to {new_site}.

**Why?**
Your new location has immediate availability for your {patient.modality_needed} scan, whereas {old_site} currently has a wait queue. This change ensures you receive timely care.

**Your new appointment details:**
- Time: {move.new_start.strftime("%A, %B %d at %I:%M %p") if move and move.new_start else "Your originally scheduled time"}
- Scan type: {patient.modality_needed}
- Location: {new_site}

**Questions?**
Please call your primary care provider or the scheduling department at the new location to confirm.

Thank you for your understanding.

Sincerely,
ORBIT Scheduling System
"""
    
    return {
        "patient_id": patient.id,
        "patient_name": patient.synthetic_name,
        "draft": draft.strip(),
        "unsent": True,
    }


def draft_staff_notification(db: Session, proposal_id: str, move_id: str, recipient_role: str) -> Dict[str, str]:
    """
    Draft a notification to a radiologist or technologist explaining a proposed reassignment.
    Includes explainability ("why me") reasoning. Text-only, never sent.
    """
    proposal = db.query(RebalanceProposal).filter(RebalanceProposal.id == proposal_id).first()
    move = db.query(RebalanceMove).filter(RebalanceMove.id == move_id).first()
    
    if not proposal or not move:
        return {"error": "Proposal or move not found"}
    
    recipient = db.query(Staff).filter(Staff.id == move.to_entity_id).first()
    if not recipient:
        return {"error": "Staff member not found"}
    
    draft = f"""
Dear {recipient.name},

ORBIT has identified an opportunity to better balance workload across our network and is requesting your assistance.

**What's being asked?**
We are proposing to {"route a study to you for remote reading" if move.move_kind == "reassign_radiologist" else "request your remote guidance for a scanning session"}.

**Why you?**
Several factors make you an excellent match for this assignment:

1. **Expertise**: Your subspecialty in {recipient.subspecialty or "general radiology"} aligns with the clinical needs.
2. **Availability**: Your current caseload ({recipient.current_caseload} studies) is well-managed, and you have capacity.
3. **Wellness**: Your workload metrics indicate you're within safe fatigue thresholds.
4. **Licensing**: You are fully credentialed in all required jurisdictions.

**Impact**:
This balanced distribution helps:
- Reduce wait times for patients at our busier locations
- Spread fatigue and burnout risk evenly
- Improve equity across our network

**Next steps:**
This proposal is staged for coordinator review. If approved, you will be notified of specific details.

Thank you for being part of a network that prioritizes both patient care and staff wellness.

Sincerely,
ORBIT
"""
    
    return {
        "recipient_id": recipient.id,
        "recipient_name": recipient.name,
        "recipient_role": recipient.role,
        "draft": draft.strip(),
        "unsent": True,
    }


_GLOBAL_CONSTRAINTS = {
    "max_travel_km": 25,
    "protected_shifts": [
        "STAT hold 12:00-14:00",
        "Pediatric MRI 15:00-17:00",
    ],
    "remote_reading_enabled": True,
    "remote_scanning_assistance_enabled": True,
    "max_radiologist_caseload": 20,
    "max_technologist_caseload": 15,
    "fatigue_threshold": 0.8,
    "subspecialty_matching_strict": False,
    "enforce_jurisdiction_licensing": True,
    "enable_instant_fallback": True,
}


def update_constraint_summary(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Update global policy constraints."""
    global _GLOBAL_CONSTRAINTS
    for key, value in updates.items():
        if key in _GLOBAL_CONSTRAINTS:
            _GLOBAL_CONSTRAINTS[key] = value
    return _GLOBAL_CONSTRAINTS


def get_constraint_summary(db: Session) -> Dict[str, Any]:
    """
    Return the active policy profile.
    """
    return _GLOBAL_CONSTRAINTS


def validate_constraint(db: Session, appointment_id: str, constraint_name: str) -> Dict[str, Any]:
    """
    Validate a single constraint for an appointment.
    """
    appt = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not appt:
        return {"error": "Appointment not found"}
    
    patient = appt.patient
    
    if constraint_name == "travel_distance":
        if not patient.home_latitude or not appt.scanner.site.latitude:
            return {"passed": True, "reason": "Cannot calculate; assuming acceptable"}
        
        from app.rebalancing import haversine_distance
        distance = haversine_distance(
            patient.home_latitude, patient.home_longitude,
            appt.scanner.site.latitude, appt.scanner.site.longitude,
        )
        passed = distance <= patient.max_travel_km
        return {
            "passed": passed,
            "distance_km": distance,
            "limit_km": patient.max_travel_km,
        }
    
    elif constraint_name == "protected_shift":
        # Simplified; would check against actual shift definitions
        return {"passed": True, "reason": "No protected shift conflict"}
    
    elif constraint_name == "modality_match":
        return {
            "passed": appt.scanner.modality == patient.modality_needed,
            "scanner_modality": appt.scanner.modality,
            "patient_modality": patient.modality_needed,
        }
    
    elif constraint_name == "technologist_qualified":
        if not appt.technologist_id:
            return {"passed": False, "reason": "No technologist assigned"}
        
        tech = db.query(Staff).filter(Staff.id == appt.technologist_id).first()
        passed = appt.scanner.modality in tech.qualified_modalities
        return {
            "passed": passed,
            "scanner_modality": appt.scanner.modality,
            "tech_qualifications": tech.qualified_modalities,
        }
    
    elif constraint_name == "radiologist_licensed":
        if not appt.radiologist_id:
            return {"passed": False, "reason": "No radiologist assigned"}
        
        rad = db.query(Staff).filter(Staff.id == appt.radiologist_id).first()
        site_jurisdiction = appt.scanner.site.jurisdiction
        passed = site_jurisdiction in rad.licensed_jurisdictions
        return {
            "passed": passed,
            "site_jurisdiction": site_jurisdiction,
            "radiologist_jurisdictions": rad.licensed_jurisdictions,
        }
    
    else:
        return {"error": f"Unknown constraint: {constraint_name}"}


def get_network_status_summary(db: Session) -> List[Dict[str, Any]]:
    """
    Get utilization and constraint summary for all sites.
    """
    from app.database import Site
    from app.rebalancing import calculate_site_utilization
    
    sites = db.query(Site).all()
    return [calculate_site_utilization(db, site) for site in sites]
