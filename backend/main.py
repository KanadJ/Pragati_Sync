
from copy import deepcopy

from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI(
    title="AICTE Approval & Compliance System",
    description=(
        "Backend API for approval workflow "
        "and institutional compliance monitoring."
    ),
    version="0.2.0",
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

# Prototype workflow configuration.
# Confirm the applicable APH rules before using routes
# as official AICTE approval procedures.
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


def make_history_event(action, stage, details=None):
    """Create a consistent audit-history event."""
    event = {
        "action": action,
        "stage": stage,
        "details": details,
    }
    return event


applications = [
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


def find_application(application_id):
    """Find an application or raise a 404 error."""
    for application in applications:
        if application["id"] == application_id:
            return application

    raise HTTPException(
        status_code=404,
        detail="Application not found",
    )


def get_application_route(application):
    """Return the configured route for an application."""
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
    return applications


@app.get("/api/workflows")
def get_workflows():
    """List the configured prototype workflow routes."""
    return {
        "workflows": WORKFLOW_ROUTES,
    }


@app.post("/api/applications/{application_id}/workflow")
def assign_workflow(application_id: str, payload: dict = Body(...)):
    """Assign a route without silently changing the current stage."""
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

    # Only allow assignment when the current stage exists in
    # the selected route. This prevents accidental stage resets.
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
    current_index = stages.index(current_stage)

    if current_stage == "Completed":
        raise HTTPException(
            status_code=400,
            detail="Application is already completed",
        )

    next_stage = stages[current_index + 1]

    was_returned = application["status"] == "Returned"
    previous_reason = application.get("reason")

    application["stage"] = next_stage
    application["reason"] = None

    if next_stage == "Completed":
        application["status"] = "Approved"
    else:
        application["status"] = "Under Review"

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

    return {
        "message": "Application returned successfully",
        "application": application,
    }
