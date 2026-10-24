
import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI(
    title="AICTE Approval & Compliance System",
    description="Approval workflows, audit history, and compliance monitoring.",
    version="0.4.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_PATH = Path(__file__).resolve().parent / "aicte.db"

WORKFLOW_ROUTES = {
    "STANDARD": [
        "Application Review", "Document Verification",
        "Final Approval", "Completed",
    ],
    "SCRUTINY_ONLY": ["Scrutiny", "Completed"],
    "SCRUTINY_EVC": ["Scrutiny", "EVC", "Completed"],
    "EVC_SAC_SHC": ["EVC", "SAC/SHC", "Completed"],
    "SCRUTINY_EVC_SAC_SHC": [
        "Scrutiny", "EVC", "SAC/SHC", "Completed",
    ],
}

INITIAL_APPLICATIONS = [
    {
        "id": "AICTE-2026-1001",
        "institution": "Western Institute of Technology",
        "region": "West",
        "stage": "Document Verification",
        "workflow_route": "STANDARD",
        "officer": "A. Sharma",
        "date": "2026-10-20",
        "status": "Under Review",
        "reason": None,
        "history": [],
    },
    {
        "id": "AICTE-2026-1002",
        "institution": "National College of Engineering",
        "region": "South",
        "stage": "Final Approval",
        "workflow_route": "STANDARD",
        "officer": "R. Kumar",
        "date": "2026-10-18",
        "status": "Pending",
        "reason": None,
        "history": [],
    },
    {
        "id": "AICTE-2026-1003",
        "institution": "Central Technical Institute",
        "region": "North",
        "stage": "Application Review",
        "workflow_route": "STANDARD",
        "officer": "S. Patel",
        "date": "2026-10-17",
        "status": "Returned",
        "reason": "Please upload the signed lease document.",
        "history": [],
    },
    {
        "id": "AICTE-2026-1004",
        "institution": "Eastern School of Technology",
        "region": "East",
        "stage": "Completed",
        "workflow_route": "STANDARD",
        "officer": "A. Sharma",
        "date": "2026-10-15",
        "status": "Approved",
        "reason": None,
        "history": [],
    },
    {
        "id": "AICTE-2026-1005",
        "institution": "City Institute of Computing",
        "region": "West",
        "stage": "Application Review",
        "workflow_route": "STANDARD",
        "officer": "R. Kumar",
        "date": "2026-10-22",
        "status": "Pending",
        "reason": None,
        "history": [],
    },
    {
        "id": "AICTE-2026-1006",
        "institution": "Greenfield Polytechnic",
        "region": "South",
        "stage": "Document Verification",
        "workflow_route": "STANDARD",
        "officer": "S. Patel",
        "date": "2026-10-23",
        "status": "Under Review",
        "reason": None,
        "history": [],
    },
]


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect_db():
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database():
    with connect_db() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS applications (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id TEXT NOT NULL,
                action TEXT NOT NULL,
                stage TEXT,
                details TEXT,
                timestamp TEXT NOT NULL
            )
        """)

        count = connection.execute(
            "SELECT COUNT(*) FROM applications"
        ).fetchone()[0]

        if count == 0:
            connection.executemany(
                "INSERT INTO applications (id, data) VALUES (?, ?)",
                [(a["id"], json.dumps(a)) for a in INITIAL_APPLICATIONS],
            )


def get_all_applications():
    with connect_db() as connection:
        rows = connection.execute(
            "SELECT data FROM applications ORDER BY id"
        ).fetchall()
    return [json.loads(row["data"]) for row in rows]


def find_application(application_id):
    with connect_db() as connection:
        row = connection.execute(
            "SELECT data FROM applications WHERE id = ?",
            (application_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Application not found")

    application = json.loads(row["data"])
    application.setdefault("history", [])
    return application


def save_application(application):
    with connect_db() as connection:
        connection.execute("""
            INSERT INTO applications (id, data) VALUES (?, ?)
            ON CONFLICT(id) DO UPDATE SET data = excluded.data
        """, (application["id"], json.dumps(application)))


def make_history_event(action, stage, details=None):
    return {
        "action": action,
        "stage": stage,
        "details": details,
        "timestamp": now_iso(),
    }


def record_event(application, action, stage, details=None):
    """Persist the event in both the application and the audit table."""
    event = make_history_event(action, stage, details)
    application.setdefault("history", []).append(event)

    with connect_db() as connection:
        connection.execute("""
            INSERT INTO audit_events
                (application_id, action, stage, details, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (
            application["id"],
            event["action"],
            event["stage"],
            event["details"],
            event["timestamp"],
        ))
        connection.execute("""
            INSERT INTO applications (id, data) VALUES (?, ?)
            ON CONFLICT(id) DO UPDATE SET data = excluded.data
        """, (application["id"], json.dumps(application)))


def get_application_route(application):
    route_id = application.get("workflow_route", "STANDARD")
    stages = WORKFLOW_ROUTES.get(route_id)

    if stages is None:
        raise HTTPException(status_code=400, detail="Unknown workflow route")

    if application.get("stage") not in stages:
        raise HTTPException(
            status_code=400,
            detail=f"Stage '{application.get('stage')}' is not in route '{route_id}'",
        )

    return stages


def is_completed(application):
    return (
        application.get("stage") == "Completed"
        or application.get("status") == "Approved"
    )


def parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


@app.on_event("startup")
def startup_event():
    initialize_database()


@app.get("/")
def home():
    return {
        "message": "AICTE Approval & Compliance System API is running",
        "status": "success",
    }


@app.get("/health")
def health_check():
    try:
        with connect_db() as connection:
            connection.execute("SELECT 1")
        return {"status": "healthy", "database": "connected"}
    except sqlite3.Error:
        raise HTTPException(status_code=503, detail="Database unavailable")


@app.get("/api/applications")
def get_applications():
    return get_all_applications()


@app.get("/api/workflows")
def get_workflows():
    return {"workflows": WORKFLOW_ROUTES}


@app.get("/api/dashboard")
def dashboard():
    applications = get_all_applications()
    by_status = {}
    by_stage = {}

    for application in applications:
        status = application.get("status", "Unknown")
        stage = application.get("stage", "Unknown")
        by_status[status] = by_status.get(status, 0) + 1
        by_stage[stage] = by_stage.get(stage, 0) + 1

    today = date.today()
    overdue = 0
    expiring = 0

    for application in applications:
        due = parse_date(application.get("due_date"))
        expiry = parse_date(application.get("document_expiry_date"))

        if due and due < today and not is_completed(application):
            overdue += 1
        if expiry and expiry < today:
            expiring += 1

    with connect_db() as connection:
        audit_count = connection.execute(
            "SELECT COUNT(*) FROM audit_events"
        ).fetchone()[0]

    return {
        "total_applications": len(applications),
        "pending_requests": sum(
            1 for a in applications
            if a.get("status") in ("Pending", "Under Review", "Returned")
        ),
        "active_blocks": 0,
        "emergency_jobs": 0,
        "approved": sum(1 for a in applications if is_completed(a)),
        "overdue_applications": overdue,
        "expired_documents": expiring,
        "audit_events": audit_count,
        "by_status": by_status,
        "by_stage": by_stage,
    }


@app.get("/api/applications/{application_id}/history")
def application_history(application_id: str):
    find_application(application_id)

    with connect_db() as connection:
        rows = connection.execute("""
            SELECT id, action, stage, details, timestamp
            FROM audit_events
            WHERE application_id = ?
            ORDER BY id ASC
        """, (application_id,)).fetchall()

    events = [dict(row) for row in rows]

    # Include older history saved before the audit table existed.
    application = find_application(application_id)
    if not events:
        events = application.get("history", [])

    return {"application_id": application_id, "history": events}


@app.get("/api/audit")
def audit_trail(limit: int = 100):
    limit = max(1, min(limit, 500))

    with connect_db() as connection:
        rows = connection.execute("""
            SELECT id, application_id, action, stage, details, timestamp
            FROM audit_events
            ORDER BY id DESC
            LIMIT ?
        """, (limit,)).fetchall()

    return {"events": [dict(row) for row in rows]}


@app.get("/api/monitoring")
def compliance_monitoring():
    """Report configured due dates and document expiry dates."""
    today = date.today()
    results = []

    for application in get_all_applications():
        due_value = application.get("due_date")
        expiry_value = application.get("document_expiry_date")
        due = parse_date(due_value)
        expiry = parse_date(expiry_value)
        alerts = []

        if due_value and not due:
            alerts.append("Invalid due_date format; use YYYY-MM-DD")
        elif due and due < today and not is_completed(application):
            alerts.append("Application deadline overdue")
        elif due and (due - today).days <= 7 and not is_completed(application):
            alerts.append("Application deadline within 7 days")

        if expiry_value and not expiry:
            alerts.append("Invalid document_expiry_date format; use YYYY-MM-DD")
        elif expiry and expiry < today:
            alerts.append("Document expired")
        elif expiry and (expiry - today).days <= 30:
            alerts.append("Document expires within 30 days")

        results.append({
            "id": application["id"],
            "institution": application.get("institution"),
            "stage": application.get("stage"),
            "status": application.get("status"),
            "due_date": due_value,
            "document_expiry_date": expiry_value,
            "alerts": alerts,
            "compliance_configured": bool(due_value or expiry_value),
        })

    return {
        "checked_on": today.isoformat(),
        "total_applications": len(results),
        "applications": results,
    }


@app.post("/api/applications/{application_id}/workflow")
def assign_workflow(application_id: str, payload: dict = Body(...)):
    application = find_application(application_id)
    route_id = payload.get("workflow_route")

    if route_id not in WORKFLOW_ROUTES:
        raise HTTPException(status_code=400, detail="Invalid workflow_route")

    if is_completed(application):
        raise HTTPException(
            status_code=400,
            detail="Cannot change workflow of a completed application",
        )

    new_stages = WORKFLOW_ROUTES[route_id]
    if application.get("stage") not in new_stages:
        raise HTTPException(
            status_code=400,
            detail="Current stage is not part of the selected route",
        )

    old_route = application.get("workflow_route", "STANDARD")
    if old_route != route_id:
        application["workflow_route"] = route_id
        record_event(
            application, "Workflow changed", application["stage"],
            f"{old_route} -> {route_id}",
        )

    return {
        "message": "Workflow route assigned successfully",
        "application": find_application(application_id),
    }


@app.post("/api/applications/{application_id}/advance")
def advance_application(application_id: str):
    application = find_application(application_id)
    stages = get_application_route(application)
    current_stage = application["stage"]

    if is_completed(application):
        raise HTTPException(status_code=400, detail="Application is already completed")

    next_stage = stages[stages.index(current_stage) + 1]
    was_returned = application.get("status") == "Returned"
    previous_reason = application.get("reason")

    application["stage"] = next_stage
    application["reason"] = None
    application["status"] = (
        "Approved" if next_stage == "Completed" else "Under Review"
    )

    if was_returned:
        record_event(application, "Return resolved", current_stage, previous_reason)

    record_event(
        application, "Stage advanced", next_stage,
        f"Previous stage: {current_stage}",
    )

    return {
        "message": "Application stage advanced successfully",
        "application": find_application(application_id),
    }


@app.post("/api/applications/{application_id}/return")
def return_application(application_id: str, payload: dict = Body(...)):
    application = find_application(application_id)
    reason = payload.get("reason", "")

    if not isinstance(reason, str) or not reason.strip():
        raise HTTPException(
            status_code=400,
            detail="A reason is required to return an application",
        )

    if is_completed(application):
        raise HTTPException(
            status_code=400,
            detail="An approved application cannot be returned",
        )

    if application.get("status") == "Returned":
        raise HTTPException(
            status_code=400,
            detail="Application has already been returned",
        )

    reason = reason.strip()
    application["status"] = "Returned"
    application["reason"] = reason

    record_event(application, "Application returned", application["stage"], reason)

    return {
        "message": "Application returned successfully",
        "application": find_application(application_id),
    }


@app.patch("/api/applications/{application_id}/compliance")
def update_compliance(application_id: str, payload: dict = Body(...)):
    """Set optional ISO-format deadline and document expiry dates."""
    application = find_application(application_id)
    allowed = {"due_date", "document_expiry_date"}
    if not payload or not set(payload.keys()).issubset(allowed):
        raise HTTPException(
            status_code=400,
            detail="Provide due_date and/or document_expiry_date only",
        )

    for field, value in payload.items():
        if value is not None and (
            not isinstance(value, str) or parse_date(value) is None
        ):
            raise HTTPException(
                status_code=400,
                detail=f"{field} must be a valid YYYY-MM-DD date or null",
            )

    for field, value in payload.items():
        if value is None:
            application.pop(field, None)
        else:
            application[field] = value

    record_event(
        application, "Compliance dates updated", application["stage"],
        json.dumps(payload),
    )

    return {
        "message": "Compliance dates updated",
        "application": find_application(application_id),
    }
