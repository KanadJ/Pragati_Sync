# AICTE Approval & Compliance Management System

Problem Statement: 26262

## Overview

A React + Vite frontend and FastAPI backend prototype for approval
workflows, application monitoring, and compliance dates. The backend stores
application records and audit events in SQLite (`backend/aicte.db`).

## Authentication and authorization

The browser opens directly into the dashboard and requests a server-issued
demo session from `POST /api/auth/demo-session`; no username or password is
requested by the frontend. The backend continues to require a bearer token
for application, workflow, dashboard, history, audit, and
compliance-monitoring reads. Demo sessions are held in server memory and
become invalid when the backend process restarts. The separate
`POST /api/auth/login` API remains available for password-authenticated
clients.

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

### Dashboard-first hackathon demo mode

The dashboard-first frontend always uses the demo-session API. Set both
`PRAGATISYNC_ENVIRONMENT=demo` and
`PRAGATISYNC_DEMO_MODE_ENABLED=true` in the backend environment to allow
demo sessions. Without both backend settings, the app shows a connection
error and a retry option rather than a login page.

For local development, start the frontend with `npm run dev:demo` from
`frontend/`; create a demo production bundle with `npm run build:demo`.
These commands load `frontend/.env.demo`, which points to the local backend
default at `http://127.0.0.1:8000`. Set `VITE_API_URL` in the build
environment to the reachable backend URL when using another server. The
backend must still be started with both demo environment gates enabled.

The frontend requests a server-issued Viewer session on startup; the visible
**Demo Role** selector can request Admin, Officer, or Viewer sessions through
`POST /api/auth/demo-session`. The backend accepts only those roles, stores
the role in its in-memory session, updates the current demo session when
switching roles, and continues applying the existing endpoint role checks.
Demo sessions are rejected if the backend demo gates are disabled.

This mode intentionally does not authenticate a person's identity: anyone
who can reach a demo-enabled backend can select any demo role, including
Admin. The selector is a demonstration convenience, not production
authorization. Keep the backend gates disabled outside an isolated,
non-production environment and use non-sensitive demo data.

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

## EVC scheduling and tracking

The EVC Tracking page lists only applications whose configured route includes
an `EVC` stage. Applications without an assigned EVC route are not shown.
Eligible applications without a tracking record display as `Pending
Scheduling`; schedule/reschedule, delayed, and completed state is persisted in
the additive SQLite `evc_tracking` table.

Authenticated roles may read `GET /api/evc`. Admin and Officer may schedule or
reschedule an EVC using
`POST /api/applications/{application_id}/evc/schedule` with a valid
`scheduled_date` (`YYYY-MM-DD`) and optional notes. The same endpoint
reschedules an existing, incomplete visit. They may mark a scheduled visit
completed through `POST /api/applications/{application_id}/evc/complete`,
including optional completion notes. Viewers cannot modify EVC records.
Scheduling, rescheduling, and completion are recorded in the existing
application audit trail with the acting username and timestamp.

The upcoming display window is configured by
`evc_tracking.upcoming_window_days` in `backend/compliance_settings.json` (30
days in the demo configuration). A scheduled visit with a past date is
reported as `Delayed` when the tracking list is fetched; this is derived from
the date. The periodic compliance checker also creates deduplicated reminders
for configured EVC lead windows and overdue scheduled dates; it does not
automatically schedule or reschedule visits. The application may reschedule
delayed cases. The configured window is a demo usability setting, not an
official AICTE processing deadline.

## Explainable workflow risk assessment

`GET /api/monitoring` includes a deterministic, rule-based `risk_score`
(0–100 or `null` when no factor can be assessed), `risk_level`,
`risk_factors`, `risk_recommendations`, and `risk_data_gaps` for each
application, plus counts by risk level. This is not machine-learning
prediction and is not an official AICTE risk determination. It is read-only
and follows the endpoint's existing authenticated access for Admin, Officer,
and Viewer roles.

The scoring settings are in `backend/compliance_settings.json` under
`workflow_risk_assessment`. Default factor caps are deadline 40, current-stage
age 25, and compliance 35 points; the caps must total 100. An overdue deadline
starts at 30 points and adds 2 points per overdue day up to 40. Upcoming
deadline bands score 25 points at 0–3 days, 20 at 4–7, 12 at 8–14, and 5 at
15–30 days. Current-stage age scores 10 at 7–13 days, 18 at 14–29, and 25 at
30 or more days. Completed applications do not accrue deadline or stage-age
risk. Existing custom settings files that omit the new section continue to
use these validated code defaults.

Compliance scores each document only once using its highest applicable
signal: expired 25, awaiting renewal or rejected 18, expiring within the
configured expiry-warning period 10, or awaiting verification 8. Each
configured required document type without a document record adds 15; the
whole compliance category is capped at 35. When document-register entries
exist, the legacy application-level document expiry is not separately scored
to avoid potentially counting the same document twice.

Risk bands are Low 0–24, Moderate 25–49, High 50–74, and Critical 75–100.
Invalid or missing deadlines, unavailable stage-entry timestamps, absent
document expiry dates, and unconfigured required-document types are surfaced
in `risk_data_gaps`; they are not silently interpreted as positive evidence
of safety. If no factor can be assessed, the score is `null` and level is
`Unknown`. Stage duration uses the latest matching stage-entry audit event
(`Application created`, workflow assignment, or `Stage advanced`); no
submission date is substituted when that audit evidence is absent. The
default weights, bands, and point values are demo policy and should be
reviewed for the intended workflow before operational use.

## Pendency report and CSV export

Authenticated Admin, Officer, and Viewer roles can download
`GET /api/reports/pendency.csv` from the Monitoring page. The report includes
application identity, institution, assigned officer, status, workflow stage,
deadline state and relevant day counts, the valid current-stage audit entry
timestamp and pending duration when available, prototype risk score/level,
prototype document compliance summary, document verification/renewal states,
and active overdue-notification counts/escalation level.

Deadline states distinguish `Overdue`, `Due today`, `Upcoming`, `Completed`,
`Not set`, and `Unknown` for invalid dates. Completed applications are not
labelled actively overdue. Pending duration is calculated from the matching
current-stage audit timestamp; if no reliable timestamp exists it is reported
as `Unknown`. Dates use the Asia/Kolkata calendar. CSV cells are serialized
with Python's CSV writer and formula-like values, including values preceded by
whitespace/control characters, are neutralized.

Risk and compliance columns are configurable prototype assessments and are
not verified official AICTE/APH compliance determinations. The report does not
invent missing due dates, stage timestamps, document statuses, or escalation
records. It is read-only and uses existing SQLite data; it introduces no
schema changes.

## Document register and compliance notifications

Application detail panels include a document register, and the Document
Tracking page lists documents across applications. Admins and Officers can add
or update document type, name, issue date, verification status, expiry date,
and reviewer remarks; Viewers can read records and summaries only. Supported
verification statuses are `Pending`, `Submitted`, `Verified`, and `Rejected`.
Submission is not verification, and a rejection must include reviewer remarks.
No document files or sensitive document contents are stored.

`GET /api/applications/{application_id}/documents` returns the document
register and its compliance summary. The summary states are `Missing`,
`Pending Verification`, `Rejected`, `Expiring Soon`, `Expired`, and
`Compliant`; an empty register or an unverified document is never compliant.
`GET /api/applications/{application_id}/compliance-summary` returns just the
summary. `GET /api/documents` lists documents across applications with their
tracking and verification states. Tracking states include `Valid`,
`Expiring Soon`, `Expired`, `Awaiting Renewal`, `Submitted for Renewal`, and
`Awaiting Verification`.

Admins and Officers submit renewal metadata (issue date, required expiry date,
and optional remarks) using
`POST /api/applications/{application_id}/documents/{document_id}/renewal`.
The existing expiry remains effective until verification. An Admin or Officer
can approve or reject a submitted renewal using
`POST /api/applications/{application_id}/documents/{document_id}/renewal/verify`
with `{"decision":"Approved"}` or `{"decision":"Rejected","remarks":"..."}`.
Rejection remarks are required. Approval applies the submitted issue/expiry
dates and marks the document `Verified`; rejection returns it to `Awaiting
Renewal`. Submission, verification, and renewal-required transitions are
recorded in the application audit trail with actor and timestamp.

Warning windows, required document types, and escalation thresholds are
configured in `backend/compliance_settings.json`. These are **demo settings**,
not validated AICTE requirements. The default has a 30-day expiry warning,
no configured required document types, and escalation thresholds at 3 and 7
days. With no authoritative required-document list configured, `Compliant`
only describes the documents recorded in the prototype as verified; it does
not certify institutional compliance.

`GET /api/notifications` lists persisted in-app notifications that have not
been viewed by the current user. Admin and Officer may mark a notification
viewed through `POST /api/notifications/{notification_id}/read`; Viewers can
read the list but cannot change viewed status. Viewed state is stored per user,
so dismissing a notification does not hide it from other users. Notifications
remain in the database, and a newly triggered event for a resolved notification
makes it visible again. Document rejection/expiry, application deadline reminders,
overdue deadlines, and configured-route EVC reminders are checked when relevant
document/compliance data changes, during startup catch-up, and periodically by
the backend. Admins can also invoke `POST /api/compliance/check` to run a check
immediately. Reminder intervals and the periodic interval are configurable in
`backend/compliance_settings.json` under `notification_schedule`; the demo
defaults are 7, 3, and 1 calendar days before deadlines/EVC visits, checked
hourly. Each check uses the most urgent reached reminder window, so a missed
check does not emit every older reminder interval at once. Overdue escalation
thresholds are calendar days after the notification is created in Asia/Kolkata;
the existing demo thresholds are 3 and 7 days.

Notifications are deduplicated by issue, date, and reminder interval. Changing
an application deadline or EVC scheduled date resolves the old active reminder;
completed EVC visits resolve their active reminders. Escalation level changes
and resolution are conditional updates so repeated checks do not duplicate
their audit actions. Notification and per-user dismissal records are retained.
Timestamps are stored in UTC, while date boundaries and displayed audit,
notification, and document timestamps use Asia/Kolkata.

The periodic checker runs as an asyncio task in each FastAPI process and runs
a catch-up check during startup. It uses the existing SQLite database and does
not require a dashboard request. This is suitable for the current single-process
prototype, not a guarantee of production scheduling: multiple server instances
may perform overlapping scans, and durability depends on the deployed SQLite
storage surviving restarts. The repository does not configure or verify a
persistent Render disk or multi-instance coordination. Email, SMS, and external
recipient escalation are not implemented. The `documents`, `notifications`,
and per-user `notification_views` tables and their indexes are created
idempotently at startup; existing application, notification, and audit records
are preserved, and this feature requires no database reset.

## Demo account behavior

The browser dashboard does not request or use username/password credentials.
The legacy password-login API is separate from the dashboard-first demo
interface. Production use requires managed identity, configurable secrets,
and an appropriate session lifecycle.

## Temporary Render demo configuration

The backend can be started from the `backend` directory with
`uvicorn main:app --host 0.0.0.0 --port $PORT`. Set `CORS_ORIGINS` to a
comma-separated list containing the deployed frontend origin, for example
`https://your-frontend.onrender.com`; localhost development origins remain
allowed. The frontend accepts the backend URL through the Vite build-time
variable `VITE_API_URL`, for example
`https://your-backend.onrender.com`, and falls back to
`http://localhost:8000` for local development.

This SQLite-backed configuration is for a temporary demonstration: Render
free-service restarts may discard database changes and in-memory login or
demo sessions. Do not use it for durable or sensitive production data.

## Running backend security tests

From the repository root, run:

```powershell
python -m unittest discover -s backend -p "test_*.py"
```

The security tests use an isolated temporary SQLite database and do not
modify `backend/aicte.db`.
