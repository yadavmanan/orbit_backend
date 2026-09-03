"""
SQLite database setup using SQLAlchemy ORM.
Defines all tables and relationships per the ORBIT Engineering Specification.
"""

from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import create_engine, Column, String, Integer, Float, DateTime, Boolean, ForeignKey, JSON, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

# SQLite database file location
DATABASE_URL = "sqlite:///./orbit.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


# ===== Domain Models =====

class Site(Base):
    """Imaging center / hospital site in the network."""
    __tablename__ = "site"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name = Column(String, nullable=False, unique=True)
    jurisdiction = Column(String, nullable=False)  # state/country for licensure
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)

    scanners = relationship("Scanner", back_populates="site")
    staff = relationship("Staff", back_populates="home_site")


class Scanner(Base):
    """Medical imaging scanner."""
    __tablename__ = "scanner"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    site_id = Column(String(36), ForeignKey("site.id"), nullable=False)
    modality = Column(String, nullable=False)  # 'CT','MRI','XRAY','US'
    model = Column(String, nullable=True)
    remote_guidance_enabled = Column(Boolean, default=False)
    status = Column(String, default="operational")  # operational, maintenance, offline

    site = relationship("Site", back_populates="scanners")
    appointments = relationship("Appointment", back_populates="scanner")


class Staff(Base):
    """Radiologist or technologist."""
    __tablename__ = "staff"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name = Column(String, nullable=False)
    role = Column(String, nullable=False)  # 'radiologist' | 'technologist'
    subspecialty = Column(String, nullable=True)  # e.g., 'neuro', 'MSK', 'body', 'general'
    home_site_id = Column(String(36), ForeignKey("site.id"), nullable=True)
    licensed_jurisdictions = Column(JSON, default=list)  # list of jurisdiction strings
    qualified_modalities = Column(JSON, default=list)  # list of modalities
    remote_eligible = Column(Boolean, default=False)
    shift_start = Column(String, nullable=True)  # HH:MM format
    shift_end = Column(String, nullable=True)
    current_caseload = Column(Integer, default=0)
    fatigue_score = Column(Float, default=0.0)  # 0.0 to 1.0

    home_site = relationship("Site", back_populates="staff")
    radiologist_appointments = relationship("Appointment", foreign_keys="Appointment.radiologist_id", back_populates="radiologist")
    technologist_appointments = relationship("Appointment", foreign_keys="Appointment.technologist_id", back_populates="technologist")


class Patient(Base):
    """Synthetic patient (never real data per spec)."""
    __tablename__ = "patient"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    synthetic_name = Column(String, nullable=False)  # e.g., "PT-1042"
    urgency_level = Column(String, nullable=False)  # 'routine', 'urgent', 'stat'
    modality_needed = Column(String, nullable=False)  # CT, MRI, XRAY, US
    suspected_subspecialty = Column(String, nullable=True)  # e.g., 'neuro'
    home_latitude = Column(Float, nullable=True)
    home_longitude = Column(Float, nullable=True)
    max_travel_km = Column(Float, default=25.0)
    created_at = Column(DateTime, default=datetime.utcnow)

    appointments = relationship("Appointment", back_populates="patient")


class Appointment(Base):
    """Scheduled scan + radiologist + technologist."""
    __tablename__ = "appointment"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    patient_id = Column(String(36), ForeignKey("patient.id"), nullable=False)
    scanner_id = Column(String(36), ForeignKey("scanner.id"), nullable=False)
    technologist_id = Column(String(36), ForeignKey("staff.id"), nullable=True)
    radiologist_id = Column(String(36), ForeignKey("staff.id"), nullable=True)
    scheduled_start = Column(DateTime, nullable=False)
    scheduled_end = Column(DateTime, nullable=False)
    read_status = Column(String, default="pending")  # pending, in_progress, completed
    status = Column(String, default="booked")  # booked, completed, no_show, cancelled, moved

    patient = relationship("Patient", back_populates="appointments")
    scanner = relationship("Scanner", back_populates="appointments")
    technologist = relationship("Staff", foreign_keys=[technologist_id], back_populates="technologist_appointments")
    radiologist = relationship("Staff", foreign_keys=[radiologist_id], back_populates="radiologist_appointments")
    rebalance_moves = relationship("RebalanceMove", back_populates="appointment")


class RebalanceProposal(Base):
    """Proposed rebalancing move(s)."""
    __tablename__ = "rebalance_proposal"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    created_at = Column(DateTime, default=datetime.utcnow)
    proposal_type = Column(String, nullable=False)  # 'scanner_move', 'remote_read', 'remote_scan_assist'
    rationale = Column(Text, nullable=False)
    simulated_impact = Column(JSON, nullable=True)  # quantified before/after metrics
    status = Column(String, default="pending")  # pending, approved, rejected, edited, executed
    reviewed_by = Column(String, nullable=True)  # user ID or name
    reviewed_at = Column(DateTime, nullable=True)

    moves = relationship("RebalanceMove", back_populates="proposal")
    audit_entries = relationship("AuditLog", back_populates="proposal")


class RebalanceMove(Base):
    """Individual action within a proposal (may have per-move approval)."""
    __tablename__ = "rebalance_move"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    proposal_id = Column(String(36), ForeignKey("rebalance_proposal.id"), nullable=False)
    appointment_id = Column(String(36), ForeignKey("appointment.id"), nullable=True)
    move_kind = Column(String, nullable=False)  # 'reschedule', 'reassign_radiologist', 'reassign_technologist', 'remote_guidance_session'
    from_entity_id = Column(String(36), nullable=True)  # scanner_id or staff_id
    to_entity_id = Column(String(36), nullable=True)
    new_start = Column(DateTime, nullable=True)
    distance_delta_km = Column(Float, nullable=True)
    constraint_checks = Column(JSON, default=dict)  # {constraint_name: {passed: bool, reason: str}}
    status = Column(String, default="pending")  # pending, approved, rejected, executed

    proposal = relationship("RebalanceProposal", back_populates="moves")
    appointment = relationship("Appointment", back_populates="rebalance_moves")


class AuditLog(Base):
    """Immutable audit trail."""
    __tablename__ = "audit_log"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    timestamp = Column(DateTime, default=datetime.utcnow)
    actor = Column(String, nullable=False)  # user ID or 'agent'
    action = Column(String, nullable=False)  # e.g., 'approve_proposal', 'execute_move'
    target_table = Column(String, nullable=True)  # e.g., 'rebalance_proposal'
    target_id = Column(String(36), nullable=True)
    detail = Column(JSON, nullable=True)
    proposal_id = Column(String(36), ForeignKey("rebalance_proposal.id"), nullable=True)
    tamper_hash = Column(String(64), nullable=True)  # SHA-256 cryptographic digest

    proposal = relationship("RebalanceProposal", back_populates="audit_entries")


# ===== DB Initialization =====

def init_db():
    """Create all tables."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """Dependency injection for FastAPI."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
