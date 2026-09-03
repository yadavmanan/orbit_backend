"""
Synthetic data generator for ORBIT demo.
Generates a 5-site network with engineered scarcity and imbalance per spec.
"""

from datetime import datetime, timedelta
from random import Random

from app.database import SessionLocal, Site, Scanner, Staff, Patient, Appointment, RebalanceProposal, RebalanceMove, AuditLog, init_db

# Seed for reproducibility
SEED = 42
random = Random(SEED)


def generate_all(reset=True):
    """Generate complete dataset with 5 sites and realistic initial state."""
    if reset:
        from app.database import Base, engine
        Base.metadata.drop_all(bind=engine)
        init_db()
    else:
        init_db()
    
    db = SessionLocal()
    
    try:
        print("Generating sites...")
        sites_data = [
            {"name": "North Campus Academic", "jurisdiction": "CA", "latitude": 37.773, "longitude": -122.485},
            {"name": "Metro Central Hospital", "jurisdiction": "CA", "latitude": 37.754, "longitude": -122.465},
            {"name": "West Valley Imaging", "jurisdiction": "CA", "latitude": 37.735, "longitude": -122.510},
            {"name": "South Bay Outpatient", "jurisdiction": "CA", "latitude": 37.710, "longitude": -122.420},
            {"name": "East River Trauma", "jurisdiction": "CA", "latitude": 37.790, "longitude": -122.445},
        ]
        sites = []
        for data in sites_data:
            site = Site(**data)
            db.add(site)
            sites.append(site)
        db.flush()
        
        print("Generating scanners...")
        scanner_config = [
            # North Campus Academic: 4 MRI (overloaded)
            {"site_name": "North Campus Academic", "modality": "MRI", "count": 4, "remote_guidance": True},
            {"site_name": "North Campus Academic", "modality": "CT", "count": 1, "remote_guidance": False},
            # Metro Central Hospital: balanced
            {"site_name": "Metro Central Hospital", "modality": "MRI", "count": 2, "remote_guidance": True},
            {"site_name": "Metro Central Hospital", "modality": "CT", "count": 2, "remote_guidance": False},
            # West Valley Imaging: open capacity
            {"site_name": "West Valley Imaging", "modality": "MRI", "count": 2, "remote_guidance": True},
            {"site_name": "West Valley Imaging", "modality": "CT", "count": 2, "remote_guidance": True},
            {"site_name": "West Valley Imaging", "modality": "XRAY", "count": 2, "remote_guidance": False},
            # South Bay Outpatient: technologist constrained
            {"site_name": "South Bay Outpatient", "modality": "US", "count": 2, "remote_guidance": True},
            {"site_name": "South Bay Outpatient", "modality": "MRI", "count": 1, "remote_guidance": True},
            # East River Trauma: Level 1 Trauma Center (heavy CT/XRAY STAT load)
            {"site_name": "East River Trauma", "modality": "CT", "count": 3, "remote_guidance": False},
            {"site_name": "East River Trauma", "modality": "XRAY", "count": 2, "remote_guidance": False},
            {"site_name": "East River Trauma", "modality": "MRI", "count": 2, "remote_guidance": True},
        ]
        
        scanners = []
        for config in scanner_config:
            site = next(s for s in sites if s.name == config["site_name"])
            for i in range(config["count"]):
                scanner = Scanner(
                    site_id=site.id,
                    modality=config["modality"],
                    model=f"{config['modality']}-{i+1}",
                    remote_guidance_enabled=config["remote_guidance"],
                    status="operational",
                )
                db.add(scanner)
                scanners.append(scanner)
        db.flush()
        
        print("Generating staff...")
        staff_config = [
            # North Campus Academic
            {"site_name": "North Campus Academic", "role": "radiologist", "name": "Dr. Sarah Chen", "subspecialty": "general", "remote_eligible": False, "modalities": ["MRI", "CT"]},
            {"site_name": "North Campus Academic", "role": "technologist", "name": "Chris Thompson", "subspecialty": None, "remote_eligible": False, "modalities": ["MRI", "CT"]},
            {"site_name": "North Campus Academic", "role": "technologist", "name": "Patricia White", "subspecialty": None, "remote_eligible": True, "modalities": ["MRI"]},
            # Metro Central Hospital
            {"site_name": "Metro Central Hospital", "role": "radiologist", "name": "Dr. Robert Taylor", "subspecialty": "neuro", "remote_eligible": True, "modalities": ["MRI"]},
            {"site_name": "Metro Central Hospital", "role": "radiologist", "name": "Dr. Jennifer Wu", "subspecialty": "body", "remote_eligible": True, "modalities": ["CT", "US"]},
            {"site_name": "Metro Central Hospital", "role": "technologist", "name": "David Lee", "subspecialty": None, "remote_eligible": True, "modalities": ["MRI", "CT"]},
            # West Valley Imaging
            {"site_name": "West Valley Imaging", "role": "radiologist", "name": "Dr. Aris Thorne", "subspecialty": "MSK", "remote_eligible": True, "modalities": ["MRI", "CT"]},
            {"site_name": "West Valley Imaging", "role": "technologist", "name": "Mike Johnson", "subspecialty": None, "remote_eligible": True, "modalities": ["CT", "XRAY"]},
            # South Bay Outpatient
            {"site_name": "South Bay Outpatient", "role": "technologist", "name": "Sarah Lin", "subspecialty": None, "remote_eligible": True, "modalities": ["US", "MRI"]},
            # East River Trauma
            {"site_name": "East River Trauma", "role": "radiologist", "name": "Dr. Dimitri Volkov", "subspecialty": "neuro", "remote_eligible": True, "modalities": ["CT", "MRI"]},
            {"site_name": "East River Trauma", "role": "radiologist", "name": "Dr. Lisa Kumar", "subspecialty": "neuro", "remote_eligible": True, "modalities": ["MRI", "CT"]},
            {"site_name": "East River Trauma", "role": "technologist", "name": "Angela Martinez", "subspecialty": None, "remote_eligible": True, "modalities": ["CT", "XRAY"]},
        ]
        
        staff = []
        for config in staff_config:
            site = next(s for s in sites if s.name == config["site_name"])
            person = Staff(
                name=config["name"],
                role=config["role"],
                subspecialty=config["subspecialty"],
                home_site_id=site.id,
                licensed_jurisdictions=["CA"],
                qualified_modalities=config["modalities"],
                remote_eligible=config["remote_eligible"],
                shift_start="08:00",
                shift_end="17:00",
                current_caseload=random.randint(2, 10),
                fatigue_score=round(random.uniform(0.1, 0.65), 2),
            )
            db.add(person)
            staff.append(person)
        db.flush()
        
        print("Generating patients...")
        urgency_distribution = [("routine", 60), ("urgent", 30), ("stat", 10)]
        modalities = ["CT", "MRI", "XRAY", "US"]
        subspecialties = ["general", "neuro", "body", "MSK"]
        
        patients = []
        for i in range(35):
            urgency = random.choices(
                [u[0] for u in urgency_distribution],
                weights=[u[1] for u in urgency_distribution],
            )[0]
            patient = Patient(
                synthetic_name=f"PT-{1000 + i}",
                urgency_level=urgency,
                modality_needed=random.choice(modalities),
                suspected_subspecialty=random.choice(subspecialties),
                home_latitude=37.7 + random.uniform(-0.1, 0.1),
                home_longitude=-122.5 + random.uniform(-0.1, 0.1),
                max_travel_km=25.0,
            )
            db.add(patient)
            patients.append(patient)
        db.flush()
        
        print("Generating appointments...")
        base_date = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
        
        # Heavy load at North Campus Academic (MRI backlog)
        nc_scanners = [s for s in scanners if s.site.name == "North Campus Academic" and s.modality == "MRI"]
        nc_techs = [s for s in staff if s.home_site.name == "North Campus Academic" and s.role == "technologist"]
        sarah = next(s for s in staff if s.name == "Dr. Sarah Chen")
        
        for i in range(16):
            patient = patients[i]
            scanner = nc_scanners[i % len(nc_scanners)]
            tech = nc_techs[i % len(nc_techs)]
            start = base_date + timedelta(hours=i * 2 - 4)
            appt = Appointment(
                patient_id=patient.id,
                scanner_id=scanner.id,
                technologist_id=tech.id,
                radiologist_id=sarah.id,
                scheduled_start=start,
                scheduled_end=start + timedelta(minutes=45),
                read_status="pending" if i % 2 == 0 else "in_progress",
                status="booked",
            )
            db.add(appt)
        
        # East River Trauma (Trauma & STAT)
        er_scanners = [s for s in scanners if s.site.name == "East River Trauma"]
        er_techs = [s for s in staff if s.home_site.name == "East River Trauma" and s.role == "technologist"]
        volkov = next(s for s in staff if s.name == "Dr. Dimitri Volkov")
        
        for i in range(10):
            patient = patients[16 + i]
            scanner = er_scanners[i % len(er_scanners)]
            tech = er_techs[0]
            start = base_date + timedelta(hours=i - 2)
            appt = Appointment(
                patient_id=patient.id,
                scanner_id=scanner.id,
                technologist_id=tech.id,
                radiologist_id=volkov.id,
                scheduled_start=start,
                scheduled_end=start + timedelta(minutes=30),
                read_status="pending",
                status="booked",
            )
            db.add(appt)
            
        # Metro Central & West Valley (open capacity)
        mc_scanners = [s for s in scanners if s.site.name == "Metro Central Hospital"]
        wv_scanners = [s for s in scanners if s.site.name == "West Valley Imaging"]
        
        for i in range(8):
            patient = patients[26 + i]
            scanner = (mc_scanners + wv_scanners)[i % len(mc_scanners + wv_scanners)]
            start = base_date + timedelta(hours=i + 1)
            appt = Appointment(
                patient_id=patient.id,
                scanner_id=scanner.id,
                scheduled_start=start,
                scheduled_end=start + timedelta(minutes=45),
                read_status="completed" if i < 2 else "pending",
                status="booked",
            )
            db.add(appt)
        
        db.commit()
        print(f"Generated {len(sites)} sites, {len([s for s in db.query(Scanner).all()])} scanners, {len([s for s in db.query(Staff).all()])} staff, {len(patients)} patients, {len([a for a in db.query(Appointment).all()])} appointments")
    
    finally:
        db.close()


if __name__ == "__main__":
    generate_all()
