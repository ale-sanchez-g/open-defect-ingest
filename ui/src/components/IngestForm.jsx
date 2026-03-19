import { useState } from 'react'

const API_BASE = '/api'
const SEVERITIES = ['critical', 'high', 'medium', 'low']
const STATUSES   = ['open', 'in-progress', 'resolved', 'closed']

const EMPTY_FORM = {
  id:          '',
  title:       '',
  description: '',
  project:     '',
  severity:    'medium',
  status:      'open',
}

export default function IngestForm() {
  const [form, setForm]       = useState(EMPTY_FORM)
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState(null)

  const handleChange = (e) =>
    setForm((prev) => ({ ...prev, [e.target.name]: e.target.value }))

  const handleSubmit = async (e) => {
    e.preventDefault()
    setLoading(true)
    setMessage(null)

    try {
      const payload = { ...form }
      if (!payload.id) delete payload.id

      const res  = await fetch(`${API_BASE}/defects/ingest`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(payload),
      })
      const data = await res.json()

      if (res.ok) {
        setMessage({
          type: 'success',
          text: `✅ Defect queued successfully! ID: ${data.defect_id ?? 'auto-assigned'}`,
        })
        setForm(EMPTY_FORM)
      } else {
        setMessage({ type: 'error', text: `❌ Failed: ${data.detail ?? 'Unknown error'}` })
      }
    } catch (err) {
      setMessage({ type: 'error', text: `❌ Network error: ${err.message}` })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="card">
      <h2>➕ Ingest New Defect</h2>
      <p style={{ color: 'var(--text-secondary)', fontSize: '0.875rem', marginBottom: '1.5rem' }}>
        Submit a defect to the RabbitMQ queue. The ingestor worker will embed it and store it in ChromaDB.
      </p>

      {message && <div className={`alert alert-${message.type}`}>{message.text}</div>}

      <form onSubmit={handleSubmit}>
        <div className="form-group">
          <label>Defect ID <span style={{ color: 'var(--text-secondary)' }}>(optional — leave blank to auto-generate)</span></label>
          <input
            type="text"
            name="id"
            value={form.id}
            onChange={handleChange}
            placeholder="e.g. BUG-1234"
          />
        </div>

        <div className="form-group">
          <label>Title <span style={{ color: 'var(--error)' }}>*</span></label>
          <input
            type="text"
            name="title"
            value={form.title}
            onChange={handleChange}
            placeholder="Brief description of the defect"
            required
          />
        </div>

        <div className="form-group">
          <label>Description <span style={{ color: 'var(--error)' }}>*</span></label>
          <textarea
            name="description"
            value={form.description}
            onChange={handleChange}
            placeholder="Detailed description, steps to reproduce, expected vs. actual behaviour…"
            required
          />
        </div>

        <div className="form-group">
          <label>Project <span style={{ color: 'var(--error)' }}>*</span></label>
          <input
            type="text"
            name="project"
            value={form.project}
            onChange={handleChange}
            placeholder="e.g. auth-service, payment-api, frontend"
            required
          />
        </div>

        <div className="form-row">
          <div className="form-group">
            <label>Severity</label>
            <select name="severity" value={form.severity} onChange={handleChange}>
              {SEVERITIES.map((s) => (
                <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>
              ))}
            </select>
          </div>
          <div className="form-group">
            <label>Status</label>
            <select name="status" value={form.status} onChange={handleChange}>
              {STATUSES.map((s) => (
                <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>
              ))}
            </select>
          </div>
        </div>

        <button type="submit" className="btn" disabled={loading}>
          {loading ? 'Submitting…' : '🚀 Submit Defect'}
        </button>
      </form>
    </div>
  )
}
