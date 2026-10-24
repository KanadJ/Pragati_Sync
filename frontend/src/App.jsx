
import { useEffect, useMemo, useState } from 'react'
import './App.css'

const initialApplications = [
  { id: 'AICTE-2026-1001', institution: 'Western Institute of Technology', region: 'West', stage: 'Document Verification', officer: 'A. Sharma', date: '2026-10-20', status: 'Under Review', history: [] },
  { id: 'AICTE-2026-1002', institution: 'National College of Engineering', region: 'South', stage: 'Final Approval', officer: 'R. Kumar', date: '2026-10-18', status: 'Pending', history: [] },
  { id: 'AICTE-2026-1003', institution: 'Central Technical Institute', region: 'North', stage: 'Application Review', officer: 'S. Patel', date: '2026-10-17', status: 'Returned', reason: 'Please upload the signed lease document.', history: [] },
  { id: 'AICTE-2026-1004', institution: 'Eastern School of Technology', region: 'East', stage: 'Completed', officer: 'A. Sharma', date: '2026-10-15', status: 'Approved', history: [] },
  { id: 'AICTE-2026-1005', institution: 'City Institute of Computing', region: 'West', stage: 'Application Review', officer: 'R. Kumar', date: '2026-10-22', status: 'Pending', history: [] },
  { id: 'AICTE-2026-1006', institution: 'Greenfield Polytechnic', region: 'South', stage: 'Document Verification', officer: 'S. Patel', date: '2026-10-23', status: 'Under Review', history: [] },
]

const stages = [
  'Application Review',
  'Document Verification',
  'Final Approval',
  'Completed',
]

function App() {
  const [applications, setApplications] = useState(initialApplications)
  const [loading, setLoading] = useState(true)
  const [selectedId, setSelectedId] = useState(initialApplications[0].id)
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState('All')
  const [returnReason, setReturnReason] = useState('')
  const [showReturn, setShowReturn] = useState(false)
  const [notice, setNotice] = useState('')
  const [page, setPage] = useState('Dashboard')

  // Load applications from the backend.
  useEffect(() => {
    fetch('http://localhost:8000/api/applications')
      .then((response) => {
        if (!response.ok) {
          throw new Error('Failed to fetch applications')
        }
        return response.json()
      })
      .then((data) => {
        setApplications(data)
      })
      .catch((error) => {
        console.error('API connection error:', error)
        setNotice('Could not load applications from the backend.')
      })
      .finally(() => {
        setLoading(false)
      })
  }, [])

  const selected = applications.find((app) => app.id === selectedId)

  const filtered = useMemo(
    () =>
      applications.filter((app) => {
        const matchesSearch =
          `${app.id} ${app.institution} ${app.officer} ${app.region}`
            .toLowerCase()
            .includes(search.toLowerCase())

        return matchesSearch && (filter === 'All' || app.status === filter)
      }),
    [applications, search, filter]
  )

  const counts = {
    total: applications.length,
    pending: applications.filter((app) =>
      ['Pending', 'Under Review'].includes(app.status)
    ).length,
    approved: applications.filter((app) => app.status === 'Approved').length,
    returned: applications.filter((app) => app.status === 'Returned').length,
  }

  // Advance an application or return it with a reason.
  async function updateApplication(id, action, reason = '') {
    const app = applications.find((item) => item.id === id)
    if (!app) return

    if (action === 'advance' && app.status === 'Approved') {
      setNotice('This application is already approved.')
      return
    }

    if (action === 'advance' && app.stage === 'Completed') {
      setNotice('This application has completed all stages.')
      return
    }

    const endpoint =
      action === 'advance' ? 'advance' : 'return'

    try {
      const options =
        action === 'advance'
          ? { method: 'POST' }
          : {
              method: 'POST',
              headers: {
                'Content-Type': 'application/json',
              },
              body: JSON.stringify({ reason }),
            }

      const response = await fetch(
        `http://localhost:8000/api/applications/${encodeURIComponent(id)}/${endpoint}`,
        options
      )

      const data = await response.json()

      if (!response.ok) {
        throw new Error(
          data.detail || 'The application could not be updated.'
        )
      }

      const updatedApp = data.application
      const description =
        action === 'advance'
          ? `Moved to ${updatedApp.stage}`
          : `Returned: ${reason}`

      setApplications((current) =>
        current.map((item) =>
          item.id === id
            ? {
                ...item,
                ...updatedApp,
                history: [
                  ...(item.history || []),
                  {
                    description,
                    time: new Date().toLocaleString(),
                  },
                ],
              }
            : item
        )
      )

      setShowReturn(false)
      setReturnReason('')
      setNotice(
        action === 'advance'
          ? 'Application stage updated successfully.'
          : 'Application returned successfully.'
      )
    } catch (error) {
      setNotice(
        error.message || 'Could not connect to the backend.'
      )
    }
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">A</div>
          <div>
            <strong>AICTE Portal</strong>
            <span>Approval &amp; Compliance</span>
          </div>
        </div>

        <div className="nav-label">WORKSPACE</div>

        {[
          ['Dashboard', '▦'],
          ['Applications', '▤'],
          ['Monitoring', '◷'],
          ['Audit Trail', '≡'],
        ].map(([name, symbol]) => (
          <button
            key={name}
            className={`nav-item ${page === name ? 'active' : ''}`}
            onClick={() => {
              setPage(name)
              setNotice('')
            }}
          >
            <span className="nav-symbol">{symbol}</span>
            {name}
          </button>
        ))}

        <div className="sidebar-bottom">
          <div className="online-dot" />
          <div>
            <strong>Prototype environment</strong>
            <span>Sample data · Local session</span>
          </div>
        </div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div className="breadcrumb">
            AICTE <span>/</span> {page}
          </div>

          <div className="user-chip">
            <div className="avatar">AD</div>
            <div>
              <strong>Administrator</strong>
              <span>System Admin</span>
            </div>
          </div>
        </header>

        <section className="page-content">
          {loading && (
            <div className="notice" role="status">
              Loading applications...
            </div>
          )}

          <div className="page-heading">
            <div>
              <div className="eyebrow">APPROVAL MANAGEMENT SYSTEM</div>
              <h1>
                {page === 'Dashboard' ? 'Application Dashboard' : page}
              </h1>
              <p>
                Monitor institutional applications and manage approval
                workflows.
              </p>
            </div>

            <button
              className="primary-button"
              onClick={() => {
                setPage('Applications')
                setNotice(
                  'Select an application below to review its workflow.'
                )
              }}
            >
              View applications <span>→</span>
            </button>
          </div>

          {notice && (
            <div className="notice" role="status">
              <span>{notice}</span>
              <button
                onClick={() => setNotice('')}
                aria-label="Dismiss"
              >
                ×
              </button>
            </div>
          )}

          {page === 'Dashboard' && (
            <>
              <div className="stats-grid">
                <Stat
                  label="Total applications"
                  value={counts.total}
                  note="Across all regions"
                  symbol="▤"
                />
                <Stat
                  label="Pending action"
                  value={counts.pending}
                  note="Awaiting processing"
                  symbol="◷"
                />
                <Stat
                  label="Approved"
                  value={counts.approved}
                  note="Completed applications"
                  symbol="✓"
                />
                <Stat
                  label="Returned"
                  value={counts.returned}
                  note="Requires correction"
                  symbol="↶"
                />
              </div>

              <div className="section-heading">
                <div>
                  <h2>Application register</h2>
                  <p>
                    Search, filter and select an application to review.
                  </p>
                </div>
                <span className="record-count">
                  {filtered.length} records
                </span>
              </div>

              <ApplicationFilters
                search={search}
                setSearch={setSearch}
                filter={filter}
                setFilter={setFilter}
              />

              <ApplicationTable
                rows={filtered}
                selectedId={selectedId}
                onSelect={setSelectedId}
              />
            </>
          )}

          {page === 'Applications' && (
            <>
              <div className="section-heading">
                <div>
                  <h2>All applications</h2>
                  <p>Choose a record to open its review panel.</p>
                </div>
              </div>

              <ApplicationFilters
                search={search}
                setSearch={setSearch}
                filter={filter}
                setFilter={setFilter}
              />

              <ApplicationTable
                rows={filtered}
                selectedId={selectedId}
                onSelect={setSelectedId}
              />
            </>
          )}

          {page === 'Monitoring' && (
            <div className="panel monitoring-panel">
              <h2>Workflow monitoring</h2>
              <p className="muted">
                Current application distribution by stage.
              </p>

              {stages.map((stage) => {
                const amount = applications.filter(
                  (app) => app.stage === stage
                ).length

                return (
                  <div className="stage-row" key={stage}>
                    <div>
                      <span>{stage}</span>
                      <strong>{amount}</strong>
                    </div>
                    <div className="progress-track">
                      <div
                        style={{
                          width: `${
                            applications.length
                              ? (amount / applications.length) * 100
                              : 0
                          }%`,
                        }}
                      />
                    </div>
                  </div>
                )
              })}
            </div>
          )}

          {page === 'Audit Trail' && (
            <div className="panel">
              <h2>Recent activity</h2>
              <p className="muted">
                Workflow changes recorded during this session.
              </p>

              {applications.flatMap((app) =>
                (app.history || []).map((event, index) => ({
                  ...event,
                  id: app.id,
                  institution: app.institution,
                  key: `${app.id}-${index}`,
                }))
              ).length === 0 ? (
                <div className="empty-state">
                  No workflow actions have been recorded yet. Advance or
                  return an application to see its history here.
                </div>
              ) : (
                applications
                  .flatMap((app) =>
                    (app.history || []).map((event, index) => ({
                      ...event,
                      id: app.id,
                      institution: app.institution,
                      key: `${app.id}-${index}`,
                    }))
                  )
                  .reverse()
                  .map((event) => (
                    <div className="audit-row" key={event.key}>
                      <div className="audit-icon">↻</div>
                      <div>
                        <strong>{event.description}</strong>
                        <p>
                          {event.id} · {event.institution}
                        </p>
                        <span>{event.time}</span>
                      </div>
                    </div>
                  ))
              )}
            </div>
          )}

          <footer>
            AICTE Approval &amp; Compliance System
            <span>Prototype · Demonstration data only</span>
          </footer>
        </section>
      </main>

      {selected && (
        <div
          className="detail-overlay"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) {
              setSelectedId('')
            }
          }}
        >
          <section
            className="detail-panel"
            aria-label="Application details"
          >
            <div className="detail-header">
              <div>
                <div className="eyebrow">APPLICATION DETAILS</div>
                <h2>Review application</h2>
              </div>
              <button
                className="close-button"
                onClick={() => setSelectedId('')}
                aria-label="Close panel"
              >
                ×
              </button>
            </div>

            <div className="detail-id">{selected.id}</div>
            <h3>{selected.institution}</h3>
            <Status status={selected.status} />

            <div className="detail-grid">
              <Detail label="Region" value={selected.region} />
              <Detail
                label="Assigned officer"
                value={selected.officer}
              />
              <Detail
                label="Submission date"
                value={selected.date}
              />
              <Detail
                label="Current stage"
                value={selected.stage}
              />
            </div>

            {selected.reason && (
              <div className="reason-box">
                <strong>Return reason</strong>
                <p>{selected.reason}</p>
              </div>
            )}

            <div className="workflow-section">
              <h3>Workflow progress</h3>

              {stages.map((stage, index) => {
                const currentIndex = stages.indexOf(selected.stage)

                return (
                  <div
                    className={`workflow-step ${
                      index <= currentIndex ? 'step-done' : ''
                    }`}
                    key={stage}
                  >
                    <div className="step-marker">
                      {index < currentIndex ? '✓' : index + 1}
                    </div>
                    <div>
                      <strong>{stage}</strong>
                      <span>
                        {index < currentIndex
                          ? 'Completed'
                          : index === currentIndex
                            ? 'Current stage'
                            : 'Upcoming'}
                      </span>
                    </div>
                  </div>
                )
              })}
            </div>

            {showReturn && (
              <div className="return-form">
                <label htmlFor="return-reason">
                  Reason for returning application *
                </label>
                <textarea
                  id="return-reason"
                  value={returnReason}
                  onChange={(e) => setReturnReason(e.target.value)}
                  placeholder="Explain what needs correction..."
                  rows={3}
                />

                <div className="form-actions">
                  <button
                    className="secondary-button"
                    onClick={() => {
                      setShowReturn(false)
                      setReturnReason('')
                    }}
                  >
                    Cancel
                  </button>

                  <button
                    className="danger-button"
                    disabled={!returnReason.trim()}
                    onClick={() =>
                      updateApplication(
                        selected.id,
                        'return',
                        returnReason.trim()
                      )
                    }
                  >
                    Confirm return
                  </button>
                </div>
              </div>
            )}

            {!showReturn && (
              <div className="detail-actions">
                <button
                  className="secondary-button"
                  disabled={selected.stage === 'Completed'}
                  onClick={() => setShowReturn(true)}
                >
                  Return application
                </button>

                <button
                  className="primary-button"
                  disabled={selected.stage === 'Completed'}
                  onClick={() =>
                    updateApplication(selected.id, 'advance')
                  }
                >
                  {selected.stage === 'Final Approval'
                    ? 'Approve & complete'
                    : 'Complete stage →'}
                </button>
              </div>
            )}

            <p className="prototype-note">
              Prototype only: workflow changes are sent to the backend.
              Data is held in backend memory and resets when the server
              restarts.
            </p>
          </section>
        </div>
      )}
    </div>
  )
}

function Stat({ label, value, note, symbol }) {
  return (
    <div className="stat-card">
      <div className="stat-top">
        <span>{label}</span>
        <div className="stat-symbol">{symbol}</div>
      </div>
      <div className="stat-value">{value}</div>
      <div className="stat-note">{note}</div>
    </div>
  )
}

function Status({ status }) {
  const className = status.toLowerCase().replaceAll(' ', '-')

  return (
    <span className={`status-badge ${className}`}>
      <span />
      {status}
    </span>
  )
}

function Detail({ label, value }) {
  return (
    <div className="detail-item">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function ApplicationFilters({
  search,
  setSearch,
  filter,
  setFilter,
}) {
  return (
    <div className="filters">
      <div className="search-box">
        <span>⌕</span>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search ID, institution, officer..."
        />
      </div>

      <select
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        aria-label="Filter by status"
      >
        {['All', 'Pending', 'Under Review', 'Approved', 'Returned'].map(
          (status) => (
            <option key={status} value={status}>
              {status === 'All' ? 'All statuses' : status}
            </option>
          )
        )}
      </select>
    </div>
  )
}

function ApplicationTable({ rows, selectedId, onSelect }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>APPLICATION / INSTITUTION</th>
            <th>STAGE</th>
            <th>OFFICER</th>
            <th>STATUS</th>
            <th />
          </tr>
        </thead>

        <tbody>
          {rows.map((app) => (
            <tr
              key={app.id}
              className={selectedId === app.id ? 'selected-row' : ''}
            >
              <td>
                <button
                  className="application-link"
                  onClick={() => onSelect(app.id)}
                >
                  {app.id}
                </button>
                <div className="institution-name">
                  {app.institution}
                </div>
              </td>
              <td>{app.stage}</td>
              <td>
                {app.officer}
                <div className="institution-name">
                  {app.region} Region
                </div>
              </td>
              <td>
                <Status status={app.status} />
              </td>
              <td>
                <button
                  className="open-button"
                  onClick={() => onSelect(app.id)}
                  aria-label={`Review ${app.id}`}
                >
                  →
                </button>
              </td>
            </tr>
          ))}

          {rows.length === 0 && (
            <tr>
              <td colSpan="5" className="empty-state">
                No applications match your search.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

export default App
