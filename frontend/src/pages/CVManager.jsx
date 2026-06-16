import { useEffect, useRef, useState } from 'react'
import { FileText, Trash2, Upload, X } from 'lucide-react'
import api from '../api'

export default function CVManager({ embedded = false }) {
  const [cvs,      setCvs]      = useState([])
  const [loading,  setLoading]  = useState(true)
  const [uploading,setUploading]= useState(false)
  const [error,    setError]    = useState('')
  const [label,    setLabel]    = useState('')
  const [file,     setFile]     = useState(null)
  const fileRef = useRef()

  const fetchCvs = () => api.get('/cvs/').then(r => setCvs(r.data)).finally(() => setLoading(false))
  useEffect(() => { fetchCvs() }, [])

  const handleUpload = async () => {
    if (!file)  { setError('Pilih file PDF dulu'); return }
    if (!label) { setError('Isi label posisi'); return }
    setError(''); setUploading(true)
    const fd = new FormData()
    fd.append('file', file)
    fd.append('position_label', label)
    try {
      await api.post('/cvs/upload', fd, { headers: { 'Content-Type': 'multipart/form-data' } })
      setFile(null); setLabel('')
      if (fileRef.current) fileRef.current.value = ''
      fetchCvs()
    } catch (err) { setError(err.response?.data?.detail || 'Upload gagal') }
    finally { setUploading(false) }
  }

  const handleDelete = async id => {
    if (!confirm('Hapus CV ini?')) return
    await api.delete(`/cvs/${id}`)
    setCvs(c => c.filter(x => x.id !== id))
  }

  const inputStyle = { border: '2px solid var(--black)', background: 'white', padding: '8px 10px', fontSize: '12px', width: '100%', outline: 'none', fontFamily: 'Dogica' }

  return (
    <div style={{ padding: embedded ? 0 : '24px', maxWidth: embedded ? 'none' : '680px' }}>
      {!embedded && (
        <>
          <h1 className="font-title" style={{ fontSize: '28px', marginBottom: '6px' }}>CV MANAGER</h1>
          <p style={{ fontSize: '12px', color: 'var(--muted)', marginBottom: '20px' }}>Upload CV PDF untuk dipakai ORDAL saat mencari kerja.</p>
        </>
      )}

      {/* Upload card */}
      <div className="card-pixel" style={{ marginBottom: '16px', overflow: 'hidden' }}>
        <div style={{ padding: '10px 16px', background: 'var(--black)', borderBottom: '2px solid var(--black)' }}>
          <span className="font-pixel" style={{ fontSize: '9px', color: 'white' }}>UPLOAD CV BARU</span>
        </div>
        <div style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {error && <p style={{ fontSize: '11px', color: '#e74c3c' }}>⚠ {error}</p>}
          <div>
            <label style={{ fontSize: '10px', fontWeight: 700, display: 'block', marginBottom: '4px' }}>LABEL POSISI</label>
            <input type="text" value={label} onChange={e => setLabel(e.target.value)}
              placeholder="contoh: Purchasing Specialist" style={inputStyle} />
          </div>
          <div>
            <label style={{ fontSize: '10px', fontWeight: 700, display: 'block', marginBottom: '4px' }}>FILE PDF</label>
            <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
              <input ref={fileRef} type="file" accept=".pdf" onChange={e => setFile(e.target.files[0])}
                style={{ ...inputStyle, padding: '6px 10px' }} />
              {file && <button onClick={() => { setFile(null); if (fileRef.current) fileRef.current.value = '' }}
                style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--muted)' }}><X size={14} /></button>}
            </div>
            {file && <p style={{ fontSize: '10px', color: 'var(--muted)', marginTop: '4px' }}>📄 {file.name}</p>}
          </div>
          <button onClick={handleUpload} disabled={uploading} className="btn-pixel" style={{ fontSize: '9px', alignSelf: 'flex-start' }}>
            <Upload size={12} /> {uploading ? 'UPLOADING...' : 'UPLOAD'}
          </button>
        </div>
      </div>

      {/* CV list */}
      <div className="card-pixel" style={{ overflow: 'hidden' }}>
        <div style={{ padding: '10px 16px', background: 'var(--black)', borderBottom: '2px solid var(--black)' }}>
          <span className="font-pixel" style={{ fontSize: '9px', color: 'white' }}>CV TERSIMPAN</span>
        </div>
        <div style={{ padding: '12px' }}>
          {loading ? (
            <p style={{ fontSize: '11px', color: 'var(--muted)' }}>Memuat...</p>
          ) : cvs.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '28px', border: '2px dashed var(--border)' }}>
              <FileText size={28} style={{ color: 'var(--border)', margin: '0 auto 8px' }} />
              <p className="font-pixel" style={{ fontSize: '8px', color: 'var(--muted)', lineHeight: 2 }}>BELUM ADA CV</p>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {cvs.map(cv => (
                <div key={cv.id} className="card-pixel-sm" style={{ padding: '10px 12px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: 'var(--cream)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <div style={{ width: '32px', height: '32px', background: 'var(--orange)', border: '2px solid var(--black)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                      <FileText size={14} color="white" />
                    </div>
                    <div>
                      <p style={{ fontSize: '12px', fontWeight: 700 }}>{cv.position_label}</p>
                      <p style={{ fontSize: '10px', color: 'var(--muted)' }}>{cv.file_name}</p>
                    </div>
                  </div>
                  <button onClick={() => handleDelete(cv.id)} className="btn-pixel-red" style={{ padding: '6px 10px', fontSize: '10px' }}>
                    <Trash2 size={12} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
