import { useEffect, useRef, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { Plus, Target, Zap, Square, FileText, ChevronDown, ChevronUp, Save, HelpCircle, Pencil } from 'lucide-react'
import api from '../api'

// ── Platform options ─────────────────────────────────────────────────────────
const PLATFORM_OPTIONS = [
  { value: 'all', label: 'Semua' },
  { value: 'linkedin', label: 'LinkedIn Jobs' },
  { value: 'linkedin_posts', label: 'LinkedIn Posts' },
  { value: 'jobstreet', label: 'JobStreet' },
]
const PLATFORM_LABELS = {
  all: 'Semua',
  linkedin: 'LinkedIn Jobs',
  linkedin_posts: 'LinkedIn Posts',
  jobstreet: 'JobStreet',
  both: 'LinkedIn Jobs + JobStreet',
}
function platformLabel(val) { return PLATFORM_LABELS[val] ?? val }

function PlatformLogo({ platform }) {
  const key = platform || 'all'
  const cfg = {
    linkedin: { text: 'in', bg: '#0077b5', color: 'white' },
    linkedin_posts: { text: 'in', bg: '#0077b5', color: 'white' },
    jobstreet: { text: 'JS', bg: 'var(--orange)', color: 'white' },
    all: { text: 'ALL', bg: 'var(--black)', color: 'white' },
  }[key] || { text: String(key).slice(0, 2).toUpperCase(), bg: 'var(--border)', color: 'var(--black)' }
  return (
    <span style={{
      width: '22px', height: '22px', flexShrink: 0,
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      background: cfg.bg, color: cfg.color,
      border: '2px solid var(--black)', boxShadow: '2px 2px 0 var(--black)',
      fontFamily: 'Dogica', fontSize: key === 'all' ? '5px' : '7px', lineHeight: 1,
    }}>{cfg.text}</span>
  )
}

const EMPLOYMENT_OPTIONS = [
  { value: 'full_time', label: 'Full Time' },
  { value: 'contract', label: 'Contract' },
  { value: 'intern', label: 'Intern' },
]
const EMPLOYMENT_LABELS = {
  full_time: 'Full Time',
  contract: 'Contract',
  intern: 'Intern',
}
function employmentLabel(val) { return EMPLOYMENT_LABELS[val] ?? 'Full Time' }

function PixelSwitch({ checked, onChange, disabled }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      style={{
        width: '104px', height: '34px', padding: '3px', flexShrink: 0,
        background: 'white',
        border: '3px solid var(--black)',
        boxShadow: '3px 3px 0 var(--black)',
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.6 : 1,
        display: 'flex', alignItems: 'center',
        justifyContent: checked ? 'flex-end' : 'flex-start',
      }}
    >
      <span style={{
        width: '42px', height: '22px',
        background: checked ? '#27ae60' : 'var(--cream-2)',
        border: '2px solid var(--black)',
        color: checked ? 'white' : 'var(--black)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontFamily: 'Dogica', fontSize: '7px', lineHeight: 1,
      }}>
        {checked ? 'ON' : 'OFF'}
      </span>
    </button>
  )
}

// ── Step config ───────────────────────────────────────────────────────────────
const STEPS = ['analisis', 'kesesuaian', 'duplikat', 'apply']
const STEP_LABELS = { analisis: 'Analisis', kesesuaian: 'Kesesuaian', duplikat: 'Duplikat', apply: 'Apply' }

function normalizeKeyPart(value) {
  return (value || '').toString().trim().toLowerCase().replace(/\s+/g, ' ')
}

function stableJobKey(event) {
  const title = normalizeKeyPart(event.job_title || event.position)
  const company = normalizeKeyPart(event.company)
  const location = normalizeKeyPart(event.job_location || event.location)
  if (title && company) return [event.platform, title, company, location].join('|')
  return event.job_id || event.job_url || [event.platform, title, company, location, Date.now()].join('|')
}

function promptKind(question) {
  const fieldType = (question?.answer_mode || question?.field_type || '').toLowerCase()
  const text = `${question?.question || ''} ${fieldType}`.toLowerCase()
  if (fieldType === 'number' || /\b(gaji|salary|umur|usia|tahun|bulan|years?|months?|nominal|amount)\b/.test(text)) return 'number'
  if (fieldType === 'yes_no') return 'yes_no'
  if (fieldType === 'dropdown') return promptOptions(question).length ? 'dropdown' : 'text'
  if (fieldType === 'textarea') return 'textarea'
  return text.length > 180 ? 'textarea' : 'text'
}

function promptOptions(question) {
  if (Array.isArray(question?.options) && question.options.length) return question.options
  const raw = question?.question || ''
  const match = raw.match(/options:\s*([\s\S]*)$/i)
  if (!match) return []
  const seen = new Set()
  return match[1]
    .split(/;|\n|,/)
    .map(v => v.trim())
    .filter(Boolean)
    .filter(v => !/^(select an option|select|pilih|choose|-|--|none)$/i.test(v))
    .filter(v => {
      const key = v.toLowerCase()
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
    .slice(0, 20)
}

function promptQuestionText(question) {
  return (question?.question || '').replace(/\n?Options:\s*[\s\S]*$/i, '').trim() || question?.question || ''
}

function formatStatusMessage(message = '') {
  const text = String(message || '').trim()
  if (!text) return ''
  if (/^Halaman LinkedIn terbuka:/i.test(text)) return 'Halaman LinkedIn terbuka'
  if (/^Tidak ada halaman LinkedIn/i.test(text)) return text.split(';')[0]
  return text.replace(/https?:\/\/\S+/g, '[link]').slice(0, 140)
}

function StepDot({ status }) {
  const base = "w-4 h-4 flex items-center justify-center shrink-0 text-[9px]"
  if (status === 'ok')      return <span className={base} style={{ background: '#27ae60', border: '1.5px solid #1e8449', color: 'white', fontFamily: 'monospace' }}>✓</span>
  if (status === 'fail')    return <span className={base} style={{ background: '#e74c3c', border: '1.5px solid #c0392b', color: 'white', fontFamily: 'monospace' }}>✗</span>
  if (status === 'skip')    return <span className={base} style={{ background: '#f39c12', border: '1.5px solid #d68910', color: 'white', fontFamily: 'monospace' }}>-</span>
  if (status === 'running') return <span className={base} style={{ background: 'var(--orange)', border: '1.5px solid var(--orange-2)', color: 'white', animation: 'blink 0.8s step-end infinite', fontFamily: 'monospace' }}>▶</span>
  return <span className={base} style={{ background: 'var(--cream-2)', border: '1.5px solid var(--border)' }} />
}

function JobCard({ job }) {
  const isFound    = job.resultType === 'found'
  const isApplied  = job.steps?.apply === 'ok' && !isFound
  const isSkipped  = job.steps?.kesesuaian === 'skip' || job.steps?.apply === 'fail'
  
  // Platform badge colors
  const platformColors = {
    linkedin: { bg: '#0077b5', border: '#005582' },
    linkedin_posts: { bg: '#0077b5', border: '#005582' },
    jobstreet: { bg: 'var(--orange)', border: 'var(--orange-2)' },
  }
  const platformLabels = {
    linkedin: 'LI',
    linkedin_posts: 'LI',
    jobstreet: 'JS',
  }

  return (
    <div className="animate-pixel-in" style={{
      background:  isApplied ? '#eafaf1' : isFound ? '#eef6fb' : isSkipped ? '#fafafa' : 'white',
      border:      `2px solid ${isApplied ? '#27ae60' : isFound ? '#2980b9' : isSkipped ? 'var(--border)' : 'var(--black)'}`,
      boxShadow:   isApplied ? '3px 3px 0 #27ae60' : isFound ? '3px 3px 0 #2980b9' : isSkipped ? 'none' : '3px 3px 0 var(--black)',
      padding:     '12px',
      marginBottom: '8px',
      opacity:     isSkipped ? 0.65 : 1,
    }}>
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="flex-1 min-w-0">
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '2px' }}>
            <p className="font-semibold truncate" style={{ fontSize: '12px', color: 'var(--black)' }}>{job.job_title}</p>
            {job.platform && (
              <span className="font-pixel shrink-0" style={{
                fontSize: '7px',
                padding: '2px 5px',
                background: platformColors[job.platform]?.bg || 'var(--muted)',
                color: 'white',
                border: `1.5px solid ${platformColors[job.platform]?.border || 'var(--border)'}`,
              }}>
                {platformLabels[job.platform] || job.platform.toUpperCase()}
              </span>
            )}
          </div>
          <p style={{ fontSize: '11px', color: 'var(--muted)', overflowWrap: 'anywhere' }}>{job.company}</p>
          {job.location && <p style={{ fontSize: '10px', color: 'var(--muted)', overflowWrap: 'anywhere' }}>{job.location}</p>}
        </div>
        <span className="font-pixel shrink-0" style={{
          fontSize: '7px',
          padding: '3px 6px',
          background: isApplied ? '#27ae60' : isFound ? '#2980b9' : isSkipped ? 'var(--border)' : 'var(--orange)',
          color: isSkipped ? 'var(--muted)' : 'white',
          border: '1.5px solid var(--black)',
        }}>
          {isFound ? 'FOUND' : isApplied ? 'APPLIED' : isSkipped ? 'SKIP' : 'PROSES'}
        </span>
      </div>

      {/* Steps */}
      <div className="flex items-center gap-1 flex-wrap">
        {STEPS.map((step, i) => {
          const status = job.steps?.[step] ?? 'pending'
          const msg    = job.messages?.[step]
          return (
            <div key={step} className="flex items-center gap-1">
              {i > 0 && <span style={{ color: 'var(--border)', fontSize: '10px' }}>›</span>}
              <div className="flex items-center gap-1" title={msg || STEP_LABELS[step]}>
                <StepDot status={status} />
                <span style={{
                  fontSize: '9px',
                  color: status === 'running' ? 'var(--orange)' : status === 'ok' ? '#27ae60' : status === 'fail' ? '#e74c3c' : status === 'skip' ? '#f39c12' : 'var(--muted)',
                  fontWeight: status === 'running' ? 700 : 400,
                }}>
                  {STEP_LABELS[step]}
                </span>
              </div>
            </div>
          )
        })}
      </div>
      {(() => {
        const failMsg = STEPS.map(s => job.messages?.[s]).filter(Boolean).pop()
        return failMsg && isSkipped ? (
          <p style={{ fontSize: '9px', color: '#e74c3c', marginTop: '4px', fontStyle: 'italic' }}>↳ {failMsg}</p>
        ) : null
      })()}
      {job.notes && (
        <pre style={{ marginTop: '8px', whiteSpace: 'pre-wrap', fontSize: '9px', color: 'var(--black-3)', background: 'white', border: '1.5px solid var(--border)', padding: '8px', maxHeight: '120px', overflow: 'auto' }}>
          {job.notes}
        </pre>
      )}
    </div>
  )
}

function FinishModal({ jobs, sessionId, onClose, onHistory }) {
  const searched = jobs.length
  const matched = jobs.filter(j => j.steps?.kesesuaian === 'ok' || j.steps?.duplikat === 'ok' || j.steps?.apply === 'ok').length
  const applied = jobs.filter(j => j.steps?.apply === 'ok' && j.resultType !== 'found')
  const perPlatform = applied.reduce((acc, job) => {
    const key = job.platform || 'unknown'
    if (!acc[key]) acc[key] = []
    acc[key].push(job)
    return acc
  }, {})
  const platformOrder = ['linkedin', 'linkedin_posts', 'jobstreet']
  const platformName = key => ({ linkedin: 'LinkedIn Jobs', linkedin_posts: 'LinkedIn Posts', jobstreet: 'JobStreet' }[key] || key)

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 60,
      background: 'rgba(0,0,0,0.62)', display: 'flex',
      alignItems: 'center', justifyContent: 'center', padding: '20px',
    }}>
      <div className="card-pixel" style={{ width: 'min(680px, 100%)', background: '#fffcf7', overflow: 'hidden' }}>
        <div style={{ background: 'var(--black)', color: 'white', padding: '14px 18px', borderBottom: '4px solid var(--orange)' }}>
          <p className="font-title" style={{ fontSize: '32px', lineHeight: 1 }}>STAGE CLEAR</p>
          <p className="font-pixel" style={{ fontSize: '9px', color: 'var(--orange-3)', marginTop: '3px' }}>
            SESI {sessionId ? `#${sessionId}` : ''} SELESAI
          </p>
        </div>

        <div style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: '10px' }}>
            {[
              ['JOB DICEK', searched, 'var(--black)'],
              ['MATCH', matched, '#2980b9'],
              ['APPLIED', applied.length, '#27ae60'],
            ].map(([label, value, color]) => (
              <div key={label} style={{ background: 'white', border: '3px solid var(--black)', boxShadow: '3px 3px 0 var(--black)', padding: '10px', textAlign: 'center' }}>
                <p className="font-pixel" style={{ fontSize: '22px', color }}>{value}</p>
                <p style={{ fontSize: '8px', color: 'var(--muted)', fontFamily: 'Dogica' }}>{label}</p>
              </div>
            ))}
          </div>

          <div style={{ background: 'white', border: '3px solid var(--black)', padding: '12px', maxHeight: '280px', overflow: 'auto' }}>
            <p className="font-pixel" style={{ fontSize: '10px', color: 'var(--black)', marginBottom: '10px' }}>LAMARAN TERKIRIM</p>
            {applied.length === 0 ? (
              <p style={{ fontSize: '11px', color: 'var(--muted)' }}>Belum ada job yang berhasil di-apply pada sesi ini.</p>
            ) : (
              platformOrder.filter(key => perPlatform[key]?.length).map(key => (
                <div key={key} style={{ marginBottom: '12px' }}>
                  <p className="font-pixel" style={{ fontSize: '9px', color: key === 'jobstreet' ? 'var(--orange)' : '#0077b5', marginBottom: '6px' }}>
                    {platformName(key)} · {perPlatform[key].length}
                  </p>
                  {perPlatform[key].map(job => (
                    <div key={job.job_id} style={{ border: '2px solid var(--border)', padding: '8px', marginBottom: '6px', background: '#fffcf7' }}>
                      <p style={{ fontSize: '11px', fontWeight: 700, color: 'var(--black)', overflowWrap: 'anywhere' }}>{job.job_title || 'Lowongan'}</p>
                      <p style={{ fontSize: '10px', color: 'var(--muted)', overflowWrap: 'anywhere' }}>{job.company || '-'} · {job.location || '-'}</p>
                    </div>
                  ))}
                </div>
              ))
            )}
          </div>

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', flexWrap: 'wrap' }}>
            <button onClick={onClose} className="btn-pixel-ghost">CLOSE</button>
            <button onClick={onHistory} className="btn-pixel">CEK RIWAYAT LAMARAN</button>
          </div>
        </div>
      </div>
    </div>
  )
}

function samePosition(a, b) {
  return (a || '').trim().toLowerCase() === (b || '').trim().toLowerCase()
}

// ── Cover Letter Editor (per position group) ──────────────────────────────────
function CoverLetterEditor({ position, coverLetter, targetId, onSaved }) {
  const [open,   setOpen]   = useState(false)
  const [text,   setText]   = useState(coverLetter || '')
  const [saving, setSaving] = useState(false)

  // Sync jika coverLetter berubah dari luar
  useEffect(() => { setText(coverLetter || '') }, [coverLetter])

  const charCount = text.length
  const hasPlaceholderCompany  = text.includes('{perusahaan}')
  const hasPlaceholderPosition = text.includes('{posisi}')

  // Preview dengan contoh penggantian
  const preview = text
    .replace(/\{perusahaan\}/g, position.split(' ')[0] + ' Corp')
    .replace(/\{posisi\}/g, position)

  const handleSave = async () => {
    setSaving(true)
    try {
      await api.put(`/targets/${targetId}/cover-letter`, { cover_letter: text })
      onSaved && onSaved(position, text)
      setOpen(false) // Close after save
    } catch (e) {
      alert('Gagal menyimpan cover letter')
    } finally { setSaving(false) }
  }

  return (
    <div style={{ marginTop: 0 }}>
      {/* Toggle button */}
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'flex', alignItems: 'center', gap: '7px',
          background: coverLetter ? 'var(--orange)' : 'white',
          border: '2px solid var(--black)',
          boxShadow: '2px 2px 0 var(--black)',
          cursor: 'pointer',
          fontSize: '9px', color: coverLetter ? 'white' : 'var(--black)',
          fontFamily: 'Dogica', padding: '7px 10px',
        }}
      >
        <FileText size={13} />
        {coverLetter ? `COVER LETTER ✓` : `TAMBAH COVER LETTER`}
        {open ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
      </button>

      {open && (
        <div style={{ marginTop: '10px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {/* Info placeholder */}
          <div style={{
            padding: '8px 10px',
            background: '#fef9e7',
            border: '1.5px solid #f39c12',
            fontSize: '12px',
            lineHeight: 1.7,
            color: '#7d6608',
          }}>
            <p style={{ fontWeight: 700, marginBottom: '4px', fontFamily: 'Dogica' }}>PLACEHOLDER:</p>
            <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
              <span style={{
                background: hasPlaceholderCompany ? '#27ae60' : 'var(--cream-2)',
                color: hasPlaceholderCompany ? 'white' : 'var(--muted)',
                padding: '2px 6px',
                border: '1.5px solid var(--black)',
                fontFamily: 'monospace',
                fontSize: '12px',
              }}>{'{perusahaan}'}</span>
              <span style={{
                background: hasPlaceholderPosition ? '#27ae60' : 'var(--cream-2)',
                color: hasPlaceholderPosition ? 'white' : 'var(--muted)',
                padding: '2px 6px',
                border: '1.5px solid var(--black)',
                fontFamily: 'monospace',
                fontSize: '12px',
              }}>{'{posisi}'}</span>
            </div>
            <p style={{ marginTop: '4px', fontSize: '11px' }}>
              Cover letter berlaku untuk <strong>semua target posisi "{position}"</strong> (semua platform & lokasi). {'{perusahaan}'} dan {'{posisi}'} otomatis diganti saat melamar.
            </p>
          </div>

          {/* Textarea */}
          <textarea
            value={text}
            onChange={e => setText(e.target.value)}
            placeholder={`Contoh:\n\nKepada HRD {perusahaan},\n\nSaya tertarik melamar posisi {posisi} di {perusahaan}. Dengan pengalaman saya di bidang...\n\nHormat saya,\n[Nama Anda]`}
            rows={10}
            style={{
              border: '2px solid var(--black)',
              background: 'white',
              padding: '10px',
              fontSize: '14px',
              fontFamily: 'monospace',
              lineHeight: 1.7,
              width: '100%',
              outline: 'none',
              resize: 'vertical',
            }}
          />

          {/* Char count */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: '9px', color: 'var(--muted)', fontFamily: 'Dogica' }}>
              {charCount} karakter
            </span>
            <button
              onClick={handleSave}
              disabled={saving}
              style={{
                display: 'flex', alignItems: 'center', gap: '5px',
                background: 'var(--orange)',
                color: 'white',
                border: `2px solid var(--black)`,
                boxShadow: '2px 2px 0 var(--black)',
                padding: '5px 10px',
                fontSize: '10px',
                fontFamily: 'Dogica',
                cursor: 'pointer',
              }}
            >
              <Save size={11} />
              {saving ? 'MENYIMPAN...' : 'SIMPAN'}
            </button>
          </div>

          {/* Preview */}
          {text && (
            <div style={{ marginTop: '2px' }}>
              <p style={{ fontSize: '9px', color: 'var(--muted)', fontFamily: 'Dogica', marginBottom: '4px' }}>
                PREVIEW (contoh):
              </p>
              <pre style={{
                whiteSpace: 'pre-wrap',
                fontSize: '10px',
                lineHeight: 1.7,
                color: 'var(--black-3)',
                background: 'var(--cream)',
                border: '1.5px solid var(--border)',
                padding: '10px',
                maxHeight: '140px',
                overflow: 'auto',
                fontFamily: 'monospace',
              }}>
                {preview}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Target Panel ──────────────────────────────────────────────────────────────
function TargetPanel({ isRunning = false }) {
  const [targets, setTargets] = useState([])
  const [cvs, setCvs]         = useState([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving]   = useState(false)
  const [savingPrefs, setSavingPrefs] = useState(false)
  const [open, setOpen]       = useState(false)
  const [error, setError]     = useState('')
  const defaultPrefs = { expected_salary: '', available_join: '', headless_mode: false, testing_email_mode: false }
  const [prefs, setPrefs]     = useState(defaultPrefs)
  const [editingTarget, setEditingTarget] = useState(null)
  const [editingGroupIds, setEditingGroupIds] = useState([])
  const [form, setForm]       = useState({
    cv_id: '',
    positions: [''],
    locations: [''],
    platforms: ['all'],
    employment_type: 'full_time',
    expected_salary: '',
    available_join: '',
    cover_letter: '',
    showCoverLetter: false,
  })

  useEffect(() => {
    Promise.all([api.get('/targets/'), api.get('/cvs/'), api.get('/preferences/')]).then(([tRes, cRes, pRes]) => {
      setTargets(tRes.data); setCvs(cRes.data)
      setPrefs({ ...defaultPrefs, ...(pRes.data || {}) })
      if (cRes.data.length > 0) setForm(f => ({
        ...f,
        cv_id: cRes.data[0].id,
        positions: [cRes.data[0].position_label || ''],
        expected_salary: f.expected_salary || pRes.data?.expected_salary || '',
        available_join: f.available_join || pRes.data?.available_join || '',
      }))
    }).finally(() => setLoading(false))
  }, [])

  const cvPosition = cvId => cvs.find(cv => String(cv.id) === String(cvId))?.position_label || ''
  const setCv = cvId => setForm(f => ({ ...f, cv_id: cvId, positions: [cvPosition(cvId)] }))

  const addField    = k => setForm(f => ({ ...f, [k]: k === 'positions' ? f[k] : [...f[k], ''] }))
  const updateField = (k, i, v) => setForm(f => { const a = [...f[k]]; a[i] = v; return { ...f, [k]: a } })
  const resetForm = () => {
    setEditingTarget(null)
    setEditingGroupIds([])
    setForm({
      cv_id: cvs[0]?.id || '',
      positions: [cvs[0]?.position_label || ''],
      locations: [''],
      platforms: ['all'],
      employment_type: 'full_time',
      expected_salary: prefs.expected_salary || '',
      available_join: prefs.available_join || '',
      cover_letter: '',
      showCoverLetter: false,
    })
  }

  const openEditAdd = () => {
    if (open) {
      setOpen(false)
      setError('')
      return
    }
    const primary = targets[0]
    if (!primary) {
      resetForm()
      setOpen(true)
      return
    }
    const sameGroup = targets.filter(t =>
      normalizeKeyPart(t.position) === normalizeKeyPart(primary.position) &&
      String(t.cv_id || '') === String(primary.cv_id || '') &&
      (t.employment_type || 'full_time') === (primary.employment_type || 'full_time') &&
      (t.expected_salary || '') === (primary.expected_salary || '') &&
      (t.available_join || '') === (primary.available_join || '')
    )
    const platforms = [...new Set(sameGroup.map(t => t.platform || 'all'))]
    const locations = [...new Set(sameGroup.flatMap(t => t.locations || [t.location]).filter(Boolean))]
    setEditingTarget(primary)
    setEditingGroupIds(sameGroup.flatMap(t => t.ids || [t.id]).filter(Boolean))
    setError('')
    setForm({
      cv_id: primary.cv_id || cvs[0]?.id || '',
      positions: [primary.position || ''],
      locations: locations.length ? locations : [primary.location || ''],
      platforms: platforms.includes('all') ? ['all'] : platforms.slice(0, 2),
      employment_type: primary.employment_type || 'full_time',
      expected_salary: primary.expected_salary || prefs.expected_salary || '',
      available_join: primary.available_join || prefs.available_join || '',
      cover_letter: primary.cover_letter || '',
      showCoverLetter: Boolean(primary.cover_letter),
    })
    setOpen(true)
  }
  const togglePlatform = value => {
    setError('')
    setForm(f => {
      if (value === 'all') return { ...f, platforms: ['all'] }
      return { ...f, platforms: [value] }
    })
  }

  const targetKey = t => [
    normalizeKeyPart(t.position),
    normalizeKeyPart(t.location),
    t.platform || 'all',
  ].join('|')

  const formPosition = () => (form.positions || []).find(p => p.trim())?.trim() || ''

  const formTargetKey = (location, platform) => [
    normalizeKeyPart(formPosition()),
    normalizeKeyPart(location),
    platform || 'all',
  ].join('|')

  const buildTargetPayload = (location, platform) => ({
    cv_id: Number(form.cv_id),
    position: formPosition(),
    location,
    platform: platform || 'all',
    employment_type: form.employment_type || 'full_time',
    expected_salary: form.expected_salary?.trim() || '',
    available_join: form.available_join?.trim() || '',
    cover_letter: form.cover_letter.trim() || null,
  })

  const handleSubmit = async () => {
    const positions = form.positions.filter(p => p.trim())
    const locations = form.locations.filter(l => l.trim())
    if (!form.cv_id)       { setError('Pilih CV dulu'); return }
    if (!positions.length) { setError('Isi minimal 1 posisi'); return }
    if (!locations.length) { setError('Isi minimal 1 lokasi'); return }
    const platforms = form.platforms?.length ? form.platforms : ['all']
    setError(''); setSaving(true)
    try {
      if (editingTarget) {
        const groupTargets = targets.filter(t => editingGroupIds.includes(t.id))
        const byKey = new Map(groupTargets.map(t => [targetKey(t), t]))
        const desired = locations.flatMap(location => platforms.map(platform => ({ location, platform, key: formTargetKey(location, platform) })))
        const desiredKeys = new Set(desired.map(item => item.key))
        const calls = []
        const usedTargetIds = new Set()

        desired.forEach(item => {
          const existing = byKey.get(item.key)
          if (existing) {
            usedTargetIds.add(existing.id)
            calls.push(api.put(`/targets/${existing.id}`, buildTargetPayload(item.location, item.platform)))
          } else {
            const reusable = groupTargets.find(t => !usedTargetIds.has(t.id))
            if (reusable) {
              usedTargetIds.add(reusable.id)
              calls.push(api.put(`/targets/${reusable.id}`, buildTargetPayload(item.location, item.platform)))
            } else {
              calls.push(api.post('/targets/', {
                cv_id: Number(form.cv_id),
                positions: [positions[0]],
                locations: [item.location],
                platforms: [item.platform],
                employment_type: form.employment_type || 'full_time',
                expected_salary: form.expected_salary?.trim() || '',
                available_join: form.available_join?.trim() || '',
                cover_letter: form.cover_letter.trim() || null,
              }))
            }
          }
        })

        groupTargets.forEach(t => {
          if (!desiredKeys.has(targetKey(t)) && !usedTargetIds.has(t.id)) calls.push(api.delete(`/targets/${t.id}`))
        })

        await Promise.all(calls)
      } else {
        await api.post('/targets/', {
          cv_id: Number(form.cv_id),
          positions: [positions[0]],
          locations,
          platforms,
          employment_type: form.employment_type || 'full_time',
          expected_salary: form.expected_salary?.trim() || '',
          available_join: form.available_join?.trim() || '',
          cover_letter: form.cover_letter.trim() || null,
        })
      }
      const res = await api.get('/targets/')
      setTargets(res.data)
      resetForm()
      setOpen(false)
    } catch (err) { setError(err.response?.data?.detail || 'Gagal menyimpan') }
    finally { setSaving(false) }
  }

  const handleEdit = target => {
    setEditingTarget(target)
    setError('')
    setForm({
      cv_id: target.cv_id || cvs[0]?.id || '',
      positions: [target.position || ''],
      locations: [target.location || ''],
      platforms: [target.platform || 'all'],
      employment_type: target.employment_type || 'full_time',
      expected_salary: target.expected_salary || prefs.expected_salary || '',
      available_join: target.available_join || prefs.available_join || '',
      cover_letter: target.cover_letter || '',
      showCoverLetter: Boolean(target.cover_letter),
    })
    setOpen(true)
  }

  const savePrefs = async (nextPrefs = prefs) => {
    setSavingPrefs(true)
    setError('')
    try {
      const res = await api.put('/preferences/', nextPrefs)
      setPrefs(res.data)
    } catch (err) {
      setError(err.response?.data?.detail || 'Gagal menyimpan preferensi')
    } finally {
      setSavingPrefs(false)
    }
  }

  const handleHeadlessChange = value => {
    const nextPrefs = { ...prefs, headless_mode: value }
    setPrefs(nextPrefs)
    savePrefs(nextPrefs)
  }

  const handleTestingEmailChange = value => {
    const nextPrefs = { ...prefs, testing_email_mode: value }
    setPrefs(nextPrefs)
    savePrefs(nextPrefs)
  }

  // Cover letter berlaku untuk semua target dengan posisi yang sama.
  const handleCoverLetterSaved = (position, newText) => {
    setTargets(prev => prev.map(t => samePosition(t.position, position) ? { ...t, cover_letter: newText } : t))
  }

  const groupedTargets = Object.entries(
    targets.reduce((groups, t) => {
      const key = [
        normalizeKeyPart(t.position),
        t.cv_id || '',
        t.platform || 'all',
        t.employment_type || 'full_time',
        t.expected_salary || '',
        t.available_join || '',
        t.cover_letter || '',
      ].join('|')
      if (!groups[key]) groups[key] = { ...t, ids: [], locations: [] }
      groups[key].ids.push(t.id)
      if (t.location && !groups[key].locations.includes(t.location)) groups[key].locations.push(t.location)
      return groups
    }, {})
  ).map(([, group]) => group)

  const inputStyle = {
    border: '2px solid var(--black)',
    background: 'white',
    padding: '8px 10px',
    fontSize: '12px',
    width: '100%',
    outline: 'none',
    fontFamily: 'Dogica',
  }

  return (
    <>
    <div className="card-pixel" style={{ overflow: 'hidden', marginBottom: '12px' }}>
      <div style={{
        padding: '12px 16px', background: '#fffcf7',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px',
      }}>
        <div style={{ minWidth: 0 }}>
          <p className="font-pixel" style={{ fontSize: '9px', color: 'var(--black)', marginBottom: '4px', fontWeight: 900 }}>HEADLESS MODE</p>
          <p style={{ fontSize: '9px', color: 'var(--muted)', lineHeight: 1.6 }}>
            {prefs.headless_mode ? 'Bot jalan diam saat sesi berikutnya.' : 'Browser bot terlihat saat sesi berikutnya.'}
          </p>
        </div>
        <PixelSwitch
          checked={Boolean(prefs.headless_mode)}
          disabled={savingPrefs || isRunning}
          onChange={handleHeadlessChange}
        />
      </div>
    </div>

    <div className="card-pixel" style={{ overflow: 'hidden', marginBottom: '12px' }}>
      <div style={{
        padding: '12px 16px', background: '#fffcf7',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px',
      }}>
        <div style={{ minWidth: 0 }}>
          <p className="font-pixel" style={{ fontSize: '9px', color: 'var(--black)', marginBottom: '4px', fontWeight: 900 }}>TESTING EMAIL</p>
          <p style={{ fontSize: '9px', color: 'var(--muted)', lineHeight: 1.6 }}>
            {prefs.testing_email_mode
              ? 'LinkedIn Posts tetap mencari lowongan match, tapi email lamaran dikirim ke diri sendiri.'
              : 'Email lamaran LinkedIn Posts dikirim ke kontak recruiter normal.'}
          </p>
        </div>
        <PixelSwitch
          checked={Boolean(prefs.testing_email_mode)}
          disabled={savingPrefs || isRunning}
          onChange={handleTestingEmailChange}
        />
      </div>
    </div>

    <div className="card-pixel" style={{ overflow: 'hidden' }}>
      <div className="flex items-center justify-between px-4 py-3" style={{ borderBottom: '2px solid var(--black)', background: 'var(--cream)' }}>
        <span className="font-pixel" style={{ fontSize: '11px', letterSpacing: '0.02em', fontWeight: 900 }}>TARGET AKTIF</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          <button onClick={openEditAdd} className="btn-pixel-ghost btn-pixel-sm">
            <Pencil size={12} /> EDIT/TAMBAH
          </button>
          {open && cvs.length > 0 && (
            <button
              onClick={handleSubmit}
              disabled={saving}
              className="btn-pixel btn-pixel-sm"
              style={{ minHeight: '34px' }}
            >
              <Save size={12} /> {saving ? 'MENYIMPAN...' : 'SAVE AND CLOSE'}
            </button>
          )}
        </div>
      </div>

      {open && (
        <div className="p-4" style={{ borderBottom: '2px solid var(--border)', background: '#fffcf7' }}>
          <p className="font-pixel" style={{ fontSize: '10px', color: 'var(--black)', marginBottom: '10px' }}>
            {editingTarget ? 'EDIT TARGET TERSIMPAN' : 'TAMBAH TARGET BARU'}
          </p>
          {error && <p style={{ fontSize: '11px', color: '#e74c3c', marginBottom: '8px' }}>{error}</p>}
          {cvs.length === 0 ? (
            <p style={{ fontSize: '11px', color: 'var(--muted)' }}>Upload CV dulu di Persiapan.</p>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <div>
                <label style={{ fontSize: '10px', fontWeight: 700, display: 'block', marginBottom: '4px' }}>CV</label>
                <select value={form.cv_id} onChange={e => setCv(e.target.value)} style={inputStyle}>
                  {cvs.map(cv => <option key={cv.id} value={cv.id}>{cv.position_label} — {cv.file_name}</option>)}
                </select>
              </div>
              <div>
                <label style={{ fontSize: '10px', fontWeight: 700, display: 'block', marginBottom: '4px' }}>POSISI</label>
                {form.positions.slice(0, 1).map((p, i) => (
                  <div key={i} style={{ display: 'flex', gap: '6px', marginBottom: '4px' }}>
                    <input 
                      type="text" 
                      value={p} 
                      onChange={e => updateField('positions', i, e.target.value)}
                      placeholder="Contoh: Software Engineer"
                      style={inputStyle} 
                    />
                  </div>
                ))}
                <p style={{ fontSize: '9px', color: 'var(--muted)', lineHeight: 1.6 }}>
                  {editingTarget ? 'Edit posisi akan update semua target dengan posisi yang sama.' : 'Satu CV hanya untuk satu posisi.'}
                </p>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '8px' }}>
                <div>
                  <label style={{ fontSize: '10px', fontWeight: 700, display: 'block', marginBottom: '4px' }}>GAJI TARGET</label>
                  <input
                    value={form.expected_salary || ''}
                    onChange={e => setForm(f => ({ ...f, expected_salary: e.target.value }))}
                    placeholder="Contoh: Rp 12.000.000"
                    style={inputStyle}
                  />
                </div>
                <div>
                  <label style={{ fontSize: '10px', fontWeight: 700, display: 'block', marginBottom: '4px' }}>BISA GABUNG</label>
                  <input
                    value={form.available_join || ''}
                    onChange={e => setForm(f => ({ ...f, available_join: e.target.value }))}
                    placeholder="Contoh: 1 month notice"
                    style={inputStyle}
                  />
                </div>
              </div>
              <div>
                <label style={{ fontSize: '10px', fontWeight: 700, display: 'block', marginBottom: '4px' }}>LOKASI</label>
                <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '6px' }}>
                  {form.locations.map((l, i) => (
                    <div key={i} style={{
                      display: 'inline-flex', alignItems: 'center', gap: '5px',
                      background: 'white', border: '2px solid var(--black)', padding: '5px 7px',
                    }}>
                      <input
                        type="text"
                        value={l}
                        onChange={e => updateField('locations', i, e.target.value)}
                        placeholder="Bekasi"
                        style={{
                          border: 0, outline: 'none', background: 'transparent',
                          fontFamily: 'Dogica', fontSize: '10px', width: `${Math.max(7, (l || '').length + 1)}ch`, minWidth: '72px',
                        }}
                      />
                    </div>
                  ))}
                  <button onClick={() => addField('locations')} style={{ fontSize: '10px', color: 'var(--orange)', background: 'none', border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '4px' }}>
                    <Plus size={11} /> tambah lokasi
                  </button>
                </div>
              </div>
              <div>
                <label style={{ fontSize: '10px', fontWeight: 700, display: 'block', marginBottom: '6px' }}>PLATFORM</label>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                  {PLATFORM_OPTIONS.map(({ value, label }) => {
                    const selected = (form.platforms || ['all']).includes(value)
                    return (
                    <button key={value} onClick={() => togglePlatform(value)} style={{
                      fontSize: '10px', padding: '5px 10px',
                      background: selected ? 'var(--black)' : 'white',
                      color: selected ? 'white' : 'var(--black)',
                      border: '2px solid var(--black)', cursor: 'pointer',
                      fontFamily: 'Dogica', fontWeight: 600,
                    }}>{label}</button>
                    )
                  })}
                </div>
              </div>

              <div>
                <label style={{ fontSize: '10px', fontWeight: 700, display: 'block', marginBottom: '6px' }}>TIPE KERJA</label>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                  {EMPLOYMENT_OPTIONS.map(({ value, label }) => {
                    const selected = (form.employment_type || 'full_time') === value
                    return (
                      <button key={value} onClick={() => setForm(f => ({ ...f, employment_type: value }))} style={{
                        fontSize: '10px', padding: '5px 10px',
                        background: selected ? 'var(--black)' : 'white',
                        color: selected ? 'white' : 'var(--black)',
                        border: '2px solid var(--black)', cursor: 'pointer',
                        fontFamily: 'Dogica', fontWeight: 600,
                      }}>{label}</button>
                    )
                  })}
                </div>
              </div>

              {/* Cover Letter di form tambah */}
              <div>
                <button
                  onClick={() => setForm(f => ({ ...f, showCoverLetter: !f.showCoverLetter }))}
                  style={{
                    display: 'flex', alignItems: 'center', gap: '5px',
                    background: 'none', border: 'none', cursor: 'pointer',
                    fontSize: '10px',
                    color: form.cover_letter ? 'var(--orange)' : 'var(--black)',
                    fontFamily: 'Dogica', fontWeight: 700, padding: '2px 0',
                  }}
                >
                  <FileText size={12} />
                  COVER LETTER (opsional)
                  {form.showCoverLetter ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
                </button>

                {form.showCoverLetter && (
                  <div style={{ marginTop: '8px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
                    <div style={{
                      padding: '8px 10px',
                      background: '#fef9e7',
                      border: '1.5px solid #f39c12',
                      fontSize: '12px',
                      lineHeight: 1.7,
                      color: '#7d6608',
                    }}>
                      Gunakan <code style={{ background: '#fff', padding: '1px 4px', border: '1px solid #f39c12', fontFamily: 'monospace' }}>{'{perusahaan}'}</code> dan <code style={{ background: '#fff', padding: '1px 4px', border: '1px solid #f39c12', fontFamily: 'monospace' }}>{'{posisi}'}</code> sebagai placeholder.
                      Akan diganti otomatis saat melamar.
                    </div>
                    <textarea
                      value={form.cover_letter}
                      onChange={e => setForm(f => ({ ...f, cover_letter: e.target.value }))}
                      placeholder={`Contoh:\n\nKepada HRD {perusahaan},\n\nSaya tertarik melamar posisi {posisi} di {perusahaan}...`}
                      rows={8}
                      style={{
                        ...inputStyle,
                        fontSize: '14px',
                        fontFamily: 'monospace',
                        lineHeight: 1.7,
                        resize: 'vertical',
                      }}
                    />
                  </div>
                )}
              </div>

              <div style={{ display: 'flex', gap: '10px' }}>
                <button onClick={() => { setOpen(false); setError(''); resetForm() }} className="btn-pixel-ghost">BATAL</button>
              </div>
            </div>
          )}
        </div>
      )}

      <div className="p-4">
        {loading ? <p style={{ fontSize: '11px', color: 'var(--muted)' }}>Memuat...</p> :
         targets.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '24px', border: '2px dashed var(--border)' }}>
            <Target size={24} style={{ color: 'var(--border)', margin: '0 auto 8px' }} />
            <p style={{ fontSize: '11px', color: 'var(--muted)' }}>Belum ada target.</p>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {/* Group targets by position */}
            {Object.entries(
              groupedTargets.reduce((groups, t) => {
                const key = t.position.trim().toLowerCase()
                if (!groups[key]) groups[key] = []
                groups[key].push(t)
                return groups
              }, {})
            ).map(([_, positionTargets]) => {
              const position = positionTargets[0].position
              const coverLetter = positionTargets[0].cover_letter
              const firstTargetId = positionTargets[0].id
              
              return (
                <div key={position} className="card-pixel-sm" style={{ padding: '10px 12px', background: 'var(--cream)' }}>
                  {/* Position header */}
                  <div style={{ marginBottom: '8px', paddingBottom: '8px', borderBottom: '1.5px solid var(--border)' }}>
                    <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '10px' }}>
                      <div style={{ minWidth: 0 }}>
                        <p style={{ fontSize: '12px', fontWeight: 700, color: 'var(--black)', overflowWrap: 'anywhere' }}>{position}</p>
                        <p style={{ fontSize: '9px', color: 'var(--muted)', marginTop: '2px' }}>
                          {positionTargets.reduce((sum, t) => sum + (t.locations?.length || 1), 0)} target · CV: {positionTargets[0].position_label}
                        </p>
                        {(positionTargets[0].expected_salary || positionTargets[0].available_join) && (
                          <p style={{ fontSize: '9px', color: 'var(--black-3)', marginTop: '4px', lineHeight: 1.6 }}>
                            {positionTargets[0].expected_salary && <>Gaji: {positionTargets[0].expected_salary}</>}
                            {positionTargets[0].expected_salary && positionTargets[0].available_join && ' · '}
                            {positionTargets[0].available_join && <>Gabung: {positionTargets[0].available_join}</>}
                          </p>
                        )}
                      </div>
                      {!open && (
                        <CoverLetterEditor
                          position={position}
                          coverLetter={coverLetter}
                          targetId={firstTargetId}
                          onSaved={handleCoverLetterSaved}
                        />
                      )}
                    </div>
                  </div>

                  {/* Target list */}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', marginBottom: '6px' }}>
                    {positionTargets.map(t => (
                      <div
                        key={t.id}
                        onClick={open ? () => handleEdit(t) : undefined}
                        title={open ? 'Klik untuk edit target' : undefined}
                        style={{
                        padding: '7px 8px',
                        background: 'white',
                        border: '1px solid var(--border)',
                        cursor: open ? 'pointer' : 'default',
                      }}>
                        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '8px' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flex: 1, minWidth: 0, whiteSpace: 'nowrap', paddingTop: '2px' }}>
                            <PlatformLogo platform={t.platform} />
                            <span style={{ fontSize: '9px', color: 'var(--black)', fontWeight: 800, flexShrink: 0 }}>{platformLabel(t.platform)}</span>
                            <span style={{ fontSize: '9px', color: 'var(--muted)' }}>·</span>
                            <span title={(t.locations || [t.location]).join(', ')} style={{ fontSize: '9px', color: 'var(--black-3)', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{(t.locations || [t.location]).join(', ')}</span>
                            <span style={{ fontSize: '9px', color: 'var(--muted)' }}>·</span>
                            <span style={{ fontSize: '9px', color: 'var(--muted)', flexShrink: 0 }}>{employmentLabel(t.employment_type)}</span>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
    </>
  )
}

// ── Persistent session state (survives tab switch) ────────────────────────────
const SESSION_KEY = 'ordal_session_state'

function loadSessionState() {
  try { return JSON.parse(sessionStorage.getItem(SESSION_KEY) || 'null') } catch { return null }
}
function saveSessionState(state) {
  try { sessionStorage.setItem(SESSION_KEY, JSON.stringify(state)) } catch {}
}
function clearSessionState() {
  try { sessionStorage.removeItem(SESSION_KEY) } catch {}
}

// ── Main Page ─────────────────────────────────────────────────────────────────
export default function CariKerja() {
  const saved = loadSessionState()
  const navigate = useNavigate()

  const [status,   setStatus]   = useState(saved?.status   ?? 'idle')
  const [counts,   setCounts]   = useState(saved?.counts   ?? { linkedin: 0, linkedin_posts: 0, jobstreet: 0 })
  const [jobMap,   setJobMap]   = useState(saved?.jobMap   ?? {})
  const [messages, setMessages] = useState(saved?.messages ?? [])
  const [sessionId, setSessionId] = useState(saved?.sessionId ?? null)
  const [error,    setError]    = useState('')
  const [pendingQuestion, setPendingQuestion] = useState(null)
  const [promptAnswer, setPromptAnswer] = useState('')
  const [promptSaving, setPromptSaving] = useState(false)
  const [showFinishModal, setShowFinishModal] = useState(false)

  const eventSourceRef = useRef(null)
  const logEndRef      = useRef(null)

  // Persist state to sessionStorage whenever it changes
  useEffect(() => {
    saveSessionState({ status, counts, jobMap, messages, sessionId })
  }, [status, counts, jobMap, messages, sessionId])

  const total    = counts.linkedin + counts.linkedin_posts + counts.jobstreet
  const isRunning = status === 'running'

  useEffect(() => { logEndRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [jobMap])

  // Re-attach SSE if bot was running when user switched tabs
  useEffect(() => {
    if (status === 'running' && !eventSourceRef.current) {
      startSSE()
    }
    return () => eventSourceRef.current?.close()
  }, [])

  const startSSE = useCallback(() => {
    if (eventSourceRef.current) eventSourceRef.current.close()
    const token = localStorage.getItem('token')
    const es    = new EventSource(`/api/sessions/live?token=${token}`)

    es.onmessage = e => {
      const event = JSON.parse(e.data)
      if (event.type === 'heartbeat') return

      if (event.type === 'applied' || event.type === 'found') {
        const jobId = stableJobKey(event)
        setJobMap(prev => {
          const existing = prev[jobId] || {
            job_id: jobId,
            job_title: event.job_title,
            company: event.company,
            location: event.job_location || event.location,
            platform: event.platform,
            steps: {},
            messages: {},
          }
          if (!existing.resultType) {
            setCounts(c => ({ ...c, [event.platform]: (c[event.platform] || 0) + 1 }))
          }
          return {
            ...prev,
            [jobId]: {
              ...existing,
              platform: event.platform,
              resultType: event.type,
              notes: event.question_answers || event.skip_reason || existing.notes,
              steps: {
                ...existing.steps,
                analisis: 'ok',
                kesesuaian: 'ok',
                duplikat: 'ok',
                apply: 'ok',
              },
              messages: {
                ...existing.messages,
                apply: event.type === 'found' ? 'Prospek dari LinkedIn post ditemukan.' : 'Lamaran terkirim.',
              },
            },
          }
        })
      }

      if (event.type === 'skipped' || event.type === 'failed') {
        const jobId = stableJobKey(event)
        setJobMap(prev => {
          const existing = prev[jobId] || {
            job_id: jobId,
            job_title: event.job_title,
            company: event.company,
            location: event.job_location || event.location,
            platform: event.platform,
            steps: {},
            messages: {},
          }
          const reason = (event.skip_reason || '').toLowerCase()
          const duplicateSkip = event.type === 'skipped' && (reason.includes('duplikat') || reason.includes('sudah') || reason.includes('dilamar'))
          const applyStatus = event.type === 'failed' ? 'fail' : 'skip'
          return {
            ...prev,
            [jobId]: {
              ...existing,
              platform: event.platform,
              resultType: event.type,
              job_title: event.job_title || existing.job_title,
              company: event.company || existing.company,
              location: event.job_location || event.location || existing.location,
              steps: {
                ...existing.steps,
                analisis: 'ok',
                ...(duplicateSkip ? { duplikat: 'skip' } : { apply: applyStatus }),
              },
              messages: {
                ...existing.messages,
                ...(duplicateSkip
                  ? { duplikat: event.skip_reason || 'Sudah pernah dilamar' }
                  : { apply: event.skip_reason || (event.type === 'failed' ? 'Gagal apply.' : 'Dilewati.') }),
              },
            },
          }
        })
      }

      if (event.type === 'progress') {
        const { job_id, job_title, company, location, step, status: stepStatus, message, platform } = event
        const stableKey = stableJobKey(event)
        setJobMap(prev => {
          const ex = prev[stableKey] || (job_id && prev[job_id]) || { job_id: stableKey, job_title, company, location, platform, steps: {}, messages: {} }
          const next = { ...prev }
          if (job_id && job_id !== stableKey) delete next[job_id]
          const inferredSteps = {}
          if (['kesesuaian', 'duplikat', 'apply'].includes(step)) inferredSteps.analisis = 'ok'
          if (['duplikat', 'apply'].includes(step)) inferredSteps.kesesuaian = ex.steps?.kesesuaian === 'skip' ? 'skip' : 'ok'
          if (step === 'apply') inferredSteps.duplikat = ex.steps?.duplikat === 'skip' ? 'skip' : 'ok'
          return { ...next, [stableKey]: {
            ...ex,
            job_id: stableKey,
            job_title: job_title || ex.job_title,
            company: company || ex.company,
            location: location || ex.location,
            platform: platform || ex.platform,
            steps:    { ...ex.steps, ...inferredSteps, [step]: stepStatus },
            messages: { ...ex.messages, [step]: message || '' },
          }}
        })
      }

      if (event.type === 'status') {
        setMessages(m => [...m, { text: formatStatusMessage(event.message), platform: event.platform, time: new Date().toLocaleTimeString('id-ID') }].slice(-15))
      }

      if (event.type === 'error') {
        setMessages(m => [...m, { text: event.message, platform: event.platform, isError: true, time: new Date().toLocaleTimeString('id-ID') }].slice(-15))
      }

      if (event.type === 'question_prompt') {
        setPendingQuestion(event)
        setPromptAnswer('')
        setMessages(m => [...m, { text: `Butuh jawaban: ${event.question}`, platform: event.platform, time: new Date().toLocaleTimeString('id-ID') }].slice(-15))
      }

      if (event.type === 'done') {
        setStatus(event.reason === 'stopped' ? 'stopped' : 'done')
        setShowFinishModal(true)
        es.close()
        eventSourceRef.current = null
      }
    }

    es.onerror = () => {
      es.close()
      eventSourceRef.current = null
      setStatus(s => s === 'running' ? 'done' : s)
    }

    eventSourceRef.current = es
  }, [])

  useEffect(() => {
    if (status !== 'running') return
    const timer = setInterval(async () => {
      try {
        const res = await api.get('/sessions/history')
        const sessions = Array.isArray(res.data) ? res.data : []
        const current = sessionId ? sessions.find(s => Number(s.id) === Number(sessionId)) : sessions[0]
        if (!current || current.status === 'running') return
        setStatus(current.status === 'stopped' ? 'stopped' : 'done')
        setShowFinishModal(true)
        eventSourceRef.current?.close()
        eventSourceRef.current = null
      } catch (_) {}
    }, 3000)
    return () => clearInterval(timer)
  }, [status, sessionId])

  const handleStart = async () => {
    setError('')
    setJobMap({})
    setMessages([])
    setCounts({ linkedin: 0, linkedin_posts: 0, jobstreet: 0 })
    setSessionId(null)
    setShowFinishModal(false)
    setStatus('running')
    clearSessionState()
    try {
      const res = await api.post('/sessions/start')
      setSessionId(res.data.session_id)
      startSSE()
    } catch (err) {
      setStatus('idle')
      setError(err.response?.data?.detail || 'Gagal memulai sesi. Pastikan CV, target, dan login platform sudah siap.')
    }
  }

  const handleStop = async () => {
    try { await api.post('/sessions/stop') } catch {}
    setStatus('stopped')
    setShowFinishModal(true)
    eventSourceRef.current?.close()
    eventSourceRef.current = null
  }

  const submitPromptAnswer = async () => {
    if (!pendingQuestion || !promptAnswer.trim()) return
    if (promptKind(pendingQuestion) === 'number' && !/^\d+(?:[.,]\d+)?$/.test(promptAnswer.trim())) {
      setError('Jawaban harus angka.')
      return
    }
    setPromptSaving(true)
    setError('')
    try {
      await api.post(`/questions/prompts/${pendingQuestion.prompt_id}/answer`, { answer: promptAnswer.trim() })
      setMessages(m => [...m, { text: 'Jawaban tersimpan dan bot lanjut.', platform: pendingQuestion.platform, time: new Date().toLocaleTimeString('id-ID') }].slice(-15))
      setPendingQuestion(null)
      setPromptAnswer('')
    } catch (err) {
      setError(err.response?.data?.detail || 'Gagal mengirim jawaban')
    } finally {
      setPromptSaving(false)
    }
  }

  const jobs       = Object.values(jobMap)
  const inProgress = jobs.filter(j => !j.steps?.apply && j.steps?.kesesuaian !== 'skip')
  const applied    = jobs.filter(j => j.steps?.apply === 'ok' && j.resultType !== 'found')
  const found      = jobs.filter(j => j.resultType === 'found')
  const skipped    = jobs.filter(j => (j.steps?.kesesuaian === 'skip' || j.steps?.apply === 'fail') && j.resultType !== 'found')

  const statusLabel = { idle: 'STANDBY', running: 'RUNNING', done: 'SELESAI', stopped: 'STOPPED' }
  const statusColor = { idle: 'var(--muted)', running: 'var(--orange)', done: '#27ae60', stopped: '#e74c3c' }

  return (
    <div style={{ padding: '24px', minHeight: '100%' }}>
      {/* Header */}
      <div className="flex items-start justify-between mb-5">
        <div>
          <h1 className="font-title" style={{ fontSize: '34px', marginBottom: '6px', color: 'var(--black)' }}>
            CARI KERJA
          </h1>
          <p style={{ fontSize: '12px', color: 'var(--muted)' }}>
            Bot cari lowongan, filter yang cocok, bantu kirim atau siapkan draft, lalu catat hasilnya.
          </p>
        </div>
        <div style={{ display: 'flex', gap: '10px' }}>
          {isRunning ? (
            <button onClick={handleStop} className="btn-pixel btn-pixel-lg" style={{ background: '#e74c3c', borderColor: '#c0392b', boxShadow: '4px 4px 0 #c0392b' }}>
              <Square size={14} /> STOP
            </button>
          ) : (
            <button onClick={handleStart} className="btn-pixel btn-pixel-lg">
              <Zap size={14} /> {status === 'idle' ? 'CARIKAN KERJAAN' : 'JALANKAN LAGI'}
            </button>
          )}
        </div>
      </div>

      {error && (
        <div style={{ marginBottom: '16px', padding: '10px 14px', background: '#fdf2f2', border: '2px solid #e74c3c', fontSize: '12px', color: '#e74c3c' }}>
          ⚠ {error}
        </div>
      )}

      {pendingQuestion && (
        (() => {
          const kind = promptKind(pendingQuestion)
          const options = promptOptions(pendingQuestion)
          const fieldStyle = {
            border: '2px solid var(--black)', background: 'white', padding: '9px 10px',
            fontSize: '12px', outline: 'none', fontFamily: 'monospace',
            lineHeight: 1.4, marginBottom: '12px', width: kind === 'number' ? '220px' : '100%',
          }
          return (
        <div style={{
          position: 'fixed', inset: 0, zIndex: 50,
          background: 'rgba(0,0,0,0.45)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          padding: '20px',
        }}>
          <div className="card-pixel" style={{ width: 'min(560px, 100%)', background: '#fffcf7', padding: '18px' }}>
            <div style={{ display: 'flex', gap: '10px', alignItems: 'flex-start', marginBottom: '12px' }}>
              <HelpCircle size={22} style={{ color: 'var(--orange)', flexShrink: 0 }} />
              <div>
                <p className="font-pixel" style={{ fontSize: '11px', color: 'var(--black)', marginBottom: '6px' }}>PERTANYAAN BUTUH JAWABAN</p>
                <p style={{ fontSize: '11px', color: 'var(--muted)' }}>
                  {pendingQuestion.platform} · {pendingQuestion.job_title || 'Lamaran kerja'} · {pendingQuestion.field_type || 'text'}
                </p>
              </div>
            </div>
            <div style={{ padding: '12px', background: 'white', border: '2px solid var(--border)', marginBottom: '10px' }}>
              <p style={{ fontSize: '12px', color: 'var(--black)', lineHeight: 1.7 }}>{promptQuestionText(pendingQuestion)}</p>
            </div>
            {kind === 'yes_no' ? (
              <div style={{ display: 'flex', gap: '8px', marginBottom: '12px' }}>
                {['Yes', 'No'].map(v => (
                  <button key={v} onClick={() => setPromptAnswer(v)} className="btn-pixel" style={promptAnswer === v ? { background: '#27ae60', borderColor: '#1e8449' } : {}}>
                    {v}
                  </button>
                ))}
              </div>
            ) : kind === 'dropdown' ? (
              <select
                autoFocus
                value={promptAnswer}
                onChange={e => setPromptAnswer(e.target.value)}
                style={fieldStyle}
              >
                <option value="">Pilih jawaban</option>
                {options.map(opt => <option key={opt} value={opt}>{opt}</option>)}
              </select>
            ) : kind === 'textarea' ? (
              <textarea
                autoFocus
                value={promptAnswer}
                onChange={e => setPromptAnswer(e.target.value)}
                onKeyDown={e => {
                  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') submitPromptAnswer()
                }}
                rows={5}
                placeholder="Tulis jawaban. Jawaban ini disimpan untuk pertanyaan serupa berikutnya."
                style={{ ...fieldStyle, resize: 'vertical', lineHeight: 1.7 }}
              />
            ) : (
              <input
                autoFocus
                type={kind === 'number' ? 'number' : 'text'}
                inputMode={kind === 'number' ? 'numeric' : 'text'}
                value={promptAnswer}
                onChange={e => setPromptAnswer(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter') submitPromptAnswer()
                }}
                placeholder={kind === 'number' ? 'Angka saja' : 'Tulis jawaban'}
                style={fieldStyle}
              />
            )}
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px' }}>
              <button onClick={submitPromptAnswer} disabled={promptSaving || !promptAnswer.trim()} className="btn-pixel">
                <Save size={13} /> {promptSaving ? 'MENGIRIM...' : 'KIRIM JAWABAN'}
              </button>
            </div>
          </div>
        </div>
          )
        })()
      )}

      {showFinishModal && ['done', 'stopped'].includes(status) && (
        <FinishModal
          jobs={jobs}
          sessionId={sessionId}
          onClose={() => setShowFinishModal(false)}
          onHistory={() => navigate('/riwayat-lamaran')}
        />
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(360px, 1.2fr)', gap: '16px', alignItems: 'start' }}>
        {/* LEFT */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', minWidth: 0 }}>

          {/* Status bar */}
          <div className="card-pixel" style={{ padding: '14px 16px', minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span className="font-pixel" style={{ fontSize: '10px', color: statusColor[status] }}>
                  {isRunning && <span className="animate-blink">▶ </span>}
                  {statusLabel[status]}
                </span>
                {sessionId && <span style={{ fontSize: '10px', color: 'var(--muted)' }}>sesi #{sessionId}</span>}
              </div>
              <div style={{ display: 'flex', gap: '12px' }}>
                {[
                  { label: 'TOTAL', val: total, color: 'var(--black)' },
                  { label: 'LI', val: counts.linkedin + counts.linkedin_posts, color: '#2980b9' },
                  { label: 'JS', val: counts.jobstreet, color: 'var(--orange)' },
                ].map(({ label, val, color }) => (
                  <div key={label} style={{ textAlign: 'center' }}>
                    <p className="font-pixel" style={{ fontSize: '14px', color }}>{val}</p>
                    <p style={{ fontSize: '8px', color: 'var(--muted)', fontFamily: 'Dogica' }}>{label}</p>
                  </div>
                ))}
              </div>
            </div>
            {isRunning && (
              <div className="pixel-loader" style={{ marginBottom: '10px' }}>
                {Array.from({ length: 18 }).map((_, i) => (
                  <span key={i} style={{ animationDelay: `${i * 0.06}s` }} />
                ))}
              </div>
            )}
            {/* Activity log */}
            <div style={{ maxHeight: '120px', overflowY: 'auto', overflowX: 'hidden' }}>
              {messages.length === 0 ? (
                <p style={{ fontSize: '10px', color: 'var(--muted)', fontStyle: 'italic' }}>
                  {isRunning ? '...' : 'Tekan CARIKAN KERJAAN untuk mulai.'}
                </p>
              ) : (
                messages.slice(-8).reverse().map((m, i) => (
                  <div key={i} style={{ display: 'flex', gap: '8px', marginBottom: '3px', minWidth: 0 }}>
                    <span style={{ fontSize: '9px', color: 'var(--muted)', flexShrink: 0, fontFamily: 'monospace' }}>{m.time}</span>
                    <span title={m.text} style={{ fontSize: '10px', color: m.isError ? '#e74c3c' : 'var(--black-3)', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {m.platform && <b>[{m.platform}] </b>}{m.text}
                    </span>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Targets */}
          <TargetPanel isRunning={isRunning} />
        </div>

        {/* RIGHT: Process log */}
        <div className="card-pixel" style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 'calc(100vh - 190px)' }}>
          <div style={{ padding: '10px 14px', borderBottom: '2px solid var(--black)', background: 'var(--black)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span className="font-pixel" style={{ fontSize: '9px', color: 'white' }}>LOG PROSES</span>
            <div style={{ display: 'flex', gap: '6px' }}>
              {applied.length > 0    && <span style={{ fontSize: '9px', padding: '2px 7px', background: '#27ae60', color: 'white', border: '1.5px solid #1e8449' }}>{applied.length} APPLIED</span>}
              {found.length > 0      && <span style={{ fontSize: '9px', padding: '2px 7px', background: '#2980b9', color: 'white', border: '1.5px solid #1f5f8d' }}>{found.length} PROSPEK</span>}
              {inProgress.length > 0 && <span style={{ fontSize: '9px', padding: '2px 7px', background: 'var(--orange)', color: 'white', border: '1.5px solid var(--orange-2)' }}>{inProgress.length} PROSES</span>}
              {skipped.length > 0    && <span style={{ fontSize: '9px', padding: '2px 7px', background: 'var(--border)', color: 'var(--muted)', border: '1.5px solid var(--border)' }}>{skipped.length} SKIP</span>}
            </div>
          </div>

          <div style={{ flex: 1, overflowY: 'auto', padding: '12px', maxHeight: 'calc(100vh - 250px)', minHeight: '560px' }}>
            {jobs.length === 0 ? (
              <div style={{ textAlign: 'center', paddingTop: '60px' }}>
                <Zap size={28} style={{ color: 'var(--border)', margin: '0 auto 12px' }} />
                <p className="font-pixel" style={{ fontSize: '8px', color: 'var(--muted)', lineHeight: 2 }}>
                  BELUM ADA<br />LOWONGAN
                </p>
                <p style={{ fontSize: '11px', color: 'var(--muted)', marginTop: '8px' }}>
                  Progress real-time muncul saat bot berjalan.
                </p>
              </div>
            ) : (
              <>
                {inProgress.map(j => <JobCard key={j.job_id} job={j} />)}
                {applied.map(j => <JobCard key={j.job_id} job={j} />)}
                {found.map(j => <JobCard key={j.job_id} job={j} />)}
                {skipped.map(j => <JobCard key={j.job_id} job={j} />)}
                <div ref={logEndRef} />
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
