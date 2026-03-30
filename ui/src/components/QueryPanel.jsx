import { useState } from 'react'

const API_BASE = '/api'

function SeverityBadge({ severity }) {
  const s = (severity || 'medium').toLowerCase()
  return <span className={`badge badge-${s}`}>{s}</span>
}

function ProjectBadge({ project }) {
  if (!project) return null
  return (
    <span className="badge" style={{ background: 'rgba(188,140,255,.2)', color: 'var(--purple)' }}>
      📁 {project}
    </span>
  )
}

export default function QueryPanel() {
  const [query,            setQuery]            = useState('')
  const [nResults,         setNResults]         = useState(5)
  const [results,          setResults]          = useState(null)
  const [analysis,         setAnalysis]         = useState('')
  const [analysisComplete, setAnalysisComplete] = useState(false)
  const [loading,          setLoading]          = useState(false)
  const [error,            setError]            = useState(null)

  const handleSearch = async (e) => {
    e.preventDefault()
    if (!query.trim()) return

    setLoading(true)
    setError(null)
    setResults(null)
    setAnalysis('')
    setAnalysisComplete(false)

    try {
      const res = await fetch(`${API_BASE}/defects/query/stream`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ query: query.trim(), n_results: nResults }),
      })

      if (!res.ok) {
        const data = await res.json()
        setError(data.detail ?? 'Query failed')
        return
      }

      const reader  = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer    = ''
      let eventType = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            eventType = line.slice(7).trim()
          } else if (line.startsWith('data: ')) {
            const data = JSON.parse(line.slice(6))
            if (eventType === 'results') {
              setResults(data)
              setLoading(false)
            } else if (eventType === 'token') {
              setAnalysis(prev => prev + data.token)
            } else if (eventType === 'error') {
              setError(data.detail)
            } else if (eventType === 'done') {
              setAnalysisComplete(true)
            }
            eventType = ''
          }
        }
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
      setAnalysisComplete(true)
    }
  }

  return (
    <div>
      <div className="card">
        <h2>🔍 Semantic Search</h2>
        <p style={{ color: 'var(--text-secondary)', fontSize: '0.875rem', marginBottom: '1.5rem' }}>
          Search ingested defects using natural language. Powered by Ollama embeddings and ChromaDB vector search.
        </p>

        <form onSubmit={handleSearch}>
          <div className="form-group">
            <label>Search Query</label>
            <textarea
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="e.g. 'users cannot log in', 'memory leak in production', 'payment processing fails'"
              style={{ minHeight: '80px' }}
            />
          </div>

          <div style={{ display: 'flex', gap: '1rem', alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <div className="form-group" style={{ marginBottom: 0, flex: '0 0 140px' }}>
              <label>Max Results</label>
              <input
                type="number"
                min={1}
                max={20}
                value={nResults}
                onChange={(e) => setNResults(Number(e.target.value))}
              />
            </div>
            <button type="submit" className="btn" disabled={loading || !query.trim()}>
              {loading ? 'Searching…' : '🔍 Search'}
            </button>
          </div>
        </form>
      </div>

      {error && <div className="alert alert-error">❌ {error}</div>}

      {loading && (
        <div className="loading">
          <div className="spinner" />
          <div>Generating embeddings and searching ChromaDB…</div>
        </div>
      )}

      {results && (
        <>
          <div className="card">
            <h2>Results ({results.results.length})</h2>

            {results.results.length === 0 ? (
              <p style={{ color: 'var(--text-secondary)' }}>No matching defects found.</p>
            ) : (
              results.results.map((r, i) => (
                <div key={i} className="result-card">
                  <h4>{r.metadata?.title || r.id}</h4>
                  <p className="result-doc">{r.document}</p>
                  <div className="result-meta">
                    <ProjectBadge project={r.metadata?.project} />
                    <SeverityBadge severity={r.metadata?.severity} />
                    {r.metadata?.status && (
                      <span className={`badge badge-${(r.metadata.status).toLowerCase()}`}>
                        {r.metadata.status}
                      </span>
                    )}
                    {r.distance != null && (
                      <span className="result-distance">
                        similarity: {(1 - r.distance).toFixed(3)}
                      </span>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>

          {(analysis || !analysisComplete) && results.results.length > 0 && (
            <div className="card">
              <h2>
                🧠 AI Analysis
                {!analysisComplete && (
                  <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginLeft: '0.75rem', fontWeight: 'normal' }}>
                    Generating…
                  </span>
                )}
              </h2>
              <div className="summary-text" style={{ whiteSpace: 'pre-wrap' }}>
                {analysis}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
