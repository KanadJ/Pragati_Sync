
import json
import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI(
    title="AICTE Approval & Compliance System",
    description=(
        "Backend API for approval workflow "
        "and institutional compliance monitoring."
    ),
    version="0.3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Store the database beside this Python file.
DATABASE_PATH = Path(__file__).resolve().parent / "aicte.db"

# Prototype configuration. Verify official APH rules before
# treating these routes as authoritative AICTE procedures.
WORKFLOW_ROUTES = {
    "STANDARD": [
        "Application Review",
        "Document Verification",
        "Final Approval",
        "Completed",
    ],
    "SCRUTINY_ONLY": [
        "Scrutiny",
        "Completed",
    ],
    "SCRUTINY_EVC": [
        "Scrutiny",
        "EVC",
        "Completed",
    ],
    "EVC_SAC_SHC": [
        "EVC",
        "SAC/SHC",
        "Completed",
    ],
    "SCRUTINY_EVC_SAC_SHC": [
        "Scrutiny",
        "EVC",
        "SAC/SHC",
        "Completed",
    ],
}

# Seed data is inserted only when the database is first initialized.
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


def connect_db():
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database():
    """Create the table and seed data only when it is empty."""
    with connect_db() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS applications (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
            """
        )

        count = connection.execute(
            "SELECT COUNT(*) FROM applications"
        ).fetchone()[0]

        if count == 0:
            connection.executemany(
                "INSERT INTO applications (id, data) VALUES (?, ?)",
                [
                    (
                        application["id"],
                        json.dumps(application),
                    )
                    for application in INITIAL_APPLICATIONS
                ],
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
        raise HTTPException(
            status_code=404,
            detail="Application not found",
        )

    return json.loads(row["data"])


def save_application(application):
    """Persist the complete application, including its history."""
    with connect_db() as connection:
        connection.execute(
            """
            INSERT INTO applications (id, data)
            VALUES (?, ?)
            ON CONFLICT(id) DO UPDATE SET data = excluded.data
            """,
            (
                application["id"],
                json.dumps(application),
            ),
        )


def make_history_event(action, stage, details=None):
    return {
        "action": action,
        "stage": stage,
        "details": details,
    }


def get_application_route(application):
    route_id = application.get("workflow_route", "STANDARD")
    stages = WORKFLOW_ROUTES.get(route_id)

    if stages is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown workflow route: {route_id}",
        )

    if application["stage"] not in stages:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Stage '{application['stage']}' is not part "
                f"of workflow route '{route_id}'"
            ),
        )

    return stages


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
    return {"status": "healthy"}


@app.get("/api/applications")
def get_applications():
    return get_all_applications()


@app.get("/api/workflows")
def get_workflows():
    return {"workflows": WORKFLOW_ROUTES}


@app.post("/api/applications/{application_id}/workflow")
def assign_workflow(application_id: str, payload: dict = Body(...)):
    application = find_application(application_id)
    route_id = payload.get("workflow_route", "")

    if route_id not in WORKFLOW_ROUTES:
        raise HTTPException(
            status_code=400,
            detail="Invalid workflow_route",
        )

    if (
        application["stage"] == "Completed"
        or application["status"] == "Approved"
    ):
        raise HTTPException(
            status_code=400,
            detail="Cannot change the workflow of a completed application",
        )

    new_stages = WORKFLOW_ROUTES[route_id]

    if application["stage"] not in new_stages:
        raise HTTPException(
            status_code=400,
            detail=(
                "The current stage is not part of the selected route. "
                "A controlled workflow migration is required."
            ),
        )

    old_route = application.get("workflow_route", "STANDARD")

    if old_route != route_id:
        application["workflow_route"] = route_id
        application["history"].append(
            make_history_event(
                "Workflow changed",
                application["stage"],
                f"{old_route} -> {route_id}",
            )
        )
        save_application(application)

    return {
        "message": "Workflow route assigned successfully",
        "application": application,
    }


@app.post(
    "/api/applications/{application_id}/advance",
    responses={
        400: {"description": "Invalid workflow transition"},
        404: {"description": "Application not found"},
    },
)
def advance_application(application_id: str):
    application = find_application(application_id)

    stages = get_application_route(application)
    current_stage = application["stage"]

    if current_stage == "Completed":
        raise HTTPException(
            status_code=400,
            detail="Application is already completed",
        )

    current_index = stages.index(current_stage)
    next_stage = stages[current_index + 1]

    was_returned = application["status"] == "Returned"
    previous_reason = application.get("reason")

    application["stage"] = next_stage
    application["reason"] = None

    application["status"] = (
        "Approved" if next_stage == "Completed" else "Under Review"
    )

    if was_returned:
        application["history"].append(
            make_history_event(
                "Return resolved",
                current_stage,
                previous_reason,
            )
        )

    application["history"].append(
        make_history_event(
            "Stage advanced",
            next_stage,
            f"Previous stage: {current_stage}",
        )
    )

    save_application(application)

    return {
        "message": "Application stage advanced successfully",
        "application": application,
    }


@app.post(
    "/api/applications/{application_id}/return",
    responses={
        400: {"description": "Invalid return request"},
        404: {"description": "Application not found"},
    },
)
def return_application(application_id: str, payload: dict = Body(...)):
    application = find_application(application_id)

    reason = payload.get("reason", "")

    if not isinstance(reason, str) or not reason.strip():
        raise HTTPException(
            status_code=400,
            detail="A reason is required to return an application",
        )

    if (
        application["stage"] == "Completed"
        or application["status"] == "Approved"
    ):
        raise HTTPException(
            status_code=400,
            detail="An approved application cannot be returned",
        )

    if application["status"] == "Returned":
        raise HTTPException(
            status_code=400,
            detail="Application has already been returned",
        )

    reason = reason.strip()
    application["status"] = "Returned"
    application["reason"] = reason

    application["history"].append(
        make_history_event(
            "Application returned",
            application["stage"],
            reason,
        )
    )

    save_application(application)

    return {
        "message": "Application returned successfully",
        "application": application,
    }

