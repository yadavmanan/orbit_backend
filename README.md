# ORBIT Backend

FastAPI + SQLAlchemy + SQLite backend for ORBIT, a radiology load-balancing system with WebMCP agent support.

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Backend runs on `http://localhost:8000`

## API endpoints

- **Health check**: `GET /api/health`
- **Dashboard**: `GET /api/dashboard`
- **WebMCP tools**: `POST /api/tools/*` (rebalance proposals, approvals, simulations, etc.)
- **API docs**: `http://localhost:8000/docs`

## Database

SQLite (`orbit.db`) auto-creates on startup with sample data. To reset:

```bash
rm orbit.db
uvicorn app.main:app --reload
```

## Environment variables

Copy `.env.example` to `.env`:

```
ALLOWED_ORIGINS=["http://localhost:5173", "https://your-frontend-host.com"]
```

## See also

- [Frontend](../frontend) — React + Vite WebMCP client
- [Demo script](../md/demo.md) — 3-minute video walkthrough
- [Agent testing guide](../md/running-with-agent.md) — Chrome WebMCP + ChatGPT integration
