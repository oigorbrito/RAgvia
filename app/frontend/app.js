const PUBLIC_CONFIG = window.RPY_PUBLIC_CONFIG || {},
  RAW_API_BASE = PUBLIC_CONFIG.apiBase,
  API_CONFIGURED = RAW_API_BASE === 'same-origin' || (typeof RAW_API_BASE === 'string' && RAW_API_BASE.trim().length > 0),
  API_BASE = RAW_API_BASE === 'same-origin' ? '' : typeof RAW_API_BASE === 'string' ? RAW_API_BASE.trim().replace(/\/+$/, '') : '';

function apiUrl(path) {
  if (!API_CONFIGURED) throw new Error('API_NOT_CONFIGURED');
  const base = new URL(API_BASE || window.location.origin, window.location.origin);
  const target = new URL(`${API_BASE}${path}`, base.origin);
  if (target.origin !== base.origin) throw new Error('API_URL_OUTSIDE_CONFIGURED_ORIGIN');
  return /^https?:\/\//i.test(API_BASE) ? target.href : `${API_BASE}${path}`;
}

function newDataJudIdempotencyKey() {
  if (typeof window.crypto?.randomUUID !== 'function') throw new Error('CRYPTO_UNAVAILABLE');
  return `datajud-${window.crypto.randomUUID()}`;
}

const form = document.querySelector('#search-form'),
  codeInput = document.querySelector('#process-code'),
  codeError = document.querySelector('#process-code-error'),
  tokenInput = document.querySelector('#bearer-token'),
  tokenError = document.querySelector('#token-error'),
  toggleToken = document.querySelector('#toggle-token'),
  accessTrigger = document.querySelector('#access-trigger'),
  accessLabel = document.querySelector('#access-label'),
  authPanel = document.querySelector('#auth-panel'),
  statusBox = document.querySelector('#status'),
  statusTitle = document.querySelector('#status-title'),
  statusDetail = document.querySelector('#status-detail'),
  retryButton = document.querySelector('#retry-search'),
  requestButton = document.querySelector('#request-process'),
  openExistingButton = document.querySelector('#open-existing-history'),
  requestRefreshButton = document.querySelector('#request-refresh'),
  confirmModal = document.querySelector('#confirm-modal'),
  confirmRefreshSubmit = document.querySelector('#confirm-refresh-submit'),
  confirmRefreshCancel = document.querySelector('#confirm-refresh-cancel'),
  historyTrigger = document.querySelector('#history-trigger'),
  historyPanel = document.querySelector('#history-panel'),
  historyList = document.querySelector('#history-list'),
  historyStatus = document.querySelector('#history-status'),
  refreshHistoryButton = document.querySelector('#refresh-history'),
  closeHistoryButton = document.querySelector('#close-history'),
  refreshFromResultButton = document.querySelector('#refresh-from-result'),
  result = document.querySelector('#result'),
  submit = document.querySelector('#search-submit'),
  copyButton = document.querySelector('#copy-summary'),
  summaryEyebrow = document.querySelector('#summary-eyebrow'),
  newSearchButton = document.querySelector('#new-search'),
  actionFeedback = document.querySelector('#action-feedback'),
  verifyTokenButton = document.querySelector('#verify-token'),
  authStatus = document.querySelector('#auth-status'),
  sourceJuditRadio = document.querySelector('#source-judit'),
  sourceDataJudRadio = document.querySelector('#source-datajud'),
  juditInfoButton = document.querySelector('#judit-info'),
  juditInfoWrap = document.querySelector('#judit-info-wrap'),
  juditTooltip = document.querySelector('#judit-tooltip'),
  datajudInfoButton = document.querySelector('#datajud-info'),
  datajudInfoWrap = document.querySelector('#datajud-info-wrap'),
  datajudTooltip = document.querySelector('#datajud-tooltip'),
  datajudStatusHint = document.querySelector('#datajud-status-hint'),
  datajudConfigureButton = document.querySelector('#datajud-configure'),
  apiConnectivity = document.querySelector('#api-connectivity'),
  apiConnectivityLabel = document.querySelector('#api-connectivity-label');

let currentSummary = '',
  pollTimer = null,
  pollAttempts = 0,
  pendingRequest = null,
  copyTimer = null,
  currentSearchId = 0,
  pendingConfirmationCode = null,
  currentSource = 'judit',
  currentAuthState = 'unconfigured',
  availableCapabilities = null,
  pendingDataJudRequest = null;

const MAX_POLL_ATTEMPTS=20,POLL_INTERVAL_MS=3000,MAX_SUMMARY_POLL_ATTEMPTS=20,SUMMARY_POLL_INTERVAL_MS=3000;

function clear(n) {
  while (n && n.firstChild) n.removeChild(n.firstChild);
}

function stopPolling(){
  if (pollTimer) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
  pollAttempts = 0;
}

function clearPendingRequest() {
  pendingRequest = null;
  if (requestButton) requestButton.hidden = true;
  if (openExistingButton) openExistingButton.hidden = true;
  if (requestRefreshButton) requestRefreshButton.hidden = true;
}

function setFieldError(i, n, m = '') {
  n.textContent = m;
  n.hidden = !m;
  i.setAttribute('aria-invalid', m ? 'true' : 'false');
}

function setStatus(t = '', d = '', { error: e = false, retry: r = false, request: q = false, openHistory: o = false, refresh: f = false } = {}) {
  statusTitle.textContent = t;
  statusDetail.textContent = d;
  statusBox.classList.toggle('error', e);
  retryButton.hidden = !r;
  requestButton.hidden = !q;
  if (openExistingButton) openExistingButton.hidden = !o;
  if (requestRefreshButton) requestRefreshButton.hidden = !f;
  statusBox.hidden = !t;
}

function announce(message) {
  actionFeedback.textContent = '';
  requestAnimationFrame(() => {
    actionFeedback.textContent = message;
  });
}

function setLoading(v) {
  submit.disabled = v;
  submit.textContent = v ? 'Consultando…' : 'Consultar processo';
  result.setAttribute('aria-busy', v ? 'true' : 'false');
}

function updateAccessState(targetState, message) {
  const token = tokenInput.value.trim();
  if (!token) {
    currentAuthState = 'unconfigured';
    accessTrigger.classList.remove('verifying', 'authenticated', 'invalid', 'configured');
    accessLabel.textContent = 'Configurar acesso';
    if (authStatus) authStatus.textContent = message || '';
    return;
  }
  if (targetState) {
    currentAuthState = targetState;
  }
  accessTrigger.classList.remove('verifying', 'authenticated', 'invalid', 'configured');
  if (currentAuthState === 'authenticated') {
    accessTrigger.classList.add('authenticated', 'configured');
    accessLabel.textContent = 'Acesso autenticado';
    if (authStatus) authStatus.textContent = message || 'Credencial autenticada na API RagV.';
  } else if (currentAuthState === 'verifying') {
    accessTrigger.classList.add('verifying');
    accessLabel.textContent = 'Verificando…';
    if (authStatus) authStatus.textContent = message || 'Verificando credencial junto à API RagV…';
  } else if (currentAuthState === 'invalid') {
    accessTrigger.classList.add('invalid');
    accessLabel.textContent = 'Credencial recusada';
    if (authStatus) authStatus.textContent = message || 'A credencial informada foi recusada pela API RagV.';
  } else {
    accessLabel.textContent = 'Configurar acesso';
    if (authStatus) authStatus.textContent = message || 'Credencial informada, clique em Verificar para validar.';
  }
}

async function verifyAuthentication(token) {
  const supplied = token !== undefined ? token : tokenInput.value.trim();
  if (!supplied) {
    setFieldError(tokenInput, tokenError, 'Informe a credencial de acesso antes de verificar.');
    updateAccessState('unconfigured', 'Informe a credencial de acesso.');
    tokenInput.focus();
    return false;
  }
  setFieldError(tokenInput, tokenError);
  updateAccessState('verifying', 'Verificando credencial junto à API RagV…');
  if (verifyTokenButton) verifyTokenButton.disabled = true;
  try {
    const response = await fetch(apiUrl('/auth/verify'), {
      headers: authHeaders(supplied),
    });
    if (response.ok) {
      updateAccessState('authenticated', 'Credencial autenticada com sucesso no ambiente.');
      return true;
    }
    if (response.status === 401) {
      setFieldError(tokenInput, tokenError, 'A credencial foi recusada. Confira o token e tente novamente.');
      updateAccessState('invalid', 'Credencial recusada pela API RagV (HTTP 401).');
      return false;
    }
    updateAccessState('invalid', 'Servidor retornou erro ao validar a credencial.');
    return false;
  } catch (err) {
    if (err instanceof Error && err.message === 'API_NOT_CONFIGURED') {
      updateAccessState('invalid', 'API de staging ainda não configurada.');
    } else {
      updateAccessState('invalid', 'Sem conexão com a API RagV para validação.');
    }
    return false;
  } finally {
    if (verifyTokenButton) verifyTokenButton.disabled = false;
  }
}

async function openDataJudConfigurator() {
  if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.open_datajud_configurator) {
    if (datajudStatusHint) datajudStatusHint.textContent = 'Abra o configurador DataJud pelo atalho da homologação.';
    return;
  }
  const result = await window.pywebview.api.open_datajud_configurator();
  if (!result || !result.ok) {
    if (datajudStatusHint) datajudStatusHint.textContent = result?.message || 'Não foi possível abrir o configurador DataJud.';
    return;
  }
  if (datajudStatusHint) datajudStatusHint.textContent = 'Conclua a autorização no configurador; depois atualizaremos o estado.';
  window.setTimeout(fetchCapabilities, 3000);
}

async function fetchCapabilities() {
  try {
    const response = await fetch(apiUrl('/capabilities'));
    if (!response.ok) {
      if (apiConnectivity) apiConnectivity.classList.add('offline');
      if (apiConnectivityLabel) apiConnectivityLabel.textContent = 'API RagV indisponível';
      return;
    }
    if (apiConnectivity) apiConnectivity.classList.remove('offline');
    if (apiConnectivityLabel) apiConnectivityLabel.textContent = 'API RagV conectada';
    const data = await response.json();
    availableCapabilities = data;
    const datajud = data.sources && data.sources.datajud;
    if (datajud && sourceDataJudRadio) {
      if (datajud.enabled) {
        sourceDataJudRadio.disabled = false;
        if (datajudConfigureButton) datajudConfigureButton.hidden = true;
        if (datajudStatusHint) {
          datajudStatusHint.textContent = '';
          datajudStatusHint.className = 'source-hint';
        }
      } else {
        sourceDataJudRadio.disabled = true;
        if (datajudConfigureButton) datajudConfigureButton.hidden = false;
        if (datajudStatusHint) {
          datajudStatusHint.textContent = datajud.reason_unavailable || 'DataJud indisponível no servidor.';
          datajudStatusHint.className = 'source-warning';
        }
      }
    }
  } catch {
    if (sourceDataJudRadio) sourceDataJudRadio.disabled = true;
    if (apiConnectivity) apiConnectivity.classList.add('offline');
    if (apiConnectivityLabel) apiConnectivityLabel.textContent = 'API RagV indisponível';
  }
}

function relativeElapsed(value) {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return '';
  const elapsedSeconds = Math.max(0, (Date.now() - timestamp) / 1000);
  const units = [
    ['year', 31536000], ['month', 2592000], ['day', 86400],
    ['hour', 3600], ['minute', 60],
  ];
  const [unit, seconds] = units.find(([, size]) => elapsedSeconds >= size) || ['second', 1];
  const amount = unit === 'second' ? 0 : Math.floor(elapsedSeconds / seconds);
  return new Intl.RelativeTimeFormat('pt-BR', { numeric: 'auto' }).format(-amount, unit);
}

function renderMarkdown(md, target) {
  clear(target);
  let container = target,
    list = null;
  for (const raw of md.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) {
      list = null;
      continue;
    }
    const headerOpen = line.match(/^<ProcessHeader\s+className=(["'])process-header\1\s*>$/);
    if (headerOpen) {
      list = null;
      const section = document.createElement('section');
      section.className = 'summary-process-header';
      section.setAttribute('data-component','ProcessHeader');
      target.appendChild(section);
      container = section;
      continue;
    }
    if (/^<\/ProcessHeader>$/.test(line)) {
      list = null;
      container = target;
      continue;
    }
    const party = line.match(/^<Party\s+name=(["'])([^"'<>]+)\1\s*\/>$/);
    if (party) {
      list = null;
      const item = document.createElement('div');
      item.className = 'summary-party';
      item.setAttribute('data-component','Party');
      item.textContent = party[2];
      container.appendChild(item);
      continue;
    }
    const h = line.match(/^(#{1,3})\s+(.+)$/);
    if (h) {
      list = null;
      const el = document.createElement(`h${Math.min(h[1].length + 1, 4)}`);
      el.textContent = h[2];
      container.appendChild(el);
      continue;
    }
    const b = line.match(/^[-*]\s+(.+)$/);
    if (b) {
      if (!list) {
        list = document.createElement('ul');
        container.appendChild(list);
      }
      const item = document.createElement('li');
      item.textContent = b[1];
      list.appendChild(item);
      continue;
    }
    list = null;
    const p = document.createElement('p');
    p.textContent = line;
    container.appendChild(p);
  }
}

function summaryPlainText(md) {
  const lines = [];
  for (const raw of md.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) {
      lines.push('');
      continue;
    }
    if (/^<ProcessHeader\s+className=(["'])process-header\1\s*>$/.test(line) || /^<\/ProcessHeader>$/.test(line)) continue;
    const party = line.match(/^<Party\s+name=(["'])([^"'<>]+)\1\s*\/>$/);
    if (party) {
      lines.push(party[2]);
      continue;
    }
    const h = line.match(/^(#{1,3})\s+(.+)$/);
    if (h) {
      lines.push(h[2]);
      continue;
    }
    const b = line.match(/^[-*]\s+(.+)$/);
    if (b) {
      lines.push(`- ${b[1]}`);
      continue;
    }
    lines.push(line);
  }
  return lines.join('\n').replace(/\n{3,}/g, '\n\n').trim();
}

function formatDate(v, { time = true } = {}) {
  if (!v) return '';
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return '';
  return new Intl.DateTimeFormat('pt-BR', time ? { dateStyle: 'medium', timeStyle: 'short' } : { dateStyle: 'medium' }).format(d);
}

function formatCnj(v) {
  const digits = String(v || '').replace(/\D/g, '').slice(0, 20);
  if (digits.length !== 20) return String(v || '—');
  return `${digits.slice(0, 7)}-${digits.slice(7, 9)}.${digits.slice(9, 13)}.${digits.slice(13, 14)}.${digits.slice(14, 16)}.${digits.slice(16)}`;
}

function formatCnjInput(value) {
  const d = String(value || '').replace(/\D/g, '').slice(0, 20);
  let out = d.slice(0, 7);
  if (d.length > 7) out += `-${d.slice(7, 9)}`;
  if (d.length > 9) out += `.${d.slice(9, 13)}`;
  if (d.length > 13) out += `.${d.slice(13, 14)}`;
  if (d.length > 14) out += `.${d.slice(14, 16)}`;
  if (d.length > 16) out += `.${d.slice(16, 20)}`;
  return out;
}

function formatAmount(v) {
  if (v === null || v === undefined || v === '') return '';
  let value = v;
  if (typeof v === 'string') {
    const normalized = v.trim().replace(/^R\$\s*/i, '').replace(/\s/g, '');
    if (/^\d{1,3}(\.\d{3})*,\d+$/.test(normalized)) value = normalized.replace(/\./g, '').replace(',', '.');
    else if (/^\d+,\d+$/.test(normalized)) value = normalized.replace(',', '.');
    else value = normalized;
  }
  const number = Number(value);
  return Number.isFinite(number) ? new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'}).format(number) : String(v);
}

function humanizePartySide(v) {
  const key = String(v || '').trim().toLowerCase().replace(/[\s-]+/g, '_');
  return { active: 'Polo ativo', plaintiff: 'Polo ativo', claimant: 'Polo ativo', author: 'Polo ativo', ativo: 'Polo ativo', passive: 'Polo passivo', defendant: 'Polo passivo', respondent: 'Polo passivo', passivo: 'Polo passivo', other: 'Outro', others: 'Outro' }[key] || String(v || '');
}

function humanizePersonType(v) {
  const key = String(v || '').trim().toLowerCase().replace(/[\s-]+/g, '_');
  return { person: 'Pessoa física', individual: 'Pessoa física', natural_person: 'Pessoa física', physical_person: 'Pessoa física', pessoa_fisica: 'Pessoa física', company: 'Pessoa jurídica', legal_person: 'Pessoa jurídica', juridical_person: 'Pessoa jurídica', organization: 'Pessoa jurídica', pessoa_juridica: 'Pessoa jurídica' }[key] || String(v || '');
}

function appendEmpty(target, text) {
  clear(target);
  const p = document.createElement('p');
  p.className = 'detail-empty';
  p.textContent = text;
  target.appendChild(p);
}

function renderParties(parties) {
  const target = document.querySelector('#parties-list');
  clear(target);
  if (!Array.isArray(parties) || !parties.length) {
    appendEmpty(target, 'Partes não informadas pela fonte.');
    return;
  }
  for (const party of parties) {
    const row = document.createElement('div');
    row.className = 'structured-item';
    const name = document.createElement('strong');
    name.textContent = party?.name || 'Nome não informado';
    const meta = document.createElement('span'),
      side = humanizePartySide(party?.side),
      type = humanizePersonType(party?.person_type);
    meta.textContent = [side, type].filter(Boolean).join(' · ') || 'Qualificação não informada';
    row.append(name, meta);
    target.appendChild(row);
  }
}

function renderSubjects(subjects) {
  const target = document.querySelector('#subjects-list');
  clear(target);
  if (!Array.isArray(subjects) || !subjects.length) {
    appendEmpty(target, 'Assuntos não informados pela fonte.');
    return;
  }
  for (const subject of subjects) {
    const row = document.createElement('div');
    row.className = 'structured-item';
    const name = document.createElement('strong');
    name.textContent = subject?.name || String(subject || 'Assunto não informado');
    row.appendChild(name);
    if (subject?.code) {
      const meta = document.createElement('span');
      meta.textContent = `Código ${subject.code}`;
      row.appendChild(meta);
    }
    target.appendChild(row);
  }
}

function renderHeader(header) {
  const target = document.querySelector('#header-data');
  clear(target);
  const labels = { name: 'Nome', instance: 'Instância', area: 'Área', justice_description: 'Justiça', county: 'Comarca', state: 'Estado', city: 'Cidade', amount: 'Valor da causa' },
    formatters = { amount: formatAmount };
  const entries = Object.entries(header || {}).filter(([k, v]) => labels[k] && v !== null && v !== '');
  if (!entries.length) {
    const div = document.createElement('div');
    div.className = 'detail-empty';
    div.textContent = 'Informações complementares não disponíveis.';
    target.appendChild(div);
    return;
  }
  for (const [key, value] of entries) {
    const wrap = document.createElement('div'),
      dt = document.createElement('dt'),
      dd = document.createElement('dd');
    dt.textContent = labels[key];
    dd.textContent = formatters[key] ? formatters[key](value) : key === 'instance' && /^\d+$/.test(String(value)) ? `${value}ª instância` : String(value);
    wrap.append(dt, dd);
    target.appendChild(wrap);
  }
}

function renderSteps(steps) {
  const target = document.querySelector('#movements-list');
  clear(target);
  if (!Array.isArray(steps) || !steps.length) {
    const li = document.createElement('li');
    li.className = 'detail-empty';
    li.textContent = 'Nenhuma movimentação disponível para a versão atual.';
    target.appendChild(li);
    return;
  }
  for (const step of steps) {
    const li = document.createElement('li');
    li.className = 'timeline-item';
    const date = document.createElement('time');
    date.textContent = formatDate(step.occurred_at,{time:false}) || 'Data não informada';
    if (step.occurred_at) date.dateTime = step.occurred_at;
    const content = document.createElement('div'),
      title = document.createElement('h4'),
      text = document.createElement('p');
    title.textContent = step.title || 'Movimentação';
    text.textContent = step.text || 'Sem descrição adicional.';
    content.append(title, text);
    li.append(date, content);
    target.appendChild(li);
  }
}

function summaryEmptyMessage(status) {
  if (status === 'processing') return 'O resumo desta versão está sendo preparado. Esta página verificará automaticamente quando ele for publicado.';
  if (status === 'unavailable') return 'Não foi possível publicar um resumo para esta versão. Os dados processuais e as movimentações disponíveis continuam abaixo.';
  return 'Esta versão do processo está disponível sem resumo publicado. Os dados processuais e as movimentações disponíveis continuam abaixo.';
}

function renderProcess(data) {
  document.querySelector('#result-code').textContent = formatCnj(data.code);
  document.querySelector('#result-class').textContent = data.class_name || 'Não informado';
  document.querySelector('#result-court').textContent = data.court || 'Não informado';
  document.querySelector('#result-updated').textContent = formatDate(data.updated_at) || 'Não informada';
  document.querySelector('#result-context').textContent = 'Versão mais recente disponível para esta credencial.';
  renderParties(data.parties);
  renderSubjects(data.subjects);
  renderHeader(data.header);
  renderSteps(data.recent_steps);

  const body = document.querySelector('#summary-body'),
    provenance = document.querySelector('#summary-provenance');
  // The legacy process endpoint returns `summary.markdown`; summary-job and
  // public v1 responses expose the same JSX document as `iaSummary`.
  // Accept only the rendered string here (the JSON format is not Markdown).
  currentSummary = typeof data.summary?.markdown === 'string'
    ? data.summary.markdown
    : typeof data.iaSummary === 'string' ? data.iaSummary : '';
  copyButton.hidden = !currentSummary;
  summaryEyebrow.hidden = !currentSummary;
  const demoSummary = data.summary?.model === 'local-demo-no-provider';
  summaryEyebrow.textContent = demoSummary ? 'SÍNTESE DE DEMONSTRAÇÃO' : 'SÍNTESE GERADA POR IA';

  if (currentSummary) {
    renderMarkdown(currentSummary, body);
    const created = formatDate(data.summary.created_at);
    provenance.textContent = demoSummary ? (created ? `Resumo sintético local publicado em ${created}. Nenhum provedor externo foi utilizado.` : 'Resumo sintético local. Nenhum provedor externo foi utilizado.') : (created ? `Resumo gerado por IA e publicado em ${created}.` : 'Resumo gerado por IA para a versão atual.');
  } else {
    clear(body);
    const p = document.createElement('p');
    p.className = 'summary-empty';
    p.textContent = summaryEmptyMessage(data.summary_status);
    body.appendChild(p);
    provenance.textContent = data.summary_status === 'processing' ? 'Status: preparação em andamento.' : data.summary_status === 'unavailable' ? 'Status: resumo indisponível.' : 'Status: sem resumo publicado.';
  }
  result.hidden = false;
}

function showProcess(data, { scroll = true, followSummary = true } = {}) {
  stopPolling();
  clearPendingRequest();
  renderProcess(data);
  setStatus();
  if (scroll) {
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    document.querySelector('#result-code').focus({preventScroll:true});
    result.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'start' });
  }
  if (followSummary && data.summary_status==='processing' && !data.summary) {
    pollAttempts = 0;
    setStatus('Preparando resumo', 'Os dados processuais já estão disponíveis. O resumo será atualizado automaticamente quando a geração terminar.');
    scheduleSummaryCheck(codeInput.value.trim(), tokenInput.value.trim());
  }
}

function showDataJudProcess(data) {
  stopPolling();
  clearPendingRequest();
  document.querySelector('#result-code').textContent = formatCnj(data.code);
  document.querySelector('#result-class').textContent = data.class_name || 'Não informado';
  document.querySelector('#result-court').textContent = data.court || 'Não informado';
  document.querySelector('#result-updated').textContent = 'Consulta direta DataJud/CNJ';
  document.querySelector('#result-context').textContent = 'Consulta textual de metadados públicos via DataJud (experimental).';
  renderParties(data.parties || []);
  renderSubjects(data.subjects || []);
  renderHeader({ 'Órgão julgador': data.adjudicating_body || 'Não informado', 'Comarca/Foro': data.county || 'Não informada' });
  renderSteps([]);

  const body = document.querySelector('#summary-body'),
    provenance = document.querySelector('#summary-provenance');
  currentSummary = '';
  copyButton.hidden = true;
  summaryEyebrow.hidden = false;
  summaryEyebrow.textContent = 'METADADOS PÚBLICOS DATAJUD';
  clear(body);
  const p = document.createElement('p');
  p.className = 'summary-empty';
  p.textContent = 'DataJud é uma integração experimental restrita a metadados cadastrais do CNJ. Esta fonte não inclui peças integrais dos autos nem síntese processual automatizada.';
  body.appendChild(p);
  provenance.textContent = 'Origem: Base pública DataJud/CNJ. Não substitui a consulta completa via Judit.';
  result.hidden = false;
  setStatus();
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  document.querySelector('#result-code').focus({preventScroll:true});
  result.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'start' });
}

function validCnj(v) {
  return /^\d{20}$/.test(v) || /^\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}$/.test(v);
}

function authHeaders(token) {
  return { Authorization: `Bearer ${token}`, Accept: 'application/json' };
}

async function fetchProcess(code, token) {
  if (!validCnj(code)) throw new Error('INVALID_CODE');
  return fetch(apiUrl(`/processes/${encodeURIComponent(code)}`), { headers: authHeaders(token) });
}

async function fetchProcessStatus(code, token, operation = null) {
  if (!validCnj(code)) throw new Error('INVALID_CODE');
  const query = operation ? `?operation=${encodeURIComponent(operation)}` : '';
  return fetch(apiUrl(`/processes/${encodeURIComponent(code)}/status${query}`), { headers: authHeaders(token) });
}

function scheduleAvailabilityCheck(code, token) {
  pollTimer = setTimeout(() => checkRequestedProcess(code, token), POLL_INTERVAL_MS);
}

function scheduleSummaryCheck(code, token) {
  pollTimer = setTimeout(() => checkSummary(code, token), SUMMARY_POLL_INTERVAL_MS);
}

async function checkSummary(code, token) {
  pollTimer = null;
  pollAttempts += 1;
  try {
    const response = await fetchProcess(code, token);
    if (response.status === 401) {
      stopPolling();
      setStatus('A credencial deixou de ser aceita', 'Configure novamente o acesso para continuar.', { error: true });
      return;
    }
    if (!response.ok) {
      stopPolling();
      setStatus('Não foi possível acompanhar o resumo', 'Os dados processuais permanecem disponíveis. Consulte novamente para verificar o resumo.', { error: true, retry: true });
      return;
    }
    const data = await response.json();
    renderProcess(data);
    if (data.summary || data.summary_status!=='processing') {
      stopPolling();
      setStatus(data.summary ? 'Resumo publicado' : 'Preparação do resumo encerrada', data.summary ? 'O resumo desta versão foi atualizado automaticamente.' : 'O estado do resumo mudou. A página foi atualizada.');
      return;
    }
  } catch {
    if (pollAttempts >= MAX_SUMMARY_POLL_ATTEMPTS) {
      stopPolling();
      setStatus('O resumo continua em preparação', 'Não foi possível confirmar a publicação automaticamente. Os dados processuais permanecem disponíveis.', {retry:true});
      return;
    }
  }
  if (pollAttempts >= MAX_SUMMARY_POLL_ATTEMPTS) {
    stopPolling();
    setStatus('O resumo continua em preparação', 'A geração ainda não terminou. Você pode sair desta página e consultar o mesmo CNJ mais tarde.', {retry:true});
    return;
  }
  setStatus('Preparando resumo', `Os dados processuais já estão disponíveis. Verificando a publicação do resumo (${pollAttempts}/${MAX_SUMMARY_POLL_ATTEMPTS}).`);
  scheduleSummaryCheck(code, token);
}

async function checkRequestedProcess(code, token) {
  pollTimer = null;
  pollAttempts += 1;
  try {
    const response = await fetchProcess(code, token);
    if (response.ok) {
      showProcess(await response.json());
      return;
    }
    if (response.status === 401) {
      stopPolling();
      setStatus('A credencial deixou de ser aceita', 'Configure novamente o acesso para continuar.', { error: true });
      return;
    }
    if (response.status !== 404) {
      stopPolling();
      setStatus('Não foi possível verificar a consulta', 'A solicitação permanece registrada. Consulte novamente mais tarde.', { error: true, retry: true });
      return;
    }
  } catch {
    if (pollAttempts >= MAX_POLL_ATTEMPTS) {
      stopPolling();
      setStatus('Consulta registrada', 'A atualização automática foi interrompida. A solicitação continua registrada; você pode sair e consultar este CNJ novamente mais tarde.', { retry: true });
      return;
    }
  }
  if (pollAttempts >= MAX_POLL_ATTEMPTS) {
    stopPolling();
    setStatus('Consulta registrada', 'Os dados ainda não chegaram. A solicitação continua registrada e não é necessário manter esta página aberta.', { retry: true });
    return;
  }
  setStatus('Aguardando dados processuais', `A consulta foi registrada. Verificando disponibilidade (${pollAttempts}/${MAX_POLL_ATTEMPTS}). Você pode sair e consultar novamente mais tarde.`);
  scheduleAvailabilityCheck(code, token);
}

async function requestUnavailableProcess(code, token) {
  if (!validCnj(code)) throw new Error('INVALID_CODE');
  requestButton.disabled = true;
  requestButton.textContent = 'Solicitando…';
  setStatus('Registrando consulta', 'Enviando a solicitação à fonte processual.');
  try {
    const response = await fetch(apiUrl(`/processes/${encodeURIComponent(code)}/request`), {
      method:'POST',
      headers: authHeaders(token),
    });
    if (response.status === 401) throw new Error('AUTH');
    if (response.status === 400) throw new Error('INVALID_CODE');
    if (response.status === 503) throw new Error('PROVIDER');
    if (!response.ok) throw new Error('SERVER');
    const data = await response.json();
    clearPendingRequest();
    if (data.status === 'available') {
      const available = await fetchProcess(code, token);
      if (available.ok) {
        showProcess(await available.json());
        return;
      }
    }
    pollAttempts = 0;
    setStatus(
      'Consulta registrada',
      data.created
        ? 'A solicitação foi aceita. Você pode manter esta página aberta ou voltar ao mesmo CNJ mais tarde.'
        : 'Este CNJ já possui uma solicitação registrada. Você pode manter esta página aberta ou consultar novamente mais tarde.'
    );
    scheduleAvailabilityCheck(code, token);
  } finally {
    requestButton.disabled = false;
    requestButton.textContent = 'Solicitar consulta';
  }
}

function showConfirmation(code) {
  pendingConfirmationCode = code;
  if (confirmModal) confirmModal.hidden = false;
  if (confirmRefreshSubmit) {
    confirmRefreshSubmit.disabled = false;
    confirmRefreshSubmit.textContent = 'Confirmar e consultar';
    confirmRefreshSubmit.focus();
  }
}

function hideConfirmation() {
  pendingConfirmationCode = null;
  if (confirmModal) confirmModal.hidden = true;
}

async function loadHistoryList() {
  const token = tokenInput.value.trim();
  if (!token) {
    if (historyStatus) historyStatus.textContent = 'Informe a credencial de acesso para visualizar o histórico.';
    return;
  }
  if (historyStatus) historyStatus.textContent = 'Carregando histórico…';
  if (historyList) {
    clear(historyList);
    historyList.setAttribute('aria-busy', 'true');
  }
  try {
    const response = await fetch(apiUrl('/processes?limit=50'), { headers: authHeaders(token) });
    if (response.status === 401) {
      if (historyStatus) historyStatus.textContent = 'Credencial recusada. Configure o acesso.';
      return;
    }
    if (!response.ok) {
      if (historyStatus) historyStatus.textContent = 'Não foi possível carregar o histórico.';
      return;
    }
    const data = await response.json();
    const items = data.items || [];
    const datajudResponse = await fetch(apiUrl('/processes/datajud/history'), { headers: authHeaders(token) }).catch(() => null);
    if (datajudResponse && datajudResponse.ok) {
      const datajudData = await datajudResponse.json();
      items.push(...(datajudData.items || []).map(item => ({
        ...item, source_datajud: true,
        acquisition_status: ['pending', 'processing'].includes(item.status) ? 'processing' : 'completed',
      })));
    }
    if (historyStatus) {
      historyStatus.textContent = items.length
        ? `${items.length} processo(s) consultado(s) pelo tenant.`
        : 'Nenhum processo consultado ainda.';
    }
    if (historyList) {
      clear(historyList);
      for (const item of items) {
        const row = document.createElement('div');
        row.className = 'history-item';
        row.setAttribute('role', 'button');
        row.setAttribute('tabindex', '0');

        const mainInfo = document.createElement('div');
        mainInfo.className = 'history-item-main';
        const codeStrong = document.createElement('strong');
        codeStrong.textContent = formatCnj(item.code);
        const metaSpan = document.createElement('span');
        const metaParts = [];
        if (item.court && item.court !== 'Não informado') metaParts.push(item.court);
        if (item.class_name && item.class_name !== 'Não informada') metaParts.push(item.class_name);
        if (item.updated_at) {
          metaParts.push('Atualizado: ' + formatDate(item.updated_at, { time: false }));
          const elapsed = relativeElapsed(item.updated_at);
          if (elapsed) metaParts.push('Consultado ' + elapsed);
        }
        metaSpan.textContent = metaParts.join(' · ') || 'Sem metadados adicionais';
        mainInfo.append(codeStrong, metaSpan);

        const badge = document.createElement('span');
        const isProcessing = item.acquisition_status === 'processing';
        badge.className = 'history-badge ' + (isProcessing ? 'processing' : 'completed');
        badge.textContent = item.source_datajud
          ? (isProcessing ? 'DataJud · em andamento' : item.status === 'dead' ? 'DataJud · falha' : item.outcome === 'not_found' ? 'DataJud · sem registro' : 'DataJud')
          : (isProcessing ? 'Em andamento' : 'Armazenado');

        row.append(mainInfo, badge);

        const selectProcess = async () => {
          codeInput.value = item.code;
          if (historyPanel) {
            historyPanel.hidden = true;
            if (historyTrigger) historyTrigger.setAttribute('aria-expanded', 'false');
          }
          if (item.source_datajud) {
            if (isProcessing) {
              pendingDataJudRequest = { code: item.code, token, jobId: item.job_id };
              const searchId = ++currentSearchId;
              await pollDataJudJob(item.code, token, searchId);
            } else if (item.metadata) {
              showDataJudProcess({ code: item.code, ...item.metadata });
            } else if (item.outcome === 'not_found') {
              setStatus('Processo não localizado no DataJud', 'A consulta foi concluída sem registros na base consultada.');
            } else {
              setStatus('Consulta DataJud indisponível', 'A solicitação não concluiu com metadados. Tente novamente mais tarde.', { error: true, retry: true });
            }
          } else if (isProcessing) {
            setStatus('Solicitação em andamento', 'A consulta para este processo já está sendo processada pela fonte. Aguarde a conclusão.');
            scheduleAvailabilityCheck(item.code, token);
          } else {
            setLoading(true);
            setStatus('Carregando processo', 'Buscando os dados armazenados no histórico.');
            try {
              const resp = await fetchProcess(item.code, token);
              if (resp.ok) showProcess(await resp.json());
              else setStatus('Não foi possível carregar o processo', 'Tente novamente.', { error: true, retry: true });
            } catch {
              setStatus('Não foi possível carregar o processo', 'Tente novamente.', { error: true, retry: true });
            } finally {
              setLoading(false);
            }
          }
        };

        row.addEventListener('click', selectProcess);
        row.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            selectProcess();
          }
        });

        historyList.appendChild(row);
      }
    }
  } catch {
    if (historyStatus) historyStatus.textContent = 'Erro ao carregar o histórico.';
  } finally {
    if (historyList) historyList.setAttribute('aria-busy', 'false');
  }
}

async function pollDataJudJob(code, token, searchId, attempt = 0) {
  if (searchId !== currentSearchId) return;
  const pending = pendingDataJudRequest;
  if (!pending || pending.code !== code || pending.token !== token || !pending.jobId) return;
  setLoading(true);
  try {
    const response = await fetch(apiUrl('/processes/datajud/jobs/status'), {
      method: 'POST',
      headers: { ...authHeaders(token), 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, job_id: pending.jobId }),
    });
    if (searchId !== currentSearchId) return;
    if (response.status === 401) {
      updateAccessState('invalid');
      setFieldError(tokenInput, tokenError, 'A credencial foi recusada. Confira o token e tente novamente.');
      pendingDataJudRequest = null;
      setStatus('Não foi possível autorizar a consulta', 'Confira a credencial do ambiente.', { error: true });
      return;
    }
    if (response.status === 202) {
      if (attempt + 1 >= MAX_POLL_ATTEMPTS) {
        setStatus('Consulta DataJud em andamento', 'A solicitação continua na fila. Tente novamente para verificar o mesmo pedido.', { retry: true });
        return;
      }
      setStatus('Consultando DataJud', 'A solicitação está sendo processada pelo worker.', { retry: true });
      pollTimer = setTimeout(() => pollDataJudJob(code, token, searchId, attempt + 1), POLL_INTERVAL_MS);
      return;
    }
    if (response.status === 404) {
      pendingDataJudRequest = null;
      setStatus('Processo não localizado no DataJud', 'Não foram encontrados registros para este CNJ na base do DataJud.', { error: true });
      return;
    }
    if (response.status === 403) {
      pendingDataJudRequest = null;
      setStatus('DataJud não autorizado', 'A consulta requer autorização explícita no servidor.', { error: true });
      return;
    }
    if (!response.ok) {
      pendingDataJudRequest = null;
      setStatus('Não foi possível consultar o DataJud', 'Falha no processamento da consulta. Tente novamente.', { error: true, retry: true });
      return;
    }
    const data = await response.json();
    if (searchId !== currentSearchId) return;
    pendingDataJudRequest = null;
    updateAccessState('authenticated');
    showDataJudProcess(data);
  } catch {
    if (searchId === currentSearchId) {
      setStatus('Não foi possível consultar o DataJud', 'Falha na comunicação com a API. Tente novamente para verificar o mesmo pedido.', { error: true, retry: true });
    }
  } finally {
    if (searchId === currentSearchId) setLoading(false);
  }
}

async function searchProcess() {
  stopPolling();
  clearPendingRequest();
  result.hidden=true;
  setFieldError(codeInput, codeError);
  setFieldError(tokenInput, tokenError);
  if (historyPanel) {
    historyPanel.hidden = true;
    if (historyTrigger) historyTrigger.setAttribute('aria-expanded', 'false');
  }

  const code = codeInput.value.trim(),
    token = tokenInput.value.trim();

  if (!validCnj(code)) {
    setFieldError(codeInput, codeError, 'Informe um número CNJ válido, formatado ou com 20 dígitos.');
    codeInput.focus();
    return;
  }
  if (!token) {
    setFieldError(tokenInput, tokenError, 'Informe a credencial de acesso antes de consultar.');
    authPanel.hidden = false;
    accessTrigger.setAttribute('aria-expanded', 'true');
    tokenInput.focus();
    return;
  }

  if (currentSource === 'datajud') {
    if (sourceDataJudRadio && sourceDataJudRadio.disabled) {
      setStatus(
        'DataJud indisponível',
        'A consulta não foi enviada. O servidor não autorizou o uso do DataJud; a fonte não será trocada automaticamente.',
        { error: true }
      );
      return;
    }
    const searchId = ++currentSearchId;
    if (!pendingDataJudRequest || pendingDataJudRequest.code !== code || pendingDataJudRequest.token !== token) {
      pendingDataJudRequest = { code, token, idempotencyKey: newDataJudIdempotencyKey(), jobId: null };
    }
    setLoading(true);
    setStatus('Consultando DataJud', 'Enviando a solicitação para processamento pelo worker.');
    try {
      const djResp = await fetch(apiUrl('/processes/datajud/jobs'), {
        method: 'POST',
        headers: { ...authHeaders(token), 'Idempotency-Key': pendingDataJudRequest.idempotencyKey, 'Content-Type': 'application/json' },
        body: JSON.stringify({ code }),
      });
      if (searchId !== currentSearchId) return;
      if (djResp.status === 401) {
        updateAccessState('invalid');
        setFieldError(tokenInput, tokenError, 'A credencial foi recusada. Confira o token e tente novamente.');
        pendingDataJudRequest = null;
        setStatus('Não foi possível autorizar a consulta', 'Confira a credencial do ambiente.', { error: true });
        return;
      }
      if (djResp.status === 403) {
        setStatus('DataJud não autorizado', 'A consulta requer autorização explícita no servidor.', { error: true });
        return;
      }
      if (djResp.status === 409) {
        pendingDataJudRequest = null;
        setStatus('Solicitação DataJud conflitante', 'A chave de idempotência foi associada a outro processo. Tente novamente.', { error: true, retry: true });
        return;
      }
      if (!djResp.ok) throw new Error('SERVER');
      const accepted = await djResp.json();
      if (searchId !== currentSearchId) return;
      pendingDataJudRequest.jobId = accepted.job_id;
      setStatus('Consultando DataJud', 'A solicitação está na fila PostgreSQL para processamento pelo worker.');
      await pollDataJudJob(code, token, searchId);
      return;
    } catch {
      if (searchId === currentSearchId) {
        setStatus('Não foi possível consultar o DataJud', 'Falha na comunicação com a API. Tente novamente para verificar o mesmo pedido.', { error: true, retry: true });
      }
      return;
    } finally {
      if (searchId === currentSearchId) setLoading(false);
    }
  }

  const searchId = ++currentSearchId;
  setLoading(true);
  setStatus('Consultando processo', 'Buscando a versão mais recente disponível para esta credencial.');

  try {
    const statusResponse = await fetchProcessStatus(code, token);
    if (searchId !== currentSearchId) return;

    if (statusResponse.status === 401) {
      updateAccessState('invalid');
      setFieldError(tokenInput, tokenError, 'A credencial foi recusada. Confira o token e tente novamente.');
      throw new Error('AUTH');
    }
    updateAccessState('authenticated');
    if (statusResponse.status === 400) throw new Error('INVALID_CODE');
    if (statusResponse.status === 404) {
      pendingRequest={code,token};
      setStatus('Processo ainda não disponível', 'Nenhum processo está disponível para esta credencial. Solicitar consulta inicia uma aquisição na fonte processual.', { request: true });
      return;
    }
    if (!statusResponse.ok) throw new Error('SERVER');

    const statusData = await statusResponse.json();
    if (searchId !== currentSearchId) return;

    const classification = statusData.classification;

    if (classification === 'acquisition_pending') {
      setStatus('Solicitação em andamento', 'Uma consulta para este CNJ já está sendo processada pela fonte. Aguarde a conclusão.');
      scheduleAvailabilityCheck(code, token);
      return;
    }

    if (classification === 'ambiguous_failure') {
      setStatus(
        'Resultado da consulta anterior é incerto',
        'A última solicitação à fonte processual apresentou resultado incerto (falha ambígua). Nova aquisição automática bloqueada pelo servidor para proteger contra cobrança duplicada.',
        { error: true }
      );
      return;
    }

    if (classification === 'acquisition_required') {
      pendingRequest={code,token};
      setStatus(
        'Processo ainda não disponível',
        'Nenhum processo está disponível para esta credencial. Solicitar consulta inicia uma aquisição na fonte processual.',
        { request: true }
      );
      return;
    }

    if (classification === 'read_existing' || classification === 'refresh_requires_confirmation') {
      let detailMsg = '';
      if (statusData.acquired_today) {
        detailMsg = 'Atenção: este processo já foi consultado hoje no fuso horário de Brasília (America/Sao_Paulo). Uma nova consulta poderá gerar cobrança adicional desnecessária.';
      } else if (statusData.last_acquisition_at) {
        detailMsg = `Última consulta realizada em ${formatDate(statusData.last_acquisition_at)}. Você pode abrir o histórico existente ou solicitar nova consulta à Judit.`;
      } else {
        detailMsg = 'Processo já consultado anteriormente. Você pode abrir o histórico existente ou solicitar nova consulta à Judit.';
      }
      setStatus('Processo já consultado', detailMsg, { openHistory: true, refresh: true });
      return;
    }

    // Direct fallback if unclassified
    const response = await fetchProcess(code, token);
    if (searchId !== currentSearchId) return;
    if (response.status === 401) throw new Error('AUTH');
    if(response.status===404){
      pendingRequest={code,token};
      setStatus('Processo ainda não disponível', 'Nenhum processo está disponível para esta credencial. Solicitar consulta inicia uma aquisição na fonte processual.', { request: true });
      return;
    }
    if (response.status === 400) throw new Error('INVALID_CODE');
    if (!response.ok) throw new Error('SERVER');
    showProcess(await response.json());
  } catch (error) {
    if (searchId !== currentSearchId) return;
    if (error instanceof Error && error.message === 'API_NOT_CONFIGURED') {
      setStatus('API de staging ainda não configurada', 'A interface está publicada, mas o endereço HTTPS do backend ainda não foi configurado.', { error: true });
    } else if (error instanceof Error && error.message === 'AUTH') {
      setStatus('Não foi possível autorizar a consulta', 'Confira a credencial do ambiente.', { error: true });
      authPanel.hidden = false;
      accessTrigger.setAttribute('aria-expanded', 'true');
      tokenInput.focus();
    } else if (error instanceof Error && error.message === 'INVALID_CODE') {
      setFieldError(codeInput, codeError, 'A API recusou o número informado. Revise o CNJ e tente novamente.');
      setStatus('Número de processo inválido', 'Corrija o CNJ informado para continuar.', { error: true });
      codeInput.focus();
    } else if (error instanceof TypeError) {
      setStatus('Sem conexão com o serviço', 'Não foi possível alcançar a API. Verifique a conexão e tente novamente.', { error: true, retry: true });
    } else {
      setStatus('A consulta não pôde ser concluída', 'O serviço respondeu com um erro inesperado. Tente novamente em instantes.', { error: true, retry: true });
    }
  } finally {
    if (searchId === currentSearchId) {
      setLoading(false);
    }
  }
}

// Event Listeners
requestButton.addEventListener('click',async()=>{
  if (!pendingRequest) return;
  try {
    await requestUnavailableProcess(pendingRequest.code, pendingRequest.token);
  } catch (error) {
    if (error instanceof Error && error.message === 'AUTH') {
      clearPendingRequest();
      setStatus('A credencial deixou de ser aceita', 'Configure novamente o acesso para continuar.', { error: true });
    } else if (error instanceof Error && error.message === 'PROVIDER') {
      setStatus('Fonte processual temporariamente indisponível', 'Não foi possível registrar a consulta agora. Tente novamente em instantes.', { error: true, request: true });
    } else {
      setStatus('Não foi possível registrar a consulta', 'A aquisição não foi iniciada. Tente novamente.', { error: true, request: true });
    }
  }
});

if (openExistingButton) {
  openExistingButton.addEventListener('click', async () => {
    const code = codeInput.value.trim(),
      token = tokenInput.value.trim();
    if (!code || !token) return;
    setLoading(true);
    setStatus('Carregando dados processuais', 'Buscando os dados armazenados no histórico.');
    try {
      const response = await fetchProcess(code, token);
      if (response.status === 401) throw new Error('AUTH');
      if (!response.ok) throw new Error('SERVER');
      showProcess(await response.json());
    } catch (err) {
      if (err instanceof Error && err.message === 'AUTH') {
        setStatus('A credencial deixou de ser aceita', 'Configure novamente o acesso para continuar.', { error: true });
      } else {
        setStatus('Não foi possível carregar o histórico', 'Tente novamente.', { error: true, retry: true });
      }
    } finally {
      setLoading(false);
    }
  });
}

if (requestRefreshButton) {
  requestRefreshButton.addEventListener('click', () => {
    const code = codeInput.value.trim();
    if (code) showConfirmation(code);
  });
}

if (refreshFromResultButton) {
  refreshFromResultButton.addEventListener('click', () => {
    const code = codeInput.value.trim();
    if (code) showConfirmation(code);
  });
}

if (confirmRefreshCancel) {
  confirmRefreshCancel.addEventListener('click', hideConfirmation);
}

if (confirmRefreshSubmit) {
  confirmRefreshSubmit.addEventListener('click', async () => {
    if (confirmRefreshSubmit.disabled) return;
    const code = pendingConfirmationCode;
    const token = tokenInput.value.trim();
    if (!code || !token) {
      hideConfirmation();
      return;
    }
    // Prevent double-click
    confirmRefreshSubmit.disabled = true;
    confirmRefreshSubmit.textContent = 'Autorizando…';
    try {
      // 1. Request single-use authorization token from server
      const authResponse = await fetch(apiUrl(`/processes/${encodeURIComponent(code)}/authorizations`), {
        method: 'POST',
        headers: { ...authHeaders(token), 'Content-Type': 'application/json' },
        body: JSON.stringify({ operation: 'refresh', source: 'judit' }),
      });
      if (authResponse.status === 401) throw new Error('AUTH');
      if (!authResponse.ok) {
        const errData = await authResponse.json().catch(() => ({}));
        throw new Error(errData.detail || 'AUTH_REJECTED');
      }
      const authData = await authResponse.json();
      const authToken = authData.token || authData.authorization_token;

      // 2. Request acquisition using the authorization token
      confirmRefreshSubmit.textContent = 'Solicitando…';
      const reqResponse = await fetch(apiUrl(`/processes/${encodeURIComponent(code)}/request`), {
        method: 'POST',
        headers: {
          ...authHeaders(token),
          'Content-Type': 'application/json',
          'X-Acquisition-Authorization': authToken,
          'X-Judit-Authorization-Token': authToken,
        },
        body: JSON.stringify({ authorization_token: authToken }),
      });
      hideConfirmation();

      if (reqResponse.status === 401) throw new Error('AUTH');
      if (reqResponse.status === 409) {
        const errData = await reqResponse.json().catch(() => ({}));
        setStatus('Consulta não autorizada ou expirada', errData.detail || 'A autorização não foi aceita pelo servidor ou já foi consumida.', { error: true });
        return;
      }
      if (!reqResponse.ok) throw new Error('SERVER');

      const reqData = await reqResponse.json();
      pollAttempts = 0;
      setStatus(
        'Consulta registrada',
        reqData.created
          ? 'A solicitação foi aceita. Você pode manter esta página aberta ou voltar ao mesmo CNJ mais tarde.'
          : 'Este CNJ já possui uma solicitação registrada. Você pode manter esta página aberta ou consultar novamente mais tarde.'
      );
      scheduleAvailabilityCheck(code, token);
    } catch (err) {
      hideConfirmation();
      if (err instanceof Error && err.message === 'AUTH') {
        setStatus('A credencial deixou de ser aceita', 'Configure novamente o acesso para continuar.', { error: true });
      } else {
        // In case of timeout or uncertainty, verify persisted state before allowing new attempt
        try {
          const verifyResp = await fetchProcessStatus(code, token);
          if (verifyResp.ok) {
            const verifyData = await verifyResp.json();
            if (verifyData.classification === 'acquisition_pending') {
              setStatus('Consulta já registrada', 'A solicitação já está em andamento. Aguarde a conclusão.');
              scheduleAvailabilityCheck(code, token);
              return;
            }
          }
        } catch {
          // ignore verification network error
        }
        setStatus('Não foi possível registrar a consulta', err instanceof Error ? err.message : 'Falha na comunicação.', { error: true });
      }
    } finally {
      confirmRefreshSubmit.disabled = false;
      confirmRefreshSubmit.textContent = 'Confirmar consulta';
    }
  });
}

if (historyTrigger) {
  historyTrigger.addEventListener('click', async () => {
    if (!historyPanel) return;
    const opening = historyPanel.hidden;
    historyPanel.hidden = !opening;
    historyTrigger.setAttribute('aria-expanded', opening ? 'true' : 'false');
    if (opening) {
      result.hidden = true;
      setStatus();
      await loadHistoryList();
    }
  });
}

if (closeHistoryButton) {
  closeHistoryButton.addEventListener('click', () => {
    if (historyPanel) historyPanel.hidden = true;
    if (historyTrigger) historyTrigger.setAttribute('aria-expanded', 'false');
    codeInput.focus();
  });
}

if (refreshHistoryButton) {
  refreshHistoryButton.addEventListener('click', loadHistoryList);
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  await searchProcess();
});

retryButton.addEventListener('click', searchProcess);

toggleToken.addEventListener('click', () => {
  const showing = tokenInput.type === 'text';
  tokenInput.type = showing ? 'password' : 'text';
  toggleToken.textContent = showing ? 'Mostrar' : 'Ocultar';
  toggleToken.setAttribute('aria-pressed', showing ? 'false' : 'true');
  toggleToken.setAttribute('aria-label', showing ? 'Mostrar token de acesso' : 'Ocultar token de acesso');
});

document.querySelectorAll('.document-nav a').forEach((link) => {
  link.addEventListener('click', () => {
    const targetId = link.getAttribute('href');
    if (targetId && targetId.startsWith('#')) {
      const target = document.querySelector(targetId);
      if (target) target.focus();
    }
  });
});

tokenInput.addEventListener('input', () => {
  stopPolling();
  pendingDataJudRequest = null;
  clearPendingRequest();
  setFieldError(tokenInput, tokenError);
  updateAccessState();
});

tokenInput.addEventListener('keydown', async (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    await searchProcess();
  }
});

codeInput.addEventListener('input', () => {
  currentSearchId++;
  stopPolling();
  pendingDataJudRequest = null;
  clearPendingRequest();
  setFieldError(codeInput, codeError);
  const formatted = formatCnjInput(codeInput.value);
  if (formatted !== codeInput.value) codeInput.value = formatted;
});

accessTrigger.addEventListener('click', () => {
  const opening = authPanel.hidden;
  authPanel.hidden = !opening;
  accessTrigger.setAttribute('aria-expanded', opening ? 'true' : 'false');
  if (opening) tokenInput.focus();
});

copyButton.addEventListener('click', async () => {
  if (!currentSummary) return;
  try {
    await navigator.clipboard.writeText(summaryPlainText(currentSummary));
    if (copyTimer) clearTimeout(copyTimer);
    copyButton.textContent = 'Resumo copiado';
    announce('Resumo copiado para a área de transferência.');
    copyTimer = setTimeout(() => {
      copyButton.textContent = 'Copiar resumo';
      copyTimer = null;
    }, 1800);
  } catch {
    setStatus('Não foi possível copiar automaticamente', 'Selecione o texto do resumo e use o comando de copiar do navegador.', { error: true });
  }
});

newSearchButton.addEventListener('click', () => {
  stopPolling();
  pendingDataJudRequest = null;
  clearPendingRequest();
  result.hidden = true;
  setStatus();
  codeInput.value = '';
  codeInput.focus();
});

if (verifyTokenButton) {
  verifyTokenButton.addEventListener('click', async () => {
    await verifyAuthentication();
  });
}

if (sourceJuditRadio) {
  sourceJuditRadio.addEventListener('change', () => {
    if (sourceJuditRadio.checked) currentSource = 'judit';
  });
}

if (datajudConfigureButton) datajudConfigureButton.addEventListener('click', openDataJudConfigurator);

if (sourceDataJudRadio) {
  sourceDataJudRadio.addEventListener('change', () => {
    if (sourceDataJudRadio.checked) currentSource = 'datajud';
  });
}

function bindProviderTooltip(button, wrapper, tooltip) {
  if (!button || !wrapper || !tooltip) return;
  let hovering = false,
    focused = false,
    clicked = false;
  const sync = () => {
    const visible = hovering || focused || clicked;
    tooltip.hidden = !visible;
    button.setAttribute('aria-expanded', String(visible));
  };
  button.addEventListener('pointerenter', () => {
    hovering = true;
    sync();
  });
  wrapper.addEventListener('pointerleave', () => {
    hovering = false;
    if (!focused) clicked = false;
    sync();
  });
  button.addEventListener('focus', () => {
    focused = true;
    sync();
  });
  button.addEventListener('blur', () => {
    focused = false;
    if (!hovering) clicked = false;
    sync();
  });
  button.addEventListener('click', () => {
    clicked = true;
    sync();
  });
  button.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    hovering = false;
    focused = false;
    clicked = false;
    sync();
    button.blur();
  });
}

bindProviderTooltip(juditInfoButton, juditInfoWrap, juditTooltip);
bindProviderTooltip(datajudInfoButton, datajudInfoWrap, datajudTooltip);

window.addEventListener('pagehide', stopPolling);
updateAccessState();
fetchCapabilities();
