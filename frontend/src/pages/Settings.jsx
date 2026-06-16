import { useEffect, useState } from 'react'
import { ExternalLink, Terminal, Trash2, RefreshCw, Loader, Mail, Eye, EyeOff, CheckCircle, Sparkles, Send, MessageCircle, Copy } from 'lucide-react'
import api from '../api'

const PLATFORMS = [
  { id: 'linkedin',  label: 'LinkedIn',  desc: 'Dipakai untuk LinkedIn Jobs dan LinkedIn Posts', loginUrl: 'https://www.linkedin.com/login' },
  { id: 'jobstreet', label: 'JobStreet', desc: 'Dipakai untuk JobStreet Indonesia', loginUrl: 'https://id.jobstreet.com/id' },
]

function isPlaywrightError(text) {
  return text && (text.includes("Executable doesn't exist") || text.includes("playwright install") || text.includes("BrowserType.launch"))
}

function PlatformCard({ platform, status, onGrab, onLogout, loading }) {
  const isLoggedIn = status?.logged_in
  const needsCapture = status?.needs_capture
  const isLoading  = loading === platform.id
  const statusText = isLoggedIn ? '● AKTIF' : needsCapture ? '△ PERLU CAPTURE' : '○ BELUM'
  const statusBg = isLoggedIn ? '#27ae60' : needsCapture ? '#f39c12' : 'var(--cream-2)'
  const statusBorder = isLoggedIn ? '#1e8449' : needsCapture ? '#d68910' : 'var(--border)'
  const statusColor = isLoggedIn || needsCapture ? 'white' : 'var(--muted)'

  return (
    <div className="card-pixel" style={{ overflow: 'hidden' }}>
      <div style={{
        padding: '16px',
        borderBottom: '2px solid var(--black)',
        background: 'var(--cream)',
        display: 'grid',
        gridTemplateColumns: '1fr auto',
        alignItems: 'start',
        gap: '12px',
      }}>
        <div style={{ minWidth: 0 }}>
          <p className="font-pixel" style={{ fontSize: '12px', marginBottom: '5px', color: 'var(--black)' }}>{platform.label}</p>
          <p style={{ fontSize: '11px', color: 'var(--muted)', lineHeight: 1.6 }}>{platform.desc}</p>
        </div>
        <span style={{
          fontSize: '9px',
          padding: '5px 8px',
          fontFamily: 'Dogica',
          lineHeight: 1,
          whiteSpace: 'nowrap',
          background: statusBg,
          color: statusColor,
          border: `2px solid ${statusBorder}`,
        }}>
          {statusText}
        </span>
      </div>

      <div style={{ padding: '16px' }}>
        {!isLoggedIn && (
          <div style={{ marginBottom: '12px', padding: '10px', background: 'var(--cream)', border: '2px solid var(--border)', fontSize: '11px', lineHeight: 1.7 }}>
            <p style={{ fontWeight: 700, marginBottom: '6px' }}>{needsCapture ? 'Session belum kebaca:' : 'Cara login:'}</p>
            {[
              'Klik Capture Session. Browser khusus ORDAL terbuka.',
              `Login ke ${platform.label} di browser itu sampai halaman akun terbuka.`,
              'Tunggu sampai ORDAL menyimpan session. Login di tab Chrome biasa belum cukup untuk bot.',
            ].map((s, i) => (
              <div key={i} style={{ display: 'flex', gap: '8px', marginBottom: '3px' }}>
                <span className="font-pixel" style={{ fontSize: '7px', minWidth: '16px', padding: '2px 4px', background: 'var(--orange)', color: 'white' }}>{i+1}</span>
                <span style={{ color: 'var(--black-3)' }}>{s}</span>
              </div>
            ))}
          </div>
        )}

        {isLoggedIn && (
          <div style={{ marginBottom: '12px', padding: '10px', background: '#eafaf1', border: '2px solid #27ae60', fontSize: '11px', color: '#1e8449' }}>
            ✓ Session browser aktif. ORDAL bisa membaca {platform.label}.
          </div>
        )}

        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))',
          gap: '10px',
          alignItems: 'stretch',
        }}>
          <a href={platform.loginUrl} target="_blank" rel="noopener noreferrer" className="btn-pixel-ghost" style={{ textDecoration: 'none', minHeight: '46px' }}>
            <ExternalLink size={14} /> BUKA {platform.label.toUpperCase()}
          </a>
          <button onClick={() => onGrab(platform.id)} disabled={isLoading} className="btn-pixel" style={{ minHeight: '46px' }}>
            {isLoading ? <><Loader size={14} style={{ animation: 'spin 1s linear infinite' }} /> MENUNGGU...</> :
             isLoggedIn ? <><RefreshCw size={14} /> CAPTURE ULANG</> : '▶ CAPTURE SESSION'}
          </button>
          {isLoggedIn && (
            <button onClick={() => onLogout(platform.id)} className="btn-pixel-red" style={{ minHeight: '46px' }}>
              <Trash2 size={14} /> HAPUS
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Email Config Card ─────────────────────────────────────────────────────────
function EmailConfigCard() {
  const appPasswordUrl = 'https://myaccount.google.com/apppasswords'
  const [config,   setConfig]   = useState(null)
  const [form,     setForm]     = useState({ smtp_host: 'smtp.gmail.com', smtp_port: 587, sender_email: '', app_password: '' })
  const [showPass, setShowPass] = useState(false)
  const [saving,   setSaving]   = useState(false)
  const [testing,  setTesting]  = useState(false)
  const [message,  setMessage]  = useState(null)
  const [editing,  setEditing]  = useState(false)

  useEffect(() => {
    api.get('/email/status').then(r => {
      setConfig(r.data)
      if (r.data.configured) {
        setForm(f => ({
          ...f,
          smtp_host:    r.data.smtp_host || 'smtp.gmail.com',
          smtp_port:    r.data.smtp_port || 587,
          sender_email: r.data.sender || '',
        }))
      }
    }).catch(() => {})
  }, [])

  const handleSave = async () => {
    if (!form.sender_email.trim()) { setMessage({ type: 'error', text: 'Email pengirim wajib diisi' }); return }
    if (!form.app_password.trim()) { setMessage({ type: 'error', text: 'App Password wajib diisi' }); return }
    setSaving(true)
    try {
      await api.put('/email', form)
      const r = await api.get('/email/status')
      setConfig(r.data)
      setMessage({ type: 'success', text: 'Konfigurasi email disimpan!' })
      setEditing(false)
      setForm(f => ({ ...f, app_password: '' }))  // clear password dari form
    } catch (e) {
      setMessage({ type: 'error', text: e.response?.data?.detail || 'Gagal menyimpan' })
    } finally { setSaving(false) }
  }

  const handleDelete = async () => {
    if (!confirm('Hapus konfigurasi email?')) return
    await api.delete('/email')
    setConfig({ configured: false, sender: '', smtp_host: 'smtp.gmail.com', smtp_port: 587 })
    setForm({ smtp_host: 'smtp.gmail.com', smtp_port: 587, sender_email: '', app_password: '' })
    setMessage({ type: 'success', text: 'Konfigurasi email dihapus' })
    setEditing(false)
  }

  const handleTestEmail = async () => {
    setTesting(true)
    try {
      const recipient = config?.sender || form.sender_email
      const res = await api.post('/email/test', { recipient_email: recipient })
      setMessage({ type: 'success', text: res.data.message })
    } catch (e) {
      setMessage({ type: 'error', text: e.response?.data?.detail || 'Gagal mengirim email test' })
    } finally { setTesting(false) }
  }

  const inputStyle = {
    border: '2px solid var(--black)',
    background: 'white',
    padding: '8px 10px',
    fontSize: '12px',
    width: '100%',
    outline: 'none',
    fontFamily: 'Dogica',
  }

  const isConfigured = config?.configured

  return (
    <div className="card-pixel" style={{ overflow: 'hidden' }}>
      <div style={{
        padding: '16px',
        borderBottom: '2px solid var(--black)',
        background: 'var(--cream)',
        display: 'grid',
        gridTemplateColumns: '1fr auto',
        alignItems: 'start',
        gap: '12px',
      }}>
        <div style={{ minWidth: 0 }}>
          <p className="font-pixel" style={{ fontSize: '12px', marginBottom: '5px', color: 'var(--black)', display: 'flex', alignItems: 'center', gap: '6px' }}>
            <Mail size={13} /> EMAIL OTOMATIS
          </p>
          <p style={{ fontSize: '11px', color: 'var(--muted)', lineHeight: 1.6 }}>
            Dipakai untuk kirim lamaran via email saat bot menemukan lowongan dari LinkedIn Posts.
          </p>
        </div>
        <span style={{
          fontSize: '9px',
          padding: '5px 8px',
          fontFamily: 'Dogica',
          lineHeight: 1,
          whiteSpace: 'nowrap',
          background: isConfigured ? '#27ae60' : 'var(--cream-2)',
          color: isConfigured ? 'white' : 'var(--muted)',
          border: `2px solid ${isConfigured ? '#1e8449' : 'var(--border)'}`,
        }}>
          {isConfigured ? '● AKTIF' : '○ BELUM'}
        </span>
      </div>

      <div style={{ padding: '16px' }}>
        {/* Message */}
        {message && (
          <div style={{
            marginBottom: '12px', padding: '10px 12px', fontSize: '11px',
            background: message.type === 'success' ? '#eafaf1' : '#fdf2f2',
            border: `2px solid ${message.type === 'success' ? '#27ae60' : '#e74c3c'}`,
            color: message.type === 'success' ? '#1e8449' : '#c0392b',
          }}>
            {message.text}
          </div>
        )}

        {/* Status aktif */}
        {isConfigured && !editing && (
          <div style={{ marginBottom: '12px', padding: '10px', background: '#eafaf1', border: '2px solid #27ae60', fontSize: '11px', color: '#1e8449', display: 'flex', alignItems: 'flex-start', gap: '8px', lineHeight: 1.6 }}>
            <CheckCircle size={14} style={{ marginTop: '1px', flexShrink: 0 }} />
            <div>
              Mengirim dari <strong>{config.sender}</strong> via {config.smtp_host}. Gunakan test email dulu untuk memastikan SMTP siap sebelum kirim ke email perusahaan.
            </div>
          </div>
        )}

        {/* Form */}
        {(!isConfigured || editing) && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', marginBottom: '12px' }}>
            {/* Panduan Gmail */}
            <div style={{ padding: '10px', background: 'var(--cream)', border: '2px solid var(--border)', fontSize: '11px', lineHeight: 1.7 }}>
              <p style={{ fontWeight: 700, marginBottom: '6px' }}>Cara pakai Gmail:</p>
              {[
                'Aktifkan 2-Factor Authentication di akun Google.',
                'Buka myaccount.google.com/apppasswords',
                'Buat App Password baru → pilih "Mail".',
                'Copy 16-digit kode yang muncul ke kolom App Password di bawah.',
              ].map((s, i) => (
                <div key={i} style={{ display: 'flex', gap: '8px', marginBottom: '3px' }}>
                  <span className="font-pixel" style={{ fontSize: '7px', minWidth: '16px', padding: '2px 4px', background: 'var(--orange)', color: 'white', flexShrink: 0 }}>{i+1}</span>
                  <span style={{ color: 'var(--black-3)' }}>{s}</span>
                </div>
              ))}
            </div>

            <div>
              <label style={{ fontSize: '10px', fontWeight: 700, display: 'block', marginBottom: '4px' }}>EMAIL PENGIRIM</label>
              <input type="email" value={form.sender_email}
                onChange={e => setForm(f => ({ ...f, sender_email: e.target.value }))}
                placeholder="kamu@gmail.com" style={inputStyle} />
            </div>
            <div>
              <label style={{ fontSize: '10px', fontWeight: 700, display: 'block', marginBottom: '4px' }}>APP PASSWORD</label>
              <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) auto', gap: '8px', alignItems: 'stretch' }}>
                <div style={{ position: 'relative', minWidth: 0 }}>
                  <input
                    type={showPass ? 'text' : 'password'}
                    value={form.app_password}
                    onChange={e => setForm(f => ({ ...f, app_password: e.target.value }))}
                    placeholder="xxxx xxxx xxxx xxxx"
                    style={{ ...inputStyle, paddingRight: '40px' }}
                  />
                  <button
                    onClick={() => setShowPass(s => !s)}
                    style={{ position: 'absolute', right: '10px', top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--muted)' }}
                  >
                    {showPass ? <EyeOff size={14} /> : <Eye size={14} />}
                  </button>
                </div>
                <a
                  href={appPasswordUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="btn-pixel-ghost"
                  style={{ minHeight: '100%', padding: '0 12px', fontSize: '8px', boxShadow: '3px 3px 0 var(--black)', textDecoration: 'none' }}
                >
                  <ExternalLink size={11} /> BUKA APP PASSWORD
                </a>
              </div>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '8px' }}>
              <div>
                <label style={{ fontSize: '10px', fontWeight: 700, display: 'block', marginBottom: '4px' }}>SMTP HOST</label>
                <input type="text" value={form.smtp_host}
                  onChange={e => setForm(f => ({ ...f, smtp_host: e.target.value }))}
                  style={inputStyle} />
              </div>
              <div>
                <label style={{ fontSize: '10px', fontWeight: 700, display: 'block', marginBottom: '4px' }}>PORT</label>
                <input type="number" value={form.smtp_port}
                  onChange={e => setForm(f => ({ ...f, smtp_port: Number(e.target.value) }))}
                  style={inputStyle} />
              </div>
            </div>
          </div>
        )}

        {/* Action buttons */}
        <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
          {(!isConfigured || editing) && (
            <button onClick={handleSave} disabled={saving} className="btn-pixel" style={{ minHeight: '42px' }}>
              {saving ? <><Loader size={14} style={{ animation: 'spin 1s linear infinite' }} /> MENYIMPAN...</> : <><Mail size={14} /> SIMPAN</>}
            </button>
          )}
          {isConfigured && !editing && (
            <button onClick={() => setEditing(true)} className="btn-pixel-ghost" style={{ minHeight: '42px' }}>
              <RefreshCw size={14} /> EDIT
            </button>
          )}
          {isConfigured && !editing && (
            <button onClick={handleTestEmail} disabled={testing} className="btn-pixel" style={{ minHeight: '42px' }}>
              {testing ? <><Loader size={14} style={{ animation: 'spin 1s linear infinite' }} /> TEST...</> : <><Send size={14} /> TEST KE EMAIL SENDIRI</>}
            </button>
          )}
          {isConfigured && (
            <button onClick={handleDelete} className="btn-pixel-red" style={{ minHeight: '42px' }}>
              <Trash2 size={14} /> HAPUS
            </button>
          )}
          {editing && (
            <button onClick={() => { setEditing(false); setMessage(null) }} className="btn-pixel-ghost" style={{ minHeight: '42px' }}>
              BATAL
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Gemini Info Card ──────────────────────────────────────────────────────────
function GeminiInfoCard() {
  return (
    <div className="card-pixel" style={{ overflow: 'hidden' }}>
      <div style={{
        padding: '16px',
        borderBottom: '2px solid var(--black)',
        background: 'var(--cream)',
        display: 'grid',
        gridTemplateColumns: '1fr auto',
        alignItems: 'start',
        gap: '12px',
      }}>
        <div style={{ minWidth: 0 }}>
          <p className="font-pixel" style={{ fontSize: '12px', marginBottom: '5px', color: 'var(--black)', display: 'flex', alignItems: 'center', gap: '6px' }}>
            <Sparkles size={13} /> GOOGLE GEMINI AI
          </p>
          <p style={{ fontSize: '11px', color: 'var(--muted)', lineHeight: 1.6 }}>
            Dipakai untuk identifikasi lowongan, generate cover letter, dan draft email otomatis.
          </p>
        </div>
        <span style={{
          fontSize: '9px',
          padding: '5px 8px',
          fontFamily: 'Dogica',
          lineHeight: 1,
          whiteSpace: 'nowrap',
          background: 'var(--orange)',
          color: 'white',
          border: '2px solid var(--black)',
        }}>
          DIKONFIGURASI
        </span>
      </div>
      <div style={{ padding: '16px' }}>
        <div style={{ padding: '10px', background: '#eafaf1', border: '2px solid #27ae60', fontSize: '11px', color: '#1e8449', lineHeight: 1.7, marginBottom: '12px' }}>
          ✓ Gemini API aktif — dikonfigurasi via <code style={{ background: 'white', padding: '1px 4px', border: '1px solid #27ae60', fontFamily: 'monospace' }}>GEMINI_API_KEY</code> di file <code style={{ background: 'white', padding: '1px 4px', border: '1px solid #27ae60', fontFamily: 'monospace' }}>.env</code> backend.
        </div>
        <div style={{ fontSize: '11px', color: 'var(--black-3)', lineHeight: 1.8 }}>
          <p style={{ fontWeight: 700, marginBottom: '6px', fontFamily: 'Dogica', fontSize: '10px' }}>FUNGSI GEMINI DI ORDAL:</p>
          {[
            ['Identifikasi lowongan', 'Analisis post LinkedIn apakah itu lowongan kerja nyata'],
            ['Cover letter', 'Generate dari CV + deskripsi lowongan (atau pakai template manual)'],
            ['Draft email', 'Tulis email lamaran otomatis berdasarkan CV dan lowongan'],
            ['Jawab pertanyaan form', 'Isi field tambahan di form lamaran LinkedIn/JobStreet'],
          ].map(([title, desc]) => (
            <div key={title} style={{ display: 'flex', gap: '8px', marginBottom: '6px', alignItems: 'flex-start' }}>
              <span style={{ color: 'var(--orange)', flexShrink: 0, marginTop: '2px' }}>▸</span>
              <div>
                <span style={{ fontFamily: 'Dogica', fontSize: '10px', fontWeight: 700 }}>{title}:</span>{' '}
                <span style={{ fontSize: '11px' }}>{desc}</span>
              </div>
            </div>
          ))}
        </div>
        <a
          href="https://aistudio.google.com/app/apikey"
          target="_blank"
          rel="noopener noreferrer"
          className="btn-pixel-ghost"
          style={{ textDecoration: 'none', display: 'inline-flex', marginTop: '8px', fontSize: '10px' }}
        >
          <ExternalLink size={12} /> BUAT / KELOLA API KEY
        </a>
      </div>
    </div>
  )
}

function TelegramCard() {
  const [tg, setTg] = useState(null)
  const [copied, setCopied] = useState(false)

  const load = () => api.get('/telegram/status').then(r => setTg(r.data)).catch(() => setTg(null))
  useEffect(() => { load() }, [])

  const copyCommand = async () => {
    if (!tg?.start_command) return
    await navigator.clipboard?.writeText(tg.start_command)
    setCopied(true)
    setTimeout(() => setCopied(false), 1200)
  }

  return (
    <div className="card-pixel" style={{ padding: '16px', background: 'var(--cream)' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '12px', marginBottom: '10px' }}>
        <div>
          <p className="font-pixel" style={{ fontSize: '12px', marginBottom: '5px', color: 'var(--black)', display: 'flex', alignItems: 'center', gap: '7px' }}>
            <MessageCircle size={15} /> TELEGRAM
          </p>
          <p style={{ fontSize: '11px', color: 'var(--muted)', lineHeight: 1.6 }}>
            Pertanyaan apply dikirim ke Telegram. Anda juga bisa balas dari Telegram dan minta report dengan /report.
          </p>
        </div>
        <span style={{ fontSize: '9px', padding: '5px 8px', background: tg?.connected ? '#27ae60' : 'var(--cream-2)', color: tg?.connected ? 'white' : 'var(--muted)', border: `2px solid ${tg?.connected ? '#1e8449' : 'var(--border)'}`, fontFamily: 'Dogica' }}>
          {tg?.connected ? 'TERHUBUNG' : 'BELUM'}
        </span>
      </div>

      {!tg?.bot_configured && (
        <div style={{ fontSize: '11px', padding: '10px', border: '2px solid #f39c12', background: '#fef9e7', marginBottom: '10px' }}>
          TELEGRAM_BOT_TOKEN belum diisi di file .env backend.
        </div>
      )}

      <div style={{ background: 'white', border: '2px solid var(--border)', padding: '10px', fontSize: '11px', lineHeight: 1.7 }}>
        <p style={{ marginBottom: '6px' }}>Kirim perintah ini ke bot Telegram Anda:</p>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
          <code style={{ background: '#1a1a1a', color: '#2ecc71', padding: '7px 9px', border: '2px solid #333', fontSize: '11px' }}>{tg?.start_command || 'Loading...'}</code>
          <button onClick={copyCommand} className="btn-pixel-ghost" style={{ fontSize: '10px' }}>
            <Copy size={12} /> {copied ? 'COPIED' : 'COPY'}
          </button>
          <button onClick={load} className="btn-pixel-ghost" style={{ fontSize: '10px' }}>
            <RefreshCw size={12} /> CEK ULANG
          </button>
        </div>
        <p style={{ marginTop: '8px', color: 'var(--muted)' }}>
          Report otomatis dikirim setiap hari jam 16:00 WIB. Untuk cek manual, kirim /report ke Telegram.
        </p>
      </div>
    </div>
  )
}

export default function Settings({ embedded = false }) {
  const [status,  setStatus]  = useState({})
  const [loading, setLoading] = useState(null)
  const [message, setMessage] = useState(null)

  const fetchStatus = () => api.get('/credentials/status').then(r => setStatus(r.data)).catch(() => {})
  useEffect(() => { fetchStatus() }, [])

  const handleGrab = async (platformId) => {
    setLoading(platformId)
    setMessage({ text: 'Browser login sedang dibuka...', type: 'info' })
    try {
      const res = await api.post(`/credentials/grab/${platformId}`, {}, { timeout: 310000 })
      if (res.data.success) {
        setMessage({ text: res.data.message, type: 'success' })
        fetchStatus()
      } else {
        setMessage({ text: res.data.message, type: 'warn', loginUrl: res.data.login_url, platform: platformId })
      }
    } catch (err) {
      const detail = err.response?.data?.detail || 'Gagal mendeteksi session.'
      setMessage({ text: detail, type: isPlaywrightError(detail) ? 'playwright' : 'error' })
    } finally { setLoading(null) }
  }

  const handleLogout = async (platformId) => {
    if (!confirm(`Hapus session ${platformId}?`)) return
    await api.delete(`/credentials/${platformId}`)
    setMessage({ text: `Session ${platformId} dihapus`, type: 'success' })
    fetchStatus()
  }

  const msgColors = {
    success:    { bg: '#eafaf1', border: '#27ae60', color: '#1e8449' },
    error:      { bg: '#fdf2f2', border: '#e74c3c', color: '#c0392b' },
    warn:       { bg: '#fef9e7', border: '#f39c12', color: '#d68910' },
    info:       { bg: '#eaf4fb', border: '#2980b9', color: '#1a5276' },
    playwright: { bg: '#fef9e7', border: '#f39c12', color: '#7d6608' },
  }

  return (
    <div style={{ padding: embedded ? 0 : '24px', maxWidth: embedded ? 'none' : '620px' }}>
      {!embedded && (
        <>
          <h1 className="font-title" style={{ fontSize: '28px', marginBottom: '6px' }}>SETTINGS</h1>
          <p style={{ fontSize: '12px', color: 'var(--muted)', marginBottom: '20px' }}>
            Login platform, email otomatis, dan konfigurasi AI.
          </p>
        </>
      )}

      {message && (
        <div style={{
          marginBottom: '16px', padding: '12px 14px', fontSize: '12px',
          background: msgColors[message.type]?.bg,
          border: `2px solid ${msgColors[message.type]?.border}`,
          color: msgColors[message.type]?.color,
          boxShadow: `3px 3px 0 ${msgColors[message.type]?.border}`,
        }}>
          {message.type === 'playwright' ? (
            <>
              <p style={{ fontWeight: 700, marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                <Terminal size={14} /> Playwright browser belum terinstall
              </p>
              <p style={{ marginBottom: '8px', fontSize: '11px' }}>Jalankan di terminal backend (venv aktif):</p>
              <div style={{ background: '#1a1a1a', color: '#2ecc71', padding: '10px', fontFamily: 'monospace', fontSize: '11px', border: '2px solid #333', marginBottom: '8px' }}>
                <p>source .venv/bin/activate</p>
                <p>python -m playwright install chromium</p>
              </div>
            </>
          ) : (
            <>
              {message.text}
              {message.loginUrl && (
                <a href={message.loginUrl} target="_blank" rel="noopener noreferrer"
                  style={{ display: 'block', marginTop: '6px', color: 'inherit', fontWeight: 700, textDecoration: 'underline' }}>
                  → Buka halaman login {message.platform}
                </a>
              )}
            </>
          )}
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
        {/* Platform login cards */}
        {PLATFORMS.map(p => (
          <PlatformCard key={p.id} platform={p} status={status[p.id]}
            onGrab={handleGrab} onLogout={handleLogout} loading={loading} />
        ))}

        {/* Email config */}
        <EmailConfigCard />

        {/* Telegram config */}
        <TelegramCard />

        {/* Gemini info */}
        <GeminiInfoCard />

        <div style={{ padding: '12px 14px', background: 'var(--cream-2)', border: '2px solid var(--border)', fontSize: '11px', color: 'var(--muted)', lineHeight: 1.7 }}>
          <p style={{ fontWeight: 700, marginBottom: '4px', color: 'var(--black)' }}>ℹ Kenapa login lewat browser app?</p>
          Login di tab Chrome biasa belum cukup untuk bot. ORDAL perlu capture session lewat browser Playwright supaya LinkedIn dan JobStreet bisa dibaca otomatis.
        </div>
      </div>
    </div>
  )
}
