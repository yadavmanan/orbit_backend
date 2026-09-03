from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os

from app.api.routes import router
from app.config import settings
from app.database import init_db, SessionLocal
from app.data_generator import generate_all


def create_app() -> FastAPI:
    application = FastAPI(title=settings.app_name)

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["*"],
    )
    application.include_router(router, prefix=settings.api_prefix)
    
    @application.on_event("startup")
    def startup():
        """Initialize database and generate sample data on startup."""
        init_db()
        
        # Check if database is empty
        db = SessionLocal()
        from app.database import RebalanceProposal, Site
        site_count = db.query(Site).count()
        proposal_count = db.query(RebalanceProposal).count()
        db.close()
        
        if site_count == 0:
            print("Database is empty. Generating sample data...")
            try:
                generate_all(reset=False)
                db = SessionLocal()
                from app.rebalancing import propose_scanner_rebalance, propose_remote_read_assignment
                propose_scanner_rebalance(db, actor="agent")
                propose_remote_read_assignment(db, actor="agent")
                db.close()
                print("Sample data and initial proposals generated successfully.")
            except Exception as e:
                print(f"Error generating sample data: {e}")
        elif proposal_count == 0:
            print("No staged proposals found. Creating demo approval workflow...")
            try:
                db = SessionLocal()
                from app.rebalancing import propose_scanner_rebalance, propose_remote_read_assignment
                propose_scanner_rebalance(db, actor="agent")
                propose_remote_read_assignment(db, actor="agent")
                db.close()
                print("Demo proposals generated successfully.")
            except Exception as e:
                print(f"Error generating demo proposal: {e}")
    
    return application


app = create_app()