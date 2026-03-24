import { useState } from 'react'

const API_BASE = '/api'

const STEP_LABELS = {
  patterns:       'Recurring Patterns',
  severity:       'Severity Trends',
  critical_areas: 'Critical Areas',
}

export default function SummaryPanel() {
  const [total,      setTotal]      = useState(null)
  const [steps,      setSteps]      = useState({})
  const [synthesis,  setSynthesis]  = useState('')
  const [done,       setDone]       = useState(false)
  const [loading,    setLoading]    = useState(false)
  const [error,      setError]      = useState(null)

  const fetchSummary = async () => {
    setLoading(true)
    setError(null)
    setTotal(null)
    setSteps({})
    setSynthesis('')
    setDone(false)

    try {
      const res = await fetch(`${API_BASE}/defects/summary/stream`)
      if (!res.ok) {
        const data = await res.json()
        setError(data.detail ?? 'Failed to generate summary')
        return
      }

      const reader  = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer    = ''
      let eventType = ''

      while (true) {
        const { done: streamDone, value } = await reader.read()
        if (streamDone) break
        buffer += decoder.decode(value, { stream: true })

        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            eventType = line.slice(7).trim()
          } else if (line.startsWith('data: ')) {
            const data = JSON.parse(line.slice(6))
            if (eventType === 'start') {
              setTotal(data.total)
            } else if (eventType === 'progress') {
              setSteps(prev => ({ ...prev, [data.step]: data.content }))
            } else if (eventType === 'token') {
              setSynthesis(prev => prev + data.token)
            } else if (eventType === 'error') {
              setError(data.detail)
            } else if (eventType === 'done') {
              if (data.total !== undefined) setTotal(data.total)
              if (data.summary) setSynthesis(data.summary)
              setDone(true)
            }
            eventType = ''
          }
        }
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
      setDone(true)
    }
  }

  const completedSteps = Object.keys(steps)
  const allStepsDone   = completedSteps.length === Object.keys(STEP_LABELS).length

  return (
    <div>
      <div className="card">
        <h2>🤖 AI-Powered Defect Summary</h2>
        <p style={{ color: 'var(--text-secondary)', fontSize: '0.875rem', marginBottom: '1.5rem' }}>
          Generates an analysis of all ingested defects using the local LLM. Identifies patterns,
          common issues, and areas needing attention.
        </p>
        <button className="btn" onClick={fetchSummary} disabled={loading}>
          {loading ? 'Analyzing…' : '🤖 Generate Summary'}
        </button>
      </div>

      {error && <div className="alert alert-error">❌ {error}</div>}

      {loading && total === null && (
        <div className="loading">
          <div className="spinner" />
          <div>Starting analysis…</div>
          <div style={{ fontSize: '0.8rem', marginTop: '0.5rem', color: 'var(--text-secondary)' }}>
            This may take a moment depending on the model size.
          </div>
        </div>
      )}

      {total !== null && (
        <div className="stats-grid">
          <div className="stat-card">
            <div className="stat-value">{total}</div>
            <div className="stat-label">Total Defects Analyzed</div>
          </div>
        </div>
      )}

      {total !== null && Object.entries(STEP_LABELS).map(([key, label]) => {
        const content = steps[key]
        if (content) {
          return (
            <div className="card" key={key}>
              <h2>✅ {label}</h2>
              <div className="summary-text" style={{ whiteSpace: 'pre-wrap' }}>{content}</div>
            </div>
          )
        }
        if (loading && !done) {
          return (
            <div className="card" key={key} style={{ opacity: 0.55 }}>
              <h2>
                ⏳ {label}
                <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginLeft: '0.75rem', fontWeight: 'normal' }}>
                  Analyzing…
                </span>
              </h2>
            </div>
          )
        }
        return null
      })}

      {(synthesis || (loading && allStepsDone)) && (
        <div className="card">
          <h2>
            📝 Synthesis
            {!done && (
              <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginLeft: '0.75rem', fontWeight: 'normal' }}>
                Generating…
              </span>
            )}
          </h2>
          <div className="summary-text" style={{ whiteSpace: 'pre-wrap' }}>{synthesis}</div>
        </div>
      )}
    </div>
  )
}
