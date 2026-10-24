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

## Workflow routing and application processing

Admins can create applications from the Applications page or through
`POST /api/applications`. Creation reuses the existing JSON record fields:
`id`, `institution`, `region`, `officer`, and `date` are required; the
`requires_scrutiny`, `requires_evc`, and `requires_sac_shc` demo routing
attributes are optional booleans. The API initializes new records as
`Pending` at `Application Review`, with no route or routing decision assigned.
It records an `Application created` event in the existing audit table and
rejects duplicate IDs without overwriting the existing application.

The creation event alone does not prevent initial automatic or manual route
assignment; any subsequent workflow/audit event locks route changes. No
database schema changes are needed.

`POST /api/applications/{application_id}/auto-route` assigns a workflow for an
application that is still pending at its first stage and has no audit
history. It is Admin-only. Routing rules and their order are configured in
`backend/routing_rules.json`; the first matching rule wins, otherwise the
configured default route is used. Each automatic decision stores its rule ID,
reason, matched attributes, timestamp, and actor in the application record
and an audit event. Admins may also assign a route manually before processing
begins.

The bundled routes and routing conditions are **demo rules only**, not
official AICTE/APH rules. The sample rules use boolean application attributes
such as `requires_evc` and `requires_scrutiny`. Validate the rules, route
stages, and inputs against current official AICTE/APH guidance before any
operational use. Applications created by other intake mechanisms must call
the routing endpoint before processing; this prototype has no application
creation/intake API.

Admin and Officer may advance an application only through the next stage of
its assigned route. Routes cannot be changed after processing or audit
history begins. Returning an application requires a reason. A returned
application must be explicitly resubmitted by Admin or Officer before its
workflow can continue; the return reason remains in the audit history.
Viewers cannot route, process, return, or resubmit applications.

Routing metadata is stored inside the existing application JSON record and
workflow events use the existing audit table. No database schema changes or
migrations are required for this feature.

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
