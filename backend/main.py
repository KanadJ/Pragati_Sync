
from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="AICTE Approval & Compliance System",
    description="Backend API for approval workflow and institutional compliance monitoring.",
    version="0.1.0",
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

applications = [
    {
        "id": "AICTE-2026-1001",
        "institution": "Western Institute of Technology",
        "region": "West",
        "stage": "Document Verification",
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
        "officer": "S. Patel",
        "date": "2026-10-23",
        "status": "Under Review",
        "reason": None,
        "history": [],
    },
]


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

@app.post(
    "/api/applications/{application_id}/advance",
    responses={
        400: {"description": "Application is already completed"},
        404: {"description": "Application not found"},
    },
)
def advance_application(application_id: str):
    for application in applications:
        if application["id"] == application_id:
            stages = [
                "Application Review",
                "Document Verification",
                "Final Approval",
                "Completed",
            ]

            current_stage = application["stage"]

            if current_stage == "Completed":
                raise HTTPException(
                    status_code=400,
                    detail="Application is already completed",
                )

            current_index = stages.index(current_stage)
            next_stage = stages[current_index + 1]

            application["stage"] = next_stage

            if next_stage == "Completed":
                application["status"] = "Approved"
            else:
                application["status"] = "Under Review"

            return {
                "message": "Application stage advanced successfully",
                "application": application,
            }

    raise HTTPException(
        status_code=404,
        detail="Application not found",
    )

@app.post(
    "/api/applications/{application_id}/return",
    responses={
        400: {"description": "Invalid return request"},
        404: {"description": "Application not found"},
    },
)
def return_application(application_id: str, payload: dict = Body(...)):
    reason = payload.get("reason", "").strip()

    if not reason:
        raise HTTPException(
            status_code=400,
            detail="A reason is required to return an application",
        )

    for application in applications:
        if application["id"] == application_id:
            if application["stage"] == "Completed" or application["status"] == "Approved":
                raise HTTPException(
                    status_code=400,
                    detail="An approved application cannot be returned",
                )

            application["status"] = "Returned"
            application["reason"] = reason

            return {
                "message": "Application returned successfully",
                "application": application,
            }

    raise HTTPException(
        status_code=404,
        detail="Application not found",
    )
