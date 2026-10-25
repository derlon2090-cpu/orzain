'use client';

import { FormEvent, useEffect, useMemo, useState } from 'react';
import { Activity, Bot, Check, CircleHelp, Command, LockKeyhole, LogOut, Plus, ShieldCheck, Sparkles, X } from 'lucide-react';

type Condition = { kind: string; indicator: string; operator: string; value: string; timeframe: string; symbol: string; period?: number | null; fast_period?: number | null; slow_period?: number | null; signal_period?: number | null };
type Interpretation = { status: 'ready' | 'needs_clarification'; entry_conditions: Condition[]; exit_conditions: Condition[]; missing_information: string[]; ambiguities: string[]; unsupported_instructions: string[]; explanation: string };
type Agent = { id: string; name: string; state: string; strategy_version: number; approval_status: string; strategy: { symbol: string; allocated_capital: string; max_risk_per_trade_percent?: string; max_daily_loss_percent?: string; stop_loss_percent?: string; entry_conditions?: Condition[]; exit_conditions?: Condition[] } };
type Connection = { id: string; exchange: string; label: string; environment: string };
type User = { id: string; email: string };

async function api<T = any>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...init, credentials: 'include', headers: { ...(init.body ? { 'Content-Type': 'application/json' } : {}), ...init.headers } });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `Request failed (${response.status})`);
  return body as T;
}

const text = {
  en: { brand: 'TRADEGEN AI', desk: 'Paper trading workspace', subtitle: 'Review each strategy before it can monitor the market.', login: 'Sign in', register: 'Create account', verify: 'Verify email', email: 'Email', password: 'Password (12+ characters)', signIn: 'Continue', create: 'Create account', verifyButton: 'Verify and continue', demo: 'Paper mode only · Live execution is disabled', title: 'Your agents', createAgent: 'New strategy', empty: 'No agents yet. Interpret an explicit strategy to start a paper agent.', mode: 'Paper', version: 'Revision', approved: 'Approved', awaiting: 'Awaiting approval', draft: 'Draft', paused: 'Paused', signOut: 'Sign out', modalTitle: 'Build a paper strategy', instruction: 'Describe explicit entry and exit rules', interpret: 'Interpret rules', connection: 'Sandbox connection for market filter checks', choose: 'Choose a Binance sandbox connection', needConnection: 'Connect Binance Testnet first. Price rules require exchange tick validation.', parseReady: 'Structured rules ready for your review', needs: 'Clarification needed', entry: 'Entry rules', exit: 'Exit rules', capital: 'Paper allocation (USDT)', tradeRisk: 'Maximum trade risk (%)', dailyLoss: 'Maximum daily loss (%)', stop: 'Stop loss (%)', name: 'Agent name', createDraft: 'Save draft for review', approve: 'Approve paper strategy', explicit: 'Approval is explicit. This starts paper monitoring and never enables live orders.', cancel: 'Cancel', close: 'Close', review: 'Review strategy', error: 'Something went wrong', logout: 'Sign out', noAi: 'AI interpretation is not configured on this server.', riskNote: 'Enter each limit yourself. The parser does not set capital or risk limits.', retry: 'Try again', limits: 'Risk limits' },
  ar: { brand: 'TRADEGEN AI', desk: 'مساحة تداول ورقي', subtitle: 'راجع كل استراتيجية قبل السماح لها بمراقبة السوق.', login: 'تسجيل الدخول', register: 'إنشاء حساب', verify: 'تأكيد البريد', email: 'البريد الإلكتروني', password: 'كلمة المرور (12 حرفاً على الأقل)', signIn: 'متابعة', create: 'إنشاء الحساب', verifyButton: 'تأكيد ومتابعة', demo: 'وضع ورقي فقط · التداول الحي معطل', title: 'وكلاؤك', createAgent: 'استراتيجية جديدة', empty: 'لا توجد وكلاء بعد. فسّر استراتيجية واضحة لبدء وكيل ورقي.', mode: 'ورقي', version: 'الإصدار', approved: 'معتمدة', awaiting: 'بانتظار الاعتماد', draft: 'مسودة', paused: 'متوقف مؤقتاً', signOut: 'تسجيل الخروج', modalTitle: 'إنشاء استراتيجية ورقية', instruction: 'اكتب شروط دخول وخروج صريحة', interpret: 'تفسير الشروط', connection: 'اتصال Sandbox للتحقق من فلاتر السوق', choose: 'اختر اتصال Binance Sandbox', needConnection: 'اربط Binance Testnet أولاً. شروط السعر تتطلب التحقق من tick البورصة.', parseReady: 'الشروط المهيكلة جاهزة لمراجعتك', needs: 'نحتاج إلى توضيح', entry: 'شروط الدخول', exit: 'شروط الخروج', capital: 'رأس المال الورقي (USDT)', tradeRisk: 'أقصى مخاطرة للصفقة (%)', dailyLoss: 'أقصى خسارة يومية (%)', stop: 'وقف الخسارة (%)', name: 'اسم الوكيل', createDraft: 'حفظ المسودة للمراجعة', approve: 'اعتماد الاستراتيجية الورقية', explicit: 'الاعتماد صريح. سيبدأ مراقبة ورقية ولا يفعّل أوامر حية.', cancel: 'إلغاء', close: 'إغلاق', review: 'مراجعة الاستراتيجية', error: 'حدث خطأ', logout: 'تسجيل الخروج', noAi: 'تفسير الذكاء الاصطناعي غير مهيأ على الخادم.', riskNote: 'أدخل كل حد بنفسك. المحلل لا يحدد رأس المال أو حدود المخاطر.', retry: 'إعادة المحاولة', limits: 'حدود المخاطر' },
};

export default function Home() {
  const [language, setLanguage] = useState<'en' | 'ar'>('en');
  const t = text[language];
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [authMode, setAuthMode] = useState<'login' | 'register' | 'verify'>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [verificationToken, setVerificationToken] = useState('');
  const [agents, setAgents] = useState<Agent[]>([]);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [connectionId, setConnectionId] = useState('');
  const [showConnection, setShowConnection] = useState(false);
  const [connectionLabel, setConnectionLabel] = useState('Binance Testnet');
  const [apiKey, setApiKey] = useState('');
  const [apiSecret, setApiSecret] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [agentName, setAgentName] = useState('');
  const [instruction, setInstruction] = useState('');
  const [interpretation, setInterpretation] = useState<Interpretation | null>(null);
  const [limits, setLimits] = useState({ capital: '', tradeRisk: '', dailyLoss: '', stop: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [reviewAgent, setReviewAgent] = useState<Agent | null>(null);

  useEffect(() => { document.documentElement.lang = language; document.documentElement.dir = language === 'ar' ? 'rtl' : 'ltr'; }, [language]);

  const refresh = async () => {
    const [nextAgents, nextConnections] = await Promise.all([api<Agent[]>('/api/v1/agents'), api<Connection[]>('/api/v1/exchange-connections')]);
    setAgents(nextAgents); setConnections(nextConnections); setConnectionId(current => current || nextConnections[0]?.id || '');
  };
  useEffect(() => { api<User>('/api/v1/auth/me').then(async current => { setUser(current); await refresh(); }).catch(() => {}).finally(() => setLoading(false)); }, []);

  async function submitAuth(event: FormEvent) {
    event.preventDefault(); setError(''); setBusy(true);
    try {
      if (authMode === 'register') {
        const result = await api<any>('/api/v1/auth/register', { method: 'POST', body: JSON.stringify({ email, password }) });
        setVerificationToken(result.verification_token || ''); setAuthMode('verify');
        if (!result.verification_token) setNotice('Verification email sent. Enter its token to continue.');
      } else if (authMode === 'verify') {
        await api('/api/v1/auth/verify-email', { method: 'POST', body: JSON.stringify({ token: verificationToken }) });
        setAuthMode('login'); setNotice('Email verified. Sign in to continue.');
      } else {
        const result = await api<{ user: User }>('/api/v1/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) });
        setUser(result.user); await refresh();
      }
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function signOut() { await api('/api/v1/auth/logout', { method: 'POST' }).catch(() => {}); setUser(null); setAgents([]); }
  async function interpret(event: FormEvent) {
    event.preventDefault(); setError(''); setBusy(true);
    try {
      const result = await api<Interpretation>('/api/v1/ai/interpret-strategy', { method: 'POST', body: JSON.stringify({ instruction, exchange_connection_id: connectionId || null }) });
      setInterpretation(result);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function addConnection() {
    setBusy(true); setError('');
    try {
      const created = await api<Connection>('/api/v1/exchange-connections', { method: 'POST', body: JSON.stringify({ exchange: 'binance', label: connectionLabel, api_key: apiKey, api_secret: apiSecret, environment: 'sandbox' }) });
      setConnections(current => [created, ...current]); setConnectionId(created.id); setShowConnection(false); setApiKey(''); setApiSecret('');
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const allRules = useMemo(() => [...(interpretation?.entry_conditions || []), ...(interpretation?.exit_conditions || [])], [interpretation]);
  const canCreate = !!interpretation && interpretation.status === 'ready' && allRules.length > 1 && !interpretation.unsupported_instructions.length && allRules.every(rule => ['PRICE', 'VOLUME', 'RSI', 'MACD', 'SMA', 'EMA'].includes(rule.indicator) && rule.symbol && rule.timeframe !== 'unspecified');
  function pairFrom(symbol: string) {
    const plain = symbol.replace('/', '').toUpperCase();
    const quotes = ['USDT', 'USDC', 'BUSD', 'FDUSD', 'TUSD', 'BTC', 'ETH', 'BNB', 'EUR', 'USD'];
    const quote = quotes.find(item => plain.endsWith(item) && plain.length > item.length) || '';
    return quote ? `${plain.slice(0, -quote.length)}/${quote}` : `${plain.slice(0, -3)}/${plain.slice(-3)}`;
  }
  async function saveDraft(event: FormEvent) {
    event.preventDefault(); if (!interpretation || !canCreate) return;
    if (allRules.some(rule => rule.symbol.replace('/', '') !== allRules[0].symbol.replace('/', ''))) { setError('All conditions must use the same market.'); return; }
    setBusy(true); setError('');
    try {
      const created = await api<Agent>('/api/v1/agents', { method: 'POST', body: JSON.stringify({ name: agentName, strategy: {
        symbol: pairFrom(allRules[0].symbol), allocated_capital: limits.capital, currency: 'USDT', market_type: 'spot', leverage_allowed: false,
        max_risk_per_trade_percent: limits.tradeRisk, max_daily_loss_percent: limits.dailyLoss, stop_loss_percent: limits.stop, stop_loss_required: true,
        entry_conditions: interpretation.entry_conditions, exit_conditions: interpretation.exit_conditions, max_open_positions: 1,
        allowed_order_types: ['market'], execution_mode: 'paper',
      } }) });
      await api(`/api/v1/agents/${created.id}/transition`, { method: 'POST', body: JSON.stringify({ target: 'VALIDATING' }) });
      const awaiting = await api<Agent>(`/api/v1/agents/${created.id}/transition`, { method: 'POST', body: JSON.stringify({ target: 'AWAITING_APPROVAL' }) });
      setReviewAgent(awaiting); setShowCreate(false); await refresh();
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function approve(agent: Agent) {
    setBusy(true); setError('');
    try { await api(`/api/v1/agents/${agent.id}/approve`, { method: 'POST', body: JSON.stringify({ explicit_approval: true }) }); setReviewAgent(null); await refresh(); setNotice(language === 'ar' ? 'تم اعتماد الإصدار وبدء المراقبة الورقية.' : 'Revision approved. Paper monitoring has started.'); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  function openCreate() { setError(''); setNotice(''); setAgentName(''); setInstruction(''); setInterpretation(null); setLimits({ capital: '', tradeRisk: '', dailyLoss: '', stop: '' }); setShowCreate(true); }

  if (loading) return <main className="auth-screen"><div className="auth-card">TRADEGEN AI</div></main>;
  if (!user) return <main className="auth-screen" dir={language === 'ar' ? 'rtl' : 'ltr'}><button className="language-toggle" onClick={() => setLanguage(language === 'ar' ? 'en' : 'ar')}>{language === 'ar' ? 'English' : 'العربية'}</button><form className="auth-card" onSubmit={submitAuth}>
    <div className="brand-mark"><Command size={19}/></div><p className="eyebrow">{t.brand}</p><h1>{authMode === 'verify' ? t.verify : authMode === 'register' ? t.register : t.login}</h1><p className="subtle">{t.demo}</p>
    {authMode !== 'verify' && <><label>{t.email}<input type="email" autoComplete="email" required value={email} onChange={e => setEmail(e.target.value)}/></label><label>{t.password}<input type="password" autoComplete={authMode === 'register' ? 'new-password' : 'current-password'} minLength={authMode === 'register' ? 12 : undefined} required value={password} onChange={e => setPassword(e.target.value)}/></label></>}
    {authMode === 'verify' && <label>{t.verify}<input required minLength={32} value={verificationToken} onChange={e => setVerificationToken(e.target.value)} placeholder="Email verification token"/></label>}
    {error && <p className="inline-error">{error}</p>}{notice && <p className="inline-success">{notice}</p>}
    <button className="primary-btn full-btn" disabled={busy}>{busy ? '…' : authMode === 'verify' ? t.verifyButton : authMode === 'register' ? t.create : t.signIn}</button>
    {authMode !== 'verify' && <button type="button" className="switch-auth" onClick={() => { setError(''); setAuthMode(authMode === 'login' ? 'register' : 'login'); }}>{authMode === 'login' ? t.register : t.login}</button>}
  </form></main>;

  return <main className="app-shell" dir={language === 'ar' ? 'rtl' : 'ltr'}>
    <aside className="sidebar"><div className="brand"><div className="brand-mark"><Command size={19}/></div><span>{t.brand}</span></div><div className="workspace-label">WORKSPACE</div><div className="workspace"><div className="workspace-avatar">T</div><div><b>{t.desk}</b><small>{user.email}</small></div></div><div className="nav-label">PLATFORM</div><nav><button className="nav-item active"><Activity size={17}/>{t.title}</button><button className="nav-item"><Bot size={17}/>{t.review}</button></nav><div className="sidebar-spacer"/><div className="security-note"><div className="security-icon"><LockKeyhole size={15}/></div><div><b>{t.demo}</b><small>{language === 'ar' ? 'لا توجد أوامر حية' : 'No live orders'}</small></div></div><button className="nav-item" onClick={signOut}><LogOut size={16}/>{t.signOut}</button></aside>
    <section className="main-area"><header className="topbar"><div className="crumb">{t.desk} <span>/</span> <b>{t.title}</b></div><div className="top-actions"><button className="language-toggle" onClick={() => setLanguage(language === 'ar' ? 'en' : 'ar')}>{language === 'ar' ? 'English' : 'العربية'}</button><div className="live-pill"><i/> PAPER ONLY</div><div className="top-avatar">{user.email.slice(0, 1).toUpperCase()}</div></div></header>
      <div className="content"><div className="page-heading"><div><div className="eyebrow"><Sparkles size={13}/> CONTROLLED PAPER TRADING</div><h1>{t.title}</h1><p>{t.subtitle}</p></div><button className="primary-btn" onClick={openCreate}><Plus size={17}/>{t.createAgent}</button></div>
      <div className="demo-banner"><div className="banner-icon"><ShieldCheck size={18}/></div><div><b>{t.demo}</b><span>{language === 'ar' ? 'تُقيّم الشروط المعتمدة مقابل شموع Binance Testnet المغلقة. كل أمر يمر بمحرك المخاطر والدفتر المزدوج.' : 'Approved rules are evaluated against closed Binance Testnet candles. Every proposal passes risk checks and the double-entry paper ledger.'}</span></div></div>
      <div className="section-top"><div><h2>{t.title} <span className="count-badge">{agents.length}</span></h2><p>{language === 'ar' ? 'الحالة والإصدار المعتمد' : 'Status and approved revision'}</p></div></div>
      {agents.length === 0 ? <section className="panel empty-panel"><Bot size={30}/><p>{t.empty}</p><button className="secondary-btn" onClick={openCreate}><Plus size={15}/>{t.createAgent}</button></section> : <section className="panel agents-panel"><div className="table-wrap"><table><thead><tr><th>{language === 'ar' ? 'الوكيل' : 'AGENT'}</th><th>{language === 'ar' ? 'السوق' : 'MARKET'}</th><th>{language === 'ar' ? 'التخصيص' : 'ALLOCATION'}</th><th>{language === 'ar' ? 'الإصدار' : 'REVISION'}</th><th>{language === 'ar' ? 'الحالة' : 'STATUS'}</th><th/></tr></thead><tbody>{agents.map((agent, index) => <tr key={agent.id}><td><div className="agent-cell"><div className={`agent-icon agent-icon-${index % 3}`}><Bot size={17}/></div><div><b>{agent.name}</b><small>{agent.approval_status}</small></div></div></td><td><span className="symbol-tag">{agent.strategy.symbol}</span></td><td className="money">{agent.strategy.allocated_capital} USDT</td><td>v{agent.strategy_version}</td><td><span className={`status ${agent.state === 'PAPER_TRADING' ? '' : 'paused'}`}><i/>{agent.state === 'PAPER_TRADING' ? t.approved : agent.state === 'AWAITING_APPROVAL' ? t.awaiting : agent.state}</span></td><td>{agent.state === 'AWAITING_APPROVAL' && <button className="text-link" onClick={() => setReviewAgent(agent)}>{t.review} →</button>}</td></tr>)}</tbody></table></div></section>}
      <div className="activity-heading"/><section className="panel activity-panel"><div className="activity-row"><div className="activity-icon"><Check size={15}/></div><div className="activity-copy"><b>{language === 'ar' ? 'فحوص المخاطر والدفتر إلزامية' : 'Risk checks and ledger are mandatory'}</b><small>{language === 'ar' ? 'يظل التنفيذ الحي معطلاً في هذا الإصدار.' : 'Live execution remains disabled in this build.'}</small></div></div></section><footer><span>© 2026 TRADEGEN AI</span><span>{t.demo}</span></footer>
      {notice && <div className="notice-toast"><CircleHelp size={17}/>{notice}<button onClick={() => setNotice('')} aria-label="Dismiss"><X size={15}/></button></div>}
      </div>
    </section>
    {showCreate && <div className="modal-backdrop"><form className="modal workflow-modal" onSubmit={interpretation ? saveDraft : interpret}><div className="modal-head"><div><span className="eyebrow">{t.modalTitle}</span><h2>{interpretation ? t.review : t.instruction}</h2></div><button type="button" className="icon-btn" onClick={() => setShowCreate(false)} aria-label={t.close}><X size={18}/></button></div>
      {!interpretation ? <><div className="modal-notice"><ShieldCheck size={17}/><span>{t.needConnection}</span></div><label>{t.connection}<select value={connectionId} onChange={e => setConnectionId(e.target.value)}><option value="">{t.choose}</option>{connections.filter(item => item.exchange === 'binance').map(item => <option key={item.id} value={item.id}>{item.label} · {item.environment}</option>)}</select></label><button type="button" className="text-link connection-link" onClick={() => setShowConnection(!showConnection)}>{language === 'ar' ? 'إضافة مفاتيح Binance Testnet' : 'Add Binance Testnet credentials'}</button>{showConnection && <div className="connection-fields"><label>{language === 'ar' ? 'اسم الاتصال' : 'Connection label'}<input required value={connectionLabel} onChange={e => setConnectionLabel(e.target.value)}/></label><label>{language === 'ar' ? 'مفتاح API التجريبي' : 'Testnet API key'}<input required autoComplete="off" value={apiKey} onChange={e => setApiKey(e.target.value)}/></label><label>{language === 'ar' ? 'سر API التجريبي' : 'Testnet API secret'}<input required type="password" autoComplete="new-password" value={apiSecret} onChange={e => setApiSecret(e.target.value)}/></label><button type="button" className="secondary-btn" disabled={busy || !apiKey || !apiSecret} onClick={addConnection}>{language === 'ar' ? 'حفظ مشفر' : 'Save encrypted'}</button></div>}<label>{t.instruction}<textarea required minLength={3} maxLength={4000} rows={5} value={instruction} onChange={e => setInstruction(e.target.value)} placeholder="BTCUSDT: buy when price crosses above 65000 on 5m; exit when it crosses below 64000 on 5m"/><small>{language === 'ar' ? 'لا يضع المحلل قيم المخاطرة أو رأس المال.' : t.riskNote}</small></label></> : <>
        <div className={`parse-status ${canCreate ? 'ready' : 'needs'}`}><ShieldCheck size={17}/><span>{canCreate ? t.parseReady : t.needs}: {interpretation.explanation}</span></div>
        <div className="rule-columns"><RuleList title={t.entry} rules={interpretation.entry_conditions} tone="entry"/><RuleList title={t.exit} rules={interpretation.exit_conditions} tone="exit"/></div>
        {(interpretation.missing_information.length + interpretation.ambiguities.length + interpretation.unsupported_instructions.length > 0) && <div className="clarifications">{[...interpretation.missing_information, ...interpretation.ambiguities, ...interpretation.unsupported_instructions].map((item, i) => <p key={`${item}-${i}`}>• {item}</p>)}</div>}
        {canCreate && <><label>{t.name}<input required minLength={1} maxLength={64} value={agentName} onChange={e => setAgentName(e.target.value)} placeholder="BTC paper strategy"/></label><p className="risk-note">{t.riskNote}</p><div className="limit-grid">
          <label>{t.capital}<input required type="number" min="0.0001" step="0.0001" value={limits.capital} onChange={e => setLimits({...limits, capital:e.target.value})}/></label>
          <label>{t.tradeRisk}<input required type="number" min="0.01" max="5" step="0.01" value={limits.tradeRisk} onChange={e => setLimits({...limits, tradeRisk:e.target.value})}/></label>
          <label>{t.dailyLoss}<input required type="number" min="0.01" max="20" step="0.01" value={limits.dailyLoss} onChange={e => setLimits({...limits, dailyLoss:e.target.value})}/></label>
          <label>{t.stop}<input required type="number" min="0.01" max="20" step="0.01" value={limits.stop} onChange={e => setLimits({...limits, stop:e.target.value})}/></label>
        </div></>}
      </>}
      {error && <p className="inline-error">{error}</p>}<div className="modal-actions"><button type="button" className="secondary-btn" onClick={() => interpretation ? setInterpretation(null) : setShowCreate(false)}>{interpretation ? t.retry : t.cancel}</button>{!interpretation && <button className="primary-btn" disabled={busy || !connectionId}>{busy ? '…' : <><Sparkles size={15}/>{t.interpret}</>}</button>}{interpretation && canCreate && <button className="primary-btn" disabled={busy || !agentName || !limits.capital || !limits.tradeRisk || !limits.dailyLoss || !limits.stop}>{busy ? '…' : t.createDraft}</button>}</div>
      {interpretation && canCreate && <p className="approval-note">{t.explicit}</p>}
      </form></div>}
    {reviewAgent && <div className="modal-backdrop"><section className="modal" role="dialog" aria-modal="true"><div className="modal-head"><div><span className="eyebrow">{t.awaiting}</span><h2>{reviewAgent.name} · v{reviewAgent.strategy_version}</h2></div><button className="icon-btn" onClick={() => setReviewAgent(null)} aria-label={t.close}><X size={18}/></button></div><p className="approval-note">{t.explicit}</p><div className="approval-summary"><b>{t.limits}</b><span>{t.capital}: {reviewAgent.strategy.allocated_capital} USDT</span><span>{t.tradeRisk}: {reviewAgent.strategy.max_risk_per_trade_percent}%</span><span>{t.dailyLoss}: {reviewAgent.strategy.max_daily_loss_percent}%</span><span>{t.stop}: {reviewAgent.strategy.stop_loss_percent}%</span></div><RuleList title={t.entry} rules={reviewAgent.strategy.entry_conditions || []} tone="entry"/><RuleList title={t.exit} rules={reviewAgent.strategy.exit_conditions || []} tone="exit"/><div className="modal-actions"><button className="secondary-btn" onClick={() => setReviewAgent(null)}>{t.cancel}</button><button className="primary-btn" disabled={busy} onClick={() => approve(reviewAgent)}><ShieldCheck size={15}/>{t.approve}</button></div>{error && <p className="inline-error">{error}</p>}</section></div>}
  </main>;
}

function RuleList({ title, rules, tone }: { title: string; rules: Condition[]; tone: 'entry' | 'exit' }) {
  return <section className="rule-list"><h3>{title}</h3>{rules.length ? rules.map((rule, i) => { const params = rule.indicator === 'MACD' ? `${rule.fast_period}/${rule.slow_period}/${rule.signal_period}` : rule.period ? `${rule.period}` : ''; const indicatorLabel = rule.indicator === 'MACD' ? 'MACD histogram' : rule.indicator; return <div className={`rule-card ${tone}`} key={`${rule.indicator}-${i}`}><b>{indicatorLabel}{params ? `(${params})` : ''} {rule.operator.replaceAll('_', ' ')} {rule.value}</b><small>{rule.symbol} · {rule.timeframe} · {rule.kind}</small></div>; }) : <p className="subtle">—</p>}</section>;
}
