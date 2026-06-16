import CVManager from './CVManager'
import Settings from './Settings'

export default function Persiapan() {
  return (
    <div style={{ padding: '24px', minHeight: '100%' }}>
      <div style={{ marginBottom: '20px' }}>
        <h1 className="font-title" style={{ fontSize: '34px', marginBottom: '4px', color: 'var(--black)' }}>
          PERSIAPAN
        </h1>
        <p style={{ fontSize: '12px', color: 'var(--muted)' }}>
          Siapkan CV dan capture session platform sebelum ORDAL jalan.
        </p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 360px), 1fr))', gap: '18px', alignItems: 'start' }}>
        <section>
          <h2 className="font-pixel" style={{ fontSize: '11px', marginBottom: '12px' }}>CV</h2>
          <CVManager embedded />
        </section>
        <section>
          <h2 className="font-pixel" style={{ fontSize: '11px', marginBottom: '12px' }}>LOGIN PLATFORM</h2>
          <Settings embedded />
        </section>
      </div>
    </div>
  )
}
