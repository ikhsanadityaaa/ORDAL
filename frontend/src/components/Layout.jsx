import { Outlet, NavLink, useNavigate } from 'react-router-dom'
import { ClipboardList, HelpCircle, LogOut, Settings, Zap } from 'lucide-react'
import useAuthStore from '../stores/authStore'

const nav = [
  { to: '/kerja',     icon: Zap,        label: 'Cari Kerja' },
  { to: '/riwayat-lamaran', icon: ClipboardList, label: 'Riwayat Lamaran' },
  { to: '/kumpulan-pertanyaan', icon: HelpCircle, label: 'Kumpulan Pertanyaan' },
  { to: '/persiapan', icon: Settings,   label: 'Persiapan' },
]

export default function Layout() {
  const { user, logout } = useAuthStore()
  const navigate = useNavigate()
  const handleLogout = () => { logout(); navigate('/login') }

  return (
    <div className="flex h-screen overflow-hidden" style={{ background: 'var(--cream)' }}>
      {/* Sidebar */}
      <aside className="pixel-noise flex flex-col shrink-0"
        style={{
          width: 'var(--sidebar-w)',
          background: 'var(--black)',
          borderRight: '5px solid var(--orange)',
        }}>

        {/* Logo */}
        <div className="px-5 py-6" style={{ borderBottom: '3px solid var(--black-3)' }}>
          <div className="flex items-center gap-4">
            <div className="w-14 h-14 flex items-center justify-center shrink-0"
              style={{ background: 'var(--orange)', border: '3px solid var(--orange-2)', boxShadow: '3px 3px 0 #000' }}>
              <Zap size={26} color="white" strokeWidth={3} />
            </div>
            <div>
              <span className="font-title" style={{ fontSize: '34px', letterSpacing: '0.1em', color: 'white', lineHeight: 1 }}>ORDAL</span>
              <p style={{ fontSize: '9px', color: 'var(--cream-2)', lineHeight: 1.6, marginTop: '4px' }}>
                Bantuin Kamu Dapet Kerja
              </p>
            </div>
          </div>
        </div>

        {/* Nav */}
        <nav className="flex-1 px-3 py-5 space-y-3">
          {nav.map(({ to, icon: Icon, label }) => (
            <NavLink key={to} to={to}>
              {({ isActive }) => (
                <div
                  className="flex items-center gap-3 px-4 py-3.5 transition-all font-pixel"
                  style={{
                    background:  isActive ? 'var(--orange)' : 'transparent',
                    color:       isActive ? 'white' : 'var(--muted)',
                    border:      isActive ? '3px solid var(--orange-2)' : '3px solid transparent',
                    boxShadow:   isActive ? '3px 3px 0 rgba(0,0,0,0.5)' : 'none',
                    fontSize: '11px',
                    fontFamily: 'Dogica',
                    fontWeight: isActive ? 'bold' : 'normal',
                  }}
                >
                  <Icon size={18} strokeWidth={isActive ? 3 : 2} />
                  {label}
                </div>
              )}
            </NavLink>
          ))}
        </nav>

        {/* User */}
        <div className="px-3 py-5" style={{ borderTop: '3px solid var(--black-3)' }}>
          <div className="px-4 py-3 mb-3">
            <p className="font-pixel" style={{ fontSize: '10px', color: 'var(--muted)', letterSpacing: '0.05em' }}>LOGGED IN AS</p>
            <p className="font-pixel truncate" style={{ fontSize: '12px', color: 'var(--cream)', marginTop: '8px', fontWeight: 'bold' }}>{user?.name}</p>
          </div>
          <button onClick={handleLogout}
            className="w-full flex items-center gap-3 px-4 py-3 transition-all font-pixel"
            style={{ color: '#e74c3c', fontSize: '12px', background: 'transparent', border: '3px solid transparent', cursor: 'pointer', fontFamily: 'Dogica', fontWeight: 'bold' }}
            onMouseEnter={e => {
              e.currentTarget.style.background = 'rgba(231,76,60,0.1)'
              e.currentTarget.style.border = '3px solid #e74c3c'
            }}
            onMouseLeave={e => {
              e.currentTarget.style.background = 'transparent'
              e.currentTarget.style.border = '3px solid transparent'
            }}
          >
            <LogOut size={16} strokeWidth={2.5} />
            LOGOUT
          </button>
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 min-w-0 overflow-auto pixel-grid">
        <Outlet />
      </main>
    </div>
  )
}
