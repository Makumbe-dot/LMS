/* The Communications section: what reaches borrowers by SMS, WhatsApp and email.

   Overview   - traffic by channel, what is waiting or failed, which channels are live
   Automation - which messages go out by themselves, how often, how soon
   Wording    - the text of every message (it used to live on Settings)
   Channels   - each channel's set-up, and a test message to any number
   The Outbox is the Messages page (/notifications), one tab along. */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import Icon from '../components/Icons.jsx'
import MessageTemplates from '../components/MessageTemplates.jsx'
import { useToast } from '../components/Toast.jsx'
import { Check, ErrorBanner, Field, Kpi, Loading, PageHeader } from '../components/ui.jsx'
import { post, put } from '../lib/api.js'
import { useAuth } from '../lib/auth.jsx'
import { dateTime, humanise } from '../lib/format.js'
import { useApi } from '../lib/useApi.js'

const CHANNELS = [
  { key: 'sms', label: 'SMS', icon: 'phone', backend: 'sms_backend', delivers: 'sms_delivers' },
  {
    key: 'whatsapp',
    label: 'WhatsApp',
    icon: 'whatsapp',
    backend: 'whatsapp_backend',
    delivers: 'whatsapp_delivers',
  },
  { key: 'email', label: 'Email', icon: 'mail', backend: 'email_backend', delivers: 'email_delivers' },
]

const count = (n) => Number(n ?? 0).toLocaleString('en-GB')

/** "Live", "Logging only" or "Off", for one channel. */
function ChannelState({ gateway, channel }) {
  const backend = gateway?.[channel.backend]
  if (backend === 'off') return <span className="badge cancelled">Off</span>
  return gateway?.[channel.delivers] ? (
    <span className="badge active">Live · {backend}</span>
  ) : (
    <span className="badge pending">Logging only</span>
  )
}

// ---------------------------------------------------------------- overview
export function CommunicationsOverview() {
  const { data, error, loading, reload } = useApi('/api/communications/overview')
  if (loading && !data) return <Loading what="Loading communications" />
  if (error) return <ErrorBanner error={error} onRetry={reload} />
  if (!data) return null

  const anyLive = CHANNELS.some((c) => data.gateway[c.delivers])
  const run = data.last_daily_run

  return (
    <>
      <PageHeader
        title="Communications"
        meta={`What goes to borrowers by SMS, WhatsApp and email. Figures are for the last ${data.days} days.`}
      >
        <Link className="btn" to="/communications/automation">
          <Icon name="settings" size={15} />
          Automation
        </Link>
        <Link className="btn primary" to="/notifications">
          <Icon name="message" size={15} />
          Outbox
        </Link>
      </PageHeader>

      {data.gateway.test_recipient ? (
        <div className="hint" role="status">
          <strong>Test mode is on. </strong>
          Every SMS and WhatsApp goes to <strong>{data.gateway.test_recipient}</strong> instead of the borrower
          {data.gateway.test_email ? `, every email to ${data.gateway.test_email}` : ''}, marked with whom it was for.
          Remove <code>MESSAGE_TEST_RECIPIENT</code> from <code>backend/.env</code> to message borrowers for real.
        </div>
      ) : null}
      <div className={anyLive ? 'hint' : 'banner'} role={anyLive ? undefined : 'status'}>
        {anyLive ? (
          <strong>Messages are reaching borrowers. </strong>
        ) : (
          <strong>Nothing is reaching borrowers yet. </strong>
        )}
        {data.rules.auto_send ? (
          <>
            The daily run sends reminders and notices automatically
            {data.rules.send_immediately ? '; receipts and payout confirmations go the moment they are posted' : ''}.
          </>
        ) : (
          <>Automatic sending is off: messages wait in the Outbox until someone sends them.</>
        )}{' '}
        {anyLive ? null : (
          <>
            Every channel is set to log only. <Link to="/communications/channels">Set up a channel</Link>.
          </>
        )}
      </div>

      <div className="stat-grid">
        <Kpi
          icon="inbox"
          tone="brand"
          label="Waiting to send"
          value={count(data.queued)}
          sub="in the Outbox"
          foot={data.rules.auto_send ? 'sent by the next daily run' : 'automatic sending is off'}
          to="/notifications"
        />
        <Kpi
          icon="alert"
          tone={data.failed_recently ? 'red' : 'green'}
          label="Failed this week"
          value={count(data.failed_recently)}
          sub={data.failed_recently ? 'look at these in the Outbox' : 'everything got through'}
          to="/notifications"
        />
        <Kpi
          icon="phone"
          tone="ink"
          label="Resent by SMS"
          value={count(data.fell_back_to_sms)}
          sub="when WhatsApp or email could not deliver"
        />
        <Kpi
          icon="history"
          tone={run?.status === 'failed' ? 'red' : 'slate'}
          label="Last daily run"
          value={run ? { ok: 'Succeeded', failed: 'Failed', running: 'Running' }[run.status] || humanise(run.status) : 'Not yet'}
          sub={run ? dateTime(run.started_at) : 'runs with the nightly batch'}
          foot={run ? (run.error || run.output || '').split('\n')[0].slice(0, 70) : ''}
          to="/jobs"
        />
      </div>

      <div className="grid cols-3">
        {CHANNELS.map((channel) => {
          const figures = data.channels[channel.key] || {}
          const prefer = data.preference[channel.key] || 0
          return (
            <div className="card comm-channel" key={channel.key}>
              <div className="card-head">
                <h3 className="comm-title">
                  <span className={`comm-icon ${channel.key}`} aria-hidden="true">
                    <Icon name={channel.icon} size={18} />
                  </span>
                  {channel.label}
                </h3>
                <ChannelState gateway={data.gateway} channel={channel} />
              </div>
              <dl className="comm-figures">
                <div>
                  <dt>Sent</dt>
                  <dd>{count(figures.sent)}</dd>
                </div>
                <div>
                  <dt>Delivered</dt>
                  <dd>{count((figures.delivered || 0) + (figures.read || 0))}</dd>
                </div>
                <div>
                  <dt>Failed</dt>
                  <dd className={figures.failed ? 'tag-danger' : undefined}>{count(figures.failed)}</dd>
                </div>
              </dl>
              <p className="muted comm-foot">
                {count(prefer)} borrower{prefer === 1 ? ' prefers' : 's prefer'} {channel.label}
                {channel.key === 'whatsapp' && figures.read ? ` · ${count(figures.read)} read` : ''}
                {channel.key === 'whatsapp' && figures.by_hand
                  ? ` · ${count(figures.by_hand)} sent by hand`
                  : ''}
                {channel.key === 'email'
                  ? ` · ${count(data.borrowers_with_email)} of ${count(data.borrowers)} have an address`
                  : ''}
              </p>
            </div>
          )
        })}
      </div>
    </>
  )
}

// ---------------------------------------------------------------- automation
export function CommunicationsAutomation() {
  const { can } = useAuth()
  const { toast, toastError } = useToast()
  const { data, error, loading, reload } = useApi('/api/communications/rules')
  const [values, setValues] = useState(null)
  const [busy, setBusy] = useState(false)
  const mayChange = can('messages')

  useEffect(() => {
    if (data) setValues(data)
  }, [data])

  if (loading && !data) return <Loading what="Loading the rules" />
  if (error) return <ErrorBanner error={error} onRetry={reload} />
  if (!values) return null

  const setKind = (kind) => (e) =>
    setValues((v) => ({ ...v, kinds: { ...v.kinds, [kind]: e.target.checked } }))

  async function save(event) {
    event.preventDefault()
    setBusy(true)
    try {
      await put('/api/communications/rules', {
        auto_send: values.auto_send,
        send_immediately: values.send_immediately,
        kinds: values.kinds,
        arrears_every_days: values.arrears_every_days,
        reminder_days_before: values.reminder_days_before,
      })
      toast('Automation saved')
      reload()
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHeader
        title="Automation"
        meta="Which messages go to borrowers by themselves, and when. Each goes by the borrower's chosen channel: SMS, WhatsApp or email."
      />
      <form className="card" onSubmit={save}>
        <fieldset disabled={!mayChange || busy}>
          <h3>Sending</h3>
          <div className="comm-switches">
            <Check
              label="Send automatically"
              checked={values.auto_send}
              onChange={(e) => setValues((v) => ({ ...v, auto_send: e.target.checked }))}
            />
            <p className="muted">
              The daily run (with the nightly batch) queues reminders and notices and sends them. Off:
              they wait in the Outbox for someone to press Send.
            </p>
            <Check
              label="Send receipts and payout confirmations straight away"
              checked={values.send_immediately}
              disabled={!values.auto_send}
              onChange={(e) => setValues((v) => ({ ...v, send_immediately: e.target.checked }))}
            />
            <p className="muted">
              The moment a repayment is posted or a loan paid out, rather than with the next daily run.
            </p>
          </div>

          <h3 style={{ marginTop: 20 }}>Messages</h3>
          <div className="comm-kinds">
            {values.catalogue.map((item) => (
              <label key={item.kind} className={`comm-kind${values.kinds[item.kind] ? ' on' : ''}`}>
                <input
                  type="checkbox"
                  checked={Boolean(values.kinds[item.kind])}
                  onChange={setKind(item.kind)}
                />
                <span>
                  <strong>{humanise(item.kind === 'welcome' ? 'payout confirmation' : item.kind)}</strong>
                  <small>{item.about}</small>
                </span>
              </label>
            ))}
          </div>

          <h3 style={{ marginTop: 20 }}>Timing</h3>
          <div className="grid cols-3">
            <Field
              label="Remind this many days before an instalment"
              type="number"
              min="0"
              max="30"
              value={values.reminder_days_before}
              onChange={(e) => setValues((v) => ({ ...v, reminder_days_before: e.target.value }))}
              hint="0 sends the reminder on the due date itself"
            />
            <Field
              label="Arrears notice at most every (days)"
              type="number"
              min="1"
              max="90"
              value={values.arrears_every_days}
              onChange={(e) => setValues((v) => ({ ...v, arrears_every_days: e.target.value }))}
              hint="1 is a notice every day a loan stays overdue; 7 is once a week"
            />
          </div>
          {mayChange ? (
            <button className="btn primary" type="submit" disabled={busy}>
              {busy ? 'Saving…' : 'Save automation'}
            </button>
          ) : (
            <p className="muted">Changing these needs the messaging right.</p>
          )}
        </fieldset>
      </form>
    </>
  )
}

// ---------------------------------------------------------------- wording
export function CommunicationsWording() {
  return (
    <>
      <PageHeader
        title="Wording"
        meta="The text of every message. The same words go by SMS, email and WhatsApp; WhatsApp templates are approved with this wording (see Channels)."
      />
      <MessageTemplates />
    </>
  )
}

// ---------------------------------------------------------------- channels
const SETUP = {
  sms: {
    lead: 'Any SMS company with a web API: a Zimbabwean bulk-SMS provider, Twilio, Infobip or Africa\'s Talking.',
    keys: ['MESSAGE_SMS_BACKEND=http (or twilio)', 'MESSAGE_HTTP_URL and the fields the provider documents', 'or the TWILIO_ settings'],
  },
  whatsapp: {
    lead: 'Automatic WhatsApp through Meta (WhatsApp Cloud API) or Twilio, with templates approved by Meta. Until then, the WhatsApp button on each loan opens WhatsApp with the message typed in.',
    keys: ['MESSAGE_WHATSAPP_BACKEND=meta', 'META_WHATSAPP_TOKEN and META_WHATSAPP_PHONE_NUMBER_ID', 'META_WHATSAPP_TEMPLATES once approved'],
  },
  email: {
    lead: 'Any mail server or email service that offers SMTP: your own domain\'s mail, Microsoft 365, Gmail for business, or a sending service.',
    keys: ['MESSAGE_EMAIL_BACKEND=smtp', 'EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD', 'DEFAULT_FROM_EMAIL'],
  },
}

export function CommunicationsChannels() {
  const { can } = useAuth()
  const { toastError } = useToast()
  const gateway = useApi('/api/notifications/gateway')
  const [test, setTest] = useState({ channel: 'sms', to: '', text: '' })
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)

  async function sendTest(event) {
    event.preventDefault()
    setBusy(true)
    setResult(null)
    try {
      setResult(await post('/api/communications/test', test))
    } catch (err) {
      toastError(err)
    } finally {
      setBusy(false)
    }
  }

  if (gateway.loading && !gateway.data) return <Loading what="Loading channels" />
  if (gateway.error) return <ErrorBanner error={gateway.error} onRetry={gateway.reload} />
  const g = gateway.data

  return (
    <>
      <PageHeader
        title="Channels"
        meta="How each kind of message leaves the building. Settings live in backend/.env; the server restarts to read them."
      />
      <div className="grid cols-3">
        {CHANNELS.map((channel) => (
          <div className="card comm-channel" key={channel.key}>
            <div className="card-head">
              <h3 className="comm-title">
                <span className={`comm-icon ${channel.key}`} aria-hidden="true">
                  <Icon name={channel.icon} size={18} />
                </span>
                {channel.label}
              </h3>
              <ChannelState gateway={g} channel={channel} />
            </div>
            <p className="comm-lead">{SETUP[channel.key].lead}</p>
            <ul className="comm-keys">
              {SETUP[channel.key].keys.map((key) => (
                <li key={key}>
                  <code>{key}</code>
                </li>
              ))}
            </ul>
            {channel.key === 'whatsapp' ? (
              <p className="muted comm-foot">
                Templates set up: {g.whatsapp_templates?.length ? g.whatsapp_templates.join(', ') : 'none yet'} ·{' '}
                <Link to="/notifications">wording to submit</Link>
              </p>
            ) : null}
          </div>
        ))}
      </div>

      {can('messages') ? (
        <form className="card" onSubmit={sendTest}>
          <h3>Send a test message</h3>
          <p className="muted" style={{ marginTop: -6 }}>
            Straight to any number or address, now, without going through the Outbox. A channel that is
            logging only will say so rather than pretend.
          </p>
          <div className="grid cols-3">
            <Field
              as="select"
              label="Channel"
              value={test.channel}
              onChange={(e) => setTest((t) => ({ ...t, channel: e.target.value }))}
            >
              {CHANNELS.map((c) => (
                <option key={c.key} value={c.key}>
                  {c.label}
                </option>
              ))}
            </Field>
            <Field
              label={test.channel === 'email' ? 'Email address' : 'Mobile number'}
              required
              value={test.to}
              placeholder={test.channel === 'email' ? 'name@example.com' : '0780 062 362'}
              onChange={(e) => setTest((t) => ({ ...t, to: e.target.value }))}
            />
            <Field
              label="Text (optional)"
              value={test.text}
              placeholder="A standard test message"
              onChange={(e) => setTest((t) => ({ ...t, text: e.target.value }))}
            />
          </div>
          <button className="btn primary" type="submit" disabled={busy}>
            <Icon name="play" size={15} />
            {busy ? 'Sending…' : 'Send test'}
          </button>
          {result ? (
            <div className={result.ok && result.delivers ? 'hint' : 'banner'} style={{ marginTop: 14 }}>
              {result.ok && result.delivers ? (
                <>
                  <strong>Sent.</strong> {result.provider} accepted it for {result.to}
                  {result.message_id ? ` (reference ${result.message_id})` : ''}. Check the phone or inbox.
                </>
              ) : result.ok ? (
                <>
                  <strong>Logged, not sent.</strong> {CHANNELS.find((c) => c.key === result.channel)?.label} is set to{' '}
                  <code>{result.provider}</code>, which writes messages to the server log. Set the channel up
                  above to deliver.
                </>
              ) : (
                <>
                  <strong>Not sent.</strong> {result.error}
                </>
              )}
            </div>
          ) : null}
        </form>
      ) : null}
    </>
  )
}
