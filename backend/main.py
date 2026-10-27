
import asyncio
import csv
import hashlib
import hmac
import io
import json
import logging
import os
import secrets
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware


DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
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
    "application_deadline_reminder",
    "evc_scheduled_reminder",
    "evc_overdue",
}
KOLKATA_TIMEZONE = ZoneInfo("Asia/Kolkata")
DEFAULT_NOTIFICATION_SCHEDULE = {
    "deadline_reminder_days": [1, 3, 7],
    "evc_reminder_days": [1, 3, 7],
    "check_interval_seconds": 3600,
}
LOGGER = logging.getLogger(__name__)
COMPLIANCE_SCHEDULER_TASK = None

DEFAULT_WORKFLOW_RISK_ASSESSMENT = {
    "factor_caps": {"deadline": 40, "stage_age": 25, "compliance": 35},
    "risk_level_thresholds": {"low_max": 24, "moderate_max": 49, "high_max": 74},
    "deadline": {
        "overdue_base_points": 30,
        "overdue_points_per_day": 2,
        "approaching_bands": [[3, 25], [7, 20], [14, 12], [30, 5]],
    },
    "stage_age": {"bands": [[7, 10], [14, 18], [30, 25]]},
    "compliance": {
        "missing_required_document_points": 15,
        "expired_document_points": 25,
        "awaiting_renewal_points": 18,
        "expiring_document_points": 10,
        "awaiting_verification_points": 8,
    },
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

DEMO_ROLES = {
    "admin": "Admin",
    "officer": "Officer",
    "viewer": "Viewer",
}


def demo_mode_enabled():
    return (
        os.getenv("PRAGATISYNC_ENVIRONMENT", "").strip().lower() == "demo"
        and os.getenv("PRAGATISYNC_DEMO_MODE_ENABLED", "").strip().lower()
        == "true"
    )


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


def now_iso(now=None):
    timestamp = normalize_datetime(now) if now is not None else datetime.now(timezone.utc)
    return timestamp.isoformat(timespec="seconds")


def normalize_datetime(value):
    if not isinstance(value, datetime):
        raise ValueError("now must be a datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def local_date(now=None):
    timestamp = normalize_datetime(now) if now is not None else datetime.now(timezone.utc)
    return timestamp.astimezone(KOLKATA_TIMEZONE).date()


def reminder_interval(days_remaining, intervals):
    for index in range(len(intervals) - 1, -1, -1):
        lead_days = intervals[index]
        lower_bound = intervals[index - 1] if index else 0
        if lower_bound < days_remaining <= lead_days:
            return lead_days
    return None


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
            CREATE TABLE IF NOT EXISTS evc_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id TEXT NOT NULL UNIQUE,
                scheduled_date TEXT,
                notes TEXT,
                status TEXT NOT NULL DEFAULT 'Pending Scheduling'
                    CHECK (status IN ('Pending Scheduling', 'Scheduled', 'Completed')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY (application_id) REFERENCES applications(id)
            )
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_evc_tracking_scheduled
            ON evc_tracking(scheduled_date)
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
    if user.get("_demo_mode") and not demo_mode_enabled():
        raise HTTPException(status_code=401, detail="Demo mode is disabled")
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


def normalized_application_label(value, fallback="Unknown"):
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def canonical_configured_label(value, configured_labels):
    label = normalized_application_label(value)
    labels_by_key = {item.casefold(): item for item in configured_labels}
    return labels_by_key.get(label.casefold(), label)


def canonical_application_status(application):
    if is_completed(application):
        return "Approved"
    return canonical_configured_label(
        application.get("status"),
        ("Pending", "Under Review", "Returned", "Approved"),
    )


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
    schedule = settings.get("notification_schedule", DEFAULT_NOTIFICATION_SCHEDULE)
    if (
        not isinstance(schedule, dict)
        or set(schedule) != set(DEFAULT_NOTIFICATION_SCHEDULE)
        or any(
            not isinstance(schedule[key], list)
            or any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 1
                for value in schedule[key]
            )
            or schedule[key] != sorted(set(schedule[key]))
            for key in ("deadline_reminder_days", "evc_reminder_days")
        )
        or not isinstance(schedule["check_interval_seconds"], int)
        or isinstance(schedule["check_interval_seconds"], bool)
        or schedule["check_interval_seconds"] < 1
    ):
        raise HTTPException(status_code=500, detail="Invalid notification schedule configuration")
    settings["notification_schedule"] = schedule
    evc_settings = settings.get("evc_tracking", {"upcoming_window_days": 30})
    if (
        not isinstance(evc_settings, dict)
        or set(evc_settings) != {"upcoming_window_days"}
        or not isinstance(evc_settings["upcoming_window_days"], int)
        or isinstance(evc_settings["upcoming_window_days"], bool)
        or evc_settings["upcoming_window_days"] < 0
    ):
        raise HTTPException(status_code=500, detail="Invalid EVC tracking configuration")
    settings["evc_tracking"] = evc_settings
    settings["workflow_risk_assessment"] = validate_workflow_risk_assessment(
        settings.get(
            "workflow_risk_assessment",
            DEFAULT_WORKFLOW_RISK_ASSESSMENT,
        )
    )
    return settings


def application_requires_evc(application):
    route = WORKFLOW_ROUTES.get(application.get("workflow_route"))
    return route is not None and "EVC" in route


def get_evc_status(record, today=None):
    today = today or local_date()
    if record["status"] == "Completed":
        return "Completed"
    scheduled_date = parse_date(record.get("scheduled_date"))
    if scheduled_date is None:
        return "Pending Scheduling"
    if scheduled_date < today:
        return "Delayed"
    return "Scheduled"


def list_evc_tracking(today=None):
    today = today or local_date()
    applications = get_all_applications()
    eligible = [app for app in applications if application_requires_evc(app)]
    if not eligible:
        return []

    with connect_db() as connection:
        records = {
            row["application_id"]: dict(row)
            for row in connection.execute("SELECT * FROM evc_tracking").fetchall()
        }

    items = []
    for application in eligible:
        record = records.get(application["id"])
        if record is None:
            items.append({
                "application_id": application["id"],
                "institution": application.get("institution"),
                "stage": application.get("stage"),
                "application_status": application.get("status"),
                "scheduled_date": None,
                "notes": None,
                "status": "Pending Scheduling",
                "created_at": None,
                "updated_at": None,
                "completed_at": None,
            })
        else:
            items.append({
                **record,
                "institution": application.get("institution"),
                "stage": application.get("stage"),
                "application_status": application.get("status"),
                "status": get_evc_status(record, today),
            })
    return sorted(
        items,
        key=lambda item: (
            item["status"] != "Delayed",
            item["scheduled_date"] is None,
            item["scheduled_date"] or "",
            item["application_id"],
        ),
    )


def validate_workflow_risk_assessment(value):
    def invalid():
        raise HTTPException(
            status_code=500,
            detail="Invalid workflow risk assessment configuration",
        )

    if not isinstance(value, dict):
        invalid()
    caps = value.get("factor_caps")
    if not isinstance(caps, dict) or set(caps) != {"deadline", "stage_age", "compliance"}:
        invalid()
    if any(
        not isinstance(points, int) or isinstance(points, bool) or points < 0
        for points in caps.values()
    ) or sum(caps.values()) != 100:
        invalid()

    levels = value.get("risk_level_thresholds")
    if not isinstance(levels, dict) or set(levels) != {"low_max", "moderate_max", "high_max"}:
        invalid()
    level_thresholds = [levels[key] for key in ("low_max", "moderate_max", "high_max")]
    if any(
        not isinstance(points, int) or isinstance(points, bool)
        for points in level_thresholds
    ) or not (0 <= level_thresholds[0] < level_thresholds[1] < level_thresholds[2] < 100):
        invalid()

    deadline = value.get("deadline")
    stage_age = value.get("stage_age")
    compliance = value.get("compliance")
    if not all(isinstance(section, dict) for section in (deadline, stage_age, compliance)):
        invalid()

    overdue_base = deadline.get("overdue_base_points")
    overdue_daily = deadline.get("overdue_points_per_day")
    if (
        not isinstance(overdue_base, int)
        or isinstance(overdue_base, bool)
        or not 0 <= overdue_base <= caps["deadline"]
        or not isinstance(overdue_daily, int)
        or isinstance(overdue_daily, bool)
        or overdue_daily < 0
    ):
        invalid()

    def valid_bands(bands, cap, increasing_points):
        if not isinstance(bands, list) or not bands:
            return False
        previous_day = 0
        previous_points = None
        for band in bands:
            if (
                not isinstance(band, list)
                or len(band) != 2
                or any(not isinstance(item, int) or isinstance(item, bool) for item in band)
            ):
                return False
            threshold_day, points = band
            if (
                threshold_day <= previous_day
                or points < 0
                or points > cap
                or (
                    previous_points is not None
                    and (points <= previous_points if increasing_points else points >= previous_points)
                )
            ):
                return False
            previous_day = threshold_day
            previous_points = points
        return True

    if not valid_bands(
        deadline.get("approaching_bands"),
        caps["deadline"],
        increasing_points=False,
    ):
        invalid()
    if not valid_bands(stage_age.get("bands"), caps["stage_age"], increasing_points=True):
        invalid()

    expected_compliance_settings = {
        "missing_required_document_points",
        "expired_document_points",
        "awaiting_renewal_points",
        "expiring_document_points",
        "awaiting_verification_points",
    }
    if set(compliance) != expected_compliance_settings or any(
        not isinstance(points, int)
        or isinstance(points, bool)
        or not 0 <= points <= caps["compliance"]
        for points in compliance.values()
    ):
        invalid()
    return value


def get_workflow_risk_level(score, thresholds):
    if score is None:
        return "Unknown"
    if score <= thresholds["low_max"]:
        return "Low"
    if score <= thresholds["moderate_max"]:
        return "Moderate"
    if score <= thresholds["high_max"]:
        return "High"
    return "Critical"


def list_application_documents(application_id):
    with connect_db() as connection:
        rows = connection.execute(
            "SELECT * FROM documents WHERE application_id = ? ORDER BY id",
            (application_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_document_tracking_status(document, settings, today=None):
    today = today or local_date()
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


def get_stage_entry_timestamps():
    with connect_db() as connection:
        rows = connection.execute("""
            SELECT application_id, stage, action, timestamp
            FROM audit_events
            WHERE action IN (
                'Application created',
                'Workflow changed',
                'Workflow automatically assigned',
                'Stage advanced'
            )
            ORDER BY id
        """).fetchall()
    timestamps = {}
    for row in rows:
        if row["stage"] in (None, ""):
            continue
        timestamp = parse_datetime(row["timestamp"])
        if timestamp is not None:
            timestamps[(
                row["application_id"],
                row["stage"].strip().casefold(),
            )] = timestamp
    return timestamps


def get_application_stage_entry_timestamp(stage_entries, application):
    stage = application.get("stage")
    if not isinstance(stage, str) or not stage.strip():
        return None
    return stage_entries.get((application["id"], stage.strip().casefold()))


def parse_datetime(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def calculate_workflow_risk(
    application,
    documents,
    settings,
    stage_entered_at=None,
    today=None,
):
    today = today or local_date()
    config = settings["workflow_risk_assessment"]
    caps = config["factor_caps"]
    factors = []
    data_gaps = []
    recommendations = []
    assessed = set()

    def add_factor(category, name, points, explanation, recommendation=None):
        if category == "compliance":
            already_used = sum(
                factor["points"]
                for factor in factors
                if factor["category"] == "compliance"
            )
            uncapped_points = points
            points = min(points, max(0, caps["compliance"] - already_used))
            if points < uncapped_points:
                explanation += " The compliance category cap limits the additional score."
        factors.append({
            "category": category,
            "name": name,
            "points": points,
            "explanation": explanation,
            "recommendation": recommendation,
        })
        if recommendation:
            recommendations.append(recommendation)

    completed = is_completed(application)
    due_value = application.get("due_date")
    due = parse_date(due_value)
    if completed:
        assessed.add("deadline")
        add_factor("deadline", "Deadline", 0, "Application is completed; deadline risk is not active.")
    elif due is not None:
        assessed.add("deadline")
        days_remaining = (due - today).days
        if days_remaining < 0:
            days_overdue = abs(days_remaining)
            points = min(
                caps["deadline"],
                config["deadline"]["overdue_base_points"]
                + config["deadline"]["overdue_points_per_day"] * days_overdue,
            )
            add_factor(
                "deadline",
                "Overdue deadline",
                points,
                f"Deadline passed {days_overdue} day(s) ago.",
                "Review the application immediately and update its due date only if the recorded deadline was incorrect.",
            )
        else:
            band = next(
                (
                    band for band in config["deadline"]["approaching_bands"]
                    if days_remaining <= band[0]
                ),
                None,
            )
            points = band[1] if band else 0
            explanation = (
                f"Deadline is in {days_remaining} day(s)."
                if points
                else f"Deadline is in {days_remaining} day(s), outside configured warning bands."
            )
            add_factor(
                "deadline",
                "Upcoming deadline",
                points,
                explanation,
                "Review outstanding workflow and compliance actions before the deadline." if points else None,
            )
    elif due_value not in (None, ""):
        data_gaps.append("Application deadline is invalid; deadline risk could not be assessed.")
    else:
        data_gaps.append("Application deadline is not recorded; deadline risk could not be assessed.")

    if completed:
        assessed.add("stage_age")
        add_factor("stage_age", "Current-stage age", 0, "Application is completed; stage-aging risk is not active.")
    elif stage_entered_at is not None:
        if isinstance(stage_entered_at, datetime):
            entered_at = stage_entered_at
        else:
            entered_at = parse_datetime(stage_entered_at)
        if entered_at is None or entered_at.date() > today:
            data_gaps.append("Current-stage entry timestamp is invalid; stage age could not be assessed.")
        else:
            assessed.add("stage_age")
            days_in_stage = (today - entered_at.date()).days
            band = next(
                (
                    band for band in reversed(config["stage_age"]["bands"])
                    if days_in_stage >= band[0]
                ),
                None,
            )
            points = band[1] if band else 0
            add_factor(
                "stage_age",
                "Time in current stage",
                points,
                f"Application has been in {application.get('stage') or 'the current stage'} for {days_in_stage} day(s).",
                "Review the current stage for blockers and determine the next processing action." if points else None,
            )
    else:
        data_gaps.append("No reliable audit timestamp for entry into the current stage; stage age could not be assessed.")

    compliance_config = config["compliance"]
    required_types = settings["required_document_types"]
    if documents or required_types:
        assessed.add("compliance")
        required_keys = set()
        document_types = set()
        for document in documents:
            document_type = document.get("document_type")
            if isinstance(document_type, str):
                document_types.add(document_type.strip().casefold())

            expiry_value = document.get("expiry_date")
            expiry = parse_date(expiry_value)
            if expiry_value not in (None, "") and expiry is None:
                data_gaps.append(
                    f"Expiry date for {document.get('document_type') or 'a document'} ({document.get('name') or document.get('id', 'unknown')}) is invalid."
                )
            elif not expiry:
                data_gaps.append(
                    f"Expiry date for {document.get('document_type') or 'a document'} ({document.get('name') or document.get('id', 'unknown')}) is not recorded."
                )

            renewal_status = document.get("renewal_status", "Not Required")
            status = document.get("status")
            if expiry and expiry < today:
                points = compliance_config["expired_document_points"]
                explanation = f"{document.get('document_type') or 'Document'} ({document.get('name') or 'unnamed'}) expired on {expiry.isoformat()}."
                recommendation = "Record and submit a renewal, then complete verification."
            elif renewal_status in {"Awaiting Renewal", "Rejected"} or status == "Rejected":
                points = compliance_config["awaiting_renewal_points"]
                explanation = f"{document.get('document_type') or 'Document'} ({document.get('name') or 'unnamed'}) is awaiting renewal after rejection or an expiry warning."
                recommendation = "Resolve the renewal issue and submit updated document metadata for verification."
            elif (
                expiry
                and status == "Verified"
                and 0 <= (expiry - today).days <= settings["expiry_warning_days"]
            ):
                points = compliance_config["expiring_document_points"]
                explanation = f"{document.get('document_type') or 'Document'} ({document.get('name') or 'unnamed'}) expires on {expiry.isoformat()}."
                recommendation = "Initiate renewal before the recorded expiry date."
            elif renewal_status == "Submitted" or status in {"Pending", "Submitted"}:
                points = compliance_config["awaiting_verification_points"]
                explanation = f"{document.get('document_type') or 'Document'} ({document.get('name') or 'unnamed'}) is awaiting verification."
                recommendation = "Complete document verification and record the decision."
            else:
                points = 0
                explanation = f"{document.get('document_type') or 'Document'} ({document.get('name') or 'unnamed'}) has no configured outstanding status issue."
                recommendation = None
            add_factor(
                "compliance",
                f"Document {document.get('id', 'unknown')}",
                points,
                explanation,
                recommendation,
            )

        for required_type in required_types:
            required_key = required_type.strip().casefold()
            if required_key in required_keys:
                continue
            required_keys.add(required_key)
            if required_key not in document_types:
                add_factor(
                    "compliance",
                    f"Missing {required_type}",
                    compliance_config["missing_required_document_points"],
                    f"Configured required document type '{required_type}' has no document record.",
                    f"Record and verify the required {required_type} document.",
                )
        if not required_types:
            data_gaps.append("Required document types are not configured; missing-document risk could not be assessed.")
        if documents and application.get("document_expiry_date") not in (None, ""):
            data_gaps.append(
                "Application-level document expiry is not scored separately when document-register entries exist, to avoid potential double-counting."
            )
    else:
        legacy_expiry_value = application.get("document_expiry_date")
        legacy_expiry = parse_date(legacy_expiry_value)
        if legacy_expiry:
            data_gaps.append("Required document types are not configured; missing-document risk could not be assessed.")
            assessed.add("compliance")
            if legacy_expiry < today:
                points = compliance_config["expired_document_points"]
                add_factor(
                    "compliance",
                    "Application-level document expiry",
                    points,
                    f"Application-level compliance document expired on {legacy_expiry.isoformat()}.",
                    "Record and verify a renewed compliance document.",
                )
            elif (legacy_expiry - today).days <= settings["expiry_warning_days"]:
                points = compliance_config["expiring_document_points"]
                add_factor(
                    "compliance",
                    "Application-level document expiry",
                    points,
                    f"Application-level compliance document expires on {legacy_expiry.isoformat()}.",
                    "Review renewal requirements before the recorded expiry date.",
                )
            else:
                add_factor(
                    "compliance",
                    "Application-level document expiry",
                    0,
                    "Application-level compliance document is outside the configured warning period.",
                )
        else:
            data_gaps.append("Required document types are not configured; missing-document risk could not be assessed.")
            if legacy_expiry_value not in (None, ""):
                data_gaps.append("Application-level document expiry is invalid; compliance expiry risk could not be assessed.")
            data_gaps.append("No document records or required document types are available for compliance risk assessment.")

    score = sum(factor["points"] for factor in factors)
    if not assessed:
        score = None
    risk_level = get_workflow_risk_level(score, config["risk_level_thresholds"])

    return {
        "risk_score": score,
        "risk_level": risk_level,
        "risk_factors": factors,
        "risk_recommendations": list(dict.fromkeys(recommendations)),
        "risk_data_gaps": list(dict.fromkeys(data_gaps)),
    }


def calculate_compliance_summary(documents, settings, today=None):
    today = today or local_date()
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


def make_history_event(action, stage, details=None, actor="System", timestamp=None):
    return {
        "action": action,
        "stage": stage,
        "details": details,
        "timestamp": timestamp or now_iso(),
        "actor": actor,
    }


def record_event(
    application,
    action,
    stage,
    details=None,
    actor="System",
    timestamp=None,
):
    event = make_history_event(action, stage, details, actor, timestamp)
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


def require_assigned_workflow_route(application):
    if not application.get("workflow_route"):
        raise HTTPException(
            status_code=409,
            detail="Assign a workflow route before processing this application.",
        )


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
    stage = normalized_application_label(application.get("stage"), "").casefold()
    status = normalized_application_label(application.get("status"), "").casefold()
    return (
        stage == "completed"
        or status == "approved"
    )


def parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def notification_event(application_id, category, message, dedupe_key, actor, now=None):
    created_at = now_iso(now)
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
                reactivated = connection.execute("""
                    UPDATE notifications
                    SET message = ?, created_at = ?, is_read = 0,
                        escalation_level = 0, resolved_at = NULL
                    WHERE id = ? AND resolved_at IS NOT NULL
                """, (message, created_at, existing["id"]))
                created = reactivated.rowcount == 1
                if created:
                    connection.execute(
                        "DELETE FROM notification_views WHERE notification_id = ?",
                        (existing["id"],),
                    )

    if created:
        application = find_application(application_id)
        record_event(
            application,
            "Notification created",
            application.get("stage"),
            f"{category}: {message}",
            actor,
            created_at,
        )
    return created


def run_compliance_check(as_of=None, actor="System", now=None):
    check_time = normalize_datetime(now) if now is not None else datetime.now(timezone.utc)
    as_of = as_of or check_time.astimezone(KOLKATA_TIMEZONE).date()
    settings = get_compliance_settings()
    schedule = settings["notification_schedule"]
    active_keys = set()
    created_count = 0
    escalated_count = 0

    applications = get_all_applications()
    applications_by_id = {application["id"]: application for application in applications}
    for application in applications:
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
                    renewal_update = connection.execute(
                        """
                        UPDATE documents
                        SET renewal_status = 'Awaiting Renewal', updated_at = ?
                        WHERE id = ? AND renewal_status IN ('Not Required', 'Approved')
                        """,
                        (now_iso(check_time), document_id),
                    )
                if renewal_update.rowcount:
                    document["renewal_status"] = "Awaiting Renewal"
                    renewal_status = "Awaiting Renewal"
                    record_event(
                        application,
                        "Document renewal required",
                        application.get("stage"),
                        f"Document {document_id} is within its configured expiry warning period.",
                        actor,
                        now_iso(check_time),
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
                    application_id, "document_rejected", message, key, actor, check_time
                )

            if expiry and expiry < as_of:
                key = f"document_expired:{document_id}:{expiry.isoformat()}"
                message = f"{document['document_type']} ({document['name']}) expired on {expiry.isoformat()}."
                active_keys.add(key)
                created_count += notification_event(
                    application_id, "document_expired", message, key, actor, check_time
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
                    application_id, "document_expiring", message, key, actor, check_time
                )

        due = parse_date(application.get("due_date"))
        if due and not is_completed(application):
            if due < as_of:
                key = f"application_overdue:{application_id}:{due.isoformat()}"
                active_keys.add(key)
                created_count += notification_event(
                    application_id,
                    "application_overdue",
                    f"Application deadline passed on {due.isoformat()}.",
                    key,
                    actor,
                    check_time,
                )
            else:
                days_remaining = (due - as_of).days
                lead_days = reminder_interval(
                    days_remaining,
                    schedule["deadline_reminder_days"],
                )
                if lead_days is not None:
                    key = (
                        f"application_deadline_reminder:{application_id}:"
                        f"{due.isoformat()}:{lead_days}"
                    )
                    active_keys.add(key)
                    created_count += notification_event(
                        application_id,
                        "application_deadline_reminder",
                        f"Application deadline is {days_remaining} day(s) away on {due.isoformat()}.",
                        key,
                        actor,
                        check_time,
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
                check_time,
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
                check_time,
            )

    with connect_db() as connection:
        evc_records = connection.execute("""
            SELECT application_id, scheduled_date, status
            FROM evc_tracking
            WHERE status != 'Completed' AND scheduled_date IS NOT NULL
        """).fetchall()
    for record in evc_records:
        application_id = record["application_id"]
        application = applications_by_id.get(application_id)
        if application is None or not application_requires_evc(application):
            continue
        scheduled_date = parse_date(record["scheduled_date"])
        if scheduled_date is None:
            continue
        if scheduled_date < as_of:
            key = f"evc_overdue:{application_id}:{scheduled_date.isoformat()}"
            active_keys.add(key)
            created_count += notification_event(
                application_id,
                "evc_overdue",
                f"The EVC scheduled for {scheduled_date.isoformat()} is overdue.",
                key,
                actor,
                check_time,
            )
        else:
            days_remaining = (scheduled_date - as_of).days
            lead_days = reminder_interval(days_remaining, schedule["evc_reminder_days"])
            if lead_days is not None:
                key = (
                    f"evc_scheduled_reminder:{application_id}:"
                    f"{scheduled_date.isoformat()}:{lead_days}"
                )
                active_keys.add(key)
                created_count += notification_event(
                    application_id,
                    "evc_scheduled_reminder",
                    f"EVC is scheduled in {days_remaining} day(s), on {scheduled_date.isoformat()}.",
                    key,
                    actor,
                    check_time,
                )

    with connect_db() as connection:
        unresolved = connection.execute(
            "SELECT * FROM notifications WHERE resolved_at IS NULL"
        ).fetchall()
    for notification in unresolved:
        if notification["dedupe_key"] not in active_keys:
            with connect_db() as connection:
                resolved = connection.execute(
                    "UPDATE notifications SET resolved_at = ? WHERE id = ? AND resolved_at IS NULL",
                    (now_iso(check_time), notification["id"]),
                )
            if resolved.rowcount:
                application = find_application(notification["application_id"])
                record_event(
                    application,
                    "Notification resolved",
                    application.get("stage"),
                    f"Notification {notification['id']} ({notification['category']}) is no longer active.",
                    actor,
                    now_iso(check_time),
                )
            continue

        created = datetime.fromisoformat(notification["created_at"])
        created_date = normalize_datetime(created).astimezone(KOLKATA_TIMEZONE).date()
        elapsed_days = max(0, (as_of - created_date).days)
        level = sum(
            1 for threshold in settings["escalation_thresholds_days"]
            if elapsed_days >= threshold
        )
        if level > notification["escalation_level"]:
            with connect_db() as connection:
                escalated = connection.execute(
                    """
                    UPDATE notifications SET escalation_level = ?
                    WHERE id = ? AND escalation_level < ? AND resolved_at IS NULL
                    """,
                    (level, notification["id"], level),
                )
            if escalated.rowcount:
                application = find_application(notification["application_id"])
                record_event(
                    application,
                    "Notification escalated",
                    application.get("stage"),
                    f"Notification {notification['id']} ({notification['category']}) escalated to level {level}.",
                    actor,
                    now_iso(check_time),
                )
                escalated_count += 1

    with connect_db() as connection:
        active_count = connection.execute(
            "SELECT COUNT(*) FROM notifications WHERE resolved_at IS NULL"
        ).fetchone()[0]
    return {
        "checked_on": as_of.isoformat(),
        "checked_at": now_iso(check_time),
        "created": created_count,
        "escalated": escalated_count,
        "active_notifications": active_count,
    }


async def compliance_scheduler_loop(interval_seconds):
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                run_compliance_check,
                None,
                "System",
            )
        except Exception:
            LOGGER.exception("Automated compliance check failed; it will retry next interval.")


@app.on_event("startup")
async def startup_event():
    global COMPLIANCE_SCHEDULER_TASK
    initialize_database()
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, run_compliance_check, None, "System")
    interval = get_compliance_settings()["notification_schedule"]["check_interval_seconds"]
    COMPLIANCE_SCHEDULER_TASK = asyncio.create_task(
        compliance_scheduler_loop(interval)
    )


@app.on_event("shutdown")
async def shutdown_event():
    global COMPLIANCE_SCHEDULER_TASK
    if COMPLIANCE_SCHEDULER_TASK is not None:
        COMPLIANCE_SCHEDULER_TASK.cancel()
        try:
            await COMPLIANCE_SCHEDULER_TASK
        except asyncio.CancelledError:
            pass
        COMPLIANCE_SCHEDULER_TASK = None


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


@app.post("/api/auth/demo-session")
def create_demo_session(
    response: Response,
    payload: dict = Body(...),
    authorization: str | None = Header(default=None),
):
    if not demo_mode_enabled():
        raise HTTPException(status_code=404, detail="Demo mode is disabled")

    requested_role = payload.get("role")
    if not isinstance(requested_role, str):
        raise HTTPException(status_code=400, detail="A demo role is required")
    role = DEMO_ROLES.get(requested_role.strip().lower())
    if role is None:
        raise HTTPException(status_code=400, detail="Unsupported demo role")

    with SESSION_LOCK:
        token = None
        if authorization:
            if not authorization.startswith("Bearer "):
                raise HTTPException(status_code=401, detail="Invalid demo session")
            previous_token = authorization.removeprefix("Bearer ").strip()
            previous_user = SESSIONS.get(previous_token)
            if not previous_user or not previous_user.get("_demo_mode"):
                raise HTTPException(status_code=401, detail="Invalid demo session")
            token = previous_token

        if token is None:
            token = secrets.token_urlsafe(32)
        user = {"username": role.lower(), "role": role}
        SESSIONS[token] = {**user, "_demo_mode": True}

    response.headers["Cache-Control"] = "no-store"
    return {"access_token": token, "token_type": "bearer", "user": user}


@app.get("/api/auth/me")
def auth_me(user=Depends(get_current_user)):
    return {
        "username": user["username"],
        "role": user["role"],
        "demo_mode": bool(user.get("_demo_mode")),
    }


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


@app.get("/api/evc")
def get_evc_tracking(user=Depends(get_current_user)):
    settings = get_compliance_settings()
    evcs = list_evc_tracking()
    today = local_date()
    upcoming_cutoff = today + timedelta(
        days=settings["evc_tracking"]["upcoming_window_days"]
    )
    return {
        "checked_on": today.isoformat(),
        "upcoming_window_days": settings["evc_tracking"]["upcoming_window_days"],
        "evcs": evcs,
        "upcoming_count": sum(
            1 for item in evcs
            if item["status"] == "Scheduled"
            and (scheduled_date := parse_date(item["scheduled_date"])) is not None
            and scheduled_date <= upcoming_cutoff
        ),
        "overdue_count": sum(1 for item in evcs if item["status"] == "Delayed"),
    }


@app.post("/api/applications/{application_id}/evc/schedule")
def schedule_evc(
    application_id: str,
    payload: dict = Body(...),
    user=Depends(require_roles("Admin", "Officer")),
):
    application = find_application(application_id)
    if not application_requires_evc(application):
        raise HTTPException(
            status_code=409,
            detail="The application's configured workflow route does not require an EVC",
        )
    if set(payload) - {"scheduled_date", "notes"} or "scheduled_date" not in payload:
        raise HTTPException(
            status_code=400,
            detail="Provide scheduled_date and optional notes",
        )
    scheduled_value = payload["scheduled_date"]
    scheduled_date = parse_date(scheduled_value)
    if (
        not isinstance(scheduled_value, str)
        or scheduled_date is None
        or scheduled_date.isoformat() != scheduled_value
    ):
        raise HTTPException(
            status_code=400,
            detail="scheduled_date must be a valid YYYY-MM-DD date",
        )
    notes = payload.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise HTTPException(status_code=400, detail="notes must be a string or null")
    notes = notes.strip() if notes else None

    with connect_db() as connection:
        existing = connection.execute(
            "SELECT * FROM evc_tracking WHERE application_id = ?",
            (application_id,),
        ).fetchone()
        if existing and existing["status"] == "Completed":
            raise HTTPException(status_code=409, detail="A completed EVC cannot be rescheduled")
        is_reschedule = existing is not None
        timestamp = now_iso()
        connection.execute("""
            INSERT INTO evc_tracking
                (application_id, scheduled_date, notes, status, created_at, updated_at, completed_at)
            VALUES (?, ?, ?, 'Scheduled', ?, ?, NULL)
            ON CONFLICT(application_id) DO UPDATE SET
                scheduled_date = excluded.scheduled_date,
                notes = excluded.notes,
                status = 'Scheduled',
                updated_at = excluded.updated_at,
                completed_at = NULL
        """, (application_id, scheduled_value, notes, timestamp, timestamp))

    record_event(
        application,
        "EVC rescheduled" if is_reschedule else "EVC scheduled",
        application.get("stage"),
        f"EVC scheduled for {scheduled_value}."
        + (f" Notes: {notes}" if notes else ""),
        user["username"],
    )
    run_compliance_check(actor=user["username"])
    with connect_db() as connection:
        record = dict(connection.execute(
            "SELECT * FROM evc_tracking WHERE application_id = ?",
            (application_id,),
        ).fetchone())
    record["status"] = get_evc_status(record)
    return {"evc": record}


@app.post("/api/applications/{application_id}/evc/complete")
def complete_evc(
    application_id: str,
    payload: dict = Body(default={}),
    user=Depends(require_roles("Admin", "Officer")),
):
    application = find_application(application_id)
    if not application_requires_evc(application):
        raise HTTPException(
            status_code=409,
            detail="The application's configured workflow route does not require an EVC",
        )
    if set(payload) - {"notes"}:
        raise HTTPException(status_code=400, detail="Only optional notes may be provided")
    notes = payload.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise HTTPException(status_code=400, detail="notes must be a string or null")
    notes = notes.strip() if notes else None

    with connect_db() as connection:
        existing = connection.execute(
            "SELECT * FROM evc_tracking WHERE application_id = ?",
            (application_id,),
        ).fetchone()
        if existing is None or not existing["scheduled_date"]:
            raise HTTPException(status_code=409, detail="Schedule the EVC before completing it")
        if existing["status"] == "Completed":
            raise HTTPException(status_code=409, detail="EVC is already completed")
        completed_at = now_iso()
        if notes:
            existing_notes = existing["notes"] or ""
            combined_notes = f"{existing_notes}\nCompletion: {notes}".strip()
        else:
            combined_notes = existing["notes"]
        connection.execute("""
            UPDATE evc_tracking
            SET status = 'Completed', notes = ?, completed_at = ?, updated_at = ?
            WHERE application_id = ?
        """, (combined_notes, completed_at, completed_at, application_id))

    record_event(
        application,
        "EVC completed",
        application.get("stage"),
        "EVC completed." + (f" Notes: {notes}" if notes else ""),
        user["username"],
    )
    run_compliance_check(actor=user["username"])
    with connect_db() as connection:
        record = dict(connection.execute(
            "SELECT * FROM evc_tracking WHERE application_id = ?",
            (application_id,),
        ).fetchone())
    return {"evc": record}


@app.get("/api/dashboard")
def dashboard(user=Depends(get_current_user)):
    applications = get_all_applications()
    settings = get_compliance_settings()
    by_status = {}
    by_stage = {}
    by_region = {}
    region_labels = {}
    status_labels = {}
    stage_labels = {}
    by_officer = {}
    today = local_date()
    overdue = 0
    expired = 0
    expiring_soon = 0
    deadlines_soon = 0

    for application in applications:
        status = canonical_application_status(application)
        status_key = status.casefold()
        status = status_labels.setdefault(status_key, status)
        stage = canonical_configured_label(
            application.get("stage"),
            (stage for route in WORKFLOW_ROUTES.values() for stage in route),
        )
        stage_key = stage.casefold()
        stage = stage_labels.setdefault(stage_key, stage)
        region = application.get("region")
        officer = application.get("officer")
        region = normalized_application_label(region, "Unassigned")
        officer = normalized_application_label(officer, "Unassigned")
        by_status[status] = by_status.get(status, 0) + 1
        by_stage[stage] = by_stage.get(stage, 0) + 1
        region_key = region.casefold()
        region_label = region_labels.setdefault(region_key, region)
        by_region[region_label] = by_region.get(region_label, 0) + 1
        by_officer[officer] = by_officer.get(officer, 0) + 1

        due = parse_date(application.get("due_date"))
        expiry = parse_date(application.get("document_expiry_date"))

        if due and due < today and not is_completed(application):
            overdue += 1
        elif due and 0 <= (due - today).days <= 7 and not is_completed(application):
            deadlines_soon += 1

        if expiry and expiry < today:
            expired += 1
        elif expiry and 0 <= (expiry - today).days <= settings["expiry_warning_days"]:
            expiring_soon += 1

    with connect_db() as connection:
        audit_count = connection.execute(
            "SELECT COUNT(*) FROM audit_events"
        ).fetchone()[0]

    return {
        "total_applications": len(applications),
        "pending_requests": sum(
            count
            for status, count in by_status.items()
            if status in ("Pending", "Under Review", "Returned")
        ),
        "active_blocks": 0,
        "emergency_jobs": 0,
        "approved": by_status.get("Approved", 0),
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
            dates["issue_date"], dates["expiry_date"], local_date().isoformat(),
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
    today = local_date()
    results = []
    settings = get_compliance_settings()
    stage_entries = get_stage_entry_timestamps()

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
        elif expiry and 0 <= (expiry - today).days <= settings["expiry_warning_days"]:
            alerts.append({
                "severity": "Warning",
                "type": "Document expiring",
                "message": (
                    "Document expires within "
                    f"{settings['expiry_warning_days']} days"
                ),
            })

        documents = list_application_documents(application["id"])
        summary = calculate_compliance_summary(documents, settings, today)
        risk = calculate_workflow_risk(
            application,
            documents,
            settings,
            get_application_stage_entry_timestamp(stage_entries, application),
            today,
        )
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
            **risk,
            "alerts": alerts,
            "compliance_configured": bool(due_value or expiry_value or documents),
        })

    risk_summary = {
        level: sum(
            1 for item in results
            if item["risk_level"] == level
        )
        for level in ("Low", "Moderate", "High", "Critical", "Unknown")
    }
    return {
        "checked_on": today.isoformat(),
        "total_applications": len(results),
        "risk_summary": risk_summary,
        "applications": results,
    }


@app.get("/api/monitoring/heatmap")
def workflow_heatmap(metric: str = "applications", user=Depends(get_current_user)):
    metric_labels = {
        "applications": "Applications",
        "overdue": "Overdue applications",
        "high_risk": "High-risk applications",
    }
    if metric not in metric_labels:
        raise HTTPException(
            status_code=400,
            detail="Metric must be applications, overdue, or high_risk",
        )

    applications = get_all_applications()
    configured_stages = list(dict.fromkeys(
        stage
        for route in WORKFLOW_ROUTES.values()
        for stage in route
    ))
    configured_stage_set = set(configured_stages)
    region_labels = {}
    categorized_applications = []
    has_unknown_stage = False

    for application in applications:
        region = application.get("region")
        if isinstance(region, str) and region.strip():
            region = region.strip()
            region_key = f"region:{region.casefold()}"
            region_labels.setdefault(region_key, region)
        else:
            region_key = "unknown"
            region_labels[region_key] = "Unknown / missing region"

        stage = canonical_configured_label(
            application.get("stage"),
            configured_stages,
        )
        if stage not in configured_stage_set:
            stage = "Unknown stage"
            has_unknown_stage = True
        categorized_applications.append((application, region_key, stage))

    regions = sorted(
        region_labels,
        key=lambda key: (key == "unknown", region_labels[key].casefold()),
    )
    stages = configured_stages + (["Unknown stage"] if has_unknown_stage else [])
    cells = {
        (region_key, stage): []
        for region_key in regions
        for stage in stages
    }
    today = local_date()
    if metric == "high_risk":
        settings = get_compliance_settings()
        stage_entries = get_stage_entry_timestamps()

    for application, region_key, stage in categorized_applications:
        include_application = metric == "applications"
        if metric == "overdue":
            due_date = parse_date(application.get("due_date"))
            include_application = (
                due_date is not None
                and due_date < today
                and not is_completed(application)
            )
        elif metric == "high_risk":
            risk = calculate_workflow_risk(
                application,
                list_application_documents(application["id"]),
                settings,
                get_application_stage_entry_timestamp(stage_entries, application),
                today,
            )
            include_application = risk["risk_level"] in {"High", "Critical"}

        if include_application:
            cells[(region_key, stage)].append({
                "id": application["id"],
                "institution": application.get("institution") or "Institution unavailable",
                "region": application.get("region"),
                "stage": application.get("stage"),
                "status": application.get("status") or "Unknown",
            })

    rows = [
        {
            "key": region_key,
            "label": region_labels[region_key],
            "is_unknown": region_key == "unknown",
            "cells": [
                {
                    "stage": stage,
                    "count": len(cells[(region_key, stage)]),
                    "applications": cells[(region_key, stage)],
                }
                for stage in stages
            ],
        }
        for region_key in regions
    ]
    matching_applications = sum(
        cell["count"]
        for row in rows
        for cell in row["cells"]
    )
    return {
        "metric": metric,
        "metric_label": metric_labels[metric],
        "total_applications": len(applications),
        "matching_applications": matching_applications,
        "regions": rows,
        "stages": stages,
        "max_count": max(
            (cell["count"] for row in rows for cell in row["cells"]),
            default=0,
        ),
    }


def safe_csv_cell(value):
    if value is None:
        return ""
    text = str(value)
    first_value_character = next(
        (
            character
            for character in text
            if not character.isspace()
            and unicodedata.category(character) not in {"Cc", "Cf"}
        ),
        "",
    )
    if first_value_character in {"=", "+", "-", "@"}:
        return "'" + text
    return text


@app.get("/api/reports/pendency.csv")
def export_pendency_report(user=Depends(get_current_user)):
    today = local_date()
    settings = get_compliance_settings()
    stage_entries = get_stage_entry_timestamps()
    applications = get_all_applications()

    with connect_db() as connection:
        active_overdue_notifications = {
            row["application_id"]: dict(row)
            for row in connection.execute("""
                SELECT application_id, COUNT(*) AS notification_count,
                       MAX(escalation_level) AS escalation_level
                FROM notifications
                WHERE category = 'application_overdue' AND resolved_at IS NULL
                GROUP BY application_id
            """).fetchall()
        }

    headers = [
        "Application ID",
        "Institution",
        "Assigned Officer",
        "Application Status",
        "Workflow Stage",
        "Deadline",
        "Deadline State",
        "Days Overdue",
        "Days Until Deadline",
        "Current Stage Entered At (Asia/Kolkata)",
        "Pending Duration (Days)",
        "Prototype Risk Score",
        "Prototype Risk Level",
        "Prototype Compliance State (Not Official APH Determination)",
        "Document Count",
        "Document Statuses",
        "Active Overdue Notifications",
        "Active Overdue Escalation Level",
    ]
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(headers)

    for application in applications:
        application_id = application["id"]
        stage = application.get("stage")
        due_value = application.get("due_date")
        due = parse_date(due_value)
        completed = is_completed(application)

        days_overdue = ""
        days_until_deadline = ""
        if completed:
            deadline_state = "Completed"
        elif due_value in (None, ""):
            deadline_state = "Not set"
        elif due is None:
            deadline_state = "Unknown"
        elif due < today:
            deadline_state = "Overdue"
            days_overdue = (today - due).days
        elif due == today:
            deadline_state = "Due today"
            days_until_deadline = 0
        else:
            deadline_state = "Upcoming"
            days_until_deadline = (due - today).days

        stage_entry = get_application_stage_entry_timestamp(
            stage_entries,
            application,
        )
        if stage_entry is None:
            stage_entry_text = "Unknown"
            pending_duration = "Not pending" if completed else "Unknown"
        else:
            stage_entry_local = stage_entry.astimezone(KOLKATA_TIMEZONE)
            stage_entry_text = stage_entry_local.isoformat(timespec="seconds")
            if completed:
                pending_duration = "Not pending"
            elif stage_entry_local.date() > today:
                stage_entry_text = "Unknown"
                pending_duration = "Unknown"
            else:
                pending_duration = (today - stage_entry_local.date()).days

        documents = list_application_documents(application_id)
        compliance = calculate_compliance_summary(documents, settings, today)
        risk = calculate_workflow_risk(
            application,
            documents,
            settings,
            stage_entry,
            today,
        )
        if documents:
            document_statuses = "; ".join(
                f"{document.get('document_type') or 'Unknown'}: "
                f"{document.get('status') or 'Unknown'} "
                f"(renewal: {document.get('renewal_status') or 'Unknown'})"
                for document in documents
            )
        else:
            document_statuses = "No documents recorded"

        overdue_notification = active_overdue_notifications.get(application_id)
        notification_count = (
            overdue_notification["notification_count"]
            if overdue_notification
            else "None"
        )
        escalation_level = (
            overdue_notification["escalation_level"]
            if overdue_notification
            else "None"
        )
        row = [
            application_id,
            application.get("institution") or "Unknown",
            application.get("officer") or "Unknown",
            application.get("status") or "Unknown",
            stage or "Unknown",
            due_value or "Not set",
            deadline_state,
            days_overdue,
            days_until_deadline,
            stage_entry_text,
            pending_duration,
            risk["risk_score"] if risk["risk_score"] is not None else "Unknown",
            risk["risk_level"] or "Unknown",
            compliance["state"] or "Unknown",
            compliance["document_count"],
            document_statuses,
            notification_count,
            escalation_level,
        ]
        writer.writerow([safe_csv_cell(value) for value in row])

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="pendency-report.csv"'},
    )


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
    require_assigned_workflow_route(application)
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
    require_assigned_workflow_route(application)

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
