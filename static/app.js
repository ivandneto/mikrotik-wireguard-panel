// ---------- utilidades ----------
async function api(method, url, body) {
  const opt = { method, headers: {}, credentials: 'same-origin' };
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
        y: { beginAtZero: true, ticks: { color: '#64748b', callback: yFmt }, grid: { color: '#1e293b' } },
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

    wg: { status: null, peers: [], filter: '' },
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
    qr: { peer: null, endpoint: 'remote', svg: '', error: '' },

    tabs: [
      { id: 'dashboard', label: 'Painel', icon: '📊' },
      { id: 'vpn', label: 'VPN WireGuard', icon: '🔐' },
      { id: 'nat', label: 'Liberação de portas', icon: '🎮' },
      { id: 'monitor', label: 'Monitoramento', icon: '📈' },
      { id: 'events', label: 'Eventos', icon: '🔔' },
      { id: 'alerts', label: 'Alertas', icon: '📣', admin: true },
      { id: 'users', label: 'Usuários', icon: '👥', admin: true },
      { id: 'backup', label: 'Backup', icon: '💾', admin: true },
      { id: 'settings', label: 'Configurações', icon: '⚙️', admin: true },
      { id: 'audit', label: 'Auditoria', icon: '📜', admin: true },
      { id: 'help', label: 'Manual', icon: '📖' },
    ],

    helpIndex: [
      { id: 'start', label: '🚀 Primeiros passos' },
      { id: 'dashboard', label: '📊 Painel' },
      { id: 'vpn', label: '🔐 VPN WireGuard' },
      { id: 'nat', label: '🎮 Liberação de portas' },
      { id: 'monitor', label: '📈 Monitoramento' },
      { id: 'events', label: '🔔 Eventos' },
      { id: 'alerts', label: '📣 Alertas' },
      { id: 'users', label: '👥 Usuários' },
      { id: 'backup', label: '💾 Backup' },
      { id: 'settings', label: '⚙️ Configurações' },
      { id: 'audit', label: '📜 Auditoria' },
      { id: 'glossary', label: '📚 Glossário' },
      { id: 'problems', label: '🛠️ Problemas comuns' },
    ],

    tour: { active: false, i: 0, steps: [], rect: null, boxStyle: '' },

    // tab: aba a abrir · sel: valor de data-tour a destacar (sem sel = balão centralizado)
    tourSteps: [
      { title: '👋 Bem-vindo ao WG Panel!', text: 'Este tour rápido mostra para que serve cada parte do painel. Use <b>Próximo</b> ou as setas do teclado. Você pode refazê-lo a qualquer momento pelo botão <b>🎓 Tour</b> no topo.' },
      { tab: 'dashboard', sel: 'nav', title: 'Menu', text: 'Aqui ficam todas as telas. Itens como Alertas, Usuários e Configurações só aparecem para administradores.' },
      { tab: 'dashboard', sel: 'status', title: 'Status da conexão', text: 'Mostra se o painel está conseguindo falar com o MikroTik. <b>⟳</b> força uma atualização, <b>🎓</b> abre este tour e <b>❓</b> abre o manual da tela atual.' },
      { tab: 'dashboard', sel: 'dash-cards', title: 'Resumo', text: 'Saúde do roteador (CPU, memória, tempo ligado), quantos clientes VPN estão conectados agora e o estado das portas liberadas. Clique nos cartões de VPN e Portas para ir direto às telas.' },
      { tab: 'dashboard', sel: 'dash-chart', title: 'Gráficos', text: 'Velocidade da internet (download/upload) e uso de CPU/memória ao longo do tempo. Para o gráfico de internet, informe a <b>Interface WAN</b> em Configurações.' },
      { tab: 'dashboard', sel: 'dash-events', title: 'Eventos recentes', text: 'Tudo que acontece aparece aqui: alguém conectou na VPN, um servidor caiu, uma porta temporária fechou…' },
      { tab: 'vpn', sel: 'sec-vpn', title: '🔐 VPN WireGuard', text: 'Permite acessar sua rede de qualquer lugar com segurança. Na primeira vez, clique em <b>Iniciar configuração</b> — o painel prepara tudo no MikroTik sozinho.' },
      { tab: 'vpn', sel: 'vpn-new', admin: true, title: 'Novo cliente', text: 'Crie <b>um cliente para cada dispositivo</b> (celular, notebook…). Depois é só escanear o <b>QR Code</b> no app WireGuard.' },
      { tab: 'vpn', sel: 'vpn-table', title: 'Lista de clientes', text: 'Veja quem está <span class="badge b-green">Online</span>, de onde está conectando e quanto usou. Os ícones da direita: 📱 QR Code/arquivo, ⏸️ bloquear, 🔑 trocar chaves, 🗑️ remover.' },
      { tab: 'nat', sel: 'nat-new', admin: true, title: '🎮 Liberar porta', text: 'Para servidores de jogos e aplicações acessíveis pela internet. Use um <b>modelo pronto</b> (Minecraft, CS2, Valheim…), informe o IP do servidor e pronto. Dá para limitar a IPs específicos e fechar automaticamente.' },
      { tab: 'nat', sel: 'nat-table', title: 'Portas liberadas', text: 'Cada liberação mostra se está ativa, quanto tráfego recebeu e se o servidor está <span class="badge b-green">Respondendo</span> ou <span class="badge b-red">Fora do ar</span>.' },
      { tab: 'nat', sel: 'nat-all', title: 'Regras existentes', text: 'Mostra também as liberações feitas fora do painel, direto no Winbox. O painel <b>nunca altera</b> essas regras — só as que ele mesmo criou.' },
      { tab: 'monitor', sel: 'mon-chart', title: '📈 Monitoramento', text: 'Escolha um cliente VPN ou uma porta e o período para ver o histórico de uso. Abaixo há o consumo total de cada cliente.' },
      { tab: 'events', sel: 'sec-events', title: '🔔 Eventos', text: 'Linha do tempo completa de tudo que aconteceu, com cores por gravidade.' },
      { tab: 'alerts', sel: 'sec-alerts', admin: true, title: '📣 Alertas', text: 'Receba avisos no <b>Discord</b> e/ou <b>Telegram</b> e escolha quais eventos geram mensagem. Use "Salvar e enviar teste" para conferir.' },
      { tab: 'users', sel: 'sec-users', admin: true, title: '👥 Usuários', text: 'Crie acessos para outras pessoas: <b>Administrador</b> (pode tudo) ou <b>Somente leitura</b> (só acompanha).' },
      { tab: 'backup', sel: 'sec-backup', admin: true, title: '💾 Backup', text: 'Baixe uma cópia de tudo do painel e crie backups do próprio MikroTik antes de mudanças grandes.' },
      { tab: 'settings', sel: 'sec-settings', admin: true, title: '⚙️ Configurações', text: 'Endereços da VPN (remoto/DDNS e local), interface de internet para os gráficos e outras opções.' },
      { tab: 'audit', sel: 'sec-audit', admin: true, title: '📜 Auditoria', text: 'Registro de quem fez o quê e quando no painel.' },
      { tab: 'help', sel: 'help-btns', title: '🎉 Pronto!', text: 'Ficou com dúvida em alguma tela? Clique em <b>❓ Ajuda</b> para abrir o manual daquela tela, ou acesse <b>📖 Manual</b> no menu. Comece pelos <b>Primeiros passos</b>!' },
    ],

    fmtBytes, fmtBps, fmtDur, fmtDate, fmtAgo,

    get isAdmin() { return this.me?.role === 'admin'; },
    get visibleTabs() { return this.tabs.filter(t => !t.admin || this.isAdmin); },
    get router() { return this.overview?.router || {}; },
    get filteredPeers() {
      const q = this.wg.filter.toLowerCase();
      return this.wg.peers.filter(p => !q || [p.name, p.owner, p.ip, p.description].join(' ').toLowerCase().includes(q));
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
      await Promise.all([this.loadOverview(), this.loadPeers(), this.loadRules(), this.loadWgStatus(),
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
      if (t === 'vpn' && !this.wg.status?.configured && this.isAdmin) this.openSetup();
      if (t === 'monitor') { this.loadUsage(); this.$nextTick(() => this.drawChart()); }
      if (t === 'events') this.loadEvents();
      if (t === 'audit') this.audit = await api('GET', '/api/monitor/audit');
      if (t === 'users') this.users = await api('GET', '/api/users');
      if (t === 'alerts') this.alerts = await api('GET', '/api/alerts');
      if (t === 'settings') this.loadSettings();
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

    loadOverview() { return api('GET', '/api/monitor/overview').then(o => this.overview = o).catch(() => {}); },
    loadPeers() { return api('GET', '/api/wireguard/peers').then(p => this.wg.peers = p).catch(() => {}); },
    loadRules() { return api('GET', '/api/nat/rules').then(r => this.nat.rules = r).catch(() => {}); },
    loadWgStatus() { return api('GET', '/api/wireguard/status').then(s => this.wg.status = s).catch(() => {}); },
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
    async openSetup() {
      const st = this.wg.status || {};
      this.setup = {
        interface: st.interface || 'wg-panel', listen_port: st.listen_port || 13231,
        address: st.address || '10.10.0.1/24', dns: st.dns || '', lan_networks: st.lan_networks || '',
        endpoint_remote: st.endpoint_remote || '', endpoint_local: st.endpoint_local || '',
        add_to_lan_list: true, open_firewall: true,
      };
      this.setupResult = null;
      try {
        this.suggest = await api('GET', '/api/wireguard/suggest');
        if (!this.setup.endpoint_remote) this.setup.endpoint_remote = this.suggest.remote[0] || '';
        if (!this.setup.endpoint_local) this.setup.endpoint_local = this.suggest.local[0] || '';
        if (!this.setup.lan_networks) this.setup.lan_networks = this.suggest.lan_networks.join(', ');
      } catch (e) {
        this.toast(e.message, 'err');
      }
      this.modal = 'setup';
    },
    async runSetup() {
      const r = await this.run(() => api('POST', '/api/wireguard/setup', this.setup), 'WireGuard configurado!');
      this.setupResult = r.steps;
      await this.loadWgStatus();
      this.loadOverview();
    },

    // ---------- WireGuard: clientes ----------
    newPeer() {
      this.form = { name: '', owner: '', description: '', tunnel_mode: 'split', ip: '' };
      this.modal = 'peer';
    },
    editPeer(p) {
      this.form = { id: p.id, name: p.name, owner: p.owner, description: p.description, tunnel_mode: p.tunnel_mode };
      this.modal = 'peer';
    },
    async savePeer() {
      const { id, ...data } = this.form;
      if (!data.ip) delete data.ip;
      if (id) {
        await this.run(() => api('PATCH', `/api/wireguard/peers/${id}`, data), 'Cliente atualizado');
        this.modal = null;
      } else {
        const peer = await this.run(() => api('POST', '/api/wireguard/peers', data), 'Cliente criado');
        await this.loadPeers();
        this.openQr(peer);
      }
      this.loadPeers();
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
    async reapplyPeer(p) {
      const r = await this.run(() => api('POST', `/api/wireguard/peers/${p.id}/reapply`));
      this.toast(r.message);
      this.loadPeers();
    },
    async regeneratePeer(p) {
      if (!confirm(`Gerar novas chaves para "${p.name}"? A configuração atual do dispositivo deixará de funcionar e será preciso importar a nova.`)) return;
      const peer = await this.run(() => api('POST', `/api/wireguard/peers/${p.id}/regenerate`), 'Novas chaves geradas');
      this.loadPeers();
      this.openQr(peer);
    },
    async openQr(p) {
      this.qr = { peer: p, endpoint: this.wg.status?.endpoint_remote ? 'remote' : 'local', svg: '', error: '' };
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
      api('GET', '/api/settings/router-options').then(o => this.routerOptions = o).catch(() => {});
    },
    async saveSettings() {
      await this.run(() => api('PUT', '/api/settings', this.settings), 'Configurações salvas');
      this.loadWgStatus();
    },
  };
}
