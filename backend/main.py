
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit

from fastapi import Body, Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware


DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://aicte-approval-frontend.onrender.com",
)


def parse_cors_origins(value):
    origins = list(DEFAULT_CORS_ORIGINS)
    if not value:
        return origins

    for configured_origin in value.split(","):
        origin = configured_origin.strip().rstrip("/")
        if not origin:
            continue
        parsed = urlsplit(origin)
        try:
            parsed.port
        except ValueError as error:
            raise ValueError(f"Invalid CORS origin: {configured_origin.strip()}") from error
        if (
            "*" in origin
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(f"Invalid CORS origin: {configured_origin.strip()}")
        if origin not in origins:
            origins.append(origin)
    return origins


CORS_ORIGINS = parse_cors_origins(os.getenv("CORS_ORIGINS", ""))


app = FastAPI(
    title="AICTE Approval & Compliance System",
    description="Approval workflows, audit history, authentication and compliance monitoring.",
    version="0.5.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_PATH = Path(__file__).resolve().parent / "aicte.db"
ROUTING_RULES_PATH = Path(__file__).resolve().parent / "routing_rules.json"
COMPLIANCE_SETTINGS_PATH = Path(__file__).resolve().parent / "compliance_settings.json"

DOCUMENT_STATUSES = {"Pending", "Submitted", "Verified", "Rejected"}
NOTIFICATION_CATEGORIES = {
    "document_rejected",
    "document_expiring",
    "document_expired",
    "application_overdue",
}

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
        connection.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id TEXT NOT NULL,
                document_type TEXT NOT NULL,
                name TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN ('Pending', 'Submitted', 'Verified', 'Rejected')
                ),
                expiry_date TEXT,
                issue_date TEXT,
                submission_date TEXT,
                reviewer_remarks TEXT,
                renewal_status TEXT NOT NULL DEFAULT 'Not Required',
                renewal_issue_date TEXT,
                renewal_expiry_date TEXT,
                renewal_remarks TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_documents_application
            ON documents(application_id)
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id TEXT NOT NULL,
                category TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                is_read INTEGER NOT NULL DEFAULT 0,
                escalation_level INTEGER NOT NULL DEFAULT 0,
                dedupe_key TEXT NOT NULL UNIQUE,
                resolved_at TEXT
            )
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_notifications_created
            ON notifications(created_at DESC)
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS notification_views (
                notification_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                viewed_at TEXT NOT NULL,
                PRIMARY KEY (notification_id, username)
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

        document_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(documents)"
            ).fetchall()
        }
        for name, definition in (
            ("issue_date", "TEXT"),
            ("submission_date", "TEXT"),
            ("renewal_status", "TEXT NOT NULL DEFAULT 'Not Required'"),
            ("renewal_issue_date", "TEXT"),
            ("renewal_expiry_date", "TEXT"),
            ("renewal_remarks", "TEXT"),
        ):
            if name not in document_columns:
                connection.execute(
                    f"ALTER TABLE documents ADD COLUMN {name} {definition}"
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


def get_compliance_settings():
    try:
        with COMPLIANCE_SETTINGS_PATH.open(encoding="utf-8") as settings_file:
            settings = json.load(settings_file)
    except (OSError, json.JSONDecodeError) as error:
        raise HTTPException(
            status_code=500,
            detail=f"Could not load compliance settings: {error}",
        ) from error

    if not isinstance(settings, dict):
        raise HTTPException(status_code=500, detail="Invalid compliance settings configuration")

    warning_days = settings.get("expiry_warning_days")
    required_types = settings.get("required_document_types")
    escalation_days = settings.get("escalation_thresholds_days")
    if (
        not isinstance(warning_days, int)
        or isinstance(warning_days, bool)
        or warning_days < 0
        or not isinstance(required_types, list)
        or any(not isinstance(value, str) or not value.strip() for value in required_types)
        or not isinstance(escalation_days, list)
        or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in escalation_days
        )
        or escalation_days != sorted(set(escalation_days))
    ):
        raise HTTPException(status_code=500, detail="Invalid compliance settings configuration")
    return settings


def list_application_documents(application_id):
    with connect_db() as connection:
        rows = connection.execute(
            "SELECT * FROM documents WHERE application_id = ? ORDER BY id",
            (application_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_document_tracking_status(document, settings, today=None):
    today = today or date.today()
    renewal_status = document.get("renewal_status", "Not Required")
    if renewal_status == "Submitted":
        return "Submitted for Renewal"
    if document["status"] == "Submitted":
        return "Awaiting Verification"
    if document["status"] == "Rejected":
        return "Awaiting Renewal"
    if document["status"] != "Verified":
        return "Awaiting Verification"

    expiry = parse_date(document.get("expiry_date"))
    if expiry and expiry < today:
        return "Expired"
    if expiry and (expiry - today).days <= settings["expiry_warning_days"]:
        return "Expiring Soon"
    if renewal_status in {"Awaiting Renewal", "Rejected"}:
        return "Awaiting Renewal"
    return "Valid"


def get_document_verification_status(document):
    if (
        document.get("renewal_status") == "Submitted"
        or document["status"] in {"Pending", "Submitted"}
    ):
        return "Awaiting Verification"
    return document["status"]


def calculate_compliance_summary(documents, settings, today=None):
    today = today or date.today()
    present_types = {
        document["document_type"].strip().casefold()
        for document in documents
    }
    missing_types = sorted(
        document_type
        for document_type in settings["required_document_types"]
        if document_type.strip().casefold() not in present_types
    )

    if not documents or missing_types:
        state = "Missing"
    elif any(
        document.get("renewal_status") == "Submitted"
        or document["status"] == "Submitted"
        for document in documents
    ):
        state = "Pending Verification"
    elif any(document["status"] == "Rejected" for document in documents):
        state = "Rejected"
    elif any(
        (expiry := parse_date(document.get("expiry_date"))) and expiry < today
        for document in documents
    ):
        state = "Expired"
    elif any(document["status"] != "Verified" for document in documents):
        state = "Pending Verification"
    elif any(
        (expiry := parse_date(document.get("expiry_date")))
        and 0 <= (expiry - today).days <= settings["expiry_warning_days"]
        for document in documents
    ):
        state = "Expiring Soon"
    else:
        state = "Compliant"

    return {
        "state": state,
        "document_count": len(documents),
        "missing_document_types": missing_types,
        "expiry_warning_days": settings["expiry_warning_days"],
    }


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


def notification_event(application_id, category, message, dedupe_key, actor):
    created_at = now_iso()
    with connect_db() as connection:
        cursor = connection.execute("""
            INSERT OR IGNORE INTO notifications
                (application_id, category, message, created_at, dedupe_key)
            VALUES (?, ?, ?, ?, ?)
        """, (application_id, category, message, created_at, dedupe_key))
        created = cursor.rowcount == 1
        if not created:
            existing = connection.execute(
                "SELECT id, resolved_at FROM notifications WHERE dedupe_key = ?",
                (dedupe_key,),
            ).fetchone()
            if existing and existing["resolved_at"] is not None:
                connection.execute("""
                    UPDATE notifications
                    SET message = ?, created_at = ?, is_read = 0,
                        escalation_level = 0, resolved_at = NULL
                    WHERE id = ?
                """, (message, created_at, existing["id"]))
                connection.execute(
                    "DELETE FROM notification_views WHERE notification_id = ?",
                    (existing["id"],),
                )
                created = True

    if created:
        application = find_application(application_id)
        record_event(
            application,
            "Notification created",
            application.get("stage"),
            f"{category}: {message}",
            actor,
        )
    return created


def run_compliance_check(as_of=None, actor="System"):
    as_of = as_of or date.today()
    settings = get_compliance_settings()
    active_keys = set()
    created_count = 0
    escalated_count = 0

    for application in get_all_applications():
        application_id = application["id"]
        documents = list_application_documents(application_id)

        for document in documents:
            document_id = document["id"]
            status = document["status"]
            expiry = parse_date(document.get("expiry_date"))
            renewal_submitted = document.get("renewal_status") == "Submitted"
            renewal_status = document.get("renewal_status", "Not Required")
            if (
                status == "Verified"
                and expiry
                and expiry <= as_of + timedelta(days=settings["expiry_warning_days"])
                and renewal_status in {"Not Required", "Approved"}
            ):
                with connect_db() as connection:
                    connection.execute(
                        "UPDATE documents SET renewal_status = 'Awaiting Renewal', updated_at = ? WHERE id = ?",
                        (now_iso(), document_id),
                    )
                document["renewal_status"] = "Awaiting Renewal"
                renewal_status = "Awaiting Renewal"
                record_event(
                    application,
                    "Document renewal required",
                    application.get("stage"),
                    f"Document {document_id} is within its configured expiry warning period.",
                    actor,
                )
            if renewal_submitted:
                continue
            if status == "Rejected":
                key = f"document_rejected:{document_id}:{document['updated_at']}"
                message = (
                    f"{document['document_type']} ({document['name']}) was rejected. "
                    f"Reviewer remarks: {document.get('reviewer_remarks') or 'Not provided'}"
                )
                active_keys.add(key)
                created_count += notification_event(
                    application_id, "document_rejected", message, key, actor
                )

            if expiry and expiry < as_of:
                key = f"document_expired:{document_id}:{expiry.isoformat()}"
                message = f"{document['document_type']} ({document['name']}) expired on {expiry.isoformat()}."
                active_keys.add(key)
                created_count += notification_event(
                    application_id, "document_expired", message, key, actor
                )
            elif (
                expiry
                and status == "Verified"
                and 0 <= (expiry - as_of).days <= settings["expiry_warning_days"]
            ):
                key = f"document_expiring:{document_id}:{expiry.isoformat()}"
                message = (
                    f"{document['document_type']} ({document['name']}) expires "
                    f"on {expiry.isoformat()}."
                )
                active_keys.add(key)
                created_count += notification_event(
                    application_id, "document_expiring", message, key, actor
                )

        due = parse_date(application.get("due_date"))
        if due and due < as_of and not is_completed(application):
            key = f"application_overdue:{application_id}:{due.isoformat()}"
            active_keys.add(key)
            created_count += notification_event(
                application_id,
                "application_overdue",
                f"Application deadline passed on {due.isoformat()}.",
                key,
                actor,
            )

        application_expiry = parse_date(application.get("document_expiry_date"))
        if application_expiry and application_expiry < as_of:
            key = f"document_expired:application:{application_id}:{application_expiry.isoformat()}"
            active_keys.add(key)
            created_count += notification_event(
                application_id,
                "document_expired",
                f"Application compliance document expired on {application_expiry.isoformat()}.",
                key,
                actor,
            )
        elif (
            application_expiry
            and 0 <= (application_expiry - as_of).days <= settings["expiry_warning_days"]
        ):
            key = f"document_expiring:application:{application_id}:{application_expiry.isoformat()}"
            active_keys.add(key)
            created_count += notification_event(
                application_id,
                "document_expiring",
                f"Application compliance document expires on {application_expiry.isoformat()}.",
                key,
                actor,
            )

    with connect_db() as connection:
        unresolved = connection.execute(
            "SELECT * FROM notifications WHERE resolved_at IS NULL"
        ).fetchall()
    for notification in unresolved:
        if notification["dedupe_key"] not in active_keys:
            with connect_db() as connection:
                connection.execute(
                    "UPDATE notifications SET resolved_at = ? WHERE id = ? AND resolved_at IS NULL",
                    (now_iso(), notification["id"]),
                )
            application = find_application(notification["application_id"])
            record_event(
                application,
                "Notification resolved",
                application.get("stage"),
                f"Notification {notification['id']} ({notification['category']}) is no longer active.",
                actor,
            )
            continue

        created = datetime.fromisoformat(notification["created_at"])
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        elapsed_days = max(0, (as_of - created.date()).days)
        level = sum(
            1 for threshold in settings["escalation_thresholds_days"]
            if elapsed_days >= threshold
        )
        if level > notification["escalation_level"]:
            with connect_db() as connection:
                connection.execute(
                    "UPDATE notifications SET escalation_level = ? WHERE id = ?",
                    (level, notification["id"]),
                )
            application = find_application(notification["application_id"])
            record_event(
                application,
                "Notification escalated",
                application.get("stage"),
                f"Notification {notification['id']} ({notification['category']}) escalated to level {level}.",
                actor,
            )
            escalated_count += 1

    with connect_db() as connection:
        active_count = connection.execute(
            "SELECT COUNT(*) FROM notifications WHERE resolved_at IS NULL"
        ).fetchone()[0]
    return {
        "checked_on": as_of.isoformat(),
        "created": created_count,
        "escalated": escalated_count,
        "active_notifications": active_count,
    }


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
    by_region = {}
    by_officer = {}
    today = date.today()
    overdue = 0
    expired = 0
    expiring_soon = 0
    deadlines_soon = 0

    for application in applications:
        status = application.get("status", "Unknown")
        stage = application.get("stage")
        region = application.get("region")
        officer = application.get("officer")
        stage = stage.strip() if isinstance(stage, str) and stage.strip() else "Unknown"
        region = region.strip() if isinstance(region, str) and region.strip() else "Unassigned"
        officer = officer.strip() if isinstance(officer, str) and officer.strip() else "Unassigned"
        by_status[status] = by_status.get(status, 0) + 1
        by_stage[stage] = by_stage.get(stage, 0) + 1
        by_region[region] = by_region.get(region, 0) + 1
        by_officer[officer] = by_officer.get(officer, 0) + 1

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
        "by_region": by_region,
        "by_officer": by_officer,
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


@app.get("/api/applications/{application_id}/documents")
def get_documents(
    application_id: str,
    user=Depends(get_current_user),
):
    find_application(application_id)
    documents = list_application_documents(application_id)
    settings = get_compliance_settings()
    for document in documents:
        document["tracking_status"] = get_document_tracking_status(document, settings)
        document["verification_status"] = get_document_verification_status(document)
    summary = calculate_compliance_summary(documents, settings)
    return {
        "application_id": application_id,
        "documents": documents,
        "compliance": summary,
    }


@app.post("/api/applications/{application_id}/documents", status_code=201)
def create_document(
    application_id: str,
    payload: dict = Body(...),
    user=Depends(require_roles("Admin", "Officer")),
):
    application = find_application(application_id)
    allowed = {
        "document_type", "name", "status", "issue_date", "submission_date",
        "expiry_date", "reviewer_remarks",
    }
    if set(payload) - allowed:
        raise HTTPException(status_code=400, detail="Unsupported document fields")

    for field in ("document_type", "name"):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise HTTPException(
                status_code=400,
                detail=f"{field} must be a non-empty string",
            )

    status = payload.get("status", "Pending")
    if not isinstance(status, str) or status not in DOCUMENT_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"status must be one of: {', '.join(sorted(DOCUMENT_STATUSES))}",
        )
    dates = {}
    for field in ("issue_date", "submission_date", "expiry_date"):
        value = payload.get(field)
        if value is not None and (
            not isinstance(value, str)
            or parse_date(value) is None
            or parse_date(value).isoformat() != value
        ):
            raise HTTPException(status_code=400, detail=f"{field} must be a valid YYYY-MM-DD date or null")
        dates[field] = value
    remarks = payload.get("reviewer_remarks")
    if remarks is not None and not isinstance(remarks, str):
        raise HTTPException(status_code=400, detail="reviewer_remarks must be a string or null")
    if status == "Rejected" and not (remarks or "").strip():
        raise HTTPException(status_code=400, detail="Reviewer remarks are required for rejection")

    timestamp = now_iso()
    with connect_db() as connection:
        cursor = connection.execute("""
            INSERT INTO documents
                (application_id, document_type, name, status, issue_date,
                 submission_date, expiry_date, reviewer_remarks, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            application_id, payload["document_type"].strip(), payload["name"].strip(),
            status, dates["issue_date"], dates["submission_date"], dates["expiry_date"],
            remarks.strip() if remarks else None,
            timestamp, timestamp,
        ))
        document_id = cursor.lastrowid

    record_event(
        application,
        "Document created",
        application.get("stage"),
        f"Document {document_id}: {payload['document_type'].strip()} / {payload['name'].strip()} ({status})",
        user["username"],
    )
    run_compliance_check(actor=user["username"])
    with connect_db() as connection:
        document = dict(connection.execute(
            "SELECT * FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone())
    document["tracking_status"] = get_document_tracking_status(document, get_compliance_settings())
    document["verification_status"] = get_document_verification_status(document)
    return {"document": document}


@app.patch("/api/applications/{application_id}/documents/{document_id}")
def update_document(
    application_id: str,
    document_id: int,
    payload: dict = Body(...),
    user=Depends(require_roles("Admin", "Officer")),
):
    application = find_application(application_id)
    allowed = {
        "document_type", "name", "status", "issue_date", "expiry_date",
        "reviewer_remarks",
    }
    if not payload or set(payload) - allowed:
        raise HTTPException(status_code=400, detail="Provide valid document fields to update")
    with connect_db() as connection:
        row = connection.execute(
            "SELECT * FROM documents WHERE id = ? AND application_id = ?",
            (document_id, application_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found")

    document = dict(row)
    updates = {}
    for field in ("document_type", "name"):
        if field in payload:
            value = payload[field]
            if not isinstance(value, str) or not value.strip():
                raise HTTPException(status_code=400, detail=f"{field} must be a non-empty string")
            updates[field] = value.strip()
    if "status" in payload:
        if not isinstance(payload["status"], str) or payload["status"] not in DOCUMENT_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"status must be one of: {', '.join(sorted(DOCUMENT_STATUSES))}",
            )
        updates["status"] = payload["status"]
    for field in ("issue_date", "expiry_date"):
        if field in payload:
            value = payload[field]
            if value is not None and (
                not isinstance(value, str)
                or parse_date(value) is None
                or parse_date(value).isoformat() != value
            ):
                raise HTTPException(status_code=400, detail=f"{field} must be a valid YYYY-MM-DD date or null")
            updates[field] = value
    if "reviewer_remarks" in payload:
        remarks = payload["reviewer_remarks"]
        if remarks is not None and not isinstance(remarks, str):
            raise HTTPException(status_code=400, detail="reviewer_remarks must be a string or null")
        updates["reviewer_remarks"] = remarks.strip() if remarks else None

    final_status = updates.get("status", document["status"])
    final_remarks = updates.get("reviewer_remarks", document["reviewer_remarks"])
    if final_status == "Rejected" and not (final_remarks or "").strip():
        raise HTTPException(status_code=400, detail="Reviewer remarks are required for rejection")
    updates["updated_at"] = now_iso()

    assignments = ", ".join(f"{field} = ?" for field in updates)
    with connect_db() as connection:
        connection.execute(
            f"UPDATE documents SET {assignments} WHERE id = ? AND application_id = ?",
            (*updates.values(), document_id, application_id),
        )
    record_event(
        application,
        "Document updated",
        application.get("stage"),
        f"Document {document_id} updated: {json.dumps(updates, sort_keys=True)}",
        user["username"],
    )
    run_compliance_check(actor=user["username"])
    with connect_db() as connection:
        updated = dict(connection.execute(
            "SELECT * FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone())
    updated["tracking_status"] = get_document_tracking_status(updated, get_compliance_settings())
    updated["verification_status"] = get_document_verification_status(updated)
    return {"document": updated}


@app.get("/api/documents")
def get_document_tracking(user=Depends(get_current_user)):
    settings = get_compliance_settings()
    with connect_db() as connection:
        rows = connection.execute("""
            SELECT d.*, a.data AS application_data
            FROM documents AS d
            JOIN applications AS a ON a.id = d.application_id
            ORDER BY d.expiry_date IS NULL, d.expiry_date, d.id
        """).fetchall()

    documents = []
    for row in rows:
        document = dict(row)
        application = json.loads(document.pop("application_data"))
        document["institution"] = application.get("institution", "")
        document["tracking_status"] = get_document_tracking_status(document, settings)
        document["verification_status"] = get_document_verification_status(document)
        documents.append(document)
    return {"documents": documents, "expiry_warning_days": settings["expiry_warning_days"]}


@app.post("/api/applications/{application_id}/documents/{document_id}/renewal")
def submit_document_renewal(
    application_id: str,
    document_id: int,
    payload: dict = Body(...),
    user=Depends(require_roles("Admin", "Officer")),
):
    application = find_application(application_id)
    allowed = {"issue_date", "expiry_date", "remarks"}
    if set(payload) - allowed or "expiry_date" not in payload:
        raise HTTPException(
            status_code=400,
            detail="Provide expiry_date and optional issue_date and remarks",
        )
    dates = {}
    for field in ("issue_date", "expiry_date"):
        value = payload.get(field)
        if field == "expiry_date" and value is None:
            raise HTTPException(status_code=400, detail="expiry_date is required")
        if value is not None and (
            not isinstance(value, str)
            or parse_date(value) is None
            or parse_date(value).isoformat() != value
        ):
            raise HTTPException(status_code=400, detail=f"{field} must be a valid YYYY-MM-DD date or null")
        dates[field] = value
    remarks = payload.get("remarks")
    if remarks is not None and not isinstance(remarks, str):
        raise HTTPException(status_code=400, detail="remarks must be a string or null")

    with connect_db() as connection:
        document = connection.execute(
            "SELECT * FROM documents WHERE id = ? AND application_id = ?",
            (document_id, application_id),
        ).fetchone()
        if document is None:
            raise HTTPException(status_code=404, detail="Document not found")
        if document["renewal_status"] == "Submitted":
            raise HTTPException(status_code=409, detail="A renewal is already awaiting verification")
        connection.execute("""
            UPDATE documents
            SET renewal_status = 'Submitted', renewal_issue_date = ?,
                renewal_expiry_date = ?, submission_date = ?, renewal_remarks = ?,
                updated_at = ?
            WHERE id = ? AND application_id = ?
        """, (
            dates["issue_date"], dates["expiry_date"], date.today().isoformat(),
            remarks.strip() if remarks else None, now_iso(), document_id, application_id,
        ))

    record_event(
        application,
        "Document renewal submitted",
        application.get("stage"),
        f"Document {document_id} renewal submitted with expiry {dates['expiry_date']}.",
        user["username"],
    )
    run_compliance_check(actor=user["username"])
    with connect_db() as connection:
        updated = dict(connection.execute(
            "SELECT * FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone())
    updated["tracking_status"] = get_document_tracking_status(updated, get_compliance_settings())
    updated["verification_status"] = get_document_verification_status(updated)
    return {"document": updated}


@app.post("/api/applications/{application_id}/documents/{document_id}/renewal/verify")
def verify_document_renewal(
    application_id: str,
    document_id: int,
    payload: dict = Body(...),
    user=Depends(require_roles("Admin", "Officer")),
):
    application = find_application(application_id)
    if set(payload) - {"decision", "remarks"}:
        raise HTTPException(status_code=400, detail="Unsupported renewal verification fields")
    decision = payload.get("decision")
    if not isinstance(decision, str) or decision not in {"Approved", "Rejected"}:
        raise HTTPException(status_code=400, detail="decision must be Approved or Rejected")
    remarks = payload.get("remarks")
    if remarks is not None and not isinstance(remarks, str):
        raise HTTPException(status_code=400, detail="remarks must be a string or null")
    remarks = remarks.strip() if remarks else None
    if decision == "Rejected" and not remarks:
        raise HTTPException(status_code=400, detail="Rejection remarks are required")

    with connect_db() as connection:
        document = connection.execute(
            "SELECT * FROM documents WHERE id = ? AND application_id = ?",
            (document_id, application_id),
        ).fetchone()
        if document is None:
            raise HTTPException(status_code=404, detail="Document not found")
        if document["renewal_status"] != "Submitted":
            raise HTTPException(status_code=409, detail="No renewal is awaiting verification")
        if decision == "Approved":
            connection.execute("""
                UPDATE documents
                SET issue_date = COALESCE(renewal_issue_date, issue_date),
                    expiry_date = renewal_expiry_date,
                    status = 'Verified', renewal_status = 'Approved',
                    reviewer_remarks = ?, updated_at = ?
                WHERE id = ? AND application_id = ?
            """, (remarks, now_iso(), document_id, application_id))
        else:
            connection.execute("""
                UPDATE documents
                SET renewal_status = 'Awaiting Renewal', reviewer_remarks = ?,
                    updated_at = ?
                WHERE id = ? AND application_id = ?
            """, (remarks, now_iso(), document_id, application_id))

    record_event(
        application,
        f"Document renewal {decision.lower()}",
        application.get("stage"),
        f"Document {document_id} renewal {decision.lower()}."
        + (f" Remarks: {remarks}" if remarks else ""),
        user["username"],
    )
    run_compliance_check(actor=user["username"])
    with connect_db() as connection:
        updated = dict(connection.execute(
            "SELECT * FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone())
    updated["tracking_status"] = get_document_tracking_status(updated, get_compliance_settings())
    return {"document": updated}


@app.get("/api/applications/{application_id}/compliance-summary")
def application_compliance_summary(
    application_id: str,
    user=Depends(get_current_user),
):
    find_application(application_id)
    documents = list_application_documents(application_id)
    return calculate_compliance_summary(documents, get_compliance_settings())


@app.get("/api/notifications")
def get_notifications(
    unread_only: bool = False,
    user=Depends(get_current_user),
):
    query = """
        SELECT n.id, n.application_id, n.category, n.message, n.created_at,
               0 AS is_read, n.escalation_level, n.resolved_at
        FROM notifications AS n
        LEFT JOIN notification_views AS nv
            ON nv.notification_id = n.id AND nv.username = ?
        WHERE nv.notification_id IS NULL
    """
    query += " ORDER BY created_at DESC, id DESC LIMIT 500"
    with connect_db() as connection:
        rows = connection.execute(query, (user["username"],)).fetchall()
    return {"notifications": [dict(row) for row in rows]}


@app.post("/api/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: int,
    user=Depends(require_roles("Admin", "Officer")),
):
    with connect_db() as connection:
        notification = connection.execute(
            "SELECT * FROM notifications WHERE id = ?",
            (notification_id,),
        ).fetchone()
        if notification is None:
            raise HTTPException(status_code=404, detail="Notification not found")
        cursor = connection.execute("""
            INSERT OR IGNORE INTO notification_views
                (notification_id, username, viewed_at)
            VALUES (?, ?, ?)
        """, (notification_id, user["username"], now_iso()))
        newly_viewed = cursor.rowcount == 1
    if newly_viewed:
        application = find_application(notification["application_id"])
        record_event(
            application,
            "Notification marked read",
            application.get("stage"),
            f"Notification {notification_id} ({notification['category']}) marked read.",
            user["username"],
        )
    with connect_db() as connection:
        updated = dict(connection.execute(
            "SELECT id, application_id, category, message, created_at, 1 AS is_read, escalation_level, resolved_at FROM notifications WHERE id = ?",
            (notification_id,),
        ).fetchone())
    return {"notification": updated}


@app.post("/api/compliance/check")
def compliance_check(user=Depends(require_roles("Admin"))):
    return run_compliance_check(actor=user["username"])


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
    settings = get_compliance_settings()

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

        documents = list_application_documents(application["id"])
        summary = calculate_compliance_summary(documents, settings, today)
        for document in documents:
            document_expiry = parse_date(document.get("expiry_date"))
            if document_expiry and document_expiry < today:
                alerts.append({
                    "severity": "Critical",
                    "type": "Document expired",
                    "message": f"{document['document_type']} ({document['name']}) expired on {document_expiry.isoformat()}",
                })
            elif (
                document_expiry
                and document["status"] == "Verified"
                and 0 <= (document_expiry - today).days <= settings["expiry_warning_days"]
            ):
                alerts.append({
                    "severity": "Warning",
                    "type": "Document expiring",
                    "message": f"{document['document_type']} ({document['name']}) expires on {document_expiry.isoformat()}",
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
            "compliance_state": summary["state"],
            "documents_count": summary["document_count"],
            "alerts": alerts,
            "compliance_configured": bool(due_value or expiry_value or documents),
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
    run_compliance_check(actor=user["username"])
    return {
        "message": "Compliance dates updated",
        "application": find_application(application_id),
    }
