import { useEffect, useMemo, useState } from 'react'
import { BriefcaseBusiness, Download, ExternalLink, Search } from 'lucide-react'
import api from '../api'

function fmtDate(value) {
  if (!value) return '-'
  return new Date(value).toLocaleDateString('id-ID', {
    day: 'numeric', month: 'long', year: 'numeric'
  }).toLowerCase()
}

function platformLabel(value) {
  return {
    linkedin: 'LinkedIn Jobs',
    linkedin_posts: 'LinkedIn Posts',
    jobstreet: 'JobStreet',
  }[value] || value || '-'
}

function parseAnswers(value) {
  if (!value) return ''
  try {
    const parsed = JSON.parse(value)
    if (Array.isArray(parsed)) return parsed.map(a => `${a.question || 'Q'}: ${a.answer || '-'}`).join(' | ')
    if (typeof parsed === 'object') return Object.entries(parsed).map(([k, v]) => `${k}: ${v}`).join(' | ')
  } catch {}
  return value
}

export default function RiwayatLamaran() {
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')

  useEffect(() => {
    api.get('/sessions/applications')
      .then(res => setRows(res.data))
      .catch(err => setError(err.response?.data?.detail || 'Gagal memuat riwayat lamaran'))
      .finally(() => setLoading(false))
  }, [])

  const filteredRows = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return rows
    return rows.filter(row => [
      row.job_title, row.company, row.job_location, row.location, row.position,
      row.salary, row.platform, row.status,
    ].filter(Boolean).join(' ').toLowerCase().includes(q))
  }, [rows, query])

  const downloadExcel = () => {
    const headers = ['Tanggal', 'Posisi', 'Perusahaan', 'Lokasi', 'Gaji', 'Platform', 'Target', 'Link', 'Jawaban']
    const cell = value => String(value ?? '-')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
    const bodyRows = filteredRows.map(row => [
      fmtDate(row.applied_at),
      row.job_title || row.position || '-',
      row.company || '-',
      row.job_location || row.location || '-',
      row.salary || '-',
      platformLabel(row.platform),
      [row.position, row.location].filter(Boolean).join(' / ') || '-',
      row.job_url || '-',
      parseAnswers(row.question_answers) || '-',
    ])
    const html = `
      <html><head><meta charset="utf-8"></head><body>
      <table border="1">
        <thead><tr>${headers.map(h => `<th style="font-weight:bold;text-align:center;background:#d9d9d9">${cell(h)}</th>`).join('')}</tr></thead>
        <tbody>${bodyRows.map(r => `<tr>${r.map(v => `<td>${cell(v)}</td>`).join('')}</tr>`).join('')}</tbody>
      </table>
      </body></html>`
    const blob = new Blob([html], { type: 'application/vnd.ms-excel;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `riwayat-lamaran-${new Date().toISOString().slice(0, 10)}.xls`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  }

  return (
    <div style={{ padding: '24px', minHeight: '100%' }}>
      <div className="flex items-start justify-between gap-4" style={{ marginBottom: '18px' }}>
        <div>
          <h1 className="font-title" style={{ fontSize: '34px', marginBottom: '4px', color: 'var(--black)' }}>
            RIWAYAT LAMARAN
          </h1>
          <p style={{ fontSize: '12px', color: 'var(--muted)' }}>
            Lamaran yang sudah berhasil dikirim dan dikonfirmasi oleh bot.
          </p>
        </div>
        <div style={{ display: 'flex', gap: '10px', alignItems: 'flex-start' }}>
          <button onClick={downloadExcel} disabled={filteredRows.length === 0} className="btn-pixel btn-pixel-sm" style={{ marginTop: '2px' }}>
            <Download size={13} /> EXCEL
          </button>
          <div style={{ minWidth: '260px', maxWidth: '360px', width: '34vw', position: 'relative' }}>
            <Search size={14} style={{ position: 'absolute', left: '12px', top: '13px', color: 'var(--muted)' }} />
            <input
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Cari posisi/perusahaan"
              style={{
                width: '100%', border: '3px solid var(--black)', background: 'white',
                padding: '10px 12px 10px 34px', fontSize: '11px', outline: 'none',
                fontFamily: 'Dogica', boxShadow: '3px 3px 0 var(--black)'
              }}
            />
          </div>
        </div>
      </div>

      {error && (
        <div style={{ marginBottom: '16px', padding: '10px 14px', background: '#fdf2f2', border: '2px solid #e74c3c', fontSize: '12px', color: '#e74c3c' }}>
          {error}
        </div>
      )}

      <div className="card-pixel" style={{ overflow: 'hidden' }}>
        <div style={{ padding: '12px 16px', background: 'var(--black)', color: 'white' }}>
          <span className="font-pixel" style={{ fontSize: '9px' }}>TOTAL {filteredRows.length} LAMARAN</span>
        </div>

        {loading ? (
          <div style={{ padding: '24px', fontSize: '12px', color: 'var(--muted)' }}>Memuat...</div>
        ) : filteredRows.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '44px', background: 'white' }}>
            <BriefcaseBusiness size={30} style={{ color: 'var(--border)', margin: '0 auto 10px' }} />
            <p className="font-pixel" style={{ fontSize: '9px', color: 'var(--muted)' }}>BELUM ADA RIWAYAT</p>
          </div>
        ) : (
          <div style={{ overflowX: 'auto', background: 'white' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: '1180px', tableLayout: 'fixed' }}>
              <colgroup>
                <col style={{ width: '125px' }} />
                <col style={{ width: '220px' }} />
                <col style={{ width: '190px' }} />
                <col style={{ width: '165px' }} />
                <col style={{ width: '135px' }} />
                <col style={{ width: '120px' }} />
                <col style={{ width: '175px' }} />
                <col style={{ width: '110px' }} />
              </colgroup>
              <thead>
                <tr style={{ background: 'var(--cream)' }}>
                  {['WAKTU', 'POSISI', 'PERUSAHAAN', 'LOKASI', 'GAJI', 'PLATFORM', 'TARGET', 'DETAIL'].map(label => (
                    <th key={label} style={{ textAlign: 'left', padding: '12px 10px', borderBottom: '3px solid var(--black)', fontSize: '9px', fontFamily: 'Dogica' }}>
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filteredRows.map(row => {
                  const answers = parseAnswers(row.question_answers)
                  return (
                    <tr key={row.id} style={{ borderBottom: '2px solid var(--border)' }}>
                      <td style={{ padding: '12px 10px', fontSize: '10px', color: 'var(--muted)', verticalAlign: 'top', overflowWrap: 'break-word', lineHeight: 1.5 }}>{fmtDate(row.applied_at)}</td>
                      <td style={{ padding: '12px 10px', fontSize: '11px', fontWeight: 700, verticalAlign: 'top', overflowWrap: 'anywhere', lineHeight: 1.5 }}>{row.job_title || row.position || '-'}</td>
                      <td style={{ padding: '12px 10px', fontSize: '11px', verticalAlign: 'top', overflowWrap: 'anywhere', lineHeight: 1.5 }}>{row.company || '-'}</td>
                      <td style={{ padding: '12px 10px', fontSize: '10px', color: 'var(--black-3)', verticalAlign: 'top', overflowWrap: 'anywhere', lineHeight: 1.6 }}>{row.job_location || row.location || '-'}</td>
                      <td style={{ padding: '12px 10px', fontSize: '10px', color: row.salary ? 'var(--black)' : 'var(--muted)', verticalAlign: 'top', overflowWrap: 'anywhere', lineHeight: 1.6 }}>{row.salary || '-'}</td>
                      <td style={{ padding: '12px 10px', fontSize: '10px', verticalAlign: 'top', overflowWrap: 'break-word', lineHeight: 1.5 }}>{platformLabel(row.platform)}</td>
                      <td style={{ padding: '12px 10px', fontSize: '10px', color: 'var(--muted)', verticalAlign: 'top', overflowWrap: 'anywhere', lineHeight: 1.6 }}>{[row.position, row.location].filter(Boolean).join(' / ') || '-'}</td>
                      <td style={{ padding: '12px 10px', fontSize: '10px', verticalAlign: 'top', overflowWrap: 'anywhere', lineHeight: 1.5 }}>
                        {row.job_url ? (
                          <a href={row.job_url} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--orange)', display: 'inline-flex', alignItems: 'center', gap: '4px', fontWeight: 700 }}>
                            Buka <ExternalLink size={12} />
                          </a>
                        ) : '-'}
                        {answers && <p style={{ marginTop: '6px', color: 'var(--muted)', maxWidth: '260px' }}>{answers}</p>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
