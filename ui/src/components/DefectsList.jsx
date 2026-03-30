import { useState, useEffect, useCallback } from 'react'

const API_BASE  = '/api'
const PAGE_SIZE = 10

function SeverityBadge({ severity }) {
  const s = (severity || 'medium').toLowerCase()
  return <span className={`badge badge-${s}`}>{s}</span>
}

function StatusBadge({ status }) {
  const s   = (status || 'open').toLowerCase()
  const cls = s === 'open' ? 'badge-open' : ['closed','resolved'].includes(s) ? 'badge-closed' : 'badge-in-progress'
  return <span className={`badge ${cls}`}>{s}</span>
}

export default function DefectsList() {
  const [defects, setDefects] = useState([])
  const [total,   setTotal]   = useState(0)
  const [offset,  setOffset]  = useState(0)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState(null)

  const fetchDefects = useCallback(async (newOffset = 0) => {
    setLoading(true)
    setError(null)
    try {
      const res  = await fetch(`${API_BASE}/defects/list?limit=${PAGE_SIZE}&offset=${newOffset}`)
      const data = await res.json()
      if (res.ok) {
        setDefects(data.defects ?? [])
        setTotal(data.total ?? 0)
        setOffset(newOffset)
      } else {
        setError(data.detail ?? 'Failed to load defects')
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchDefects(0) }, [fetchDefects])

  const totalPages  = Math.ceil(total / PAGE_SIZE)
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1

  return (
    <div className="card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
        <h2>📋 Defects ({total})</h2>
        <button className="btn btn-secondary" onClick={() => fetchDefects(offset)}>
          🔄 Refresh
        </button>
      </div>

      {error && <div className="alert alert-error">❌ {error}</div>}

      {loading ? (
        <div className="loading">
          <div className="spinner" />
          <div>Loading defects…</div>
        </div>
      ) : defects.length === 0 ? (
        <p style={{ color: 'var(--text-secondary)', textAlign: 'center', padding: '2rem' }}>
          No defects ingested yet — use the <strong>Ingest Defect</strong> tab to add some!
        </p>
      ) : (
        <>
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Title</th>
                  <th>Project</th>
                  <th>Severity</th>
                  <th>Status</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {defects.map((d) => (
                  <tr key={d.id}>
                    <td style={{ fontFamily: 'monospace', fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
                      {d.id?.length > 12 ? `${d.id.substring(0, 12)}…` : d.id}
                    </td>
                    <td>{d.metadata?.title || d.document?.substring(0, 60) || d.id}</td>
                    <td>
                      {d.metadata?.project ? (
                        <span className="badge" style={{ background: 'rgba(188,140,255,.2)', color: 'var(--purple)' }}>
                          {d.metadata.project}
                        </span>
                      ) : '—'}
                    </td>
                    <td><SeverityBadge severity={d.metadata?.severity} /></td>
                    <td><StatusBadge   status={d.metadata?.status} /></td>
                    <td style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                      {d.metadata?.created_at
                        ? new Date(d.metadata.created_at).toLocaleDateString()
                        : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {totalPages > 1 && (
            <div className="pagination">
              <button
                className="btn btn-secondary"
                disabled={offset === 0}
                onClick={() => fetchDefects(Math.max(0, offset - PAGE_SIZE))}
              >
                ← Prev
              </button>
              <span>Page {currentPage} of {totalPages}</span>
              <button
                className="btn btn-secondary"
                disabled={offset + PAGE_SIZE >= total}
                onClick={() => fetchDefects(offset + PAGE_SIZE)}
              >
                Next →
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
