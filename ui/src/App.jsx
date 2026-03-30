import { useState } from 'react'
import QueueDashboard from './components/QueueDashboard'
import IngestForm from './components/IngestForm'
import QueryPanel from './components/QueryPanel'
import SummaryPanel from './components/SummaryPanel'
import DefectsList from './components/DefectsList'
import './App.css'

const TABS = [
  { id: 'dashboard', label: '📊 Queue Dashboard' },
  { id: 'ingest',    label: '➕ Ingest Defect'   },
  { id: 'query',     label: '🔍 Query'            },
  { id: 'summary',   label: '🤖 AI Summary'       },
  { id: 'defects',   label: '📋 Defects'          },
]

function App() {
  const [activeTab, setActiveTab] = useState('dashboard')

  return (
    <div className="app">
      <header className="app-header">
        <h1>🐛 Defect Intelligence Hub</h1>
        <p>Powered by RabbitMQ · ChromaDB · Local LLM (Ollama)</p>
      </header>

      <nav className="tab-nav">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            className={`tab-btn${activeTab === tab.id ? ' active' : ''}`}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      <main className="app-main">
        {activeTab === 'dashboard' && <QueueDashboard />}
        {activeTab === 'ingest'    && <IngestForm />}
        {activeTab === 'query'     && <QueryPanel />}
        {activeTab === 'summary'   && <SummaryPanel />}
        {activeTab === 'defects'   && <DefectsList />}
      </main>
    </div>
  )
}

export default App
