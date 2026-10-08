import Icon from '../components/Icons.jsx'
import { ErrorBanner, Loading, PageHeader } from '../components/ui.jsx'
import { dateTime } from '../lib/format.js'
import { useApi } from '../lib/useApi.js'

const MARK = {
  pass: { icon: 'check', label: 'Done' },
  warn: { icon: 'alert', label: 'Look at' },
  fail: { icon: 'ban', label: 'Fix before going live' },
}

/**
 * What stands between this installation and real borrowers: security, your
 * details, demo data, messaging and the nightly batch. It only reports; each
 * item says where to put it right.
 */
export default function GoLive() {
  const { data, error, loading, reload } = useApi('/api/go-live')
  if (loading && !data) return <Loading what="Checking" />
  if (error) return <ErrorBanner error={error} onRetry={reload} />
  if (!data) return null

  const areas = []
  for (const item of data.items) {
    let area = areas.find((a) => a.name === item.area)
    if (!area) areas.push((area = { name: item.area, items: [] }))
    area.items.push(item)
  }

  return (
    <>
      <PageHeader
        title="Go-live checklist"
        meta={`Checked ${dateTime(data.checked_at)}. Nothing here changes anything; each item says where to put it right.`}
      >
        <button type="button" className="btn" onClick={reload}>
          <Icon name="history" size={15} />
          Check again
        </button>
      </PageHeader>

      <div className={data.ready ? 'hint' : 'banner'} role="status">
        {data.ready ? (
          <strong>Ready for real borrowers. </strong>
        ) : (
          <strong>Not ready yet: {data.fail} thing{data.fail === 1 ? '' : 's'} to fix. </strong>
        )}
        {data.pass} done, {data.warn} to look at.
      </div>

      {areas.map((area) => (
        <div className="card golive-area" key={area.name}>
          <h3>{area.name}</h3>
          <ul className="golive-list">
            {area.items.map((item) => (
              <li key={item.key} className={`golive-item ${item.status}`}>
                <span className="golive-mark" title={MARK[item.status].label}>
                  <Icon name={MARK[item.status].icon} size={15} />
                </span>
                <span className="golive-text">
                  <strong>{item.label}</strong>
                  <span>{item.detail}</span>
                  {item.status !== 'pass' && item.fix ? <em>{item.fix}</em> : null}
                </span>
                <span className={`badge ${item.status === 'pass' ? 'active' : item.status === 'warn' ? 'pending' : 'failed'}`}>
                  {MARK[item.status].label}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </>
  )
}
