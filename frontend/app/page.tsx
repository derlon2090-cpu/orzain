'use client';

import { FormEvent, useEffect, useMemo, useState } from 'react';
import { Activity, ArrowUpRight, Bot, Check, ChevronDown, CircleHelp, Command, Globe2, Layers3, LockKeyhole, LogOut, Plus, ShieldCheck, Sparkles, X } from 'lucide-react';

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

  const activeAgents = agents.filter(agent => agent.state === 'PAPER_TRADING').length;
  const pendingAgents = agents.filter(agent => agent.state === 'AWAITING_APPROVAL').length;
  const isArabic = language === 'ar';

  if (loading) return <main className="auth-screen"><div className="loading-mark"><div className="brand-mark"><Command size={20}/></div><span>{t.brand}</span></div></main>;
  if (!user) return <main className="auth-screen" dir={isArabic ? 'rtl' : 'ltr'}>
    <button className="language-toggle auth-language" onClick={() => setLanguage(isArabic ? 'en' : 'ar')}>{isArabic ? 'English' : 'العربية'}</button>
    <div className="auth-layout">
      <section className="auth-story"><div className="brand auth-brand"><div className="brand-mark"><Command size={19}/></div><span>{t.brand}</span></div><div className="story-copy"><div className="eyebrow"><span className="eyebrow-line"/>{isArabic ? 'مساحة تداول منضبطة' : 'A more deliberate trading desk'}</div><h2>{isArabic ? 'خطط بوضوح.\nنفّذ بثقة.' : 'Trade with a plan.\nStay in control.'}</h2><p>{isArabic ? 'مساحة آمنة لاختبار الاستراتيجيات ومراجعتها قبل أي مراقبة للسوق.' : 'A focused workspace to shape, review and paper-test your strategies before they monitor a market.'}</p><div className="story-points"><div><ShieldCheck size={17}/><span>{isArabic ? 'الموافقة البشرية مطلوبة' : 'Human approval stays in control'}</span></div><div><Activity size={17}/><span>{isArabic ? 'تنفيذ ورقي فقط' : 'Paper execution only'}</span></div></div></div><div className="story-footer"><span>© 2026 TRADEGEN AI</span><span>{isArabic ? 'مصمم للمراجعة قبل التنفيذ' : 'Built for considered decisions'}</span></div><div className="story-orbit orbit-one"/><div className="story-orbit orbit-two"/><div className="story-grid"/></section>
      <section className="auth-side"><form className="auth-card" onSubmit={submitAuth}>
        <div className="auth-heading"><span className="auth-kicker">{authMode === 'verify' ? (isArabic ? 'تأكيد الحساب' : 'ACCOUNT VERIFICATION') : isArabic ? 'مرحباً بعودتك' : 'WELCOME BACK'}</span><h1>{authMode === 'verify' ? t.verify : authMode === 'register' ? t.register : t.login}</h1><p>{authMode === 'verify' ? (isArabic ? 'أدخل رمز التحقق الذي وصلك عبر البريد.' : 'Enter the verification token sent to your email.') : isArabic ? 'سجّل الدخول إلى مساحة العمل الآمنة.' : 'Sign in to your secure trading workspace.'}</p></div>
        {authMode !== 'verify' && <><label>{t.email}<input type="email" autoComplete="email" required value={email} onChange={e => setEmail(e.target.value)} placeholder="name@example.com"/></label><label>{t.password}<input type="password" autoComplete={authMode === 'register' ? 'new-password' : 'current-password'} minLength={authMode === 'register' ? 12 : undefined} required value={password} onChange={e => setPassword(e.target.value)} placeholder="••••••••••••"/></label></>}
        {authMode === 'verify' && <label>{t.verify}<input required minLength={32} value={verificationToken} onChange={e => setVerificationToken(e.target.value)} placeholder="Email verification token"/></label>}
        {error && <p className="inline-error">{error}</p>}{notice && <p className="inline-success">{notice}</p>}
        <button className="primary-btn full-btn" disabled={busy}>{busy ? '…' : authMode === 'verify' ? t.verifyButton : authMode === 'register' ? t.create : t.signIn}<ArrowUpRight size={16}/></button>
        {authMode !== 'verify' && <button type="button" className="switch-auth" onClick={() => { setError(''); setAuthMode(authMode === 'login' ? 'register' : 'login'); }}>{authMode === 'login' ? t.register : t.login}</button>}
        <div className="auth-assurance"><LockKeyhole size={14}/><span>{isArabic ? 'بياناتك مشفرة، والتنفيذ الحي معطل.' : 'Encrypted credentials. Live execution disabled.'}</span></div>
      </form><div className="auth-side-foot"><Globe2 size={14}/>{isArabic ? 'بيئة آمنة للتداول الورقي' : 'A secure environment for paper trading'}</div></section>
    </div>
  </main>;

  return <main className="app-shell" dir={isArabic ? 'rtl' : 'ltr'}>
    <aside className="sidebar"><div className="brand"><div className="brand-mark"><Command size={19}/></div><span>TRADEGEN <em>AI</em></span></div><div className="workspace-label">{isArabic ? 'مساحة العمل' : 'WORKSPACE'}</div><div className="workspace"><div className="workspace-avatar">T</div><div><b>{isArabic ? 'مكتب التداول' : 'Trading desk'}</b><small>{user.email}</small></div><ChevronDown size={15}/></div><div className="nav-label">{isArabic ? 'إدارة' : 'MANAGE'}</div><nav><button className="nav-item active"><Layers3 size={17}/>{isArabic ? 'الاستراتيجيات' : 'Strategies'}<span className="nav-count">{agents.length}</span></button></nav><div className="sidebar-bottom"><div className="security-note"><div className="security-icon"><LockKeyhole size={16}/></div><div><b>{isArabic ? 'حساب محمي' : 'Protected workspace'}</b><small>{isArabic ? 'الأوامر الحية معطلة' : 'Live orders are disabled'}</small></div></div><button className="nav-item signout-item" onClick={signOut}><LogOut size={16}/>{t.signOut}</button><div className="profile"><div className="profile-pic">{user.email.slice(0, 1).toUpperCase()}</div><div className="profile-name"><b>{user.email.split('@')[0]}</b><small>{isArabic ? 'متداول ورقي' : 'Paper trader'}</small></div><button className="profile-more" onClick={signOut} aria-label={t.signOut}><LogOut size={15}/></button></div></div></aside>
    <section className="main-area"><header className="topbar"><div className="crumb"><span>{isArabic ? 'مساحة العمل' : 'Workspace'}</span><span className="crumb-separator">/</span><b>{isArabic ? 'الاستراتيجيات' : 'Strategies'}</b></div><div className="top-actions"><div className="env-chip"><i/>{isArabic ? 'وضع ورقي' : 'PAPER MODE'}</div><span className="top-divider"/><button className="language-toggle" onClick={() => setLanguage(isArabic ? 'en' : 'ar')}>{isArabic ? 'EN' : 'عربي'}</button><div className="top-avatar">{user.email.slice(0, 1).toUpperCase()}</div></div></header>
      <div className="content"><div className="page-heading"><div><div className="eyebrow"><span className="eyebrow-line"/>{isArabic ? 'مركز الاستراتيجيات' : 'STRATEGY CONTROL CENTER'}</div><h1>{isArabic ? 'مساحة تداولك' : 'Your trading desk'}<span className="heading-period">.</span></h1><p>{isArabic ? 'راجع استراتيجياتك الورقية وحالة الموافقات من مكان واحد.' : 'Review your paper strategies and approvals from one considered workspace.'}</p></div><button className="primary-btn create-btn" onClick={openCreate}><Plus size={17}/>{t.createAgent}</button></div>
      <section className="hero-banner"><div className="hero-banner-copy"><div className="hero-kicker"><ShieldCheck size={15}/>{isArabic ? 'بيئة محاكاة محمية' : 'PROTECTED SIMULATION ENVIRONMENT'}</div><h2>{isArabic ? 'كل قرار يمر عبر مراجعتك.' : 'Every strategy stays under your review.'}</h2><p>{isArabic ? 'تُختبر الشروط على بيانات Binance Testnet، ولا يبدأ أي وكيل بالمراقبة قبل اعتمادك الصريح.' : 'Rules are evaluated against Binance Testnet data. No agent monitors a market until you explicitly approve it.'}</p></div><div className="hero-visual" aria-hidden="true"><div className="visual-ring ring-a"/><div className="visual-ring ring-b"/><div className="visual-core"><ShieldCheck size={28}/></div><span className="visual-node node-a"/><span className="visual-node node-b"/><span className="visual-node node-c"/></div><div className="hero-status"><span className="status-dot"/><span>{isArabic ? 'التنفيذ الحي معطل' : 'LIVE EXECUTION DISABLED'}</span></div></section>
      <div className="metrics-grid"><article className="metric-card"><div className="metric-title"><span>{isArabic ? 'كل الاستراتيجيات' : 'Total strategies'}</span><span className="metric-icon"><Layers3 size={16}/></span></div><div className="metric-value">{agents.length.toString().padStart(2, '0')}</div><div className="metric-detail">{isArabic ? 'مسجلة في مساحة العمل' : 'In your workspace'}</div></article><article className="metric-card"><div className="metric-title"><span>{isArabic ? 'مراقبة ورقية' : 'Paper monitoring'}</span><span className="metric-icon metric-green"><Activity size={16}/></span></div><div className="metric-value">{activeAgents.toString().padStart(2, '0')}</div><div className="metric-detail">{isArabic ? 'استراتيجيات معتمدة' : 'Approved strategies'}</div></article><article className="metric-card"><div className="metric-title"><span>{isArabic ? 'بانتظار مراجعتك' : 'Awaiting review'}</span><span className="metric-icon metric-amber"><ShieldCheck size={16}/></span></div><div className="metric-value">{pendingAgents.toString().padStart(2, '0')}</div><div className="metric-detail">{isArabic ? 'تحتاج موافقة صريحة' : 'Need your explicit approval'}</div></article><article className="metric-card"><div className="metric-title"><span>{isArabic ? 'اتصالات Testnet' : 'Testnet connections'}</span><span className="metric-icon metric-violet"><Globe2 size={16}/></span></div><div className="metric-value">{connections.length.toString().padStart(2, '0')}</div><div className="metric-detail">{isArabic ? 'بيانات تجريبية فقط' : 'Sandbox credentials only'}</div></article></div>
      <div className="dashboard-grid"><section className="panel strategy-panel"><div className="panel-head"><div><div className="panel-title-row"><h2>{isArabic ? 'استراتيجياتك' : 'Your strategies'}</h2><span className="count-badge">{agents.length}</span></div><p>{isArabic ? 'الحالة والتخصيص والإصدار الحالي' : 'Current status, allocation and revision'}</p></div><button className="subtle-link" onClick={openCreate}>{isArabic ? 'استراتيجية جديدة' : 'New strategy'}<ArrowUpRight size={14}/></button></div>
        {agents.length === 0 ? <div className="empty-panel"><div className="empty-illustration"><Layers3 size={25}/><span><Plus size={12}/></span></div><h3>{isArabic ? 'ابدأ باستراتيجية واضحة' : 'Start with a clear strategy'}</h3><p>{isArabic ? 'أضف شروط دخول وخروج صريحة، ثم راجع التفسير وحدود المخاطرة قبل الحفظ.' : 'Add explicit entry and exit rules, then review the interpretation and risk limits before saving.'}</p><button className="secondary-btn" onClick={openCreate}><Plus size={15}/>{t.createAgent}</button></div> : <div className="table-wrap"><table><thead><tr><th>{isArabic ? 'الاستراتيجية' : 'STRATEGY'}</th><th>{isArabic ? 'السوق' : 'MARKET'}</th><th>{isArabic ? 'التخصيص الورقي' : 'PAPER ALLOCATION'}</th><th>{isArabic ? 'الإصدار' : 'REVISION'}</th><th>{isArabic ? 'الحالة' : 'STATUS'}</th><th/></tr></thead><tbody>{agents.map((agent, index) => <tr key={agent.id}><td><div className="agent-cell"><div className={`agent-icon agent-icon-${index % 3}`}><Bot size={17}/></div><div><b>{agent.name}</b><small>{agent.approval_status}</small></div></div></td><td><span className="symbol-tag">{agent.strategy.symbol}</span></td><td className="money">{agent.strategy.allocated_capital} <small>USDT</small></td><td><span className="revision-tag">v{agent.strategy_version}</span></td><td><span className={`status ${agent.state === 'PAPER_TRADING' ? '' : 'paused'}`}><i/>{agent.state === 'PAPER_TRADING' ? t.approved : agent.state === 'AWAITING_APPROVAL' ? t.awaiting : agent.state}</span></td><td>{agent.state === 'AWAITING_APPROVAL' && <button className="text-link" onClick={() => setReviewAgent(agent)}>{t.review}<ArrowUpRight size={13}/></button>}</td></tr>)}</tbody></table></div>}</section>
        <aside className="panel guardrails-panel"><div className="panel-head"><div><div className="panel-title-row"><h2>{isArabic ? 'ضوابط المنصة' : 'Platform guardrails'}</h2></div><p>{isArabic ? 'حماية مدمجة في كل خطوة' : 'Protection built into every step'}</p></div><span className="guardrail-shield"><ShieldCheck size={17}/></span></div><div className="guardrail-list"><div className="guardrail-item"><span className="guardrail-icon"><Check size={15}/></span><div><b>{isArabic ? 'موافقة بشرية صريحة' : 'Explicit human approval'}</b><small>{isArabic ? 'لا تبدأ المراقبة دون اعتمادك' : 'Monitoring waits for your approval'}</small></div></div><div className="guardrail-item"><span className="guardrail-icon"><Check size={15}/></span><div><b>{isArabic ? 'حدود مخاطر إلزامية' : 'Required risk limits'}</b><small>{isArabic ? 'تحدد رأس المال والمخاطرة بنفسك' : 'You set capital and risk limits'}</small></div></div><div className="guardrail-item"><span className="guardrail-icon"><Check size={15}/></span><div><b>{isArabic ? 'دفتر ورقي مزدوج القيد' : 'Double-entry paper ledger'}</b><small>{isArabic ? 'التنفيذ الحي معطل بنيوياً' : 'Live execution is structurally disabled'}</small></div></div></div><div className="guardrail-foot"><LockKeyhole size={14}/><span>{isArabic ? 'بيانات الاعتماد مشفرة قبل التخزين' : 'Credentials are encrypted at rest'}</span></div></aside></div>
      {error && <p className="inline-error page-message">{error}</p>}{notice && <div className="notice-toast"><CircleHelp size={17}/>{notice}<button onClick={() => setNotice('')} aria-label="Dismiss"><X size={15}/></button></div>}
      <footer><span>© 2026 TRADEGEN AI</span><span>{isArabic ? 'تداول ورقي · تداول حي معطل' : 'Paper trading · Live execution disabled'}</span></footer>
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
