import { useState, useEffect, useCallback } from 'react'

const API_BASE = '/api'

function StateBadge({ state }) {
  const norm = (state || 'unknown').toLowerCase()
  const cls =
    norm === 'running' ? 'badge-running' :
    norm === 'idle'    ? 'badge-idle'    :
    'badge-unknown'
  return <span className={`badge ${cls}`}>{norm}</span>
}

export default function QueueDashboard() {
  const [stats, setStats]           = useState(null)
  const [loading, setLoading]       = useState(true)
  const [lastUpdated, setLastUpdated] = useState(null)

  const fetchStats = useCallback(async () => {
    try {
      const res  = await fetch(`${API_BASE}/queue/stats`)
      const data = await res.json()
      setStats(data)
      setLastUpdated(new Date())
    } catch (err) {
      console.error('Failed to fetch queue stats:', err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchStats()
    const timer = setInterval(fetchStats, 5000)
    return () => clearInterval(timer)
  }, [fetchStats])

  return (
    <div>
      {/* Queue stats card */}
      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
          <h2>📊 Queue Dashboard</h2>
          <div className="refresh-indicator">
            <div className="dot" />
            <span>Auto-refreshing every 5 s</span>
            {lastUpdated && <span>· {lastUpdated.toLocaleTimeString()}</span>}
          </div>
        </div>

        {loading ? (
          <div className="loading">
            <div className="spinner" />
            <div>Loading queue stats…</div>
          </div>
        ) : stats ? (
          <>
            <div className="stats-grid">
              <div className="stat-card">
                <div className="stat-value">{stats.messages ?? 0}</div>
                <div className="stat-label">Total Messages</div>
              </div>
              <div className="stat-card">
                <div className="stat-value" style={{ color: 'var(--success)' }}>
                  {stats.messages_ready ?? 0}
                </div>
                <div className="stat-label">Ready</div>
              </div>
              <div className="stat-card">
                <div className="stat-value" style={{ color: 'var(--warning)' }}>
                  {stats.messages_unacknowledged ?? 0}
                </div>
                <div className="stat-label">Unacknowledged</div>
              </div>
              <div className="stat-card">
                <div className="stat-value" style={{ color: 'var(--purple)' }}>
                  {stats.consumers ?? 0}
                </div>
                <div className="stat-label">Consumers</div>
              </div>
            </div>

            <div style={{ display: 'flex', gap: '1rem', alignItems: 'center', flexWrap: 'wrap' }}>
              <span style={{ fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
                Queue:&nbsp;<strong style={{ color: 'var(--text-primary)' }}>{stats.queue}</strong>
              </span>
              <span style={{ fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
                State:&nbsp;<StateBadge state={stats.state} />
              </span>
            </div>

            {stats.error && (
              <div className="alert alert-error" style={{ marginTop: '1rem' }}>
                ⚠️ Could not reach RabbitMQ management API: {stats.error}
              </div>
            )}
          </>
        ) : (
          <div className="alert alert-error">Failed to load queue statistics.</div>
        )}
      </div>

      {/* Link to native RabbitMQ management UI */}
      <div className="card">
        <h2>🐰 RabbitMQ Management Console</h2>
        <p style={{ color: 'var(--text-secondary)', fontSize: '0.875rem', marginBottom: '1rem' }}>
          Access the full RabbitMQ management UI for detailed queue, exchange, and connection monitoring.
        </p>
        <a
          href="http://localhost:15672"
          target="_blank"
          rel="noopener noreferrer"
          className="btn btn-secondary"
          style={{ display: 'inline-block', textDecoration: 'none' }}
        >
          Open RabbitMQ Management →
        </a>
      </div>
    </div>
  )
}
