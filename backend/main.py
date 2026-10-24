
import hashlib
import hmac
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Lock

from fastapi import Body, Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI(
    title="AICTE Approval & Compliance System",
    description="Approval workflows, audit history, authentication and compliance monitoring.",
    version="0.5.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_PATH = Path(__file__).resolve().parent / "aicte.db"
ROUTING_RULES_PATH = Path(__file__).resolve().parent / "routing_rules.json"

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

# Demo-only accounts. Passwords are hashed using PBKDF2.
# Change these credentials before presenting publicly.
DEMO_USERS = {
    "admin": {
        "role": "Admin",
        "password_hash": hashlib.pbkdf2_hmac(
            "sha256", b"Admin@26262", b"aicte-admin-demo", 200_000
        ).hex(),
        "salt": b"aicte-admin-demo",
    },
    "officer": {
        "role": "Officer",
        "password_hash": hashlib.pbkdf2_hmac(
            "sha256", b"Officer@26262", b"aicte-officer-demo", 200_000
        ).hex(),
        "salt": b"aicte-officer-demo",
    },
    "viewer": {
        "role": "Viewer",
        "password_hash": hashlib.pbkdf2_hmac(
            "sha256", b"Viewer@26262", b"aicte-viewer-demo", 200_000
        ).hex(),
        "salt": b"aicte-viewer-demo",
    },
}

# Prototype sessions are kept in memory and expire when the server restarts.
SESSIONS = {}
SESSION_LOCK = Lock()

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


@contextmanager
def connect_db():
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


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
        # Safe schema migration for existing databases.
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(audit_events)"
            ).fetchall()
        }
        if "actor" not in columns:
            connection.execute(
                "ALTER TABLE audit_events ADD COLUMN actor TEXT"
            )

        count = connection.execute(
            "SELECT COUNT(*) FROM applications"
        ).fetchone()[0]
        if count == 0:
            connection.executemany(
                "INSERT INTO applications (id, data) VALUES (?, ?)",
                [(a["id"], json.dumps(a)) for a in INITIAL_APPLICATIONS],
            )


def get_current_user(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Login required")

    token = authorization.removeprefix("Bearer ").strip()
    with SESSION_LOCK:
        user = SESSIONS.get(token)

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired session. Please log in again.",
        )
    return user


def require_roles(*roles):
    def checker(user=Depends(get_current_user)):
        if user["role"] not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Permission denied. Allowed roles: {', '.join(roles)}",
            )
        return user
    return checker


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


def make_history_event(action, stage, details=None, actor="System"):
    return {
        "action": action,
        "stage": stage,
        "details": details,
        "timestamp": now_iso(),
        "actor": actor,
    }


def record_event(application, action, stage, details=None, actor="System"):
    event = make_history_event(action, stage, details, actor)
    application.setdefault("history", []).append(event)

    with connect_db() as connection:
        connection.execute("""
            INSERT INTO audit_events
                (application_id, action, stage, details, timestamp, actor)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            application["id"], event["action"], event["stage"],
            event["details"], event["timestamp"], actor,
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


def choose_workflow_route(application):
    try:
        with ROUTING_RULES_PATH.open(encoding="utf-8") as rules_file:
            config = json.load(rules_file)
    except (OSError, json.JSONDecodeError) as error:
        raise HTTPException(
            status_code=500,
            detail=f"Could not load workflow routing rules: {error}",
        ) from error

    if not isinstance(config, dict) or not isinstance(config.get("rules"), list):
        raise HTTPException(status_code=500, detail="Invalid workflow routing rules configuration")

    attributes = {
        key: application.get(key)
        for key in ("requires_scrutiny", "requires_evc", "requires_sac_shc")
        if key in application
    }
    for rule in config["rules"]:
        conditions = rule.get("when")
        route_id = rule.get("route")
        if (
            not isinstance(conditions, dict)
            or route_id not in WORKFLOW_ROUTES
            or any(not isinstance(value, bool) for value in conditions.values())
        ):
            raise HTTPException(
                status_code=500,
                detail=f"Invalid workflow routing rule: {rule.get('id', 'unknown')}",
            )
        if all(application.get(key) is value for key, value in conditions.items()):
            return {
                "route": route_id,
                "rule_id": rule.get("id", "unnamed"),
                "reason": rule.get("reason", "Configured rule matched."),
                "attributes": attributes,
            }

    default = config.get("default", {})
    route_id = default.get("route")
    if route_id not in WORKFLOW_ROUTES:
        raise HTTPException(status_code=500, detail="Invalid default workflow routing rule")
    return {
        "route": route_id,
        "rule_id": default.get("id", "default"),
        "reason": default.get("reason", "Default routing rule applied."),
        "attributes": attributes,
    }


def ensure_workflow_not_started(application):
    route_id = application.get("workflow_route")
    stages = WORKFLOW_ROUTES.get(route_id)
    if route_id and (
        not stages or application.get("stage") != stages[0]
    ):
        raise HTTPException(
            status_code=409,
            detail="The workflow route cannot be changed after the application has left its initial stage.",
        )
    history = application.get("history", [])
    non_creation_history = [
        event for event in history
        if event.get("action") != "Application created"
    ]
    if application.get("status") != "Pending" or non_creation_history:
        raise HTTPException(
            status_code=409,
            detail="The workflow route can only be assigned before processing begins.",
        )
    with connect_db() as connection:
        events = connection.execute(
            "SELECT action FROM audit_events WHERE application_id = ?",
            (application["id"],),
        ).fetchall()
    if any(event["action"] != "Application created" for event in events):
        raise HTTPException(
            status_code=409,
            detail="The workflow route cannot be changed after audit history exists.",
        )


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


@app.post("/api/auth/login")
def login(payload: dict = Body(...)):
    username = payload.get("username", "")
    password = payload.get("password", "")
    if not isinstance(username, str) or not isinstance(password, str):
        raise HTTPException(status_code=400, detail="Invalid login fields")

    account = DEMO_USERS.get(username.strip().lower())
    if not account:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), account["salt"], 200_000
    ).hex()
    if not hmac.compare_digest(candidate, account["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = secrets.token_urlsafe(32)
    user = {"username": username.strip().lower(), "role": account["role"]}
    with SESSION_LOCK:
        SESSIONS[token] = user

    return {"access_token": token, "token_type": "bearer", "user": user}


@app.get("/api/auth/me")
def auth_me(user=Depends(get_current_user)):
    return user


@app.post("/api/auth/logout")
def logout(
    authorization: str | None = Header(default=None),
    user=Depends(get_current_user),
):
    token = authorization.removeprefix("Bearer ").strip()
    with SESSION_LOCK:
        SESSIONS.pop(token, None)
    return {"message": "Logged out successfully"}


@app.get("/api/applications")
def get_applications(user=Depends(get_current_user)):
    return get_all_applications()


@app.post("/api/applications", status_code=201)
def create_application(
    payload: dict = Body(...),
    user=Depends(require_roles("Admin")),
):
    required_fields = {"id", "institution", "region", "officer", "date"}
    optional_fields = {
        "requires_scrutiny", "requires_evc", "requires_sac_shc",
    }
    supplied_fields = set(payload)
    if not required_fields.issubset(supplied_fields):
        missing = sorted(required_fields - supplied_fields)
        raise HTTPException(
            status_code=400,
            detail=f"Missing required application fields: {', '.join(missing)}",
        )
    unknown_fields = supplied_fields - required_fields - optional_fields
    if unknown_fields:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported application fields: {', '.join(sorted(unknown_fields))}",
        )

    for field in required_fields:
        value = payload[field]
        if not isinstance(value, str) or not value.strip():
            raise HTTPException(
                status_code=400,
                detail=f"{field} must be a non-empty string",
            )

    submission_date = parse_date(payload["date"])
    if submission_date is None or payload["date"] != submission_date.isoformat():
        raise HTTPException(
            status_code=400,
            detail="date must be a valid YYYY-MM-DD date",
        )

    for field in optional_fields:
        if field in payload and not isinstance(payload[field], bool):
            raise HTTPException(
                status_code=400,
                detail=f"{field} must be a boolean",
            )

    application_id = payload["id"].strip()
    application = {
        "id": application_id,
        "institution": payload["institution"].strip(),
        "region": payload["region"].strip(),
        "officer": payload["officer"].strip(),
        "date": payload["date"],
        "stage": "Application Review",
        "workflow_route": None,
        "status": "Pending",
        "reason": None,
        "history": [],
        **{
            field: payload[field]
            for field in optional_fields
            if field in payload
        },
    }
    event = make_history_event(
        "Application created",
        application["stage"],
        "Application created; workflow route not yet assigned.",
        user["username"],
    )
    application["history"].append(event)

    try:
        with connect_db() as connection:
            connection.execute(
                "INSERT INTO applications (id, data) VALUES (?, ?)",
                (application_id, json.dumps(application)),
            )
            connection.execute("""
                INSERT INTO audit_events
                    (application_id, action, stage, details, timestamp, actor)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                application_id, event["action"], event["stage"],
                event["details"], event["timestamp"], event["actor"],
            ))
    except sqlite3.IntegrityError as error:
        raise HTTPException(
            status_code=409,
            detail="An application with this ID already exists",
        ) from error

    return {
        "message": "Application created successfully",
        "application": application,
    }


@app.get("/api/workflows")
def get_workflows(user=Depends(get_current_user)):
    return {"workflows": WORKFLOW_ROUTES}


@app.get("/api/dashboard")
def dashboard(user=Depends(get_current_user)):
    applications = get_all_applications()
    by_status = {}
    by_stage = {}
    today = date.today()
    overdue = 0
    expired = 0
    expiring_soon = 0
    deadlines_soon = 0

    for application in applications:
        status = application.get("status", "Unknown")
        stage = application.get("stage", "Unknown")
        by_status[status] = by_status.get(status, 0) + 1
        by_stage[stage] = by_stage.get(stage, 0) + 1

        due = parse_date(application.get("due_date"))
        expiry = parse_date(application.get("document_expiry_date"))

        if due and due < today and not is_completed(application):
            overdue += 1
        elif due and 0 <= (due - today).days <= 7 and not is_completed(application):
            deadlines_soon += 1

        if expiry and expiry < today:
            expired += 1
        elif expiry and 0 <= (expiry - today).days <= 30:
            expiring_soon += 1

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
        "expired_documents": expired,
        "expiring_soon_documents": expiring_soon,
        "deadlines_soon": deadlines_soon,
        "audit_events": audit_count,
        "by_status": by_status,
        "by_stage": by_stage,
    }


@app.get("/api/applications/{application_id}/history")
def application_history(
    application_id: str,
    user=Depends(get_current_user),
):
    application = find_application(application_id)
    with connect_db() as connection:
        rows = connection.execute("""
            SELECT id, action, stage, details, timestamp, actor
            FROM audit_events
            WHERE application_id = ?
            ORDER BY id ASC
        """, (application_id,)).fetchall()

    events = [dict(row) for row in rows]
    if not events:
        events = application.get("history", [])
    return {"application_id": application_id, "history": events}


@app.get("/api/audit")
def audit_trail(limit: int = 100, user=Depends(get_current_user)):
    limit = max(1, min(limit, 500))
    with connect_db() as connection:
        rows = connection.execute("""
            SELECT id, application_id, action, stage, details, timestamp, actor
            FROM audit_events
            ORDER BY id DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return {"events": [dict(row) for row in rows]}


@app.get("/api/monitoring")
def compliance_monitoring(user=Depends(get_current_user)):
    today = date.today()
    results = []

    for application in get_all_applications():
        due_value = application.get("due_date")
        expiry_value = application.get("document_expiry_date")
        due = parse_date(due_value)
        expiry = parse_date(expiry_value)
        alerts = []

        if due_value and not due:
            alerts.append({
                "severity": "Warning",
                "type": "Invalid date",
                "message": "Invalid due_date format; use YYYY-MM-DD",
            })
        elif due and due < today and not is_completed(application):
            alerts.append({
                "severity": "Critical",
                "type": "Overdue",
                "message": "Application deadline overdue",
            })
        elif due and 0 <= (due - today).days <= 7 and not is_completed(application):
            alerts.append({
                "severity": "Warning",
                "type": "Deadline approaching",
                "message": "Application deadline within 7 days",
            })

        if expiry_value and not expiry:
            alerts.append({
                "severity": "Warning",
                "type": "Invalid date",
                "message": "Invalid document_expiry_date format; use YYYY-MM-DD",
            })
        elif expiry and expiry < today:
            alerts.append({
                "severity": "Critical",
                "type": "Document expired",
                "message": "Document expired",
            })
        elif expiry and 0 <= (expiry - today).days <= 30:
            alerts.append({
                "severity": "Warning",
                "type": "Document expiring",
                "message": "Document expires within 30 days",
            })

        if not due_value and not expiry_value:
            alerts.append({
                "severity": "Info",
                "type": "Configuration",
                "message": "No compliance dates configured",
            })

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
def assign_workflow(
    application_id: str,
    payload: dict = Body(...),
    user=Depends(require_roles("Admin")),
):
    application = find_application(application_id)
    route_id = payload.get("workflow_route")

    if route_id not in WORKFLOW_ROUTES:
        raise HTTPException(status_code=400, detail="Invalid workflow_route")
    ensure_workflow_not_started(application)

    old_route = application.get("workflow_route", "UNASSIGNED")
    if old_route != route_id:
        application["workflow_route"] = route_id
        application["stage"] = WORKFLOW_ROUTES[route_id][0]
        record_event(
            application, "Workflow changed", application["stage"],
            f"{old_route} -> {route_id}; route manually assigned before processing",
            user["username"],
        )

    return {
        "message": "Workflow route assigned successfully",
        "application": find_application(application_id),
    }


@app.post("/api/applications/{application_id}/auto-route")
def auto_route_application(
    application_id: str,
    user=Depends(require_roles("Admin")),
):
    application = find_application(application_id)
    ensure_workflow_not_started(application)
    decision = choose_workflow_route(application)
    application["workflow_route"] = decision["route"]
    application["stage"] = WORKFLOW_ROUTES[decision["route"]][0]
    application["routing_decision"] = {
        **decision,
        "timestamp": now_iso(),
        "actor": user["username"],
    }
    record_event(
        application,
        "Workflow automatically assigned",
        application["stage"],
        json.dumps(decision, sort_keys=True),
        user["username"],
    )
    return {
        "message": "Workflow automatically assigned",
        "application": find_application(application_id),
        "routing_decision": application["routing_decision"],
    }


@app.post("/api/applications/{application_id}/advance")
def advance_application(
    application_id: str,
    user=Depends(require_roles("Admin", "Officer")),
):
    application = find_application(application_id)
    stages = get_application_route(application)
    current_stage = application["stage"]

    if is_completed(application):
        raise HTTPException(status_code=400, detail="Application is already completed")
    if application.get("status") == "Returned":
        raise HTTPException(
            status_code=409,
            detail="Resubmit the application for corrections before advancing its workflow.",
        )

    next_stage = stages[stages.index(current_stage) + 1]

    application["stage"] = next_stage
    application["reason"] = None
    application["status"] = "Approved" if next_stage == "Completed" else "Under Review"

    record_event(
        application, "Stage advanced", next_stage,
        f"Previous stage: {current_stage}", user["username"],
    )
    return {
        "message": "Application stage advanced successfully",
        "application": find_application(application_id),
    }


@app.post("/api/applications/{application_id}/resubmit")
def resubmit_application(
    application_id: str,
    user=Depends(require_roles("Admin", "Officer")),
):
    application = find_application(application_id)
    if application.get("status") != "Returned":
        raise HTTPException(
            status_code=409,
            detail="Only a returned application can be resubmitted.",
        )
    reason = application.get("reason")
    application["status"] = "Under Review"
    application["reason"] = None
    record_event(
        application,
        "Application resubmitted",
        application["stage"],
        f"Corrections resubmitted; prior return reason: {reason}",
        user["username"],
    )
    return {
        "message": "Application resubmitted; workflow may continue from the current stage.",
        "application": find_application(application_id),
    }


@app.post("/api/applications/{application_id}/return")
def return_application(
    application_id: str,
    payload: dict = Body(...),
    user=Depends(require_roles("Admin", "Officer")),
):
    application = find_application(application_id)
    reason = payload.get("reason", "")

    if not isinstance(reason, str) or not reason.strip():
        raise HTTPException(status_code=400, detail="A reason is required to return an application")
    if is_completed(application):
        raise HTTPException(status_code=400, detail="An approved application cannot be returned")
    if application.get("status") == "Returned":
        raise HTTPException(status_code=400, detail="Application has already been returned")

    reason = reason.strip()
    application["status"] = "Returned"
    application["reason"] = reason
    record_event(application, "Application returned", application["stage"], reason, user["username"])

    return {
        "message": "Application returned successfully",
        "application": find_application(application_id),
    }


@app.patch("/api/applications/{application_id}/compliance")
def update_compliance(
    application_id: str,
    payload: dict = Body(...),
    user=Depends(require_roles("Admin", "Officer")),
):
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
        json.dumps(payload), user["username"],
    )
    return {
        "message": "Compliance dates updated",
        "application": find_application(application_id),
    }
