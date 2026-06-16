import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Zap } from 'lucide-react'
import api from '../api'
import useAuthStore from '../stores/authStore'

export default function Register() {
  const [name,     setName]     = useState('')
  const [email,    setEmail]    = useState('')
  const [password, setPassword] = useState('')
  const [error,    setError]    = useState('')
  const [loading,  setLoading]  = useState(false)
  const { setAuth } = useAuthStore()
  const navigate = useNavigate()

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError(''); setLoading(true)
    try {
      const res = await api.post('/auth/register', { name, email, password })
      setAuth(res.data.token, res.data.user)
      navigate('/kerja')
    } catch (err) {
      setError(err.response?.data?.detail || 'Registrasi gagal')
    } finally { setLoading(false) }
  }

  const inputStyle = {
    border: '4px solid var(--black)', background: 'white',
    padding: '16px 18px', fontSize: '16px', width: '100%',
    outline: 'none', fontFamily: 'Dogica', boxShadow: '5px 5px 0 var(--black)',
    color: 'var(--black)',
  }

  return (
    <div style={{ minHeight: '100vh', background: 'var(--cream)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '20px' }} className="pixel-grid">
      <div style={{ width: '100%', maxWidth: '480px' }}>
        <div style={{ textAlign: 'center', marginBottom: '48px' }}>
          <div style={{ width: '100px', height: '100px', background: 'var(--orange)', border: '6px solid var(--black)', boxShadow: '8px 8px 0 var(--black)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 24px' }}>
            <Zap size={50} color="white" strokeWidth={3} />
          </div>
          <h1 className="font-title" style={{ fontSize: '40px', marginBottom: '16px', color: 'var(--black)', letterSpacing: '0.1em' }}>ORDAL</h1>
          <p style={{ fontSize: '12px', color: 'var(--muted)' }}>Bantuin Kamu Dapet Kerja</p>
        </div>

        <div className="card-pixel" style={{ overflow: 'hidden' }}>
          <div style={{ padding: '20px 28px', background: 'var(--black)', borderBottom: '4px solid var(--black)' }}>
            <span className="font-title" style={{ fontSize: '20px', color: 'white', letterSpacing: '0.1em' }}>DAFTAR AKUN</span>
          </div>
          <form onSubmit={handleSubmit} style={{ padding: '36px', display: 'flex', flexDirection: 'column', gap: '28px' }}>
            {error && <div style={{ padding: '16px 20px', background: '#fdf2f2', border: '4px solid #c0392b', fontSize: '13px', color: '#c0392b', fontFamily: 'Dogica', boxShadow: '4px 4px 0 #c0392b', lineHeight: '1.6' }}>⚠ {error}</div>}
            <div>
              <label className="font-pixel" style={{ fontSize: '13px', display: 'block', marginBottom: '14px', color: 'var(--black)', fontWeight: 'bold' }}>NAMA</label>
              <input type="text" value={name} onChange={e => setName(e.target.value)} placeholder="NAMA KAMU" required style={inputStyle} />
            </div>
            <div>
              <label className="font-pixel" style={{ fontSize: '13px', display: 'block', marginBottom: '14px', color: 'var(--black)', fontWeight: 'bold' }}>EMAIL</label>
              <input type="email" value={email} onChange={e => setEmail(e.target.value)} placeholder="KAMU@EMAIL.COM" required style={inputStyle} />
            </div>
            <div>
              <label className="font-pixel" style={{ fontSize: '13px', display: 'block', marginBottom: '14px', color: 'var(--black)', fontWeight: 'bold' }}>PASSWORD</label>
              <input type="password" value={password} onChange={e => setPassword(e.target.value)} placeholder="••••••••" required style={inputStyle} />
            </div>
            <button type="submit" disabled={loading} className="btn-pixel" style={{ width: '100%', justifyContent: 'center', marginTop: '12px' }}>
              <Zap size={18} strokeWidth={3} /> {loading ? 'LOADING...' : 'DAFTAR'}
            </button>
          </form>
        </div>

        <p className="font-pixel" style={{ textAlign: 'center', marginTop: '28px', fontSize: '13px', color: 'var(--black)', letterSpacing: '0.05em' }}>
          SUDAH PUNYA AKUN?{' '}
          <Link to="/login" style={{ color: 'var(--orange)', textDecoration: 'none', fontWeight: 'bold' }}>MASUK</Link>
        </p>
      </div>
    </div>
  )
}
