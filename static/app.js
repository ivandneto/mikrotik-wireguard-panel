// ---------- utilidades ----------
async function api(method, url, body) {
  const opt = { method, headers: {}, credentials: 'same-origin' };
  if (window.__routerId) opt.headers['X-Router-Id'] = String(window.__routerId);
  if (body !== undefined) {
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  const r = await fetch(url, opt);
  if (r.status === 401) {
    location.href = '/login';
    throw new Error('Sessão expirada');
  }
  const isJson = (r.headers.get('content-type') || '').includes('json');
  const data = isJson ? await r.json() : await r.text();
  if (!r.ok) {
    let msg = isJson ? data.detail : data;
    if (Array.isArray(msg)) msg = msg.map(e => `${(e.loc || []).slice(-1)[0]}: ${e.msg}`).join('; ');
    const err = new Error(msg || `Erro ${r.status}`);
    err.status = r.status;
    throw err;
  }
  return data;
}

function fmtBytes(n) {
  if (n == null) return '—';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
}

function fmtBps(n) {
  if (n == null) return '—';
  const u = ['bps', 'Kbps', 'Mbps', 'Gbps'];
  let i = 0;
  while (n >= 1000 && i < u.length - 1) { n /= 1000; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
}

function fmtDur(s) {
  if (s == null) return 'nunca';
  if (s < 60) return `${s}s`;
  const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  return [d && `${d}d`, h && `${h}h`, m && `${m}min`].filter(Boolean).slice(0, 2).join(' ');
}

function fmtDate(iso) {
  return iso ? new Date(iso).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' }) : '—';
}

function fmtAgo(iso) {
  if (!iso) return 'nunca';
  return fmtDur(Math.max(0, Math.round((Date.now() - new Date(iso)) / 1000))) + ' atrás';
}

const CHART_COLORS = { a: '#3b82f6', b: '#22c55e', c: '#f59e0b', d: '#a855f7' };

function lineChart(canvas, labels, datasets, yFmt) {
  if (canvas._chart) canvas._chart.destroy();
  canvas._chart = new Chart(canvas, {
    type: 'line',
    data: {
      labels,
      datasets: datasets.map(ds => ({
        // com poucos dados (painel recém-instalado) mostra os pontos, senão a linha some
        borderWidth: 2, pointRadius: ds.data.length < 10 ? 3 : 0, tension: .3, fill: true,
        backgroundColor: ds.borderColor + '22', ...ds,
      })),
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: { ticks: { color: '#64748b', maxTicksLimit: 8 }, grid: { color: '#1e293b' } },
        y: { beginAtZero: true, ticks: { color: '#64748b', callback: yFmt, precision: 0 }, grid: { color: '#1e293b' } },
      },
      plugins: {
        legend: { labels: { color: '#cbd5e1', boxWidth: 12 } },
        tooltip: { callbacks: { label: c => `${c.dataset.label}: ${yFmt(c.parsed.y)}` } },
      },
    },
  });
}

const shortTime = iso => new Date(iso).toLocaleString('pt-BR', {
  day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
});

// ---------- aplicação ----------
function panel() {
  return {
    tab: 'dashboard',
    me: null,
    overview: null,
    toasts: [],
    busy: false,
    loading: {},

    routerId: null,
    routers: [],
    rtForm: null,
    wg: { info: null, peers: [], filter: '', ifaceId: null },
    script: { peer: null, text: '', error: '', notice: '', part: 'all' },
    setup: null,
    suggest: null,
    setupResult: null,

    nat: { rules: [], templates: [], routerRules: null, filter: '' },

    events: [],
    audit: [],
    users: [],
    settings: null,
    routerOptions: { interfaces: [], interface_lists: [] },
    alerts: null,
    usage: [],
    usageDays: 30,

    chart: { target: '', hours: 24, info: null },
    dashHours: 24,

    modal: null,
    form: {},
    importList: null,
    importSel: [],
    qr: { peer: null, endpoint: 'remote', svg: '', error: '', notice: '' },

    tabs: [
      { id: 'dashboard', label: 'Painel', icon: '📊' },
      { id: 'routers', label: 'Roteadores', icon: '🖧', group: 'net' },
      { id: 'vpn', label: 'VPN WireGuard', icon: '🔐', group: 'net' },
      { id: 'nat', label: 'Liberação de portas', icon: '🎮', group: 'net' },
      { id: 'monitor', label: 'Gráficos e consumo', icon: '📈', group: 'mon' },
      { id: 'events', label: 'Eventos', icon: '🔔', group: 'mon' },
      { id: 'alerts', label: 'Alertas', icon: '📣', admin: true, group: 'mon' },
      { id: 'users', label: 'Usuários', icon: '👥', admin: true, group: 'adm' },
      { id: 'backup', label: 'Backup', icon: '💾', admin: true, group: 'adm' },
      { id: 'settings', label: 'Configurações', icon: '⚙️', admin: true, group: 'adm' },
      { id: 'audit', label: 'Auditoria', icon: '📜', admin: true, group: 'adm' },
      { id: 'help', label: 'Manual', icon: '📖' },
    ],

    navGroups: { net: 'Rede', mon: 'Monitoramento', adm: 'Administração' },
    navClosed: {},

    helpIndex: [
      { id: 'start', label: '🚀 Primeiros passos' },
      { id: 'security', label: '🔒 Segurança' },
      { id: 'dashboard', label: '📊 Painel' },
      { id: 'routers', label: '🖧 Roteadores' },
      { id: 'vpn', label: '🔐 VPN WireGuard' },
      { id: 'site', label: '🖧 Outro MikroTik' },
      { id: 'nat', label: '🎮 Liberação de portas' },
      { id: 'monitor', label: '📈 Gráficos e consumo' },
      { id: 'events', label: '🔔 Eventos' },
      { id: 'alerts', label: '📣 Alertas' },
      { id: 'users', label: '👥 Usuários' },
      { id: 'backup', label: '💾 Backup' },
      { id: 'settings', label: '⚙️ Configurações' },
      { id: 'audit', label: '📜 Auditoria' },
      { id: 'glossary', label: '📚 Glossário' },
      { id: 'about', label: '💬 Ideias e suporte' },
      { id: 'problems', label: '🛠️ Problemas comuns' },
    ],

    tour: { active: false, i: 0, steps: [], rect: null, boxStyle: '' },

    // tab: aba a abrir · sel: valor de data-tour a destacar (sem sel = balão centralizado)
    tourSteps: [
      { title: '👋 Bem-vindo ao WG Panel!', text: 'Este tour mostra para que serve cada parte do painel: <b>VPN WireGuard</b> (seus aparelhos e MikroTiks de clientes), <b>liberação de portas</b> e <b>monitoramento</b> com alertas. Use <b>Próximo</b> ou as setas do teclado; refaça quando quiser pelo botão <b>🎓 Tour</b>.' },
      { tab: 'dashboard', sel: 'nav', title: 'Menu', text: 'As telas ficam agrupadas: <b>Rede</b> (VPN e portas), <b>Monitoramento</b> (gráficos, eventos, alertas) e <b>Administração</b> (usuários, backup, configurações, auditoria — só para administradores). Clique no nome do grupo para abrir/fechar.' },
      { tab: 'dashboard', sel: 'router-select', title: '🖧 Qual roteador', text: 'O painel gerencia <b>vários MikroTiks</b>: o seu e os dos clientes. Escolha aqui qual você está vendo — VPN, portas, gráficos e configurações passam a ser desse roteador. 🟢 online · 🔴 offline.' },
      { tab: 'dashboard', sel: 'status', title: 'Status da conexão', text: 'Mostra se o painel está falando com o MikroTik. <b>⟳</b> força uma atualização, <b>🎓</b> abre este tour e <b>❓</b> abre o manual da tela atual.' },
      { tab: 'dashboard', sel: 'dash-cards', title: 'Resumo', text: 'Saúde do roteador escolhido, quantos clientes VPN dele estão conectados agora e o estado das portas liberadas. Com vários MikroTiks, acima aparece <b>Seus roteadores</b> com o status de todos. Clique nos cartões para ir direto às telas.' },
      { tab: 'dashboard', sel: 'dash-chart', title: 'Gráficos', text: 'Velocidade da internet e uso de CPU/memória ao longo do tempo. A interface de internet é <b>detectada sozinha</b> — o nome aparece embaixo do gráfico.' },
      { tab: 'dashboard', sel: 'dash-events', title: 'Eventos recentes', text: 'Tudo que acontece aparece aqui: alguém conectou na VPN, um cliente caiu, um servidor parou, uma porta temporária fechou…' },
      { tab: 'routers', sel: 'sec-routers', title: '🖧 Roteadores', text: 'Todos os MikroTiks gerenciados, com status, recursos e clientes. <b>🌐 WebFig</b> abre a página do roteador (com o Terminal dele) numa aba nova, com o seu próprio login. O jeito mais fácil de adicionar o de um cliente é pelo <b>🖧 Outro MikroTik</b> na VPN: o script cria um <b>usuário limitado</b> que só entra pelo túnel, e o roteador aparece aqui sozinho.' },
      { tab: 'vpn', sel: 'sec-vpn', title: '🔐 VPN WireGuard', text: 'Acesse sua rede de qualquer lugar com segurança. Dois tipos de cliente: <b>📱 Dispositivo</b> (celular, notebook — via QR Code) e <b>🖧 Outro MikroTik</b> (o roteador de um cliente e a rede dele — via script). Na primeira vez, clique em <b>Iniciar configuração</b>.' },
      { tab: 'vpn', sel: 'vpn-ifaces', title: 'Interfaces', text: 'Cada aba é uma interface WireGuard com porta e faixa próprias (ex.: <span class="mono">wg-casa</span> para seus aparelhos, <span class="mono">wg-clientes</span> para MikroTiks de clientes). O número mostra <b>online/total</b>. <b>＋ Nova interface</b> cria outra ou adota uma que já existe no MikroTik.' },
      { tab: 'vpn', sel: 'vpn-actions', admin: true, title: 'Adicionar clientes', text: '<b>＋ Novo dispositivo</b>: um por aparelho, depois é só escanear o QR Code. <b>🖧 Outro MikroTik</b>: informe a rede do cliente, escolha <b>➡️ só eu acesso</b> ou <b>↔️ mão dupla</b> e cole o script gerado no MikroTik dele. <b>⬇️ Importar</b>: traz peers criados pelo Winbox.' },
      { tab: 'vpn', sel: 'vpn-table', title: 'Lista de clientes', text: 'Quem está <span class="badge b-green">Online</span>, de onde conecta e quanto usou. MikroTiks remotos aparecem com 🖧, as redes deles e o tipo de acesso. Ícones: 📱 QR / 📜 script, 🖥️ abrir o roteador do cliente / 🛠️ passar a gerenciá-lo, ✏️ editar, ⏸️ bloquear, 🔑 trocar chaves, 🗑️ remover — e 🔁 quando o MikroTik estiver diferente do painel.' },
      { tab: 'nat', sel: 'nat-new', admin: true, title: '🎮 Liberar porta', text: 'Para servidores de jogos e aplicações acessíveis pela internet. Use um <b>modelo pronto</b> (Minecraft, CS2, Valheim…), informe o IP do servidor e pronto. Dá para limitar a IPs específicos e fechar automaticamente.' },
      { tab: 'nat', sel: 'nat-table', title: 'Portas liberadas', text: 'Cada liberação mostra se está ativa, quanto tráfego recebeu e se o servidor está <span class="badge b-green">Respondendo</span> ou <span class="badge b-red">Fora do ar</span>.' },
      { tab: 'nat', sel: 'nat-all', title: 'Regras existentes', text: 'Mostra também as liberações feitas fora do painel, direto no Winbox. O painel <b>nunca altera</b> essas regras — só as que ele mesmo criou.' },
      { tab: 'monitor', sel: 'mon-chart', title: '📈 Gráficos e consumo', text: 'Escolha um cliente (dispositivo ou MikroTik remoto) ou uma porta e o período para ver o histórico. Abaixo há o consumo total de cada cliente.' },
      { tab: 'events', sel: 'sec-events', title: '🔔 Eventos', text: 'Linha do tempo de tudo que aconteceu, com cores por gravidade.' },
      { tab: 'alerts', sel: 'sec-alerts', admin: true, title: '📣 Alertas', text: 'Receba avisos no <b>Discord</b> e/ou <b>Telegram</b>: cliente conectou/caiu, servidor fora do ar, MikroTik offline… Valem para todos os roteadores, e cada aviso diz de qual é (ex.: <span class="mono">[Padaria]</span>). Use "Salvar e enviar teste".' },
      { tab: 'users', sel: 'sec-users', admin: true, title: '👥 Usuários', text: 'Crie acessos para outras pessoas: <b>Administrador</b> (pode tudo) ou <b>Somente leitura</b> (só acompanha).' },
      { tab: 'backup', sel: 'sec-backup', admin: true, title: '💾 Backup', text: 'Baixe uma cópia de tudo do painel (roteadores, interfaces, clientes, portas, alertas, usuários) e crie backups do MikroTik escolhido no seletor antes de mudanças grandes.' },
      { tab: 'settings', sel: 'sec-settings', admin: true, title: '⚙️ Configurações', text: 'Itens marcados com <b>🖧</b> são do roteador escolhido (DDNS, NAT, WAN); o resto vale para o painel todo — como a opção de <b>guardar as chaves dos clientes no MikroTik</b>, para importar com QR Code em qualquer painel.' },
      { tab: 'audit', sel: 'sec-audit', admin: true, title: '📜 Auditoria', text: 'Registro de quem fez o quê e quando no painel.' },
      { tab: 'help', sel: 'help-btns', title: '🎉 Pronto!', text: 'Ficou com dúvida? <b>❓ Ajuda</b> abre o manual da tela atual, e o <b>📖 Manual</b> no menu tem tudo — inclusive <b>🖧 Roteadores</b> e o passo a passo do <b>🖧 Outro MikroTik</b>. Comece pelos <b>Primeiros passos</b> e veja <b>🔒 Segurança</b>: o painel nunca deve ficar exposto na internet — acesse pela rede local ou pela VPN.' },
    ],

    fmtBytes, fmtBps, fmtDur, fmtDate, fmtAgo,

    get isAdmin() { return this.me?.role === 'admin'; },
    get visibleTabs() { return this.tabs.filter(t => !t.admin || this.isAdmin); },
    get router() { return this.overview?.router || {}; },
    get routerList() { return this.overview?.routers || []; },
    get curRouter() { return this.routerList.find(r => r.id === this.routerId) || this.routerList[0]; },
    routerDot(r) { return r.online === true ? '🟢' : r.online === false ? '🔴' : '⚪'; },
    // WebFig fica na raiz do mesmo serviço www/www-ssl que a API usa
    webfigUrl(url) { return (url || '').replace(/\/+$/, '') + '/'; },
    // menu lateral: itens soltos e grupos, na ordem de `tabs`
    get navTree() {
      const out = [];
      for (const t of this.visibleTabs) {
        if (!t.group) { out.push({ key: t.id, tab: t }); continue; }
        let g = out.find(e => e.group === t.group);
        if (!g) out.push(g = { key: 'g-' + t.group, group: t.group, label: this.navGroups[t.group], items: [] });
        g.items.push(t);
      }
      return out;
    },
    isGroupOpen(e) { return !this.navClosed[e.group] || e.items.some(t => t.id === this.tab); },
    toggleGroup(e) {
      this.navClosed[e.group] = this.isGroupOpen(e);
      try { localStorage.setItem('wgpanel_nav_closed', JSON.stringify(this.navClosed)); } catch {}
    },
    get wgIfaces() { return this.wg.info?.interfaces || []; },
    get curIface() { return this.wgIfaces.find(i => i.id === this.wg.ifaceId) || this.wgIfaces[0]; },
    get filteredPeers() {
      const q = this.wg.filter.toLowerCase();
      return this.wg.peers.filter(p => p.interface_id === this.curIface?.id).filter(p => !q || [p.name, p.owner, p.ip, p.description].join(' ').toLowerCase().includes(q));
    },
    get filteredRules() {
      const q = this.nat.filter.toLowerCase();
      return this.nat.rules.filter(r => !q || [r.name, r.external_port, r.internal_ip].join(' ').toLowerCase().includes(q));
    },
    get templateGroups() {
      const groups = {};
      for (const t of this.nat.templates) (groups[t.category] ||= []).push(t);
      return groups;
    },
    get selectedTemplate() { return this.nat.templates.find(t => t.id === this.form.template); },

    async init() {
      try { this.me = await api('GET', '/api/auth/me'); } catch { return; }
      try { this.routerId = Number(localStorage.getItem('wgpanel_router')) || null; } catch {}
      window.__routerId = this.routerId;
      try { this.navClosed = JSON.parse(localStorage.getItem('wgpanel_nav_closed')) || {}; } catch {}
      await Promise.all([this.loadOverview(), this.loadPeers(), this.loadRules(), this.loadWg(),
        this.loadEvents(), api('GET', '/api/nat/templates').then(t => this.nat.templates = t)]);
      const hash = location.hash.slice(1);
      if (this.visibleTabs.some(t => t.id === hash)) this.tab = hash;
      this.$watch('tab', t => { location.hash = t; this.onTab(t); });
      this.onTab(this.tab);
      setInterval(() => this.refreshLive(), 15000);
      window.addEventListener('resize', () => this.tour.active && this.placeTour());
      let seen = false;
      try { seen = localStorage.getItem('wgpanel_tour_seen') === '1'; } catch {}
      if (!seen) setTimeout(() => this.startTour(), 800);
    },

    // ---------- manual ----------
    openHelp(fromTab) {
      this.tab = 'help';
      const id = this.helpIndex.some(h => h.id === fromTab) ? fromTab : 'start';
      setTimeout(() => this.scrollHelp(id), 50);
    },
    scrollHelp(id) {
      document.getElementById('help-' + id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    },

    // ---------- tour ----------
    startTour() {
      this.modal = null;
      this.tour.steps = this.tourSteps.filter(s => !s.admin || this.isAdmin);
      this.tour.active = true;
      this.tourGo(0);
    },
    endTour() {
      this.tour.active = false;
      try { localStorage.setItem('wgpanel_tour_seen', '1'); } catch {}
    },
    tourNext() {
      if (this.tour.i >= this.tour.steps.length - 1) return this.endTour();
      this.tourGo(this.tour.i + 1);
    },
    tourPrev() { if (this.tour.i > 0) this.tourGo(this.tour.i - 1); },
    tourGo(i) {
      this.tour.i = i;
      const step = this.tour.steps[i];
      if (step.tab && this.tab !== step.tab) this.tab = step.tab;
      // espera a aba renderizar antes de medir o elemento
      setTimeout(() => {
        const el = step.sel && document.querySelector(`[data-tour="${step.sel}"]`);
        if (el && el.offsetParent !== null) el.scrollIntoView({ block: 'center' });
        setTimeout(() => this.placeTour(), 80);
      }, 120);
    },
    placeTour() {
      const step = this.tour.steps[this.tour.i];
      const el = step.sel && document.querySelector(`[data-tour="${step.sel}"]`);
      const vw = window.innerWidth, vh = window.innerHeight, boxW = Math.min(380, vw - 32), gap = 16;
      if (!el || el.offsetParent === null) {
        this.tour.rect = null;
        this.tour.boxStyle = `top:50%;left:50%;transform:translate(-50%,-50%)`;
        return;
      }
      const r = el.getBoundingClientRect();
      const rect = {
        top: Math.max(r.top, 8), left: Math.max(r.left, 8),
        width: Math.min(r.width, vw - 16), height: Math.min(r.bottom, vh - 8) - Math.max(r.top, 8),
      };
      this.tour.rect = rect;
      const boxH = 260;
      let top, left;
      if (rect.left + rect.width + gap + boxW < vw && rect.height > vh * .5) {
        // elemento alto (ex.: menu lateral): balão ao lado
        left = rect.left + rect.width + gap;
        top = Math.min(Math.max(rect.top, 16), vh - boxH - 16);
      } else if (rect.top + rect.height + gap + boxH < vh) {
        top = rect.top + rect.height + gap;
      } else if (rect.top - gap - boxH > 0) {
        top = rect.top - gap - boxH;
      } else {
        top = vh - boxH - 16;
      }
      if (left === undefined) left = Math.min(Math.max(rect.left, 16), vw - boxW - 16);
      this.tour.boxStyle = `top:${top}px;left:${left}px`;
    },

    toast(msg, type = 'ok') {
      const id = Date.now() + Math.random();
      this.toasts.push({ id, msg, type });
      setTimeout(() => this.toasts = this.toasts.filter(t => t.id !== id), type === 'err' ? 8000 : 4000);
    },

    async run(fn, okMsg) {
      this.busy = true;
      try {
        const r = await fn();
        if (okMsg) this.toast(okMsg);
        return r;
      } catch (e) {
        this.toast(e.message, 'err');
        throw e;
      } finally {
        this.busy = false;
      }
    },

    async onTab(t) {
      if (t === 'dashboard') this.$nextTick(() => this.drawDashboard());
      if (t === 'vpn' && this.wg.info && !this.wgIfaces.length && this.isAdmin) this.openSetup();
      if (t === 'monitor') { this.loadUsage(); this.$nextTick(() => this.drawChart()); }
      if (t === 'events') this.loadEvents();
      if (t === 'audit') this.audit = await api('GET', '/api/monitor/audit');
      if (t === 'users') this.users = await api('GET', '/api/users');
      if (t === 'alerts') this.alerts = await api('GET', '/api/alerts');
      if (t === 'settings') this.loadSettings();
      if (t === 'routers') this.loadRouters();
    },

    async refreshLive() {
      await Promise.all([this.loadOverview(), this.loadPeers(), this.loadRules()]);
      if (this.tab === 'events' || this.tab === 'dashboard') this.loadEvents();
    },

    async forceRefresh() {
      await this.run(() => api('POST', '/api/monitor/refresh'), 'Dados atualizados');
      await this.refreshLive();
      if (this.tab === 'dashboard') this.drawDashboard();
    },

    async logout() {
      await api('POST', '/api/auth/logout');
      location.href = '/login';
    },

    loadOverview() {
      return api('GET', '/api/monitor/overview').then(o => {
        this.overview = o;
        if (this.routerId !== o.router_id) {  // roteador salvo não existe mais: volta para o principal
          this.routerId = o.router_id;
          window.__routerId = o.router_id;
        }
      }).catch(() => {});
    },
    async switchRouter(id) {
      this.routerId = id;
      window.__routerId = id;
      try { localStorage.setItem('wgpanel_router', String(id)); } catch {}
      this.wg.ifaceId = null;
      this.nat.routerRules = null;
      this.chart.target = '';
      this.rtForm = null;
      await Promise.all([this.loadOverview(), this.loadPeers(), this.loadRules(), this.loadWg()]);
      this.onTab(this.tab);
      this.toast(`Roteador: ${this.curRouter?.name || ''}`);
    },
    // ---------- roteadores ----------
    async loadRouters() { this.routers = await api('GET', '/api/routers'); },
    newRouter() {
      this.form = { name: '', url: 'http://', username: 'wgpanel', password: '', verify_tls: false, enabled: true,
        endpoint_remote: '', endpoint_local: '', nat_match_mode: 'wan_list', nat_in_target: 'WAN', wan_interface: '' };
      this.modal = 'router';
    },
    editRouter(r) {
      this.form = { ...r, password: '' };
      this.modal = 'router';
    },
    async saveRouter() {
      const { id, ...body } = this.form;
      if (id) await this.run(() => api('PATCH', `/api/routers/${id}`, body), 'Roteador salvo');
      else await this.run(() => api('POST', '/api/routers', body), 'Roteador adicionado');
      this.modal = null;
      await Promise.all([this.loadRouters(), this.loadOverview()]);
    },
    async testRouter(r) {
      const res = await this.run(() => api('POST', `/api/routers/${r.id}/test`));
      this.toast(res.message, res.ok ? 'ok' : 'err');
      this.loadRouters();
    },
    async deleteRouter(r) {
      if (!confirm(`Tirar "${r.name}" do painel?\n\nO painel apaga o que ele guardava desse roteador (interfaces, clientes, portas e histórico), mas NÃO mexe na configuração do MikroTik dele.`)) return;
      await this.run(() => api('DELETE', `/api/routers/${r.id}`), 'Roteador removido do painel');
      if (this.routerId === r.id) await this.switchRouter(this.routerList.find(x => x.is_main)?.id);
      await Promise.all([this.loadRouters(), this.loadOverview()]);
    },
    openRouter(id, tab = 'dashboard') {
      this.switchRouter(id);
      this.tab = tab;
    },
    loadPeers() { return api('GET', '/api/wireguard/peers').then(p => this.wg.peers = p).catch(() => {}); },
    loadRules() { return api('GET', '/api/nat/rules').then(r => this.nat.rules = r).catch(() => {}); },
    loadWg() {
      return api('GET', '/api/wireguard/interfaces').then(info => {
        this.wg.info = info;
        if (!info.interfaces.some(i => i.id === this.wg.ifaceId)) this.wg.ifaceId = info.interfaces[0]?.id ?? null;
      }).catch(() => {});
    },
    loadEvents() { return api('GET', '/api/monitor/events?limit=200').then(e => this.events = e).catch(() => {}); },
    async loadUsage() { this.usage = await api('GET', `/api/monitor/usage?days=${this.usageDays}`); },

    // ---------- dashboard ----------
    async drawDashboard() {
      const canvas = this.$refs.dashChart;
      if (!canvas) return;
      const { points } = await api('GET', `/api/monitor/system/history?hours=${this.dashHours}`);
      const labels = points.map(p => shortTime(p.ts));
      lineChart(canvas, labels, [
        { label: 'WAN download', data: points.map(p => p.wan_rx_bps), borderColor: CHART_COLORS.a },
        { label: 'WAN upload', data: points.map(p => p.wan_tx_bps), borderColor: CHART_COLORS.b },
      ], fmtBps);
      lineChart(this.$refs.cpuChart, labels, [
        { label: 'CPU %', data: points.map(p => p.cpu), borderColor: CHART_COLORS.c },
        { label: 'Memória %', data: points.map(p => p.mem_pct), borderColor: CHART_COLORS.d },
      ], v => `${v}%`);
    },

    // ---------- status helpers ----------
    peerStatus(p) {
      if (!p.enabled) return { label: 'Desativado', cls: 'b-gray' };
      if (!p.live) return { label: 'Aguardando', cls: 'b-gray' };
      if (!p.live.exists) return { label: 'Ausente no MikroTik', cls: 'b-red' };
      if (p.live.router_disabled) return { label: 'Desativado no MikroTik', cls: 'b-red' };
      if (p.live.online) return { label: 'Online', cls: 'b-green' };
      return { label: 'Offline', cls: 'b-amber' };
    },
    ruleStatus(r) {
      if (r.expired) return { label: 'Expirada', cls: 'b-gray' };
      if (!r.enabled) return { label: 'Desativada', cls: 'b-gray' };
      if (!r.live) return { label: 'Aguardando', cls: 'b-gray' };
      if (r.live.missing) return { label: 'Ausente no MikroTik', cls: 'b-red' };
      return { label: 'Ativa', cls: 'b-green' };
    },
    healthBadge(r) {
      if (!r.enabled || !r.health_check || r.protocol === 'udp') return null;
      const h = r.live?.healthy;
      if (h === true) return { label: 'Respondendo', cls: 'b-green' };
      if (h === false) return { label: 'Fora do ar', cls: 'b-red' };
      return { label: 'Verificando', cls: 'b-gray' };
    },
    protoLabel(p) { return { tcp: 'TCP', udp: 'UDP', both: 'TCP+UDP' }[p] || p; },
    levelCls(l) { return { success: 'b-green', warn: 'b-amber', error: 'b-red', info: 'b-blue' }[l] || 'b-gray'; },

    // ---------- WireGuard: configuração inicial ----------
    // iface = null: nova interface (ou adotar uma existente); senão, reconfigurar
    async openSetup(iface = null) {
      const info = this.wg.info || {};
      this.setup = {
        editing: !!iface, interface: iface?.name || (this.wgIfaces.length ? '' : 'wg-panel'),
        description: iface?.description || '', listen_port: iface?.listen_port || 13231,
        address: iface?.address || '10.10.0.1/24', dns: iface?.dns || '', lan_networks: iface?.lan_networks || '',
        endpoint_remote: info.endpoint_remote || '', endpoint_local: info.endpoint_local || '',
        add_to_lan_list: true, open_firewall: true,
      };
      this.setupResult = null;
      this.modal = 'setup';
      try {
        this.suggest = await api('GET', '/api/wireguard/suggest');
        if (!this.setup.endpoint_remote) this.setup.endpoint_remote = this.suggest.remote[0] || '';
        if (!this.setup.endpoint_local) this.setup.endpoint_local = this.suggest.local[0] || '';
        if (!iface) {
          this.setup.listen_port = this.suggest.next_port;
          this.setup.address = this.suggest.next_address;
          this.setup.lan_networks = this.suggest.lan_networks.join(', ');
        }
      } catch (e) {
        this.toast(e.message, 'err');
      }
    },
    adoptIface(i) {
      this.setup.interface = i.name;
      this.setup.listen_port = Number(i.listen_port);
      if (i.address) this.setup.address = i.address;
    },
    async runSetup() {
      const { editing, ...body } = this.setup;
      const r = await this.run(() => api('POST', '/api/wireguard/interfaces', body), 'Interface configurada!');
      this.setupResult = r.steps;
      this.wg.ifaceId = r.id;
      await this.loadWg();
      this.loadOverview();
    },
    async deleteIface(i) {
      if (i.peers_total) return this.toast('Remova os clientes desta interface antes de excluí-la.', 'err');
      if (!confirm(`Excluir a interface ${i.name} do painel?`)) return;
      const fromRouter = confirm(`Remover também a interface ${i.name} do MikroTik (com o IP e a regra de firewall criados pelo painel)?\n\nOK = remover do MikroTik também\nCancelar = só tirar do painel`);
      await this.run(() => api('DELETE', `/api/wireguard/interfaces/${i.id}?remove_from_router=${fromRouter}`), 'Interface excluída');
      this.wg.ifaceId = null;
      this.loadWg();
    },

    // ---------- WireGuard: clientes ----------
    newPeer(kind = 'device') {
      this.form = { interface_id: this.curIface?.id, kind, name: '', owner: '', description: '', tunnel_mode: 'split',
        ip: '', remote_networks: '', site_access: 'one_way', manage: true, api_port: 80 };
      this.modal = 'peer';
    },
    editPeer(p) {
      this.form = { id: p.id, kind: p.kind, name: p.name, owner: p.owner, description: p.description,
        tunnel_mode: p.tunnel_mode, original_mode: p.tunnel_mode, remote_networks: p.remote_networks.join(', '),
        site_access: p.site_access };
      this.modal = 'peer';
    },
    async savePeer() {
      const { id, original_mode, ...data } = this.form;
      if (!data.ip) delete data.ip;
      if (data.kind !== 'site') { delete data.remote_networks; delete data.site_access; }
      if (id) {
        delete data.kind;
        const peer = await this.run(() => api('PATCH', `/api/wireguard/peers/${id}`, data), 'Cliente atualizado');
        this.modal = null;
        if (peer.kind === 'device' && original_mode !== data.tunnel_mode) {
          // o modo fica no arquivo do dispositivo: precisa importar de novo
          this.openQr(peer, 'O modo de navegação mudou. Escaneie este QR Code (ou importe o .conf) de novo no dispositivo — a configuração antiga continua no modo anterior.');
        }
      } else {
        const peer = await this.run(() => api('POST', '/api/wireguard/peers', data),
          data.kind === 'site' ? 'MikroTik remoto criado' : 'Cliente criado');
        await this.loadPeers();
        this.openPeerConfig(peer);
      }
      this.loadPeers();
      this.loadWg();
    },
    openPeerConfig(p, notice = '') { return p.kind === 'site' ? this.openScript(p, notice) : this.openQr(p, notice); },
    async openScript(p, notice = '', part = 'all') {
      this.script = { peer: p, text: '', error: '', notice, part };
      this.modal = 'script';
      try {
        this.script.text = await api('GET', `/api/wireguard/peers/${p.id}/script?endpoint=remote&part=${part}`);
      } catch (e) {
        this.script.error = e.message;
      }
    },
    async manageSite(p) {
      const port = prompt(`Gerenciar "${p.name}" pelo painel.\n\nPorta do WebFig (serviço www) do MikroTik do cliente — veja em IP → Services → www no Winbox dele:`, '80');
      if (!port) return;
      const peer = await this.run(() => api('POST', `/api/wireguard/peers/${p.id}/manage`, { api_port: Number(port) }));
      this.loadPeers();
      this.openScript(peer, 'Cole este trecho no Terminal do MikroTik do cliente: ele cria o usuário limitado "wgpanel" (que só entra pelo túnel) e libera a API. Depois o roteador aparece em Roteadores.', 'management');
    },
    async copyScript() {
      try {
        await navigator.clipboard.writeText(this.script.text);
      } catch {
        // http sem HTTPS (ex.: painel no container do MikroTik): clipboard API indisponível
        this.$refs.scriptBox.select();
        document.execCommand('copy');
      }
      this.toast('Script copiado! Cole no Terminal do MikroTik do cliente.');
    },
    async togglePeer(p) {
      await this.run(() => api('PATCH', `/api/wireguard/peers/${p.id}`, { enabled: !p.enabled }),
        p.enabled ? 'Cliente desativado' : 'Cliente ativado');
      this.loadPeers();
    },
    async deletePeer(p) {
      if (!confirm(`Remover o cliente "${p.name}"? Ele perde o acesso imediatamente.`)) return;
      await this.run(() => api('DELETE', `/api/wireguard/peers/${p.id}`), 'Cliente removido');
      this.loadPeers();
    },
    async openImport() {
      this.importList = null;
      this.importSel = [];
      this.modal = 'import';
      this.importList = await this.run(() => api('GET', '/api/wireguard/importable'));
      this.importSel = this.importList.map(p => p.id);
    },
    async runImport() {
      const r = await this.run(() => api('POST', '/api/wireguard/import', { ids: this.importSel }));
      if (r.imported.length) this.toast(`Importado(s): ${r.imported.join(', ')}`);
      r.errors.forEach(e => this.toast(e, 'err'));
      this.modal = null;
      this.loadPeers();
    },
    async reapplyPeer(p) {
      const r = await this.run(() => api('POST', `/api/wireguard/peers/${p.id}/reapply`));
      this.toast(r.message);
      this.loadPeers();
    },
    async regeneratePeer(p) {
      if (!confirm(`Gerar novas chaves para "${p.name}"? A configuração atual do dispositivo deixará de funcionar e será preciso importar a nova.`)) return;
      const peer = await this.run(() => api('POST', `/api/wireguard/peers/${p.id}/regenerate`), 'Novas chaves geradas');
      this.loadPeers();
      this.openPeerConfig(peer, p.kind === 'site' ? 'Chaves novas: no MikroTik do cliente, remova a interface wg-central antiga e rode este script de novo.' : '');
    },
    async openQr(p, notice = '') {
      this.qr = { peer: p, endpoint: this.wg.info?.endpoint_remote ? 'remote' : 'local', svg: '', error: '', notice };
      this.modal = 'qr';
      await this.loadQr();
    },
    async loadQr() {
      this.qr.svg = '';
      this.qr.error = '';
      try {
        this.qr.svg = await api('GET', `/api/wireguard/peers/${this.qr.peer.id}/qr?endpoint=${this.qr.endpoint}`);
      } catch (e) {
        this.qr.error = e.message;
      }
    },
    configUrl(p, endpoint) { return `/api/wireguard/peers/${p.id}/config?endpoint=${endpoint}`; },
    showPeerChart(p) {
      this.chart.target = `peer:${p.id}`;
      this.tab = 'monitor';
    },

    // ---------- NAT ----------
    newRule() {
      this.form = {
        template: '', name: '', protocol: 'tcp', external_port: '', internal_ip: '', internal_port: '',
        whitelist: '', health_check: true, expires: '', expires_custom: '',
      };
      this.modal = 'nat';
    },
    applyTemplate() {
      const t = this.selectedTemplate;
      if (!t) return;
      this.form.name = t.name;
      this.form.protocol = t.protocol;
      this.form.external_port = t.port;
      this.form.internal_port = '';
    },
    expiresHours() {
      if (this.form.expires === 'custom') {
        if (!this.form.expires_custom) return null;
        const h = (new Date(this.form.expires_custom) - Date.now()) / 3600000;
        return h > 0 ? h : null;
      }
      return this.form.expires ? Number(this.form.expires) : null;
    },
    async saveRule(force = false) {
      const f = this.form;
      const body = {
        name: f.name, template: f.template, protocol: f.protocol, external_port: String(f.external_port),
        internal_ip: f.internal_ip, internal_port: String(f.internal_port || ''), whitelist: f.whitelist,
        health_check: f.health_check, expires_hours: this.expiresHours(), force,
      };
      this.busy = true;
      try {
        await api('POST', '/api/nat/rules', body);
        this.toast('Porta liberada!');
        this.modal = null;
        this.loadRules();
      } catch (e) {
        if (e.status === 409 && confirm(`${e.message}\n\nCriar mesmo assim?`)) {
          this.busy = false;
          return this.saveRule(true);
        }
        if (e.status !== 409) this.toast(e.message, 'err');
      } finally {
        this.busy = false;
      }
    },
    async toggleRule(r) {
      await this.run(() => api('PATCH', `/api/nat/rules/${r.id}`, { enabled: !r.enabled }),
        r.enabled ? 'Liberação desativada' : 'Liberação ativada');
      this.loadRules();
    },
    async extendRule(r) {
      const h = prompt('Expirar daqui a quantas horas? (0 = nunca expira)', '24');
      if (h === null) return;
      await this.run(() => api('PATCH', `/api/nat/rules/${r.id}`, { expires_hours: Number(h) }), 'Expiração atualizada');
      this.loadRules();
    },
    async reapplyRule(r) {
      await this.run(() => api('POST', `/api/nat/rules/${r.id}/reapply`), 'Regra recriada no MikroTik');
      this.loadRules();
    },
    async deleteRule(r) {
      if (!confirm(`Fechar a porta "${r.name}" (${this.protoLabel(r.protocol)} ${r.external_port})?`)) return;
      await this.run(() => api('DELETE', `/api/nat/rules/${r.id}`), 'Liberação removida');
      this.loadRules();
    },
    async loadRouterRules() {
      this.nat.routerRules = await this.run(() => api('GET', '/api/nat/router-rules'));
    },
    showRuleChart(r) {
      this.chart.target = `nat:${r.id}`;
      this.tab = 'monitor';
    },

    // ---------- monitoramento ----------
    async drawChart() {
      if (!this.chart.target) {
        const first = this.wg.peers[0] ? `peer:${this.wg.peers[0].id}` : (this.nat.rules[0] ? `nat:${this.nat.rules[0].id}` : '');
        this.chart.target = first;
      }
      if (!this.chart.target) return;
      const [kind, id] = this.chart.target.split(':');
      const canvas = this.$refs.monChart;
      if (kind === 'peer') {
        const d = await api('GET', `/api/monitor/peers/${id}/history?hours=${this.chart.hours}`);
        this.chart.info = { kind, ...d };
        lineChart(canvas, d.points.map(p => shortTime(p.ts)), [
          { label: 'Download (MikroTik → cliente)', data: d.points.map(p => p.tx * 8 / d.bucket_seconds), borderColor: CHART_COLORS.a },
          { label: 'Upload (cliente → MikroTik)', data: d.points.map(p => p.rx * 8 / d.bucket_seconds), borderColor: CHART_COLORS.b },
        ], fmtBps);
      } else {
        const d = await api('GET', `/api/monitor/nat/${id}/history?hours=${this.chart.hours}`);
        this.chart.info = { kind, ...d };
        lineChart(canvas, d.points.map(p => shortTime(p.ts)), [
          { label: 'Tráfego de entrada', data: d.points.map(p => p.bytes * 8 / d.bucket_seconds), borderColor: CHART_COLORS.c },
        ], fmtBps);
      }
    },

    // ---------- alertas ----------
    async saveAlerts() {
      await this.run(() => api('PUT', '/api/alerts', this.alerts.config), 'Alertas salvos');
    },
    async testAlerts() {
      await this.saveAlerts();
      const r = await this.run(() => api('POST', '/api/alerts/test'));
      for (const [ch, err] of Object.entries(r.results)) {
        this.toast(err ? `${ch}: ${err}` : `${ch}: mensagem enviada!`, err ? 'err' : 'ok');
      }
    },

    // ---------- usuários ----------
    newUser() { this.form = { username: '', password: '', role: 'viewer' }; this.modal = 'user'; },
    async saveUser() {
      await this.run(() => api('POST', '/api/users', this.form), 'Usuário criado');
      this.modal = null;
      this.users = await api('GET', '/api/users');
    },
    async setRole(u, role) {
      await this.run(() => api('PATCH', `/api/users/${u.id}`, { role }), 'Permissão alterada');
      this.users = await api('GET', '/api/users');
    },
    async resetPassword(u) {
      const pw = prompt(`Nova senha para ${u.username} (mínimo 8 caracteres):`);
      if (!pw) return;
      await this.run(() => api('PATCH', `/api/users/${u.id}`, { password: pw }), 'Senha alterada');
    },
    async deleteUser(u) {
      if (!confirm(`Remover o usuário ${u.username}?`)) return;
      await this.run(() => api('DELETE', `/api/users/${u.id}`), 'Usuário removido');
      this.users = await api('GET', '/api/users');
    },
    changeMyPassword() { this.form = { current_password: '', new_password: '' }; this.modal = 'password'; },
    async saveMyPassword() {
      await this.run(() => api('POST', '/api/auth/password', this.form), 'Senha alterada');
      this.modal = null;
    },

    // ---------- backup ----------
    async importBackup(ev) {
      const file = ev.target.files[0];
      ev.target.value = '';
      if (!file) return;
      let data;
      try { data = JSON.parse(await file.text()); } catch { return this.toast('Arquivo inválido', 'err'); }
      const apply = confirm('Recriar no MikroTik os clientes e regras que não existirem lá?\n\nOK = sim (recomendado ao migrar/restaurar)\nCancelar = só importar para o painel');
      const r = await this.run(() => api('POST', '/api/backup/import', { data, apply_to_router: apply }));
      this.toast(`Importado: ${r.peers_created} clientes novos, ${r.peers_updated} atualizados, ${r.nat_created} regras NAT, ${r.users_created} usuários`);
      r.errors.forEach(e => this.toast(e, 'err'));
      this.refreshLive();
    },
    async mikrotikBackup() {
      const r = await this.run(() => api('POST', '/api/backup/mikrotik'));
      this.toast(`Backup salvo no MikroTik: ${r.files.join(', ')} (menu Files)`);
    },

    // ---------- configurações ----------
    async loadSettings() {
      this.settings = await api('GET', '/api/settings');
      const list = await api('GET', '/api/routers');
      this.routers = list;
      this.rtForm = { ...(list.find(r => r.id === this.routerId) || list[0]), password: '' };
      api('GET', '/api/settings/router-options').then(o => this.routerOptions = o).catch(() => {});
    },
    async saveSettings() {
      await this.run(async () => {
        await api('PUT', '/api/settings', this.settings);
        const { id, ...rt } = this.rtForm;
        await api('PATCH', `/api/routers/${id}`, rt);
      }, 'Configurações salvas');
      this.loadWg();
    },
  };
}
