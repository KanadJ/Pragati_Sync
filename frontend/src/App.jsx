
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import './App.css'

const API = (import.meta.env.VITE_API_URL || 'http://localhost:8000').replace(/\/+$/, '')
const TOKEN_KEY = 'aicte_demo_token'
const AVAILABLE_ROLES = ['Admin', 'Officer', 'Viewer']

const FALLBACK_STAGES = [
  'Application Review',
  'Document Verification',
  'Final Approval',
  'Completed',
]

const PAGE_DESCRIPTIONS = {
  Applications: 'Review application records, configured workflows, and available actions.',
  'EVC Tracking': 'Schedule and follow Expert Visit Committee cases required by configured approval routes.',
  'Document Tracking': 'Review document validity, renewal submissions, and verification status.',
  Monitoring: 'Review configured deadline alerts, document status, and explainable prototype risk assessments.',
  Notifications: 'Review compliance notifications and their current read or resolution state.',
  'Audit Trail': 'Review recorded workflow and compliance actions with their actor and timestamp.',
}

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
  const [sessionConnecting, setSessionConnecting] = useState(true)
  const [sessionError, setSessionError] = useState('')
  const [sessionRetry, setSessionRetry] = useState(0)
  const [roleSwitchBusy, setRoleSwitchBusy] = useState(false)

  const [applications, setApplications] = useState([])
  const [dashboardData, setDashboardData] = useState(null)
  const [dashboardError, setDashboardError] = useState('')
  const [workflows, setWorkflows] = useState({})
  const [auditEvents, setAuditEvents] = useState([])
  const [monitoring, setMonitoring] = useState(null)
  const [heatmap, setHeatmap] = useState(null)
  const [heatmapMetric, setHeatmapMetric] = useState('applications')
  const [heatmapLoading, setHeatmapLoading] = useState(false)
  const [heatmapError, setHeatmapError] = useState('')
  const [selectedHeatmapCell, setSelectedHeatmapCell] = useState(null)
  const heatmapMetricRef = useRef('applications')
  const heatmapRefreshRef = useRef({ promise: null, requested: false })
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
  const selected = applications.find((item) => item.id === selectedId)
  const selectedDueDate = selected?.due_date || ''
  const selectedExpiryDate = selected?.document_expiry_date || ''
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
  const [complianceDateDraft, setComplianceDateDraft] = useState({
    applicationId: selectedId,
    sourceDueDate: selectedDueDate,
    sourceExpiryDate: selectedExpiryDate,
    dueDate: selectedDueDate,
    expiryDate: selectedExpiryDate,
  })
  if (
    complianceDateDraft.applicationId !== selectedId
    || complianceDateDraft.sourceDueDate !== selectedDueDate
    || complianceDateDraft.sourceExpiryDate !== selectedExpiryDate
  ) {
    setComplianceDateDraft({
      applicationId: selectedId,
      sourceDueDate: selectedDueDate,
      sourceExpiryDate: selectedExpiryDate,
      dueDate: selectedDueDate,
      expiryDate: selectedExpiryDate,
    })
  }
  const [notice, setNotice] = useState('')
  const [noticeType, setNoticeType] = useState('info')
  const [page, setPage] = useState('Dashboard')
  const [mobileNavOpen, setMobileNavOpen] = useState(false)

  const role = user?.role || ''
  const isAdmin = role.toLowerCase() === 'admin'
  const canProcess = ['admin', 'officer'].includes(role.toLowerCase())
  const canEditWorkflow = isAdmin

  const showNotice = useCallback((message, type = 'success') => {
    setNoticeType(type)
    setNotice(message)
  }, [])

  const clearNotice = useCallback(() => {
    setNotice('')
    setNoticeType('info')
  }, [])

  const request = useCallback(async (path, options = {}) => {
    const { responseType, skipAuth = false, ...requestOptions } = options
    const currentToken = skipAuth ? '' : token || localStorage.getItem(TOKEN_KEY)

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

    if (
      response.status === 401
      && path !== '/api/auth/demo-session'
    ) {
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

  const refreshHeatmap = useCallback((metric = heatmapMetricRef.current) => {
    heatmapMetricRef.current = metric
    const refresh = heatmapRefreshRef.current
    if (refresh.promise) {
      refresh.requested = true
      return refresh.promise
    }

    setHeatmapLoading(true)
    setHeatmapError('')
    refresh.promise = (async () => {
      do {
        refresh.requested = false
        const requestedMetric = heatmapMetricRef.current
        try {
          const data = await request(
            `/api/monitoring/heatmap?metric=${encodeURIComponent(requestedMetric)}`
          )
          if (requestedMetric !== heatmapMetricRef.current) continue
          setHeatmap(data)
          setSelectedHeatmapCell((current) => {
            if (!current) return null
            const region = data.regions.find((item) => item.key === current.key)
            const cell = region?.cells.find((item) => item.stage === current.stage)
            return cell
              ? {
                  key: current.key,
                  region: region.label,
                  stage: cell.stage,
                  count: cell.count,
                  applications: cell.applications,
                }
              : null
          })
          setHeatmapError('')
        } catch (error) {
          if (requestedMetric === heatmapMetricRef.current) {
            setHeatmap(null)
            setHeatmapError(error.message || 'Could not load the regional workflow heatmap.')
          }
        }
      } while (refresh.requested)
    })().finally(() => {
      refresh.promise = null
      setHeatmapLoading(false)
    })

    return refresh.promise
  }, [request])

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
        refreshHeatmap(),
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
      return ''
    })
  }, [request, refreshHeatmap])

  // Reuse a valid demo session or create a Viewer session on startup.
  useEffect(() => {
    let active = true

    async function connectDemoSession() {
      const savedToken = localStorage.getItem(TOKEN_KEY)

      if (savedToken) {
        try {
          const data = await request('/api/auth/me')
          if (!active) return
          if (data.demo_mode === true && AVAILABLE_ROLES.includes(data.role)) {
            setToken(savedToken)
            setUser(data)
            setSessionError('')
            setSessionConnecting(false)
            return
          }
        } catch {
          if (!active) return
        }
      }

      localStorage.removeItem(TOKEN_KEY)
      if (active) setToken('')

      try {
        const data = await request('/api/auth/demo-session', {
          method: 'POST',
          body: JSON.stringify({ role: 'Viewer' }),
          skipAuth: true,
        })
        if (!active) return
        if (!data.access_token || data.user?.role !== 'Viewer') {
          throw new Error('The backend did not create a Viewer demo session.')
        }
        localStorage.setItem(TOKEN_KEY, data.access_token)
        setToken(data.access_token)
        setUser(data.user)
        setSessionError('')
      } catch (error) {
        if (active) {
          setSessionError(error.message || 'Could not connect to the demo workspace.')
        }
      } finally {
        if (active) {
          setSessionConnecting(false)
          setLoading(false)
        }
      }
    }

    connectDemoSession()

    return () => {
      active = false
    }
  }, [request, sessionRetry])

  // Load application data after a demo session is available.
  useEffect(() => {
    if (!user || !token) return

    let active = true

    Promise.resolve()
      .then(() => refreshData())
      .catch((error) => {
        if (active) showNotice(`Could not load backend data: ${error.message}`, 'error')
      })
      .finally(() => {
        if (active) setLoading(false)
      })

    return () => {
      active = false
    }
  }, [user, token, refreshData, showNotice])

  async function handleRoleChange(nextRole) {
    setRoleSwitchBusy(true)
    try {
      const data = await request('/api/auth/demo-session', {
        method: 'POST',
        body: JSON.stringify({ role: nextRole }),
      })
      if (!data.access_token || data.user?.role !== nextRole) {
        throw new Error('The backend did not confirm the requested demo role.')
      }

      localStorage.setItem(TOKEN_KEY, data.access_token)
      setToken(data.access_token)
      setUser(data.user)
      clearNotice()
    } catch (error) {
      const message = error instanceof TypeError
        ? `Cannot reach ${API}. Check VITE_API_URL and confirm the FastAPI demo backend is running.`
        : error.message
      showNotice(`Could not switch demo role: ${message}`, 'error')
    } finally {
      setRoleSwitchBusy(false)
    }
  }

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
        if (active) showNotice(`Could not load document register: ${error.message}`, 'error')
      })
    return () => { active = false }
  }, [request, selectedId, user, showNotice])

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
  const priorityApplications = useMemo(() => {
    const applicationById = new Map(applications.map((application) => [application.id, application]))

    const rankedApplications = (monitoring?.applications || [])
      .flatMap((item) => {
        if (!item?.id) return []

        const alerts = (item.alerts || []).map((alert) => (
          typeof alert === 'string'
            ? { type: '', message: alert }
            : { type: alert?.type || '', message: alert?.message || '' }
        ))
        const overdueAlert = alerts.find((alert) =>
          /overdue/i.test(`${alert.type} ${alert.message}`)
        )
        const expiredAlert = alerts.find((alert) =>
          /document expired|expired document/i.test(alert.type)
          || (!alert.type && /document expired|expired document/i.test(alert.message))
        )
        const expiringAlert = alerts.find((alert) =>
          /document expiring/i.test(alert.type)
          || (!alert.type && /document.*(expiring|expires)|(?:expiring|expires).*document/i.test(alert.message))
        )
        const riskLevel = item.risk_level || 'Unknown'
        const highRisk = ['Critical', 'High'].includes(riskLevel)

        if (!overdueAlert && !highRisk && !expiredAlert && !expiringAlert) return []

        let priority
        let priorityRank
        let priorityReason
        if (overdueAlert) {
          priority = 'Overdue'
          priorityRank = 0
          priorityReason = overdueAlert.message || overdueAlert.type || 'Application deadline overdue'
        } else if (highRisk) {
          priority = `${riskLevel} risk`
          priorityRank = 1
          const contributingFactor = (item.risk_factors || []).find((factor) => factor.points > 0)
          priorityReason = contributingFactor?.explanation || `Rule-based assessment reports ${riskLevel.toLowerCase()} risk.`
        } else {
          const expiryAlert = expiredAlert || expiringAlert
          priority = expiredAlert ? 'Expired document' : 'Expiring document'
          priorityRank = 2
          priorityReason = expiryAlert.message || expiryAlert.type || priority
        }

        return [{
          item,
          application: applicationById.get(item.id),
          priority,
          priorityRank,
          riskRank: riskLevel === 'Critical' ? 0 : 1,
          priorityReason,
        }]
      })
      .sort((left, right) =>
        left.priorityRank - right.priorityRank
        || (left.priorityRank === 1
          ? left.riskRank - right.riskRank
            || (right.item.risk_score ?? -1) - (left.item.risk_score ?? -1)
          : 0)
        || left.item.id.localeCompare(right.item.id)
      )
    const seenIds = new Set()
    return rankedApplications
      .filter(({ item }) => {
        if (seenIds.has(item.id)) return false
        seenIds.add(item.id)
        return true
      })
      .slice(0, 6)
  }, [applications, monitoring])

  async function performAction(action, reason = '') {
    if (!selected || busy || !canProcess) return

    setBusy(true)
    clearNotice()

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
      showNotice(successMessage)
    } catch (error) {
      showNotice(error.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  async function changeWorkflow(route) {
    if (!selected || busy || !canEditWorkflow) return

    setBusy(true)
    clearNotice()

    try {
      await request(
        `/api/applications/${encodeURIComponent(selected.id)}/workflow`,
        { method: 'POST', body: JSON.stringify({ workflow_route: route }) }
      )

      await refreshData()
      showNotice('Workflow route updated successfully.')
    } catch (error) {
      showNotice(error.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  async function saveCompliance() {
    if (!selected || busy || !canProcess) return

    setBusy(true)
    clearNotice()

    try {
      const payload = {
        due_date: complianceDateDraft.dueDate || null,
        document_expiry_date: complianceDateDraft.expiryDate || null,
      }

      await request(
        `/api/applications/${encodeURIComponent(selected.id)}/compliance`,
        { method: 'PATCH', body: JSON.stringify(payload) }
      )

      await refreshData()
      await refreshDocuments()
      showNotice('Compliance dates saved.')
    } catch (error) {
      showNotice(error.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  async function createDocument(event) {
    event.preventDefault()
    if (!selected || !canProcess || busy) return
    setBusy(true)
    clearNotice()
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
      showNotice('Document added to the register.')
    } catch (error) {
      showNotice(error.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  async function updateDocument(documentId) {
    if (!canProcess || busy) return
    setBusy(true)
    clearNotice()
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
      showNotice('Document record updated.')
    } catch (error) {
      showNotice(error.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  async function submitRenewal(document) {
    if (!canProcess || busy) return
    const draft = renewalDrafts[document.id] || {}
    setBusy(true)
    clearNotice()
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
      showNotice('Renewal submitted for verification.')
    } catch (error) {
      showNotice(error.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  async function verifyRenewal(document, decision) {
    if (!canProcess || busy) return
    const draft = renewalDrafts[document.id] || {}
    setBusy(true)
    clearNotice()
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
      showNotice(`Renewal ${decision.toLowerCase()}.`)
    } catch (error) {
      showNotice(error.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  async function saveEvcSchedule(evc) {
    if (!canProcess || busy) return
    const draft = evcDrafts[evc.application_id] || {}
    setBusy(true)
    clearNotice()
    try {
      await request(`/api/applications/${encodeURIComponent(evc.application_id)}/evc/schedule`, {
        method: 'POST',
        body: JSON.stringify({
          scheduled_date: draft.scheduled_date || '',
          notes: draft.notes || null,
        }),
      })
      await refreshData()
      showNotice(evc.status === 'Pending Scheduling' ? 'EVC scheduled.' : 'EVC rescheduled.')
    } catch (error) {
      showNotice(error.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  async function completeEvc(evc) {
    if (!canProcess || busy) return
    const draft = evcDrafts[evc.application_id] || {}
    setBusy(true)
    clearNotice()
    try {
      await request(`/api/applications/${encodeURIComponent(evc.application_id)}/evc/complete`, {
        method: 'POST',
        body: JSON.stringify({ notes: draft.completion_notes || null }),
      })
      await refreshData()
      showNotice('EVC marked as completed.')
    } catch (error) {
      showNotice(error.message, 'error')
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
      showNotice(error.message, 'error')
    }
  }

  async function runComplianceCheck() {
    if (!isAdmin || busy) return
    setBusy(true)
    clearNotice()
    try {
      const result = await request('/api/compliance/check', { method: 'POST' })
      await refreshData()
      showNotice(`Compliance check complete: ${result.created} new notification(s), ${result.escalated} escalation(s).`)
    } catch (error) {
      showNotice(error.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  async function exportPendencyReport() {
    if (reportBusy) return
    setReportBusy(true)
    clearNotice()
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
      showNotice('Pendency report downloaded.')
    } catch (error) {
      showNotice(error.message, 'error')
    } finally {
      setReportBusy(false)
    }
  }

  async function createApplication(event) {
    event.preventDefault()
    if (!isAdmin || createBusy) return

    setCreateBusy(true)
    clearNotice()
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
      showNotice(`Application ${result.application.id} created. Assign its route automatically from the application panel.`)
    } catch (error) {
      showNotice(error.message, 'error')
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

  if (sessionConnecting || !user || !token) {
    return (
      <main className="demo-session-status" role={sessionError ? 'alert' : 'status'} aria-live="polite">
        <div className="panel">
          <img className="demo-session-logo" src="/pragatisync-logo.jpeg" alt="PragatiSync" />
          {sessionConnecting ? (
            <>
              <strong>Connecting to the demo dashboard…</strong>
              <div className="loading-indicator" aria-hidden="true" />
            </>
          ) : (
            <>
              <h1>Demo workspace unavailable</h1>
              <p>{sessionError || 'Could not connect to the demo workspace.'}</p>
              <p className="muted">
                The backend must be configured for the isolated demo environment.
              </p>
              <button
                className="secondary-button"
                type="button"
                onClick={() => {
                  setSessionError('')
                  setSessionConnecting(true)
                  setSessionRetry((attempt) => attempt + 1)
                }}
              >
                Retry connection
              </button>
            </>
          )}
        </div>
      </main>
    )
  }

  return (
    <div className="app-shell app-shell-authenticated">
      {mobileNavOpen && (
        <button
          className="nav-backdrop"
          type="button"
          aria-label="Close navigation menu"
          onClick={() => setMobileNavOpen(false)}
        />
      )}
      <aside className={`sidebar ${mobileNavOpen ? 'sidebar-open' : ''}`} aria-label="Main navigation">
        <div className="brand">
          <img className="brand-logo" src="/pragatisync-logo.jpeg" alt="PragatiSync" />
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
            aria-current={page === name ? 'page' : undefined}
            onClick={() => {
              setPage(name)
              clearNotice()
              setMobileNavOpen(false)
            }}
          >
            <span className="nav-symbol" aria-hidden="true">{symbol}</span>
            <span className="nav-item-label">{name}</span>
            {name === 'Notifications' && notifications.some((item) => !item.is_read) && (
              <span className="nav-count" aria-label={`${notifications.filter((item) => !item.is_read).length} unread`}>
                {notifications.filter((item) => !item.is_read).length}
              </span>
            )}
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
          <div className="topbar-leading">
            <button
              className="mobile-menu-button"
              type="button"
              aria-label={mobileNavOpen ? 'Close navigation menu' : 'Open navigation menu'}
              aria-expanded={mobileNavOpen}
              onClick={() => setMobileNavOpen((open) => !open)}
            >
              <span />
              <span />
              <span />
            </button>
            <div className="breadcrumb"><span className="breadcrumb-root">AICTE WORKSPACE</span><span className="breadcrumb-separator">/</span>{page}</div>
          </div>
          <label className="demo-role-control">
            <span>Demo Role</span>
            <select
              aria-label="Demo Role"
              value={role}
              disabled={roleSwitchBusy}
              onChange={(event) => handleRoleChange(event.target.value)}
            >
              {AVAILABLE_ROLES.map((availableRole) => (
                <option key={availableRole} value={availableRole}>{availableRole}</option>
              ))}
            </select>
          </label>
        </header>

        <section className="page-content">
          {loading && <div className="notice" role="status" aria-live="polite">Loading application data…</div>}

          <div className="page-heading">
            <div>
              <div className="eyebrow">APPROVAL MANAGEMENT SYSTEM <span className="heading-divider">/</span> {page.toUpperCase()}</div>
              <h1>{page === 'Dashboard' ? 'Application Dashboard' : page}</h1>
              <p>{PAGE_DESCRIPTIONS[page] || 'Monitor institutional applications and manage approval workflows.'}</p>
              {!canProcess && <p className="viewer-mode-label"><span aria-hidden="true">●</span> Viewer mode · Read-only access</p>}
            </div>
            <button className="primary-button page-heading-action" onClick={() => setPage('Applications')}>
              View applications <span>→</span>
            </button>
          </div>

          {notice && (
            <div
              className={`notice notice-${noticeType}`}
              role={noticeType === 'error' ? 'alert' : 'status'}
              aria-live={noticeType === 'error' ? 'assertive' : 'polite'}
            >
              <span>{notice}</span>
              <button type="button" onClick={clearNotice} aria-label="Dismiss notification">×</button>
            </div>
          )}

          {page === 'Dashboard' && (
            <div className="dashboard-view">
              <div className="stats-grid dashboard-kpi-grid">
                <Stat label="Total applications" value={dashboardData?.total_applications ?? '—'} note="All recorded applications" symbol="▤" />
                <Stat label="Pending action" value={dashboardData?.pending_requests ?? '—'} note="Includes returned applications" symbol="◷" />
                <Stat label="Overdue applications" value={dashboardData?.overdue_applications ?? '—'} note="Incomplete applications past deadline" symbol="!" />
                <Stat label="Approved" value={dashboardData?.approved ?? '—'} note="Completed applications" symbol="✓" />
                <Stat label="Upcoming deadlines" value={dashboardData?.deadlines_soon ?? '—'} note="Due today through the next 7 days" symbol="⌁" />
                <Stat label="Returned" value={dashboardData ? dashboardData.by_status?.Returned || 0 : '—'} note="Counted within pending action" symbol="↶" />
              </div>

              {dashboardError ? (
                <div className="panel dashboard-state" role="alert">
                  <strong>Dashboard analytics could not be loaded.</strong>
                  <p>{dashboardError}</p>
                  <button className="secondary-button" onClick={() => refreshData().catch((error) => showNotice(error.message, 'error'))}>
                    Retry
                  </button>
                </div>
              ) : !dashboardData ? (
                <div className="panel dashboard-state" role="status">Loading dashboard analytics...</div>
              ) : (
                <div className="dashboard-distribution-grid">
                  <DashboardSummary title="Regional overview" data={dashboardData.by_region} />
                  <DashboardSummary title="Workflow stages" data={dashboardData.by_stage} />
                  <DashboardSummary title="Assigned officer workload" data={dashboardData.by_officer} />
                  <DashboardSummary title="Risk distribution" data={riskSummary} />
                </div>
              )}
              <p className="dashboard-data-note">
                Risk levels use the system’s configurable demo rules and are not official AICTE determinations.
                Deadline indicators use recorded application dates and configured demo windows.
              </p>

              <div className="dashboard-section-heading">
                <div>
                  <h2>Upcoming &amp; overdue compliance</h2>
                  <p>Recorded deadlines, document alerts, and elevated risks ranked for review.</p>
                </div>
                <span className="record-count">{priorityApplications.length} priority records</span>
              </div>
              <div className="dashboard-priority-activity-grid">
                <section className="panel dashboard-priority-panel" aria-labelledby="priority-applications-heading">
                  <div className="section-heading">
                    <div>
                      <h2 id="priority-applications-heading">Needs attention</h2>
                      <p>Open an application to review its existing details.</p>
                    </div>
                  </div>
                  {priorityApplications.length === 0 ? (
                    <div className="empty-state">
                      {monitoring ? 'No overdue, high-risk, or document-expiry applications are currently identified.' : 'Priority applications are unavailable until monitoring data loads.'}
                    </div>
                  ) : (
                    <div className="table-wrap dashboard-priority-table-wrap">
                      <table className="dashboard-priority-table">
                        <thead>
                          <tr>
                            <th scope="col">APPLICATION</th>
                            <th scope="col">REGION</th>
                            <th scope="col">STAGE</th>
                            <th scope="col">DEADLINE</th>
                            <th scope="col">RISK</th>
                            <th scope="col">PRIORITY / REASON</th>
                            <th scope="col"><span className="sr-only">Actions</span></th>
                          </tr>
                        </thead>
                        <tbody>
                          {priorityApplications.map(({ item, application, priority, priorityReason }) => (
                            <tr
                              key={item.id}
                              className="dashboard-priority-row"
                              tabIndex={0}
                              aria-label={`Open application details for ${item.id}`}
                              onClick={() => {
                                setSelectedId(item.id)
                                setPage('Applications')
                              }}
                              onKeyDown={(event) => {
                                if (event.key === 'Enter' || event.key === ' ') {
                                  event.preventDefault()
                                  setSelectedId(item.id)
                                  setPage('Applications')
                                }
                              }}
                            >
                              <td>
                                <button
                                  className="application-link"
                                  onClick={() => {
                                    setSelectedId(item.id)
                                    setPage('Applications')
                                  }}
                                >
                                  {item.institution || application?.institution || item.id}
                                </button>
                                <div className="institution-name">{item.id}</div>
                              </td>
                              <td>{application?.region || 'Unknown'}</td>
                              <td>{item.stage || application?.stage || 'Unknown'}</td>
                              <td>{item.due_date || application?.due_date || 'Not set'}</td>
                              <td><span className={`risk-badge risk-${(item.risk_level || 'Unknown').toLowerCase()}`}>{item.risk_level || 'Unknown'}</span></td>
                              <td>
                                <strong className="priority-label">{priority}</strong>
                                <div className="priority-reason">{priorityReason}</div>
                                {['Critical', 'High'].includes(item.risk_level) && (item.risk_recommendations || []).length > 0 && (
                                  <div className="priority-recommendation">Recommended: {item.risk_recommendations[0]}</div>
                                )}
                              </td>
                              <td>
                                <button
                                  className="open-button"
                                  aria-label={`Open application ${item.id}`}
                                  onClick={(event) => {
                                    event.stopPropagation()
                                    setSelectedId(item.id)
                                    setPage('Applications')
                                  }}
                                >→</button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </section>

                <section className="panel dashboard-activity-panel" aria-labelledby="recent-activity-heading">
                  <div className="section-heading">
                    <div>
                      <h2 id="recent-activity-heading">Recent activity</h2>
                      <p>Latest events from the existing audit trail.</p>
                    </div>
                  </div>
                  {auditEvents.length === 0 ? (
                    <div className="empty-state">No audit activity is available.</div>
                  ) : auditEvents.slice(0, 5).map((event) => (
                    <div className="dashboard-activity-item" key={event.id}>
                      <span className="dashboard-activity-mark" aria-hidden="true">•</span>
                      <div className="dashboard-activity-copy">
                        <strong>{event.action || 'Audit event'}</strong>
                        <p>{event.application_id || 'Application unavailable'}{event.stage ? ` · ${event.stage}` : ''}</p>
                        {event.actor && <span>By {event.actor}</span>}
                        <time dateTime={event.timestamp || undefined}>
                          {event.timestamp ? formatKolkataTimestamp(event.timestamp) : 'Timestamp unavailable'}
                        </time>
                      </div>
                      {event.application_id && (
                        <button
                          className="activity-open-button"
                          aria-label={`Open application ${event.application_id}`}
                          onClick={() => {
                            setSelectedId(event.application_id)
                            setPage('Applications')
                          }}
                        >→</button>
                      )}
                    </div>
                  ))}
                </section>
              </div>

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
              <ApplicationTable rows={filtered} selectedId={selectedId} onSelect={setSelectedId} loading={loading} />
            </div>
          )}

          {page === 'Applications' && (
            <div className="applications-page">
              <div className="section-heading applications-heading">
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
              <ApplicationTable rows={filtered} selectedId={selectedId} onSelect={setSelectedId} loading={loading} />
            </div>
          )}

          {page === 'Monitoring' && (
            <>
              <div className="stats-grid">
                <Stat label="Applications monitored" value={monitoring?.total_applications ?? '—'} note="Current records" symbol="▤" />
                <Stat label="Overdue deadlines" value={monitoring ? overdueCount : '—'} note="Requires attention" symbol="!" />
                <Stat label="Document expiry alerts" value={monitoring ? expiryCount : '—'} note="Check validity" symbol="◷" />
                <Stat label="Audit events" value={auditEvents.length} note="Persisted event log" symbol="≡" />
              </div>

              <section className="panel regional-heatmap" aria-labelledby="regional-heatmap-title">
                <div className="section-heading">
                  <div>
                    <h2 id="regional-heatmap-title">Regional workflow heatmap</h2>
                    <p>Explore applications across recorded regions and configured workflow stages.</p>
                  </div>
                  <label className="heatmap-metric-select" htmlFor="heatmap-metric">
                    Metric
                    <select
                      id="heatmap-metric"
                      value={heatmapMetric}
                      onChange={(event) => {
                        const metric = event.target.value
                        heatmapMetricRef.current = metric
                        setHeatmapMetric(metric)
                        refreshHeatmap(metric)
                      }}
                    >
                      <option value="applications">Application count</option>
                      <option value="overdue">Overdue applications</option>
                      <option value="high_risk">High-risk applications</option>
                    </select>
                  </label>
                </div>
                <p className="heatmap-explanation">
                  {heatmapMetric === 'applications'
                    ? 'This shows workload only; a larger count does not indicate a compliance problem.'
                    : heatmapMetric === 'overdue'
                      ? 'Counts applications with a valid past due date that are not completed.'
                      : 'Counts applications rated High or Critical by the configured rule-based risk assessment.'}
                </p>
                {heatmap && (
                  <p className="heatmap-count-summary" aria-live="polite">
                    {heatmap.metric_label}: <strong>{heatmap.matching_applications}</strong>
                    {' '}of {heatmap.total_applications} total applications
                  </p>
                )}
                <p className="heatmap-data-note">
                  Region labels are shown as recorded. Current records use broad regional labels, not state-level locations; missing regions are kept separate.
                </p>

                {heatmapLoading && <div className="empty-state" role="status">Loading regional workflow data…</div>}
                {!heatmapLoading && heatmapError && (
                  <div className="heatmap-error" role="alert">{heatmapError}</div>
                )}
                {!heatmapLoading && !heatmapError && heatmap && heatmap.total_applications === 0 && (
                  <div className="empty-state">No applications are available to display in the heatmap.</div>
                )}
                {!heatmapLoading && !heatmapError && heatmap && heatmap.total_applications > 0 && (
                  <>
                    <div className="heatmap-legend" aria-label="Heatmap color intensity legend">
                      <span>Fewer</span>
                      <span className="heatmap-legend-swatch heatmap-intensity-0">0</span>
                      <span className={`heatmap-legend-swatch ${heatmapMetric === 'applications' ? 'heatmap-intensity-1' : 'heatmap-issue-intensity-1'}`}>1–25%</span>
                      <span className={`heatmap-legend-swatch ${heatmapMetric === 'applications' ? 'heatmap-intensity-2' : 'heatmap-issue-intensity-2'}`}>26–50%</span>
                      <span className={`heatmap-legend-swatch ${heatmapMetric === 'applications' ? 'heatmap-intensity-3' : 'heatmap-issue-intensity-3'}`}>51–75%</span>
                      <span className={`heatmap-legend-swatch ${heatmapMetric === 'applications' ? 'heatmap-intensity-4' : 'heatmap-issue-intensity-4'}`}>76–100%</span>
                      <span>More</span>
                    </div>
                    <div className="heatmap-scroll">
                      <table className="heatmap-table">
                        <thead>
                          <tr>
                            <th scope="col">REGION (AS RECORDED)</th>
                            {heatmap.stages.map((stage) => <th scope="col" key={stage}>{stage}</th>)}
                          </tr>
                        </thead>
                        <tbody>
                          {heatmap.regions.map((region) => (
                            <tr key={region.key}>
                              <th scope="row">
                                {region.label}
                                {region.is_unknown && <span className="heatmap-unknown-label">Missing location</span>}
                              </th>
                              {region.cells.map((cell) => {
                                const intensity = cell.count === 0 || !heatmap.max_count
                                  ? 0
                                  : Math.min(4, Math.ceil((cell.count / heatmap.max_count) * 4))
                                const selectedCell = selectedHeatmapCell?.key === region.key
                                  && selectedHeatmapCell.stage === cell.stage
                                const intensityClass = heatmapMetric === 'applications'
                                  ? `heatmap-intensity-${intensity}`
                                  : `heatmap-issue-intensity-${intensity}`
                                return (
                                  <td key={cell.stage}>
                                    <button
                                      type="button"
                                      className={`heatmap-cell ${intensityClass} ${selectedCell ? 'heatmap-cell-selected' : ''}`}
                                      aria-label={`${region.label}, ${cell.stage}: ${cell.count} ${heatmap.metric_label.toLowerCase()}`}
                                      aria-pressed={selectedCell}
                                      onClick={() => setSelectedHeatmapCell({
                                        key: region.key,
                                        region: region.label,
                                        stage: cell.stage,
                                        count: cell.count,
                                        applications: cell.applications,
                                      })}
                                    >
                                      {cell.count}
                                    </button>
                                  </td>
                                )
                              })}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    {selectedHeatmapCell && (
                      <div className="heatmap-cell-details" aria-live="polite">
                        <div className="section-heading">
                          <div>
                            <h3>{selectedHeatmapCell.region} · {selectedHeatmapCell.stage}</h3>
                            <p>{selectedHeatmapCell.count} {heatmap.metric_label.toLowerCase()}</p>
                          </div>
                          <button
                            className="close-button"
                            type="button"
                            aria-label="Close heatmap cell details"
                            onClick={() => setSelectedHeatmapCell(null)}
                          >×</button>
                        </div>
                        {selectedHeatmapCell.applications.length === 0 ? (
                          <p className="muted">No applications match this region, stage, and metric.</p>
                        ) : (
                          <ul className="heatmap-application-list">
                            {selectedHeatmapCell.applications.map((application) => (
                              <li key={application.id}>
                                <span>
                                  <strong>{application.institution}</strong>
                                  <small>{application.id} · {application.status}</small>
                                </span>
                                {applications.some((item) => item.id === application.id) && (
                                  <button
                                    className="application-link"
                                    type="button"
                                    onClick={() => {
                                      setSelectedId(application.id)
                                      setPage('Applications')
                                    }}
                                  >Open application</button>
                                )}
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                    )}
                  </>
                )}
              </section>

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
                    <div className="audit-row monitoring-application-row" key={item.id}>
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
                          <p className={`compliance-alert compliance-alert-${(alert.severity || 'info').toLowerCase()}`} key={`${item.id}-${index}`}>
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
            <div className="panel evc-tracking-page">
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
            <div className="panel notifications-page">
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
            <div className="panel document-tracking-page">
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
                        <th scope="col">APPLICATION / INSTITUTION</th>
                        <th scope="col">DOCUMENT</th>
                        <th scope="col">DATES</th>
                        <th scope="col">TRACKING STATUS</th>
                        <th scope="col">RENEWAL ACTION</th>
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
            <div className="panel audit-page">
              <div className="section-heading">
                <div><h2>Persistent audit trail</h2><p>Events loaded from the backend database.</p></div>
                <button className="secondary-button" onClick={() => refreshData().catch((error) => showNotice(error.message, 'error'))}>Refresh</button>
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
                value={complianceDateDraft.dueDate}
                disabled={!canProcess || busy}
                onChange={(event) => setComplianceDateDraft((current) => ({
                  ...current,
                  dueDate: event.target.value,
                }))}
              />

              <label htmlFor="expiry-date">Document expiry date</label>
              <input
                id="expiry-date"
                type="date"
                value={complianceDateDraft.expiryDate}
                disabled={!canProcess || busy}
                onChange={(event) => setComplianceDateDraft((current) => ({
                  ...current,
                  expiryDate: event.target.value,
                }))}
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
              The role selector is for this academic prototype, not production authorization. Admin can configure workflows; Admin and Officer can process applications; Viewer is read-only.
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
  const maximumCount = Math.max(0, ...entries.map(([, count]) => Number(count) || 0))

  return (
    <div className="panel dashboard-summary">
      <h2>{title}</h2>
      {entries.length === 0 ? (
        <div className="empty-state">No applications to summarize.</div>
      ) : (
        <div className="dashboard-bar-list" role="list">
          {entries.map(([label, count]) => {
            const numericCount = Number(count) || 0
            const barWidth = maximumCount > 0 ? (numericCount / maximumCount) * 100 : 0
            return (
              <div className="dashboard-bar-item" role="listitem" aria-label={`${label}: ${numericCount}`} key={label}>
                <div className="dashboard-bar-label">
                  <span>{label}</span>
                  <strong>{numericCount}</strong>
                </div>
                <div
                  className="dashboard-bar-track"
                  role="progressbar"
                  aria-label={`${label} applications`}
                  aria-valuemin="0"
                  aria-valuemax={maximumCount || 1}
                  aria-valuenow={numericCount}
                >
                  <span style={{ width: `${barWidth}%` }} />
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function ApplicationFilters({ search, setSearch, filter, setFilter }) {
  return (
    <div className="filters">
      <div className="search-box">
        <span>⌕</span>
        <input
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search ID, institution, officer..."
          aria-label="Search applications by ID, institution, officer, or region"
        />
      </div>
      <select value={filter} onChange={(event) => setFilter(event.target.value)} aria-label="Filter by status">
        {['All', 'Pending', 'Under Review', 'Approved', 'Returned'].map((status) => (
          <option key={status} value={status}>{status === 'All' ? 'All statuses' : status}</option>
        ))}
      </select>
    </div>
  )
}

function ApplicationTable({ rows, selectedId, onSelect, loading = false }) {
  return (
    <div className="table-wrap" aria-busy={loading}>
      <table>
        <thead><tr><th scope="col">APPLICATION / INSTITUTION</th><th scope="col">SUBMITTED</th><th scope="col">STAGE</th><th scope="col">OFFICER</th><th scope="col">STATUS</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
        <tbody>
          {rows.map((item) => (
            <tr key={item.id} className={selectedId === item.id ? 'selected-row' : ''}>
              <td>
                <button type="button" className="application-link" onClick={() => onSelect(item.id)}>{item.id}</button>
                <div className="institution-name">{item.institution}</div>
              </td>
              <td>{item.date || '—'}</td>
              <td>{item.stage}</td>
              <td>{item.officer}<div className="institution-name">{item.region} Region</div></td>
              <td><Status status={item.status} /></td>
              <td><button type="button" className="open-button" onClick={() => onSelect(item.id)} aria-label={`Review ${item.id}`}>→</button></td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr><td colSpan="6" className="empty-state" role="status">
              {loading ? 'Loading applications…' : 'No applications match your search.'}
            </td></tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

export default App
