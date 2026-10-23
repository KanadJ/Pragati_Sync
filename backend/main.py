
from fastapi import FastAPI

app = FastAPI(
    title="AICTE Approval & Compliance System",
    description="Backend API for approval workflow and institutional compliance monitoring.",
    version="0.1.0",
)


@app.get("/")
def home():
    return {
        "message": "AICTE Approval & Compliance System API is running",
        "status": "success",
    }


@app.get("/health")
def health_check():
    return {"status": "healthy"}
