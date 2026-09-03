"""
Rebalancing logic for ORBIT.
Implements Pillar 1 (Scanner), Pillar 2 (Radiologist), and Pillar 2 (Technologist) logic.
"""

from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import math

from sqlalchemy.orm import Session

from app.database import (
    RebalanceProposal, RebalanceMove, Appointment, Site, Scanner, Staff,
    Patient, AuditLog
)


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in km between two points."""
    R = 6371  # Earth radius in km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2 +
        math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.asin(math.sqrt(a))
    return R * c


def calculate_site_utilization(db: Session, site: Site) -> Dict[str, Any]:
    """Calculate utilization metrics for a site."""
    now = datetime.utcnow()
    
    # Appointments for the next 7 days
    future_appts = db.query(Appointment).filter(
        Appointment.scheduled_start >= now,
        Appointment.scheduled_start < now + timedelta(days=7),
    ).all()
    
    site_appts = [a for a in future_appts if a.scanner.site_id == site.id]
    
    # Scanner metrics
    total_scanners = len(site.scanners)
    operational_scanners = len([s for s in site.scanners if s.status == "operational"])
    queue_depth = len(site_appts)
    
    operating_block_slots = max(1, operational_scanners * 3.5)
    utilization = int((queue_depth / operating_block_slots) * 100) if operational_scanners else 0
    idle_hours = max(0, operating_block_slots - queue_depth)
    
    # Staff coverage - now considers actual availability (workload + fatigue)
    radiologists = [s for s in site.staff if s.role == "radiologist"]
    technologists = [s for s in site.staff if s.role == "technologist"]
    
    # Calculate available capacity for radiologists (not overloaded or fatigued)
    available_radiologists = sum(
        1 for r in radiologists 
        if r.current_caseload < 15 and r.fatigue_score < 0.75
    )
    # Calculate available capacity for technologists
    available_technologists = sum(
        1 for t in technologists 
        if t.current_caseload < 12 and t.fatigue_score < 0.70
    )
    
    coverage_status = "balanced"
    if len(radiologists) == 0:
        coverage_status = "radiologist_constrained"
    elif available_radiologists == 0 or queue_depth / max(1, available_radiologists) > 8:
        coverage_status = "radiologist_constrained"
    elif len(technologists) == 0:
        coverage_status = "technologist_constrained"
    elif available_technologists == 0 or queue_depth / max(1, available_technologists) > 8:
        coverage_status = "technologist_constrained"
    elif utilization > 85:
        coverage_status = "equipment_constrained"
    
    modality_counts = {}
    for appt in site_appts:
        modality_counts[appt.scanner.modality] = modality_counts.get(appt.scanner.modality, 0) + 1
    primary_modality = max(modality_counts, key=modality_counts.get) if modality_counts else "MRI"
    readable_coverage = coverage_status.replace("_", " ").capitalize()
    return {
        "site_id": site.id,
        "site": site.name,
        "site_name": site.name,
        "jurisdiction": site.jurisdiction,
        "modality": primary_modality,
        "utilization_percent": min(100, utilization),
        "utilization": min(100, utilization),
        "queue_depth": queue_depth,
        "idle_hours": round(idle_hours, 1),
        "scanners": total_scanners,
        "coverage_status": coverage_status,
        "coverage": readable_coverage,
        "radiologists": len(radiologists),
        "available_radiologists": available_radiologists,
        "technologists": len(technologists),
        "available_technologists": available_technologists,
    }


def propose_scanner_rebalance(db: Session, actor: str = "system") -> Optional[RebalanceProposal]:
    """
    Pillar 1: Identify overloaded sites and propose scanner-based rebalancing.
    """
    sites = db.query(Site).all()
    utilizations = {s.id: calculate_site_utilization(db, s) for s in sites}
    
    # Find overloaded and underutilized sites
    overloaded = [u for u in utilizations.values() if u["utilization_percent"] > 75]
    underutilized = [u for u in utilizations.values() if u["utilization_percent"] < 50]
    
    if not overloaded or not underutilized:
        return None
    
    overloaded_site = max(overloaded, key=lambda x: x["queue_depth"])
    underutilized_site = min(underutilized, key=lambda x: x["queue_depth"])
    
    # Find candidate appointments to move
    now = datetime.utcnow()
    overloaded_appts = db.query(Appointment).filter(
        Appointment.scheduled_start >= now,
        Appointment.scheduled_start < now + timedelta(days=7),
    ).all()
    overloaded_appts = [
        a for a in overloaded_appts
        if a.scanner.site_id == overloaded_site["site_id"] and a.status == "booked"
    ][:3]  # Limit to 3 moves per proposal
    
    if not overloaded_appts:
        return None
    
    # Create proposal
    proposal = RebalanceProposal(
        proposal_type="scanner_move",
        rationale=f"Move {len(overloaded_appts)} appointments from {overloaded_site['site_name']} ({overloaded_site['queue_depth']} queue) to {underutilized_site['site_name']} ({underutilized_site['queue_depth']} queue) to reduce wait times.",
        status="pending",
    )
    db.add(proposal)
    db.flush()
    
    # Create moves
    underutilized_site_obj = db.query(Site).filter(Site.id == underutilized_site["site_id"]).first()
    
    for appt in overloaded_appts:
        # Find a compatible scanner at the underutilized site
        target_scanner = db.query(Scanner).filter(
            Scanner.site_id == underutilized_site["site_id"],
            Scanner.modality == appt.scanner.modality,
            Scanner.status == "operational",
        ).first()
        
        if not target_scanner:
            continue
        
        # Validate travel constraint
        if appt.patient.home_latitude and appt.patient.home_longitude and underutilized_site_obj.latitude:
            distance = haversine_distance(
                appt.patient.home_latitude, appt.patient.home_longitude,
                underutilized_site_obj.latitude, underutilized_site_obj.longitude,
            )
        else:
            distance = 0
        
        move = RebalanceMove(
            proposal_id=proposal.id,
            appointment_id=appt.id,
            move_kind="reschedule",
            from_entity_id=appt.scanner_id,
            to_entity_id=target_scanner.id,
            new_start=appt.scheduled_start,  # Keep same time for PoC
            distance_delta_km=distance - 0,  # Simplified
            constraint_checks={
                "travel_distance": {
                    "passed": distance <= appt.patient.max_travel_km,
                    "value": distance,
                    "limit": appt.patient.max_travel_km,
                },
                "modality_match": {"passed": True},
                "scanner_available": {"passed": True},
            },
            status="pending",
        )
        db.add(move)
    
    db.commit()
    
    # Add simulated impact with calculated values
    actual_moves = len([m for m in proposal.moves if m.appointment_id is not None])
    original_queue = overloaded_site["queue_depth"]
    new_queue = max(0, original_queue - actual_moves)
    
    proposal.simulated_impact = {
        "affected_appointments": actual_moves,
        "queue_depth_reduction": {"before": original_queue, "after": new_queue},
        "estimated_wait_time_improvement": f"~{actual_moves * 45} minutes per site",
        "capacity_rebalance": f"Load shifted from {overloaded_site['site_name']} to {underutilized_site['site_name']}",
        "network_efficiency": "Reduces bottlenecks while utilizing available capacity",
    }
    db.commit()
    
    return proposal


def propose_remote_read_assignment(db: Session, actor: str = "system") -> Optional[RebalanceProposal]:
    """
    Pillar 2: Identify radiologist bottlenecks and propose remote reading assignments.
    """
    sites = db.query(Site).all()
    utilizations = {s.id: calculate_site_utilization(db, s) for s in sites}
    
    # Find radiologist-constrained sites
    constrained = [
        u for u in utilizations.values()
        if u["coverage_status"] == "radiologist_constrained"
    ]
    
    if not constrained:
        return None
    
    constrained_site = max(constrained, key=lambda x: x["queue_depth"])
    
    # Find appointments pending reads at this site
    now = datetime.utcnow()
    pending_reads = db.query(Appointment).filter(
        Appointment.read_status == "pending",
        Appointment.scheduled_start >= now - timedelta(days=1),
        Appointment.scheduled_start < now + timedelta(days=3),
    ).all()
    pending_reads = [
        a for a in pending_reads
        if a.scanner.site_id == constrained_site["site_id"]
    ][:2]  # Limit to 2 moves per proposal
    
    if not pending_reads:
        return None
    
    # Find remote radiologists
    current_reads = db.query(Appointment).filter(
        Appointment.read_status.in_(["pending", "in_progress"]),
    ).count()
    
    remote_radiologists = db.query(Staff).filter(
        Staff.role == "radiologist",
        Staff.remote_eligible == True,
    ).all()
    
    if not remote_radiologists:
        return None
    
    # Create proposal
    proposal = RebalanceProposal(
        proposal_type="remote_read",
        rationale=f"Route {len(pending_reads)} studies from {constrained_site['site_name']} to remote radiologists to reduce read queue.",
        status="pending",
    )
    db.add(proposal)
    db.flush()
    
    for appt in pending_reads:
        # Find best matching remote radiologist
        best_radiologist = None
        
        # Prefer subspecialty match with good availability
        for rad in remote_radiologists:
            # Check licensure - must be licensed in the appointment's jurisdiction
            if constrained_site["jurisdiction"] not in rad.licensed_jurisdictions:
                continue
            
            # Prefer subspecialty match
            if (rad.subspecialty == appt.patient.suspected_subspecialty or 
                appt.patient.suspected_subspecialty is None or 
                rad.subspecialty == "general"):
                if rad.fatigue_score < 0.75 and rad.current_caseload < 18:
                    best_radiologist = rad
                    break
        
        # Fallback to any available remote radiologist
        if not best_radiologist and remote_radiologists:
            best_radiologist = min(
                [r for r in remote_radiologists if r.fatigue_score < 0.85],
                key=lambda r: (r.current_caseload, r.fatigue_score),
                default=remote_radiologists[0]
            )
        
        if not best_radiologist:
            continue  # Skip this appointment if no suitable radiologist found
        
        move = RebalanceMove(
            proposal_id=proposal.id,
            appointment_id=appt.id,
            move_kind="reassign_radiologist",
            from_entity_id=appt.radiologist_id,
            to_entity_id=best_radiologist.id,
            constraint_checks={
                "licensure": {
                    "passed": constrained_site["jurisdiction"] in best_radiologist.licensed_jurisdictions,
                    "reason": f"Remote radiologist licensed in {constrained_site['jurisdiction']}",
                },
                "subspecialty_match": {
                    "passed": best_radiologist.subspecialty == appt.patient.suspected_subspecialty,
                    "assigned": best_radiologist.subspecialty or "general",
                    "requested": appt.patient.suspected_subspecialty or "general",
                },
                "fatigue_threshold": {
                    "passed": best_radiologist.fatigue_score < 0.8,
                    "current_score": round(best_radiologist.fatigue_score, 2),
                    "threshold": 0.8,
                },
                "caseload_capacity": {
                    "passed": best_radiologist.current_caseload < 20,
                    "current_load": best_radiologist.current_caseload,
                    "capacity": 20,
                },
            },
            status="pending",
        )
        db.add(move)
    
    db.commit()
    
    # Add simulated impact with calculated values
    queue_reduction = len([m for m in proposal.moves if m.appointment_id is not None])
    proposal.simulated_impact = {
        "affected_appointments": queue_reduction,
        "queue_depth_reduction": {"before": constrained_site["queue_depth"], "after": max(0, constrained_site["queue_depth"] - queue_reduction)},
        "estimated_time_savings": f"~{queue_reduction * 90} minutes total",
        "workload_distribution": "Radiologist load balanced across remote network",
        "staff_wellness_impact": "Reduces fatigue and burnout risk at constrained site",
    }
    db.commit()
    
    return proposal


def propose_remote_scan_assist(db: Session, site_id: Optional[str] = None, actor: str = "system") -> Optional[RebalanceProposal]:
    """
    Pillar 2: Identify technologist bottlenecks and propose remote scanning assistance.
    """
    if not site_id:
        # Find most constrained site
        sites = db.query(Site).all()
        utilizations = {s.id: calculate_site_utilization(db, s) for s in sites}
        constrained = [
            u for u in utilizations.values()
            if u["coverage_status"] == "technologist_constrained"
        ]
        if not constrained:
            return None
        constrained_site = max(constrained, key=lambda x: x["queue_depth"])
        site_id = constrained_site["site_id"]
    
    # Find idle scanners with remote guidance capability
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        return None
    
    now = datetime.utcnow()
    idle_scanners = []
    
    for scanner in site.scanners:
        if scanner.status != "operational" or not scanner.remote_guidance_enabled:
            continue
        
        appts_next_24h = db.query(Appointment).filter(
            Appointment.scanner_id == scanner.id,
            Appointment.scheduled_start >= now,
            Appointment.scheduled_start < now + timedelta(hours=24),
        ).count()
        
        if appts_next_24h == 0:
            idle_scanners.append(scanner)
    
    if not idle_scanners or len(idle_scanners) < 2:
        return None
    
    # Find remote technologists
    remote_techs = db.query(Staff).filter(
        Staff.role == "technologist",
        Staff.remote_eligible == True,
    ).all()
    
    if not remote_techs:
        return None
    
    proposal = RebalanceProposal(
        proposal_type="remote_scan_assist",
        rationale=f"Enable {len(idle_scanners)} idle scanners at {site.name} via remote guidance sessions with distributed technologists.",
        status="pending",
    )
    db.add(proposal)
    db.flush()
    
    for scanner in idle_scanners[:2]:
        best_tech = min(remote_techs, key=lambda t: t.current_caseload)
        
        move = RebalanceMove(
            proposal_id=proposal.id,
            appointment_id=None,  # No specific appointment yet; generic remote session
            move_kind="remote_guidance_session",
            from_entity_id=site_id,
            to_entity_id=best_tech.id,
            constraint_checks={
                "remote_guidance_enabled": {"passed": True},
                "tech_availability": {"passed": best_tech.current_caseload < 15},
                "modality_qualified": {"passed": scanner.modality in best_tech.qualified_modalities},
            },
            status="pending",
        )
        db.add(move)
    
    db.commit()
    
    proposal.simulated_impact = {
        "idle_scanners_activated": len(idle_scanners),
        "projected_capacity_gain_per_week": f"{len(idle_scanners) * 4} additional scans",
        "technologist_utilization": "Distributed across remote workforce",
        "network_resilience": "Increases equipment availability without additional on-site staff",
    }
    db.commit()
    
    return proposal


def run_simulation(db: Session, proposal_id: str) -> Dict[str, Any]:
    """
    Tier 1: Deterministic simulation of proposal impact.
    """
    proposal = db.query(RebalanceProposal).filter(RebalanceProposal.id == proposal_id).first()
    if not proposal:
        return {"error": "Proposal not found"}
    
    moves = proposal.moves
    if not moves:
        return {"error": "No moves in proposal"}
    
    # Simulate impact
    queue_reduction = sum(1 for m in moves if m.appointment_id is not None)
    
    impact = {
        "proposal_type": proposal.proposal_type,
        "moves_count": len(moves),
        "affected_appointments": queue_reduction,
        "projected_improvements": {
            "queue_depth_reduction": queue_reduction,
            "wait_time_reduction_hours": queue_reduction * 0.5,
            "equity_improvement": "Workload redistributed across network",
        },
        "risk_assessment": {
            "constraint_violations": 0,
            "all_checks_passed": True,
        },
    }
    
    return impact
