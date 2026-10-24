
import { useCallback, useEffect, useMemo, useState } from 'react'
import './App.css'

const API = (import.meta.env.VITE_API_URL || 'http://localhost:8000').replace(/\/+$/, '')
const TOKEN_KEY = 'aicte_demo_token'

const FALLBACK_STAGES = [
  'Application Review',
  'Document Verification',
  'Final Approval',
  'Completed',
]

function formatKolkataTimestamp(value, includeTime = true) {
  if (!value) return ''
  const timestamp = new Date(value)
  if (Number.isNaN(timestamp.getTime())) return 'Timestamp unavailable'
  return new Intl.DateTimeFormat('en-IN', {
    timeZone: 'Asia/Kolkata',
    ...(includeTime
      ? { dateStyle: 'medium', timeStyle: 'short' }
      : { dateStyle: 'medium' }),
  }).format(timestamp)
}

function App() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) || '')
  const [user, setUser] = useState(null)
  const [authChecking, setAuthChecking] = useState(true)
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [loginBusy, setLoginBusy] = useState(false)
  const [loginError, setLoginError] = useState('')

  const [applications, setApplications] = useState([])
  const [dashboardData, setDashboardData] = useState(null)
  const [dashboardError, setDashboardError] = useState('')
  const [workflows, setWorkflows] = useState({})
  const [auditEvents, setAuditEvents] = useState([])
  const [monitoring, setMonitoring] = useState(null)
  const [notifications, setNotifications] = useState([])
  const [evcTracking, setEvcTracking] = useState({ evcs: [] })
  const [evcDrafts, setEvcDrafts] = useState({})
  const [trackedDocuments, setTrackedDocuments] = useState([])
  const [expiryWarningDays, setExpiryWarningDays] = useState(30)
  const [renewalDrafts, setRenewalDrafts] = useState({})
  const [documents, setDocuments] = useState([])
  const [complianceSummary, setComplianceSummary] = useState(null)
  const [documentEdits, setDocumentEdits] = useState({})
  const [documentDraft, setDocumentDraft] = useState({
    document_type: '',
    name: '',
    expiry_date: '',
  })
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [reportBusy, setReportBusy] = useState(false)
  const [selectedId, setSelectedId] = useState('')
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState('All')
  const [returnReason, setReturnReason] = useState('')
  const [showReturn, setShowReturn] = useState(false)
  const [showCreateApplication, setShowCreateApplication] = useState(false)
  const [createBusy, setCreateBusy] = useState(false)
  const [newApplication, setNewApplication] = useState({
    id: '',
    institution: '',
    region: '',
    officer: '',
    date: '',
    requires_scrutiny: false,
    requires_evc: false,
    requires_sac_shc: false,
  })
  const [dueDate, setDueDate] = useState('')
  const [expiryDate, setExpiryDate] = useState('')
  const [notice, setNotice] = useState('')
  const [page, setPage] = useState('Dashboard')

  const role = user?.role || ''
  const isAdmin = role.toLowerCase() === 'admin'
  const canProcess = ['admin', 'officer'].includes(role.toLowerCase())
  const canEditWorkflow = isAdmin

  const request = useCallback(async (path, options = {}) => {
    const currentToken = token || localStorage.getItem(TOKEN_KEY)
    const { responseType, ...requestOptions } = options

    const response = await fetch(`${API}${path}`, {
      ...requestOptions,
      headers: {
        ...(requestOptions.body ? { 'Content-Type': 'application/json' } : {}),
        ...(currentToken
          ? { Authorization: `Bearer ${currentToken}` }
          : {}),
        ...requestOptions.headers,
      },
    })

    const data = responseType === 'blob'
      ? await response.blob()
      : await response.json().catch(() => ({}))

    if (response.status === 401 && path !== '/api/auth/login') {
      localStorage.removeItem(TOKEN_KEY)
      setToken('')
      setUser(null)
      throw new Error('Your session has expired. Please log in again.')
    }

    if (!response.ok) {
      const errorData = data instanceof Blob
        ? await data.text().then((text) => JSON.parse(text)).catch(() => ({}))
        : data
      throw new Error(
        errorData && typeof errorData.detail === 'string'
          ? errorData.detail
          : `Request failed (${response.status})`
      )
    }

    if (responseType === 'blob') {
      return { data, headers: response.headers }
    }
    return data
  }, [token])

  const refreshData = useCallback(async () => {
    const [apps, workflowData, auditData, monitoringData, notificationData, evcData, trackingData, dashboardResult] =
      await Promise.all([
        request('/api/applications'),
        request('/api/workflows'),
        request('/api/audit'),
        request('/api/monitoring'),
        request('/api/notifications'),
        request('/api/evc'),
        request('/api/documents'),
        request('/api/dashboard')
          .then((data) => ({ data, error: '' }))
          .catch((error) => ({ data: null, error: error.message })),
      ])

    setApplications(apps)
    setWorkflows(workflowData.workflows || {})
    setAuditEvents(auditData.events || [])
    setMonitoring(monitoringData)
    setNotifications(notificationData.notifications || [])
    setEvcTracking(evcData)
    setTrackedDocuments(trackingData.documents || [])
    setExpiryWarningDays(trackingData.expiry_warning_days)
    setDashboardData(dashboardResult.data)
    setDashboardError(dashboardResult.error)

    setSelectedId((current) => {
      if (current && apps.some((item) => item.id === current)) return current
      return apps[0]?.id || ''
    })
  }, [request])

  // Validate an existing session when the page opens.
  useEffect(() => {
    let active = true

    async function checkSession() {
      const savedToken = localStorage.getItem(TOKEN_KEY)

      if (!savedToken) {
        setAuthChecking(false)
        setLoading(false)
        return
      }

      try {
        const data = await request('/api/auth/me')
        if (!active) return
        setToken(savedToken)
        setUser(data.user || data)
      } catch {
        if (!active) return
        localStorage.removeItem(TOKEN_KEY)
        setToken('')
        setUser(null)
      } finally {
        if (active) setAuthChecking(false)
      }
    }

    checkSession()

    return () => {
      active = false
    }
  }, [request])

  // Load application data only after authentication.
  useEffect(() => {
    if (!user || !token) {
      setLoading(false)
      return
    }

    let active = true
    setLoading(true)

    refreshData()
      .catch((error) => {
        if (active) setNotice(`Could not load backend data: ${error.message}`)
      })
      .finally(() => {
        if (active) setLoading(false)
      })

    return () => {
      active = false
    }
  }, [user, token, refreshData])

  async function handleLogin(event) {
    event.preventDefault()
    setLoginBusy(true)
    setLoginError('')

    try {
      const data = await request('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username: username.trim(), password }),
      })

      if (!data.access_token) {
        throw new Error('The server did not return an access token.')
      }

      localStorage.setItem(TOKEN_KEY, data.access_token)
      setToken(data.access_token)
      setUser(data.user)
      setPassword('')
      setPage('Dashboard')
      setNotice('')
    } catch (error) {
      setLoginError(error.message || 'Login failed.')
    } finally {
      setLoginBusy(false)
      setAuthChecking(false)
    }
  }

  async function handleLogout() {
    try {
      await request('/api/auth/logout', { method: 'POST' })
    } catch {
      // Clear the local session even if the server is unavailable.
    }

    localStorage.removeItem(TOKEN_KEY)
    setToken('')
    setUser(null)
    setApplications([])
    setDashboardData(null)
    setDashboardError('')
    setWorkflows({})
    setAuditEvents([])
    setMonitoring(null)
    setNotifications([])
    setEvcTracking({ evcs: [] })
    setTrackedDocuments([])
    setDocuments([])
    setComplianceSummary(null)
    setSelectedId('')
    setShowReturn(false)
    setNotice('')
    setLoginError('')
    setPage('Dashboard')
  }

  const selected = applications.find((item) => item.id === selectedId)

  useEffect(() => {
    setDueDate(selected?.due_date || '')
    setExpiryDate(selected?.document_expiry_date || '')
  }, [selectedId, selected?.due_date, selected?.document_expiry_date])

  const refreshDocuments = useCallback(async () => {
    if (!selectedId) return
    const data = await request(`/api/applications/${encodeURIComponent(selectedId)}/documents`)
    setDocuments(data.documents || [])
    setComplianceSummary(data.compliance || null)
    setDocumentEdits(Object.fromEntries((data.documents || []).map((document) => [
      document.id,
      {
        document_type: document.document_type,
        name: document.name,
        status: document.status,
        issue_date: document.issue_date || '',
        expiry_date: document.expiry_date || '',
        reviewer_remarks: document.reviewer_remarks || '',
      },
    ])))
  }, [request, selectedId])

  useEffect(() => {
    if (!user || !selectedId) return undefined
    let active = true
    Promise.resolve()
      .then(() => request(`/api/applications/${encodeURIComponent(selectedId)}/documents`))
      .then((data) => {
        if (!active) return
        setDocuments(data.documents || [])
        setComplianceSummary(data.compliance || null)
        setDocumentEdits(Object.fromEntries((data.documents || []).map((document) => [
          document.id,
          {
            document_type: document.document_type,
            name: document.name,
            status: document.status,
            issue_date: document.issue_date || '',
            expiry_date: document.expiry_date || '',
            reviewer_remarks: document.reviewer_remarks || '',
          },
        ])))
      })
      .catch((error) => {
        if (active) setNotice(`Could not load document register: ${error.message}`)
      })
    return () => { active = false }
  }, [request, selectedId, user])

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

  // Monitoring alerts are objects: { severity, type, message }.
  const allAlerts = (monitoring?.applications || []).flatMap((item) =>
    (item.alerts || []).map((alert) =>
      typeof alert === 'string'
        ? { severity: 'Warning', type: 'general', message: alert }
        : alert
    )
  )

  const overdueCount = (monitoring?.applications || []).filter((item) =>
    (item.alerts || []).some((alert) => {
      const message = typeof alert === 'string' ? alert : alert.message || ''
      const type = typeof alert === 'string' ? '' : alert.type || ''
      return /overdue/i.test(message) || /overdue/i.test(type)
    })
  ).length

  const expiryCount = (monitoring?.applications || []).filter((item) =>
    (item.alerts || []).some((alert) => {
      const message = typeof alert === 'string' ? alert : alert.message || ''
      const type = typeof alert === 'string' ? '' : alert.type || ''
      return /document|expir/i.test(`${message} ${type}`)
    })
  ).length

  const riskSummary = monitoring?.risk_summary || {}
  const highestRiskApplications = [...(monitoring?.applications || [])]
    .filter((item) => item.risk_score !== null && item.risk_score !== undefined)
    .sort((left, right) => right.risk_score - left.risk_score)
    .slice(0, 5)

  async function performAction(action, reason = '') {
    if (!selected || busy || !canProcess) return

    setBusy(true)
    setNotice('')

    try {
      const options = action === 'return'
        ? { method: 'POST', body: JSON.stringify({ reason }) }
        : { method: 'POST' }

      const result = await request(
        `/api/applications/${encodeURIComponent(selected.id)}/${action}`,
        options
      )

      await refreshData()
      setShowReturn(false)
      setReturnReason('')
      const decision = result.routing_decision
      const successMessage = action === 'auto-route' && decision
        ? `Auto-assigned ${decision.route} using ${decision.rule_id}: ${decision.reason}`
        : {
            advance: 'Application stage updated successfully.',
            return: 'Application returned successfully. Audit history saved.',
            resubmit: 'Corrections resubmitted. The workflow can continue.',
          }[action] || 'Application workflow updated successfully.'
      setNotice(successMessage)
    } catch (error) {
      setNotice(error.message)
    } finally {
      setBusy(false)
    }
  }

  async function changeWorkflow(route) {
    if (!selected || busy || !canEditWorkflow) return

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
    if (!selected || busy || !canProcess) return

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
      await refreshDocuments()
      setNotice('Compliance dates saved.')
    } catch (error) {
      setNotice(error.message)
    } finally {
      setBusy(false)
    }
  }

  async function createDocument(event) {
    event.preventDefault()
    if (!selected || !canProcess || busy) return
    setBusy(true)
    setNotice('')
    try {
      await request(`/api/applications/${encodeURIComponent(selected.id)}/documents`, {
        method: 'POST',
        body: JSON.stringify({
          ...documentDraft,
          expiry_date: documentDraft.expiry_date || null,
        }),
      })
      setDocumentDraft({ document_type: '', name: '', expiry_date: '' })
      await refreshDocuments()
      await refreshData()
      setNotice('Document added to the register.')
    } catch (error) {
      setNotice(error.message)
    } finally {
      setBusy(false)
    }
  }

  async function updateDocument(documentId) {
    if (!canProcess || busy) return
    setBusy(true)
    setNotice('')
    const edit = documentEdits[documentId] || {}
    try {
      await request(`/api/applications/${encodeURIComponent(selected.id)}/documents/${documentId}`, {
        method: 'PATCH',
        body: JSON.stringify({
          ...edit,
          expiry_date: edit.expiry_date || null,
          reviewer_remarks: edit.reviewer_remarks || null,
        }),
      })
      await refreshDocuments()
      await refreshData()
      setNotice('Document record updated.')
    } catch (error) {
      setNotice(error.message)
    } finally {
      setBusy(false)
    }
  }

  async function submitRenewal(document) {
    if (!canProcess || busy) return
    const draft = renewalDrafts[document.id] || {}
    setBusy(true)
    setNotice('')
    try {
      await request(
        `/api/applications/${encodeURIComponent(document.application_id)}/documents/${document.id}/renewal`,
        {
          method: 'POST',
          body: JSON.stringify({
            issue_date: draft.issue_date || null,
            expiry_date: draft.expiry_date || null,
            remarks: draft.remarks || null,
          }),
        }
      )
      await refreshData()
      setNotice('Renewal submitted for verification.')
    } catch (error) {
      setNotice(error.message)
    } finally {
      setBusy(false)
    }
  }

  async function verifyRenewal(document, decision) {
    if (!canProcess || busy) return
    const draft = renewalDrafts[document.id] || {}
    setBusy(true)
    setNotice('')
    try {
      await request(
        `/api/applications/${encodeURIComponent(document.application_id)}/documents/${document.id}/renewal/verify`,
        {
          method: 'POST',
          body: JSON.stringify({
            decision,
            remarks: draft.verifyRemarks || null,
          }),
        }
      )
      await refreshData()
      setNotice(`Renewal ${decision.toLowerCase()}.`)
    } catch (error) {
      setNotice(error.message)
    } finally {
      setBusy(false)
    }
  }

  async function saveEvcSchedule(evc) {
    if (!canProcess || busy) return
    const draft = evcDrafts[evc.application_id] || {}
    setBusy(true)
    setNotice('')
    try {
      await request(`/api/applications/${encodeURIComponent(evc.application_id)}/evc/schedule`, {
        method: 'POST',
        body: JSON.stringify({
          scheduled_date: draft.scheduled_date || '',
          notes: draft.notes || null,
        }),
      })
      await refreshData()
      setNotice(evc.status === 'Pending Scheduling' ? 'EVC scheduled.' : 'EVC rescheduled.')
    } catch (error) {
      setNotice(error.message)
    } finally {
      setBusy(false)
    }
  }

  async function completeEvc(evc) {
    if (!canProcess || busy) return
    const draft = evcDrafts[evc.application_id] || {}
    setBusy(true)
    setNotice('')
    try {
      await request(`/api/applications/${encodeURIComponent(evc.application_id)}/evc/complete`, {
        method: 'POST',
        body: JSON.stringify({ notes: draft.completion_notes || null }),
      })
      await refreshData()
      setNotice('EVC marked as completed.')
    } catch (error) {
      setNotice(error.message)
    } finally {
      setBusy(false)
    }
  }

  async function markNotificationRead(notificationId) {
    if (!canProcess) return
    try {
      await request(`/api/notifications/${notificationId}/read`, { method: 'POST' })
      setNotifications((current) => current.filter((item) => item.id !== notificationId))
    } catch (error) {
      setNotice(error.message)
    }
  }

  async function runComplianceCheck() {
    if (!isAdmin || busy) return
    setBusy(true)
    setNotice('')
    try {
      const result = await request('/api/compliance/check', { method: 'POST' })
      await refreshData()
      setNotice(`Compliance check complete: ${result.created} new notification(s), ${result.escalated} escalation(s).`)
    } catch (error) {
      setNotice(error.message)
    } finally {
      setBusy(false)
    }
  }

  async function exportPendencyReport() {
    if (reportBusy) return
    setReportBusy(true)
    setNotice('')
    try {
      const result = await request('/api/reports/pendency.csv', {
        responseType: 'blob',
      })
      const objectUrl = URL.createObjectURL(result.data)
      const link = document.createElement('a')
      link.href = objectUrl
      link.download = 'pendency-report.csv'
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0)
      setNotice('Pendency report downloaded.')
    } catch (error) {
      setNotice(error.message)
    } finally {
      setReportBusy(false)
    }
  }

  async function createApplication(event) {
    event.preventDefault()
    if (!isAdmin || createBusy) return

    setCreateBusy(true)
    setNotice('')
    try {
      const payload = {
        ...newApplication,
        id: newApplication.id.trim(),
        institution: newApplication.institution.trim(),
        region: newApplication.region.trim(),
        officer: newApplication.officer.trim(),
      }
      const result = await request('/api/applications', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      await refreshData()
      setSelectedId(result.application.id)
      setShowCreateApplication(false)
      setPage('Applications')
      setNewApplication({
        id: '',
        institution: '',
        region: '',
        officer: '',
        date: '',
        requires_scrutiny: false,
        requires_evc: false,
        requires_sac_shc: false,
      })
      setNotice(`Application ${result.application.id} created. Assign its route automatically from the application panel.`)
    } catch (error) {
      setNotice(error.message)
    } finally {
      setCreateBusy(false)
    }
  }

  const selectedAudit = auditEvents.filter(
    (event) => event.application_id === selectedId
  )
  const selectedRoute = workflows[selected?.workflow_route] || FALLBACK_STAGES
  const hasWorkflowHistory = Boolean(
    selected?.history?.some((event) => event.action !== 'Application created')
    || selectedAudit.some((event) => event.action !== 'Application created')
  )
  const canAssignWorkflow = Boolean(
    isAdmin
    && selected?.status === 'Pending'
    && selected.stage === selectedRoute[0]
    && !hasWorkflowHistory
  )
  const routingUnavailableReason = selected?.status !== 'Pending'
    ? 'Automatic routing is only available for pending applications.'
    : selected?.stage !== selectedRoute[0]
      ? 'This application has already passed its initial workflow stage.'
      : hasWorkflowHistory
        ? 'This application already has audit history and its route is locked.'
        : ''

  if (authChecking) {
    return <div className="app-shell"><main className="main-content"><div className="notice">Checking your session...</div></main></div>
  }

  if (!user || !token) {
    return (
      <div className="app-shell">
        <main className="main-content">
          <section className="page-content">
            <div className="brand" style={{ marginBottom: 32 }}>
              <div className="brand-mark">A</div>
              <div>
                <strong>AICTE Portal</strong>
                <span>Approval &amp; Compliance</span>
              </div>
            </div>

            <div className="panel" style={{ maxWidth: 440, margin: '8vh auto', padding: 28 }}>
              <div className="eyebrow">SECURE WORKSPACE</div>
              <h1 style={{ marginTop: 8 }}>Sign in</h1>
              <p className="muted">Sign in to manage approval workflows and compliance.</p>

              {loginError && <div className="notice" role="alert">{loginError}</div>}

              <form onSubmit={handleLogin}>
                <label htmlFor="login-username">Username</label>
                <input
                  id="login-username"
                  autoComplete="username"
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  required
                />

                <label htmlFor="login-password" style={{ display: 'block', marginTop: 16 }}>Password</label>
                <input
                  id="login-password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  required
                />

                <button className="primary-button" type="submit" disabled={loginBusy} style={{ marginTop: 20, width: '100%' }}>
                  {loginBusy ? 'Signing in...' : 'Sign in'}
                </button>
              </form>

              <div className="prototype-note" style={{ marginTop: 24 }}>
                Demo accounts:
                <p>Admin: admin / Admin@26262</p>
                <p>Officer: officer / Officer@26262</p>
                <p>Viewer: viewer / Viewer@26262</p>
                <span>Demonstration credentials only. Do not use these passwords in production.</span>
              </div>
            </div>
          </section>
        </main>
      </div>
    )
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
          ['EVC Tracking', '◎'],
          ['Document Tracking', '▧'],
          ['Monitoring', '◷'],
          ['Notifications', '♢'],
          ['Audit Trail', '≡'],
        ].map(([name, symbol]) => (
          <button
            key={name}
            className={`nav-item ${page === name ? 'active' : ''}`}
            onClick={() => { setPage(name); setNotice('') }}
          >
            <span className="nav-symbol">{symbol}</span>
            {name}{name === 'Notifications' && notifications.some((item) => !item.is_read) ? ` (${notifications.filter((item) => !item.is_read).length})` : ''}
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
            <div className="avatar">{(user.username || 'U').slice(0, 2).toUpperCase()}</div>
            <div>
              <strong>{user.username}</strong>
              <span>{role}</span>
            </div>
            <button className="secondary-button" onClick={handleLogout}>Logout</button>
          </div>
        </header>

        <section className="page-content">
          {loading && <div className="notice">Loading backend data...</div>}

          <div className="page-heading">
            <div>
              <div className="eyebrow">APPROVAL MANAGEMENT SYSTEM</div>
              <h1>{page === 'Dashboard' ? 'Application Dashboard' : page}</h1>
              <p>Monitor institutional applications and manage approval workflows.</p>
              {!canProcess && <p className="muted">Viewer mode · Read-only access</p>}
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
                <Stat label="Total applications" value={dashboardData?.total_applications ?? '—'} note="Across all regions" symbol="▤" />
                <Stat label="Pending action" value={dashboardData?.pending_requests ?? '—'} note="Awaiting processing" symbol="◷" />
                <Stat label="Approved" value={dashboardData?.approved ?? '—'} note="Completed applications" symbol="✓" />
                <Stat label="Returned" value={dashboardData ? dashboardData.by_status?.Returned || 0 : '—'} note="Requires correction" symbol="↶" />
              </div>

              {dashboardError ? (
                <div className="panel dashboard-state" role="alert">
                  <strong>Dashboard analytics could not be loaded.</strong>
                  <p>{dashboardError}</p>
                  <button className="secondary-button" onClick={() => refreshData().catch((error) => setNotice(error.message))}>
                    Retry
                  </button>
                </div>
              ) : !dashboardData ? (
                <div className="panel dashboard-state" role="status">Loading dashboard analytics...</div>
              ) : (
                <div className="dashboard-summary-grid">
                  <DashboardSummary title="Applications by region" data={dashboardData.by_region} />
                  <DashboardSummary title="Applications by assigned officer" data={dashboardData.by_officer} />
                  <DashboardSummary title="Applications by workflow stage" data={dashboardData.by_stage} />
                </div>
              )}

              <div className="panel risk-overview">
                <div className="section-heading">
                  <div>
                    <h2>Explainable workflow risk</h2>
                    <p>Rule-based assessment from available workflow and compliance data; not machine-learning prediction.</p>
                  </div>
                  <span className="record-count">
                    {Object.entries(riskSummary)
                      .map(([level, count]) => `${level}: ${count}`)
                      .join(' · ') || 'Loading risk assessment'}
                  </span>
                </div>
                {highestRiskApplications.length === 0 ? (
                  <p className="muted">No assessed application risks are available yet. Missing inputs are reported as unknown, not low risk.</p>
                ) : highestRiskApplications.map((item) => (
                  <div className="risk-overview-row" key={item.id}>
                    <span className={`risk-badge risk-${item.risk_level.toLowerCase()}`}>
                      {item.risk_score} · {item.risk_level}
                    </span>
                    <div>
                      <strong>{item.institution || item.id}</strong>
                      <p>{item.id} · {(item.risk_factors || []).filter((factor) => factor.points > 0).map((factor) => factor.explanation).join(' ') || 'No elevated factors in available data.'}</p>
                      {(item.risk_recommendations || []).length > 0 && (
                        <p>Recommended: {item.risk_recommendations.join(' ')}</p>
                      )}
                    </div>
                    <button className="secondary-button" onClick={() => { setSelectedId(item.id); setPage('Monitoring') }}>
                      View factors
                    </button>
                  </div>
                ))}
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
                {isAdmin && (
                  <button
                    className="primary-button"
                    onClick={() => setShowCreateApplication((visible) => !visible)}
                  >
                    {showCreateApplication ? 'Cancel' : 'Create application'}
                  </button>
                )}
              </div>
              {isAdmin && showCreateApplication && (
                <form className="panel create-application-form" onSubmit={createApplication}>
                  <h3>Create application</h3>
                  <p className="muted">New applications start pending at Application Review without a route or routing decision.</p>
                  <div className="create-fields-grid">
                    {[
                      ['id', 'Application ID'],
                      ['institution', 'Institution'],
                      ['region', 'Region'],
                      ['officer', 'Assigned officer'],
                    ].map(([field, label]) => (
                      <label key={field} htmlFor={`new-application-${field}`}>
                        {label} *
                        <input
                          id={`new-application-${field}`}
                          value={newApplication[field]}
                          required
                          onChange={(event) => setNewApplication((current) => ({
                            ...current,
                            [field]: event.target.value,
                          }))}
                        />
                      </label>
                    ))}
                    <label htmlFor="new-application-date">
                      Submission date *
                      <input
                        id="new-application-date"
                        type="date"
                        value={newApplication.date}
                        required
                        onChange={(event) => setNewApplication((current) => ({
                          ...current,
                          date: event.target.value,
                        }))}
                      />
                    </label>
                  </div>
                  <fieldset className="routing-attributes">
                    <legend>Optional demo routing attributes</legend>
                    {[
                      ['requires_scrutiny', 'Requires scrutiny'],
                      ['requires_evc', 'Requires EVC'],
                      ['requires_sac_shc', 'Requires SAC/SHC'],
                    ].map(([field, label]) => (
                      <label key={field}>
                        <input
                          type="checkbox"
                          checked={newApplication[field]}
                          onChange={(event) => setNewApplication((current) => ({
                            ...current,
                            [field]: event.target.checked,
                          }))}
                        />
                        {label}
                      </label>
                    ))}
                  </fieldset>
                  <div className="form-actions">
                    <button
                      className="secondary-button"
                      type="button"
                      onClick={() => setShowCreateApplication(false)}
                    >
                      Cancel
                    </button>
                    <button className="primary-button" type="submit" disabled={createBusy}>
                      {createBusy ? 'Creating...' : 'Create application'}
                    </button>
                  </div>
                </form>
              )}
              <ApplicationFilters search={search} setSearch={setSearch} filter={filter} setFilter={setFilter} />
              <ApplicationTable rows={filtered} selectedId={selectedId} onSelect={setSelectedId} />
            </>
          )}

          {page === 'Monitoring' && (
            <>
              <div className="stats-grid">
                <Stat label="Applications monitored" value={monitoring?.total_applications ?? '—'} note="Current records" symbol="▤" />
                <Stat label="Overdue deadlines" value={monitoring ? overdueCount : '—'} note="Requires attention" symbol="!" />
                <Stat label="Document expiry alerts" value={monitoring ? expiryCount : '—'} note="Check validity" symbol="◷" />
                <Stat label="Audit events" value={auditEvents.length} note="Persisted event log" symbol="≡" />
              </div>

              <div className="panel monitoring-panel">
                <div className="section-heading">
                  <div><h2>Compliance and risk monitoring</h2><p>Configurable demo thresholds; this is an explainable rule-based assessment, not machine learning or an official AICTE determination.</p></div>
                  <div className="form-actions">
                    <button className="secondary-button" disabled={reportBusy} onClick={exportPendencyReport}>
                      {reportBusy ? 'Exporting...' : 'Export Pendency Report'}
                    </button>
                    {isAdmin && <button className="secondary-button" disabled={busy} onClick={runComplianceCheck}>Run compliance check</button>}
                  </div>
                </div>
                <p className="muted">
                  {monitoring ? `Checked on ${monitoring.checked_on}. Set dates in an application's review panel.` : 'Loading compliance data...'}
                </p>
                {monitoring && (
                  <div className="risk-level-summary" aria-label="Risk level counts">
                    {Object.entries(riskSummary).map(([level, count]) => (
                      <span className={`risk-badge risk-${level.toLowerCase()}`} key={level}>
                        {level}: {count}
                      </span>
                    ))}
                  </div>
                )}

                {(monitoring?.applications || []).map((item) => {
                  const alerts = (item.alerts || []).map((alert) =>
                    typeof alert === 'string'
                      ? { severity: 'Warning', type: 'general', message: alert }
                      : alert
                  )

                  return (
                    <div className="audit-row" key={item.id}>
                      <div className="audit-icon">{alerts.length ? '!' : '✓'}</div>
                      <div>
                        <strong>{item.institution}</strong>
                        <p>{item.id} · {item.stage}</p>
                        <div className="risk-detail">
                          <span className={`risk-badge risk-${item.risk_level.toLowerCase()}`}>
                            {item.risk_score === null ? 'Score unavailable' : `Risk ${item.risk_score}/100`} · {item.risk_level}
                          </span>
                          {(item.risk_factors || []).map((factor) => (
                            <p className="risk-factor" key={`${item.id}-${factor.category}-${factor.name}`}>
                              <strong>{factor.name}: +{factor.points}</strong> — {factor.explanation}
                              {factor.recommendation && <span> Recommendation: {factor.recommendation}</span>}
                            </p>
                          ))}
                          {(item.risk_data_gaps || []).map((gap) => (
                            <p className="risk-gap" key={`${item.id}-${gap}`}>Not assessed: {gap}</p>
                          ))}
                        </div>
                        <p>Document-register status: <strong>{item.compliance_state || 'Unknown'}</strong> · {item.documents_count || 0} document(s)</p>
                        <span>
                          Deadline: {item.due_date || 'Not set'} · Document expiry: {item.document_expiry_date || 'Not set'}
                        </span>

                        {alerts.length ? alerts.map((alert, index) => (
                          <p key={`${item.id}-${index}`}>
                            <strong>{alert.severity || 'Info'}:</strong> {alert.message || alert.type || 'Compliance alert'}
                          </p>
                        )) : <p>No configured date alerts</p>}

                        <button className="secondary-button" onClick={() => { setSelectedId(item.id); setPage('Applications') }}>
                          View application
                        </button>
                      </div>
                    </div>
                  )
                })}

                {allAlerts.length > 0 && (
                  <p className="muted">Total alerts: {allAlerts.length}</p>
                )}
              </div>
            </>
          )}

          {page === 'EVC Tracking' && (
            <div className="panel">
              <div className="section-heading">
                <div>
                  <h2>Expert Visit Committee tracking</h2>
                  <p>Only applications with a configured route containing an EVC stage are listed. Upcoming window: {evcTracking.upcoming_window_days ?? 30} days (configurable demo setting).</p>
                </div>
                <div className="evc-counts">
                  <span className="record-count">{evcTracking.upcoming_count || 0} upcoming</span>
                  <span className="record-count">{evcTracking.overdue_count || 0} overdue</span>
                </div>
              </div>
              {(evcTracking.evcs || []).length === 0 ? (
                <div className="empty-state">No applications currently have a configured route requiring an EVC.</div>
              ) : (evcTracking.evcs || []).map((evc) => {
                const draft = evcDrafts[evc.application_id] || {}
                return (
                  <div className="audit-row evc-row" key={evc.application_id}>
                    <div className={`evc-status-mark evc-${evc.status.toLowerCase().replaceAll(' ', '-')}`}>
                      {evc.status === 'Completed' ? '✓' : evc.status === 'Delayed' ? '!' : '◎'}
                    </div>
                    <div className="evc-content">
                      <strong>{evc.institution || evc.application_id}</strong>
                      <p>{evc.application_id} · {evc.stage || 'Stage unavailable'} · application {evc.application_status || 'status unavailable'}</p>
                      <p><strong>EVC status: {evc.status}</strong>{evc.scheduled_date ? ` · Scheduled ${evc.scheduled_date}` : ''}</p>
                      {evc.notes && <p className="muted">{evc.notes}</p>}
                      {canProcess && evc.status !== 'Completed' && (
                        <div className="evc-actions">
                          <label>Scheduled date
                            <input
                              type="date"
                              value={draft.scheduled_date ?? evc.scheduled_date ?? ''}
                              disabled={busy}
                              onChange={(event) => setEvcDrafts((current) => ({
                                ...current,
                                [evc.application_id]: {
                                  ...current[evc.application_id],
                                  scheduled_date: event.target.value,
                                },
                              }))}
                            />
                          </label>
                          <label>Scheduling / rescheduling notes
                            <textarea
                              rows={2}
                              value={draft.notes ?? evc.notes ?? ''}
                              disabled={busy}
                              onChange={(event) => setEvcDrafts((current) => ({
                                ...current,
                                [evc.application_id]: {
                                  ...current[evc.application_id],
                                  notes: event.target.value,
                                },
                              }))}
                            />
                          </label>
                          <button
                            className="secondary-button"
                            disabled={busy || !(draft.scheduled_date ?? evc.scheduled_date)}
                            onClick={() => saveEvcSchedule(evc)}
                          >
                            {evc.status === 'Pending Scheduling' ? 'Schedule EVC' : 'Reschedule EVC'}
                          </button>
                          {evc.scheduled_date && (
                            <>
                              <label>Completion notes
                                <textarea
                                  rows={2}
                                  value={draft.completion_notes || ''}
                                  disabled={busy}
                                  onChange={(event) => setEvcDrafts((current) => ({
                                    ...current,
                                    [evc.application_id]: {
                                      ...current[evc.application_id],
                                      completion_notes: event.target.value,
                                    },
                                  }))}
                                />
                              </label>
                              <button className="primary-button" disabled={busy} onClick={() => completeEvc(evc)}>
                                Mark completed
                              </button>
                            </>
                          )}
                        </div>
                      )}
                      {!canProcess && <span className="muted">Viewer access is read-only.</span>}
                    </div>
                  </div>
                )
              })}
            </div>
          )}

          {page === 'Notifications' && (
            <div className="panel">
              <div className="section-heading">
                <div><h2>In-app notifications</h2><p>Compliance alerts and deadline escalations linked to applications.</p></div>
                <span className="record-count">{notifications.filter((item) => !item.is_read).length} unread</span>
              </div>
              {notifications.length === 0 ? (
                <div className="empty-state">No notifications have been generated. Admins can run a compliance check from Monitoring.</div>
              ) : notifications.map((item) => (
                <div className={`audit-row notification-row ${item.is_read ? 'notification-read' : ''}`} key={item.id}>
                  <div className="audit-icon">{item.is_read ? '✓' : '!'}</div>
                  <div>
                    <strong>{item.category.replaceAll('_', ' ')} · escalation {item.escalation_level}</strong>
                    <p>{item.message}</p>
                    <span>{item.application_id} · {formatKolkataTimestamp(item.created_at)} · {item.resolved_at ? 'Resolved' : item.is_read ? 'Read' : 'Unread'}</span>
                    <div className="form-actions">
                      <button className="secondary-button" onClick={() => {
                        setSelectedId(item.application_id)
                        setPage('Applications')
                      }}>Open application</button>
                      {!item.is_read && canProcess && (
                        <button className="secondary-button" onClick={() => markNotificationRead(item.id)}>Mark as viewed</button>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {page === 'Document Tracking' && (
            <div className="panel">
              <div className="section-heading">
                <div>
                  <h2>Document expiry and renewal tracking</h2>
                  <p>Expiry warnings use the configured {expiryWarningDays}-day window; it is a configurable demo setting, not an official AICTE deadline.</p>
                </div>
                <span className="record-count">{trackedDocuments.length} document(s)</span>
              </div>
              {trackedDocuments.length === 0 ? (
                <div className="empty-state">No institution documents are recorded yet.</div>
              ) : (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>APPLICATION / INSTITUTION</th>
                        <th>DOCUMENT</th>
                        <th>DATES</th>
                        <th>TRACKING STATUS</th>
                        <th>RENEWAL ACTION</th>
                      </tr>
                    </thead>
                    <tbody>
                      {trackedDocuments.map((document) => {
                        const draft = renewalDrafts[document.id] || {}
                        return (
                          <tr key={document.id}>
                            <td>
                              <button className="application-link" onClick={() => {
                                setSelectedId(document.application_id)
                                setPage('Applications')
                              }}>{document.application_id}</button>
                              <div className="institution-name">{document.institution}</div>
                            </td>
                            <td>{document.document_type}<div className="institution-name">{document.name}</div></td>
                            <td>
                              <div>Issued: {document.issue_date || '—'}</div>
                              <div>Expires: {document.expiry_date || 'Not set'}</div>
                              <div>Submitted: {document.submission_date || '—'}</div>
                              {document.renewal_status === 'Submitted' && (
                                <>
                                  <div>Renewal issue: {document.renewal_issue_date || '—'}</div>
                                  <div>Renewal expiry: {document.renewal_expiry_date || '—'}</div>
                                  <div>Renewal remarks: {document.renewal_remarks || '—'}</div>
                                </>
                              )}
                            </td>
                            <td>
                              <strong>{document.tracking_status}</strong>
                              <div className="institution-name">Verification: {document.verification_status || document.status}</div>
                              <div className="institution-name">Renewal: {document.renewal_status}</div>
                            </td>
                            <td className="renewal-actions">
                              {canProcess && document.renewal_status !== 'Submitted' && (
                                <div className="renewal-form">
                                  <label>Renewed issue date
                                    <input type="date" value={draft.issue_date || ''} disabled={busy} onChange={(event) => setRenewalDrafts((current) => ({
                                      ...current,
                                      [document.id]: { ...current[document.id], issue_date: event.target.value },
                                    }))} />
                                  </label>
                                  <label>Renewed expiry date
                                    <input type="date" value={draft.expiry_date || ''} disabled={busy} onChange={(event) => setRenewalDrafts((current) => ({
                                      ...current,
                                      [document.id]: { ...current[document.id], expiry_date: event.target.value },
                                    }))} />
                                  </label>
                                  <label>Submission remarks
                                    <textarea rows={2} value={draft.remarks || ''} disabled={busy} onChange={(event) => setRenewalDrafts((current) => ({
                                      ...current,
                                      [document.id]: { ...current[document.id], remarks: event.target.value },
                                    }))} />
                                  </label>
                                  <button className="secondary-button" disabled={busy || !draft.expiry_date} onClick={() => submitRenewal(document)}>Submit renewal</button>
                                </div>
                              )}
                              {canProcess && document.renewal_status === 'Submitted' && (
                                <div className="renewal-form">
                                  <label>Verifier remarks
                                    <textarea rows={2} value={draft.verifyRemarks || ''} disabled={busy} onChange={(event) => setRenewalDrafts((current) => ({
                                      ...current,
                                      [document.id]: { ...current[document.id], verifyRemarks: event.target.value },
                                    }))} />
                                  </label>
                                  <button className="primary-button" disabled={busy} onClick={() => verifyRenewal(document, 'Approved')}>Approve</button>
                                  <button className="danger-button" disabled={busy || !draft.verifyRemarks?.trim()} onClick={() => verifyRenewal(document, 'Rejected')}>Reject with reason</button>
                                </div>
                              )}
                              {!canProcess && <span className="institution-name">Read-only</span>}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {page === 'Audit Trail' && (
            <div className="panel">
              <div className="section-heading">
                <div><h2>Persistent audit trail</h2><p>Events loaded from the backend database.</p></div>
                <button className="secondary-button" onClick={() => refreshData().catch((error) => setNotice(error.message))}>Refresh</button>
              </div>

              {auditEvents.length === 0 ? (
                <div className="empty-state">No audit events are available yet.</div>
              ) : [...auditEvents].map((event) => (
                <div className="audit-row" key={event.id}>
                  <div className="audit-icon">↻</div>
                  <div>
                    <strong>{event.action}</strong>
                    <p>{event.application_id} · {event.stage || '—'}</p>
                    <span>{event.timestamp ? formatKolkataTimestamp(event.timestamp) : 'Timestamp unavailable'}</span>
                    {event.actor && <p>Actor: {event.actor}</p>}
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
        <div className="detail-overlay" onMouseDown={(event) => {
          if (event.target === event.currentTarget) setSelectedId('')
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
              <Detail label="Assigned route" value={selected.workflow_route} />
            </div>

            {selected.routing_decision && (
              <div className="reason-box">
                <strong>Automatic routing decision</strong>
                <p>{selected.routing_decision.reason}</p>
                <span>Rule: {selected.routing_decision.rule_id}</span>
              </div>
            )}

            {selected.reason && (
              <div className="reason-box"><strong>Return reason</strong><p>{selected.reason}</p></div>
            )}

            <div className="workflow-section">
              <h3>Workflow progress</h3>
              {(() => {
                const route = workflows[selected.workflow_route] || FALLBACK_STAGES
                const currentIndex = route.indexOf(selected.stage)
                const nextStage = currentIndex >= 0 ? route[currentIndex + 1] : null

                return (
                  <>
                    {route.map((stage, index) => (
                      <div className={`workflow-step ${index <= currentIndex ? 'step-done' : ''}`} key={stage}>
                        <div className="step-marker">{index < currentIndex ? '✓' : index + 1}</div>
                        <div>
                          <strong>{stage}</strong>
                          <span>{index < currentIndex ? 'Completed' : index === currentIndex ? 'Current stage' : 'Upcoming'}</span>
                        </div>
                      </div>
                    ))}
                    <p className="muted">
                      {selected.status === 'Returned'
                        ? 'Action required: resubmit corrections before continuing.'
                        : nextStage
                          ? `Next allowed stage: ${nextStage}`
                          : 'No further stages.'}
                    </p>
                  </>
                )
              })()}
            </div>

            {canEditWorkflow && (
                <div className="panel" style={{ marginTop: 16 }}>
                  <h3>Automatic workflow routing</h3>
                  <p className="muted">Uses configurable demo rules. Validate them against official AICTE/APH guidance before use.</p>
                  <button
                    className="secondary-button"
                    disabled={busy || !canAssignWorkflow}
                    onClick={() => performAction('auto-route')}
                  >
                    Auto-assign route
                  </button>
                  {!canAssignWorkflow && <p className="muted">{routingUnavailableReason}</p>}
                </div>
              )}

            {canEditWorkflow && (
              <div className="panel" style={{ marginTop: 16 }}>
                <h3>Workflow configuration</h3>
                <label htmlFor="workflow-route">Workflow route</label>
                <select
                  id="workflow-route"
                  value={selected.workflow_route || 'STANDARD'}
                  disabled={busy || !canAssignWorkflow}
                  onChange={(event) => changeWorkflow(event.target.value)}
                >
                  {Object.keys(workflows).map((route) => <option key={route} value={route}>{route}</option>)}
                </select>
                <p className="muted">Only Admin can change the workflow route.</p>
              </div>
            )}

            <div className="panel" style={{ marginTop: 16 }}>
              <h3>Compliance dates</h3>
              <label htmlFor="due-date">Application deadline</label>
              <input
                id="due-date"
                type="date"
                value={dueDate}
                disabled={!canProcess || busy}
                onChange={(event) => setDueDate(event.target.value)}
              />

              <label htmlFor="expiry-date">Document expiry date</label>
              <input
                id="expiry-date"
                type="date"
                value={expiryDate}
                disabled={!canProcess || busy}
                onChange={(event) => setExpiryDate(event.target.value)}
              />

              {canProcess && (
                <button className="primary-button" disabled={busy} onClick={saveCompliance}>
                  {busy ? 'Saving...' : 'Save compliance dates'}
                </button>
              )}
              {!canProcess && <p className="muted">Viewer access is read-only.</p>}
            </div>

            <div className="panel document-register" style={{ marginTop: 16 }}>
              <div className="section-heading">
                <div>
                  <h3>Document register</h3>
                  <p>Compliance status: <strong>{complianceSummary?.state || 'Loading'}</strong></p>
                </div>
                <span className="record-count">{documents.length} record(s)</span>
              </div>

              {documents.length === 0 ? (
                <p className="muted">No documents recorded. An empty register is not compliant.</p>
              ) : documents.map((document) => {
                const edit = documentEdits[document.id] || {}
                return (
                  <div className="document-row" key={document.id}>
                    <span>Created {formatKolkataTimestamp(document.created_at, false)}</span>
                    <span>Tracking: {document.tracking_status || '—'} · Renewal: {document.renewal_status || 'Not Required'}</span>
                    <label>Document type
                      <input
                        value={edit.document_type ?? document.document_type}
                        disabled={!canProcess || busy}
                        onChange={(event) => setDocumentEdits((current) => ({
                          ...current,
                          [document.id]: { ...current[document.id], document_type: event.target.value },
                        }))}
                      />
                    </label>
                    <label>Issue date
                      <input
                        type="date"
                        value={edit.issue_date ?? document.issue_date ?? ''}
                        disabled={!canProcess || busy}
                        onChange={(event) => setDocumentEdits((current) => ({
                          ...current,
                          [document.id]: { ...current[document.id], issue_date: event.target.value },
                        }))}
                      />
                    </label>
                    <label>Name
                      <input
                        value={edit.name ?? document.name}
                        disabled={!canProcess || busy}
                        onChange={(event) => setDocumentEdits((current) => ({
                          ...current,
                          [document.id]: { ...current[document.id], name: event.target.value },
                        }))}
                      />
                    </label>
                    <label>Status
                      <select
                        value={edit.status || document.status}
                        disabled={!canProcess || busy}
                        onChange={(event) => setDocumentEdits((current) => ({
                          ...current,
                          [document.id]: { ...current[document.id], status: event.target.value },
                        }))}
                      >
                        {['Pending', 'Submitted', 'Verified', 'Rejected'].map((status) => <option key={status}>{status}</option>)}
                      </select>
                    </label>
                    <label>Expiry date
                      <input
                        type="date"
                        value={edit.expiry_date ?? document.expiry_date ?? ''}
                        disabled={!canProcess || busy}
                        onChange={(event) => setDocumentEdits((current) => ({
                          ...current,
                          [document.id]: { ...current[document.id], expiry_date: event.target.value },
                        }))}
                      />
                    </label>
                    <label>Reviewer remarks
                      <textarea
                        rows={2}
                        value={edit.reviewer_remarks ?? document.reviewer_remarks ?? ''}
                        disabled={!canProcess || busy}
                        onChange={(event) => setDocumentEdits((current) => ({
                          ...current,
                          [document.id]: { ...current[document.id], reviewer_remarks: event.target.value },
                        }))}
                      />
                    </label>
                    {canProcess && <button className="secondary-button" disabled={busy} onClick={() => updateDocument(document.id)}>Save document</button>}
                  </div>
                )
              })}

              {canProcess && (
                <form className="document-create-form" onSubmit={createDocument}>
                  <h4>Add document</h4>
                  <label>Document type<input value={documentDraft.document_type} required onChange={(event) => setDocumentDraft((draft) => ({ ...draft, document_type: event.target.value }))} /></label>
                  <label>Name<input value={documentDraft.name} required onChange={(event) => setDocumentDraft((draft) => ({ ...draft, name: event.target.value }))} /></label>
                  <label>Issue date<input type="date" value={documentDraft.issue_date || ''} onChange={(event) => setDocumentDraft((draft) => ({ ...draft, issue_date: event.target.value }))} /></label>
                  <label>Expiry date<input type="date" value={documentDraft.expiry_date} onChange={(event) => setDocumentDraft((draft) => ({ ...draft, expiry_date: event.target.value }))} /></label>
                  <button className="primary-button" type="submit" disabled={busy}>Add as pending</button>
                  <p className="muted">Submission does not mean verification. Rejection requires reviewer remarks.</p>
                </form>
              )}
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
                    {event.actor && <p>Actor: {event.actor}</p>}
                    <span>{event.timestamp ? formatKolkataTimestamp(event.timestamp) : ''}</span>
                  </div>
                </div>
              ))}
            </div>

            {canProcess && showReturn && (
              <div className="return-form">
                <label htmlFor="return-reason">Reason for returning application *</label>
                <textarea
                  id="return-reason"
                  value={returnReason}
                  onChange={(event) => setReturnReason(event.target.value)}
                  placeholder="Explain what needs correction..."
                  rows={3}
                />
                <div className="form-actions">
                  <button className="secondary-button" onClick={() => { setShowReturn(false); setReturnReason('') }}>Cancel</button>
                  <button className="danger-button" disabled={!returnReason.trim() || busy} onClick={() => performAction('return', returnReason.trim())}>Confirm return</button>
                </div>
              </div>
            )}

            {canProcess && !showReturn && selected.status === 'Returned' && (
              <div className="detail-actions">
                <button
                  className="primary-button"
                  disabled={busy}
                  onClick={() => performAction('resubmit')}
                >
                  {busy ? 'Processing...' : 'Resubmit corrections'}
                </button>
              </div>
            )}

            {canProcess && !showReturn && selected.status !== 'Returned' && (
              <div className="detail-actions">
                <button
                  className="secondary-button"
                  disabled={busy || selected.stage === 'Completed' || selected.status === 'Approved' || selected.status === 'Returned'}
                  onClick={() => setShowReturn(true)}
                >
                  Return application
                </button>
                <button
                  className="primary-button"
                  disabled={busy || selected.stage === 'Completed' || selected.status === 'Approved'}
                  onClick={() => performAction('advance')}
                >
                  {busy ? 'Processing...' : selected.stage === 'Final Approval' ? 'Approve & complete' : 'Complete stage →'}
                </button>
              </div>
            )}

            <p className="prototype-note">
              Demo authentication is enabled. Admin can configure workflows; Admin and Officer can process applications; Viewer is read-only.
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

function DashboardSummary({ title, data = {} }) {
  const entries = Object.entries(data)
    .sort(([leftName, leftCount], [rightName, rightCount]) =>
      rightCount - leftCount || leftName.localeCompare(rightName)
    )

  return (
    <div className="panel dashboard-summary">
      <h2>{title}</h2>
      {entries.length === 0 ? (
        <div className="empty-state">No applications to summarize.</div>
      ) : entries.map(([label, count]) => (
        <div className="dashboard-summary-row" key={label}>
          <span>{label}</span>
          <strong>{count}</strong>
        </div>
      ))}
    </div>
  )
}

function ApplicationFilters({ search, setSearch, filter, setFilter }) {
  return (
    <div className="filters">
      <div className="search-box">
        <span>⌕</span>
        <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search ID, institution, officer..." />
      </div>
      <select value={filter} onChange={(event) => setFilter(event.target.value)} aria-label="Filter by status">
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
