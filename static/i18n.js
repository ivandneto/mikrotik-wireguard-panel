// ---------- idiomas ----------
// O painel é escrito em português (pt-BR) e o próprio texto em português é a chave da tradução:
// cada idioma tem um arquivo /static/i18n/<código>.js com { "texto em pt": "tradução" } e o manual traduzido.
// Texto sem tradução continua em português, então nada quebra se faltar alguma frase.
const LANGS = [
  { code: 'pt-BR', name: 'Português (Brasil)', flag: '🇧🇷', locale: 'pt-BR' },
  { code: 'en', name: 'English', flag: '🇺🇸', locale: 'en-US' },
  { code: 'es', name: 'Español', flag: '🇪🇸', locale: 'es-ES' },
  { code: 'fr', name: 'Français', flag: '🇫🇷', locale: 'fr-FR' },
  { code: 'de', name: 'Deutsch', flag: '🇩🇪', locale: 'de-DE' },
  { code: 'it', name: 'Italiano', flag: '🇮🇹', locale: 'it-IT' },
  { code: 'ru', name: 'Русский', flag: '🇷🇺', locale: 'ru-RU' },
  { code: 'zh', name: '中文（简体）', flag: '🇨🇳', locale: 'zh-CN' },
  { code: 'ja', name: '日本語', flag: '🇯🇵', locale: 'ja-JP' },
];

function detectLang() {
  try {
    const saved = localStorage.getItem('wgpanel_lang');
    if (LANGS.some(l => l.code === saved)) return saved;
  } catch {}
  for (const nav of navigator.languages || [navigator.language || '']) {
    const base = nav.toLowerCase().split('-')[0];
    if (base === 'pt') return 'pt-BR';
    const hit = LANGS.find(l => l.code === base);
    if (hit) return hit.code;
  }
  return 'en';
}

const LANG = detectLang();
const LOCALE = LANGS.find(l => l.code === LANG).locale;
const I18N = { dict: {}, manual: null };

// arquivo do idioma carregado de forma síncrona, antes do app.js, para t() já funcionar ao montar a tela
if (LANG !== 'pt-BR') document.write(`<script src="/static/i18n/${LANG}.js"><\/script>`);

function setLang(code) {
  try { localStorage.setItem('wgpanel_lang', code); } catch {}
  location.reload();
}

const norm = s => s.replace(/\s+/g, ' ').trim();

// t('Remover {name}?', { name }) — tradução com variáveis
function t(s, vars) {
  let out = I18N.dict[s] ?? I18N.dict[norm(s)] ?? s;
  if (vars) out = out.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? vars[k] : m));
  return out;
}

// Traduz o HTML fixo da página antes de o Alpine montar a tela: textos, atributos (title, placeholder…)
// e os trechos 'entre aspas' das expressões x-text/:title/:placeholder.
const I18N_ATTRS = ['title', 'placeholder', 'alt', 'aria-label'];
const I18N_EXPRS = ['x-text', 'x-html', ':title', ':placeholder', ':alt', ':aria-label'];

function trText(v) {
  const k = norm(v);
  if (!k) return v;
  const tr = I18N.dict[k];
  if (tr == null) return v;
  return v.match(/^\s*/)[0] + tr + v.match(/\s*$/)[0];
}

function trExpr(expr) {
  // não mexe em literais comparados (p.kind === 'site'): são valores, não texto
  return expr.replace(/'((?:[^'\\]|\\.)*)'/g, (m, lit, off, all) => {
    if (/[=!]==?\s*$/.test(all.slice(0, off)) || /^\s*[=!]==?/.test(all.slice(off + m.length))) return m;
    const tr = I18N.dict[lit.replace(/\\'/g, "'")];
    return tr == null ? m : `'${tr.replace(/\\/g, '\\\\').replace(/'/g, "\\'")}'`;
  });
}

function translateTree(root) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
  const templates = [];
  for (let n = walker.currentNode; n; n = walker.nextNode()) {
    if (n.nodeType === Node.TEXT_NODE) {
      const p = n.parentNode?.nodeName;
      if (p !== 'SCRIPT' && p !== 'STYLE') n.nodeValue = trText(n.nodeValue);
      continue;
    }
    if (n.nodeType !== Node.ELEMENT_NODE) continue;  // raiz de <template>.content é um fragmento
    for (const a of I18N_ATTRS) if (n.hasAttribute(a)) n.setAttribute(a, trText(n.getAttribute(a)));
    for (const a of I18N_EXPRS) if (n.hasAttribute(a)) n.setAttribute(a, trExpr(n.getAttribute(a)));
    if (n.nodeName === 'TEMPLATE') templates.push(n.content);
  }
  templates.forEach(translateTree);
}

function applyI18n() {
  document.documentElement.lang = LANG;
  if (LANG === 'pt-BR') return;
  translateTree(document.body);
  // o manual vem inteiro no arquivo do idioma (texto corrido não se traduz bem por pedaços)
  const manual = document.querySelector('article.help');
  if (manual && I18N.manual) manual.innerHTML = I18N.manual;
  document.title = t(document.title);
}

// index.html: antes do Alpine percorrer a página · login.html: quando a página termina de carregar
document.addEventListener('alpine:init', applyI18n);
document.addEventListener('DOMContentLoaded', () => { if (!window.Alpine) applyI18n(); });
