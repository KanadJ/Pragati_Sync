
import { useCallback, useEffect, useMemo, useState } from 'react'
import './App.css'

const API = 'http://localhost:8000'
const FALLBACK_STAGES = [
  'Application Review',
  'Document Verification',
  'Final Approval',
  'Completed',
]

function App() {
  const [applications, setApplications] = useState([])
  const [workflows, setWorkflows] = useState({})
  const [auditEvents, setAuditEvents] = useState([])
  const [monitoring, setMonitoring] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [selectedId, setSelectedId] = useState('')
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState('All')
  const [returnReason, setReturnReason] = useState('')
  const [showReturn, setShowReturn] = useState(false)
  const [dueDate, setDueDate] = useState('')
  const [expiryDate, setExpiryDate] = useState('')
  const [notice, setNotice] = useState('')
  const [page, setPage] = useState('Dashboard')

  const request = useCallback(async (path, options = {}) => {
    const response = await fetch(`${API}${path}`, {
      ...options,
      headers: {
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        ...options.headers,
      },
    })

    const data = await response.json().catch(() => ({}))

    if (!response.ok) {
      throw new Error(data.detail || `Request failed (${response.status})`)
    }

    return data
  }, [])

  const refreshData = useCallback(async () => {
    const [apps, workflowData, auditData, monitoringData] =
      await Promise.all([
        request('/api/applications'),
        request('/api/workflows'),
        request('/api/audit'),
        request('/api/monitoring'),
      ])

    setApplications(apps)
    setWorkflows(workflowData.workflows || {})
    setAuditEvents(auditData.events || [])
    setMonitoring(monitoringData)

    setSelectedId((current) => {
      if (current && apps.some((item) => item.id === current)) return current
      return apps[0]?.id || ''
    })
  }, [request])

  useEffect(() => {
    refreshData()
      .catch((error) => setNotice(`Could not load backend data: ${error.message}`))
      .finally(() => setLoading(false))
  }, [refreshData])

  const selected = applications.find((item) => item.id === selectedId)

  useEffect(() => {
    setDueDate(selected?.due_date || '')
    setExpiryDate(selected?.document_expiry_date || '')
  }, [selectedId, selected?.due_date, selected?.document_expiry_date])

  const filtered = useMemo(() => {
    const query = search.toLowerCase()
    return applications.filter((item) => {
      const matchesSearch =
        `${item.id} ${item.institution} ${item.officer} ${item.region}`
          .toLowerCase()
          .includes(query)

      return matchesSearch && (filter === 'All' || item.status === filter)
    })
  }, [applications, search, filter])

  const counts = {
    total: applications.length,
    pending: applications.filter((item) =>
      ['Pending', 'Under Review', 'Returned'].includes(item.status)
    ).length,
    approved: applications.filter((item) => item.status === 'Approved').length,
    returned: applications.filter((item) => item.status === 'Returned').length,
  }

  async function performAction(action, reason = '') {
    if (!selected || busy) return

    setBusy(true)
    setNotice('')

    try {
      const options = action === 'return'
        ? { method: 'POST', body: JSON.stringify({ reason }) }
        : { method: 'POST' }

      await request(
        `/api/applications/${encodeURIComponent(selected.id)}/${action}`,
        options
      )

      await refreshData()
      setShowReturn(false)
      setReturnReason('')
      setNotice(action === 'advance'
        ? 'Application stage updated successfully.'
        : 'Application returned successfully. Audit history saved.')
    } catch (error) {
      setNotice(error.message)
    } finally {
      setBusy(false)
    }
  }

  async function changeWorkflow(route) {
    if (!selected || busy) return
    setBusy(true)
    setNotice('')

    try {
      await request(
        `/api/applications/${encodeURIComponent(selected.id)}/workflow`,
        { method: 'POST', body: JSON.stringify({ workflow_route: route }) }
      )
      await refreshData()
      setNotice('Workflow route updated successfully.')
    } catch (error) {
      setNotice(error.message)
    } finally {
      setBusy(false)
    }
  }

  async function saveCompliance() {
    if (!selected || busy) return
    setBusy(true)
    setNotice('')

    try {
      const payload = {
        due_date: dueDate || null,
        document_expiry_date: expiryDate || null,
      }

      await request(
        `/api/applications/${encodeURIComponent(selected.id)}/compliance`,
        { method: 'PATCH', body: JSON.stringify(payload) }
      )

      await refreshData()
      setNotice('Compliance dates saved.')
    } catch (error) {
      setNotice(error.message)
    } finally {
      setBusy(false)
    }
  }

  const selectedAudit = auditEvents.filter(
    (event) => event.application_id === selectedId
  )

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
            onClick={() => { setPage(name); setNotice('') }}
          >
            <span className="nav-symbol">{symbol}</span>
            {name}
          </button>
        ))}

        <div className="sidebar-bottom">
          <div className="online-dot" />
          <div>
            <strong>Prototype environment</strong>
            <span>SQLite · Local backend</span>
          </div>
        </div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div className="breadcrumb">AICTE <span>/</span> {page}</div>
          <div className="user-chip">
            <div className="avatar">AD</div>
            <div><strong>Administrator</strong><span>System Admin</span></div>
          </div>
        </header>

        <section className="page-content">
          {loading && <div className="notice">Loading backend data...</div>}

          <div className="page-heading">
            <div>
              <div className="eyebrow">APPROVAL MANAGEMENT SYSTEM</div>
              <h1>{page === 'Dashboard' ? 'Application Dashboard' : page}</h1>
              <p>Monitor institutional applications and manage approval workflows.</p>
            </div>
            <button className="primary-button" onClick={() => setPage('Applications')}>
              View applications <span>→</span>
            </button>
          </div>

          {notice && (
            <div className="notice" role="status">
              <span>{notice}</span>
              <button onClick={() => setNotice('')} aria-label="Dismiss">×</button>
            </div>
          )}

          {page === 'Dashboard' && (
            <>
              <div className="stats-grid">
                <Stat label="Total applications" value={counts.total} note="Across all regions" symbol="▤" />
                <Stat label="Pending action" value={counts.pending} note="Awaiting processing" symbol="◷" />
                <Stat label="Approved" value={counts.approved} note="Completed applications" symbol="✓" />
                <Stat label="Returned" value={counts.returned} note="Requires correction" symbol="↶" />
              </div>

              <div className="section-heading">
                <div><h2>Application register</h2><p>Search, filter and select an application to review.</p></div>
                <span className="record-count">{filtered.length} records</span>
              </div>

              <ApplicationFilters search={search} setSearch={setSearch} filter={filter} setFilter={setFilter} />
              <ApplicationTable rows={filtered} selectedId={selectedId} onSelect={setSelectedId} />
            </>
          )}

          {page === 'Applications' && (
            <>
              <div className="section-heading">
                <div><h2>All applications</h2><p>Choose a record to open its review panel.</p></div>
              </div>
              <ApplicationFilters search={search} setSearch={setSearch} filter={filter} setFilter={setFilter} />
              <ApplicationTable rows={filtered} selectedId={selectedId} onSelect={setSelectedId} />
            </>
          )}

          {page === 'Monitoring' && (
            <>
              <div className="stats-grid">
                <Stat label="Applications monitored" value={monitoring?.total_applications ?? '—'} note="Current records" symbol="▤" />
                <Stat label="Overdue deadlines" value={monitoring?.applications?.filter((a) => a.alerts.some((x) => x.includes('overdue'))).length ?? '—'} note="Requires attention" symbol="!" />
                <Stat label="Document expiry alerts" value={monitoring?.applications?.filter((a) => a.alerts.some((x) => x.includes('Document'))).length ?? '—'} note="Check validity" symbol="◷" />
                <Stat label="Audit events" value={auditEvents.length} note="Persisted event log" symbol="≡" />
              </div>

              <div className="panel monitoring-panel">
                <h2>Compliance monitoring</h2>
                <p className="muted">
                  {monitoring ? `Checked on ${monitoring.checked_on}. Set dates in an application's review panel.` : 'Loading compliance data...'}
                </p>
                {(monitoring?.applications || []).map((item) => (
                  <div className="audit-row" key={item.id}>
                    <div className="audit-icon">{item.alerts.length ? '!' : '✓'}</div>
                    <div>
                      <strong>{item.institution}</strong>
                      <p>{item.id} · {item.stage}</p>
                      <span>
                        Deadline: {item.due_date || 'Not set'} · Document expiry: {item.document_expiry_date || 'Not set'}
                      </span>
                      <p>{item.alerts.length ? item.alerts.join(' · ') : 'No configured date alerts'}</p>
                      <button className="secondary-button" onClick={() => { setSelectedId(item.id); setPage('Applications') }}>
                        Configure dates
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}

          {page === 'Audit Trail' && (
            <div className="panel">
              <div className="section-heading">
                <div><h2>Persistent audit trail</h2><p>Events loaded from the backend database.</p></div>
                <button className="secondary-button" onClick={() => refreshData().catch((e) => setNotice(e.message))}>Refresh</button>
              </div>

              {auditEvents.length === 0 ? (
                <div className="empty-state">
                  No new audit events yet. Advance, return, or update compliance dates on an application to record activity.
                </div>
              ) : [...auditEvents].map((event) => (
                <div className="audit-row" key={event.id}>
                  <div className="audit-icon">↻</div>
                  <div>
                    <strong>{event.action}</strong>
                    <p>{event.application_id} · {event.stage || '—'}</p>
                    <span>{event.timestamp ? new Date(event.timestamp).toLocaleString() : 'Timestamp unavailable'}</span>
                    {event.details && <p>{event.details}</p>}
                  </div>
                </div>
              ))}
            </div>
          )}

          <footer>
            AICTE Approval &amp; Compliance System
            <span>Prototype · Demonstration data only</span>
          </footer>
        </section>
      </main>

      {selected && (
        <div className="detail-overlay" onMouseDown={(e) => {
          if (e.target === e.currentTarget) setSelectedId('')
        }}>
          <section className="detail-panel" aria-label="Application details">
            <div className="detail-header">
              <div><div className="eyebrow">APPLICATION DETAILS</div><h2>Review application</h2></div>
              <button className="close-button" onClick={() => setSelectedId('')} aria-label="Close panel">×</button>
            </div>

            <div className="detail-id">{selected.id}</div>
            <h3>{selected.institution}</h3>
            <Status status={selected.status} />

            <div className="detail-grid">
              <Detail label="Region" value={selected.region} />
              <Detail label="Assigned officer" value={selected.officer} />
              <Detail label="Submission date" value={selected.date} />
              <Detail label="Current stage" value={selected.stage} />
            </div>

            {selected.reason && (
              <div className="reason-box"><strong>Return reason</strong><p>{selected.reason}</p></div>
            )}

            <div className="workflow-section">
              <h3>Workflow progress</h3>
              {(() => {
                const route = workflows[selected.workflow_route] || FALLBACK_STAGES
                const currentIndex = route.indexOf(selected.stage)
                return route.map((stage, index) => (
                  <div className={`workflow-step ${index <= currentIndex ? 'step-done' : ''}`} key={stage}>
                    <div className="step-marker">{index < currentIndex ? '✓' : index + 1}</div>
                    <div>
                      <strong>{stage}</strong>
                      <span>{index < currentIndex ? 'Completed' : index === currentIndex ? 'Current stage' : 'Upcoming'}</span>
                    </div>
                  </div>
                ))
              })()}
            </div>

            <div className="panel" style={{ marginTop: 16 }}>
              <h3>Workflow configuration</h3>
              <label htmlFor="workflow-route">Workflow route</label>
              <select
                id="workflow-route"
                value={selected.workflow_route || 'STANDARD'}
                disabled={busy || selected.stage === 'Completed' || selected.status === 'Approved'}
                onChange={(e) => changeWorkflow(e.target.value)}
              >
                {Object.keys(workflows).map((route) => <option key={route} value={route}>{route}</option>)}
              </select>
              <p className="muted">A route can only be assigned when the current stage exists in it.</p>
            </div>

            <div className="panel" style={{ marginTop: 16 }}>
              <h3>Compliance dates</h3>
              <label htmlFor="due-date">Application deadline</label>
              <input id="due-date" type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} />
              <label htmlFor="expiry-date">Document expiry date</label>
              <input id="expiry-date" type="date" value={expiryDate} onChange={(e) => setExpiryDate(e.target.value)} />
              <button className="primary-button" disabled={busy} onClick={saveCompliance}>
                {busy ? 'Saving...' : 'Save compliance dates'}
              </button>
            </div>

            <div className="workflow-section">
              <h3>Application audit history</h3>
              {selectedAudit.length === 0 ? (
                <p className="muted">No events in the audit table for this application yet.</p>
              ) : selectedAudit.map((event) => (
                <div className="audit-row" key={event.id}>
                  <div className="audit-icon">↻</div>
                  <div>
                    <strong>{event.action}</strong>
                    <p>{event.details || event.stage}</p>
                    <span>{event.timestamp ? new Date(event.timestamp).toLocaleString() : ''}</span>
                  </div>
                </div>
              ))}
            </div>

            {showReturn && (
              <div className="return-form">
                <label htmlFor="return-reason">Reason for returning application *</label>
                <textarea id="return-reason" value={returnReason} onChange={(e) => setReturnReason(e.target.value)} placeholder="Explain what needs correction..." rows={3} />
                <div className="form-actions">
                  <button className="secondary-button" onClick={() => { setShowReturn(false); setReturnReason('') }}>Cancel</button>
                  <button className="danger-button" disabled={!returnReason.trim() || busy} onClick={() => performAction('return', returnReason.trim())}>Confirm return</button>
                </div>
              </div>
            )}

            {!showReturn && (
              <div className="detail-actions">
                <button className="secondary-button" disabled={busy || selected.stage === 'Completed' || selected.status === 'Approved' || selected.status === 'Returned'} onClick={() => setShowReturn(true)}>
                  Return application
                </button>
                <button className="primary-button" disabled={busy || selected.stage === 'Completed' || selected.status === 'Approved'} onClick={() => performAction('advance')}>
                  {busy ? 'Processing...' : selected.stage === 'Final Approval' ? 'Approve & complete' : 'Complete stage →'}
                </button>
              </div>
            )}

            <p className="prototype-note">
              Prototype environment. Workflow changes and configured dates are stored in SQLite. Role-based authentication is not yet implemented.
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
      <div className="stat-top"><span>{label}</span><div className="stat-symbol">{symbol}</div></div>
      <div className="stat-value">{value}</div>
      <div className="stat-note">{note}</div>
    </div>
  )
}

function Status({ status }) {
  const className = (status || 'Unknown').toLowerCase().replaceAll(' ', '-')
  return <span className={`status-badge ${className}`}><span />{status || 'Unknown'}</span>
}

function Detail({ label, value }) {
  return <div className="detail-item"><span>{label}</span><strong>{value || '—'}</strong></div>
}

function ApplicationFilters({ search, setSearch, filter, setFilter }) {
  return (
    <div className="filters">
      <div className="search-box">
        <span>⌕</span>
        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search ID, institution, officer..." />
      </div>
      <select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter by status">
        {['All', 'Pending', 'Under Review', 'Approved', 'Returned'].map((status) => (
          <option key={status} value={status}>{status === 'All' ? 'All statuses' : status}</option>
        ))}
      </select>
    </div>
  )
}

function ApplicationTable({ rows, selectedId, onSelect }) {
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>APPLICATION / INSTITUTION</th><th>STAGE</th><th>OFFICER</th><th>STATUS</th><th /></tr></thead>
        <tbody>
          {rows.map((item) => (
            <tr key={item.id} className={selectedId === item.id ? 'selected-row' : ''}>
              <td>
                <button className="application-link" onClick={() => onSelect(item.id)}>{item.id}</button>
                <div className="institution-name">{item.institution}</div>
              </td>
              <td>{item.stage}</td>
              <td>{item.officer}<div className="institution-name">{item.region} Region</div></td>
              <td><Status status={item.status} /></td>
              <td><button className="open-button" onClick={() => onSelect(item.id)} aria-label={`Review ${item.id}`}>→</button></td>
            </tr>
          ))}
          {rows.length === 0 && <tr><td colSpan="5" className="empty-state">No applications match your search.</td></tr>}
        </tbody>
      </table>
    </div>
  )
}

export default App
