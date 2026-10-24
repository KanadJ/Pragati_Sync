# AICTE Approval & Compliance Management System

Problem Statement: 26262

## Overview

A React + Vite frontend and FastAPI backend prototype for approval
workflows, application monitoring, and compliance dates. The backend stores
application records and audit events in SQLite (`backend/aicte.db`).

## Authentication and authorization

The API uses bearer tokens issued by `POST /api/auth/login`. Tokens are held
in server memory and become invalid when the backend process restarts. Login
is required for application, workflow, dashboard, history, audit, and
compliance-monitoring reads.

- **Admin:** read access; may configure application workflow routes and perform
  application processing and compliance updates.
- **Officer:** read access; may advance or return applications and update
  compliance dates. Cannot configure workflow routes.
- **Viewer:** read-only access; all application and workflow mutations are
  rejected by the backend.
- Requests without a valid session receive `401 Unauthorized`; authenticated
  requests that lack the required role receive `403 Forbidden`.

The current implementation has no per-institution or per-region data scopes:
all authenticated roles can read the same application, audit, and monitoring
data. The root and health-check endpoints remain public.

## Demo accounts

These fixed credentials are for local demonstration only:

| Role | Username | Password |
| --- | --- | --- |
| Admin | `admin` | `Admin@26262` |
| Officer | `officer` | `Officer@26262` |
| Viewer | `viewer` | `Viewer@26262` |

Do not deploy the demo accounts or passwords to a production environment.
Production use requires managed identity, configurable secrets, and an
appropriate session lifecycle.

## Running backend security tests

From the repository root, run:

```powershell
python -m unittest discover -s backend -p "test_*.py"
```

The security tests use an isolated temporary SQLite database and do not
modify `backend/aicte.db`.
