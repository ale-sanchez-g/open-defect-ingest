import { useState } from 'react'

const API_BASE = '/api'

export default function SummaryPanel() {
  const [summary, setSummary] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState(null)

  const fetchSummary = async () => {
    setLoading(true)
    setError(null)
    setSummary(null)

    try {
      const res  = await fetch(`${API_BASE}/defects/summary`)
      const data = await res.json()
      if (res.ok) {
        setSummary(data)
      } else {
        setError(data.detail ?? 'Failed to generate summary')
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

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

      {loading && (
        <div className="loading">
          <div className="spinner" />
          <div>Analyzing defects with local LLM…</div>
          <div style={{ fontSize: '0.8rem', marginTop: '0.5rem', color: 'var(--text-secondary)' }}>
            This may take a moment depending on the model size.
          </div>
        </div>
      )}

      {summary && (
        <>
          <div className="stats-grid">
            <div className="stat-card">
              <div className="stat-value">{summary.total}</div>
              <div className="stat-label">Total Defects Analyzed</div>
            </div>
          </div>
          <div className="card">
            <h2>📝 Analysis</h2>
            <div className="summary-text">{summary.summary}</div>
          </div>
        </>
      )}
    </div>
  )
}
