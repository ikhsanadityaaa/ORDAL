import { useEffect, useMemo, useState } from 'react'
import { Plus, Save, Search, Trash2 } from 'lucide-react'
import api from '../api'

const PLATFORM_LABELS = {
  '': 'Semua',
  linkedin: 'LinkedIn Jobs',
  jobstreet: 'JobStreet',
  linkedin_posts: 'LinkedIn Posts',
}

export default function KumpulanPertanyaan() {
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [form, setForm] = useState({ question: '', answer: '', platform: '', field_type: 'text' })

  const load = () => {
    setLoading(true)
    api.get('/questions/')
      .then(res => setRows(res.data))
      .catch(err => setError(err.response?.data?.detail || 'Gagal memuat pertanyaan'))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return rows
    return rows.filter(r => [r.question, r.answer, r.platform].filter(Boolean).join(' ').toLowerCase().includes(q))
  }, [rows, query])

  const inputStyle = {
    border: '2px solid var(--black)',
    background: 'white',
    padding: '8px 10px',
    fontSize: '11px',
    width: '100%',
    outline: 'none',
    fontFamily: 'Dogica',
  }

  const create = async () => {
    if (!form.question.trim() || !form.answer.trim()) { setError('Pertanyaan dan jawaban wajib diisi'); return }
    setSaving(true); setError('')
    try {
      await api.post('/questions/', form)
      setForm({ question: '', answer: '', platform: '', field_type: 'text' })
      load()
    } catch (err) { setError(err.response?.data?.detail || 'Gagal menyimpan') }
    finally { setSaving(false) }
  }

  const update = async row => {
    setSaving(true); setError('')
    try {
      await api.put(`/questions/${row.id}`, row)
      load()
    } catch (err) { setError(err.response?.data?.detail || 'Gagal update') }
    finally { setSaving(false) }
  }

  const remove = async id => {
    await api.delete(`/questions/${id}`)
    setRows(prev => prev.filter(r => r.id !== id))
  }

  return (
    <div style={{ padding: '24px', minHeight: '100%' }}>
      <div className="flex items-start justify-between gap-4" style={{ marginBottom: '18px' }}>
        <div>
          <h1 className="font-title" style={{ fontSize: '34px', marginBottom: '4px', color: 'var(--black)' }}>
            KUMPULAN PERTANYAAN
          </h1>
          <p style={{ fontSize: '12px', color: 'var(--muted)' }}>
            Jawaban otomatis dari LinkedIn dan JobStreet. Edit agar pertanyaan mirip dijawab sesuai kamu.
          </p>
        </div>
        <div style={{ minWidth: '260px', maxWidth: '360px', width: '34%', position: 'relative' }}>
          <Search size={14} style={{ position: 'absolute', left: '12px', top: '13px', color: 'var(--muted)' }} />
          <input value={query} onChange={e => setQuery(e.target.value)} placeholder="Cari pertanyaan/jawaban" style={{ ...inputStyle, paddingLeft: '34px', boxShadow: '3px 3px 0 var(--black)' }} />
        </div>
      </div>

      {error && <div style={{ marginBottom: '14px', padding: '10px 14px', background: '#fdf2f2', border: '2px solid #e74c3c', fontSize: '12px', color: '#e74c3c' }}>{error}</div>}

      <div className="card-pixel" style={{ padding: '14px', marginBottom: '18px' }}>
        <p className="font-pixel" style={{ fontSize: '10px', marginBottom: '10px' }}>TAMBAH JAWABAN MANUAL</p>
        <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr auto', gap: '8px', alignItems: 'start' }}>
          <textarea rows={2} value={form.question} onChange={e => setForm(f => ({ ...f, question: e.target.value }))} placeholder="Pertanyaan" style={{ ...inputStyle, lineHeight: 1.5, resize: 'vertical' }} />
          <textarea rows={2} value={form.answer} onChange={e => setForm(f => ({ ...f, answer: e.target.value }))} placeholder="Jawaban" style={{ ...inputStyle, lineHeight: 1.5, resize: 'vertical' }} />
          <button onClick={create} disabled={saving} className="btn-pixel btn-pixel-sm"><Plus size={12} /> ADD</button>
        </div>
      </div>

      <div className="card-pixel" style={{ overflow: 'hidden' }}>
        <div style={{ padding: '12px 16px', background: 'var(--black)', color: 'white' }}>
          <span className="font-pixel" style={{ fontSize: '9px' }}>TOTAL {filtered.length} PERTANYAAN</span>
        </div>
        {loading ? <div style={{ padding: '24px', fontSize: '12px', color: 'var(--muted)' }}>Memuat...</div> : (
          <div style={{ background: 'white', padding: '12px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {filtered.length === 0 ? <p style={{ fontSize: '12px', color: 'var(--muted)', padding: '20px' }}>Belum ada pertanyaan.</p> : filtered.map(row => (
              <QuestionRow key={row.id} row={row} setRows={setRows} update={update} remove={remove} saving={saving} inputStyle={inputStyle} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function QuestionRow({ row, setRows, update, remove, saving, inputStyle }) {
  const patch = changes => setRows(prev => prev.map(r => r.id === row.id ? { ...r, ...changes } : r))
  const answerRows = Math.min(5, Math.max(1, Math.ceil(((row.answer || '').length || 1) / 80)))
  return (
    <div style={{ border: '2px solid var(--border)', padding: '10px', background: '#fffcf7' }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1.25fr 1fr auto auto', gap: '8px', alignItems: 'start' }}>
        <div style={{ minHeight: '34px', lineHeight: 1.55, color: 'var(--black)', fontWeight: 800, fontSize: '11px', overflowWrap: 'anywhere', padding: '4px 0' }}>
          {row.question || '-'}
        </div>
        <textarea rows={answerRows} value={row.answer || ''} onChange={e => patch({ answer: e.target.value })} style={{ ...inputStyle, lineHeight: 1.5, resize: 'none', overflow: 'hidden' }} />
        <button onClick={() => update(row)} disabled={saving} className="btn-pixel-ghost btn-pixel-sm"><Save size={12} /> SAVE</button>
        <button onClick={() => remove(row.id)} className="btn-pixel-red btn-pixel-sm"><Trash2 size={12} /></button>
      </div>
      <p style={{ marginTop: '6px', fontSize: '9px', color: 'var(--muted)' }}>
        {PLATFORM_LABELS[row.platform || ''] || row.platform || 'Semua'} · dipakai {row.use_count || 0}x
      </p>
    </div>
  )
}
