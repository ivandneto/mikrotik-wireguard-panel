"""Traduções das mensagens do servidor.

O texto em português é a chave; cada idioma tem app/locales/<código>.json com {"texto em pt": "tradução"}.
Respostas da API saem no idioma de quem está usando (cabeçalho X-Lang que o painel envia); alertas e
eventos saem no idioma escolhido em Configurações. Frase sem tradução continua em português.
"""
import json
from contextvars import ContextVar
from pathlib import Path

LANGS = ("pt-BR", "en", "es", "fr", "de", "it", "ru", "zh", "ja")
DEFAULT = "pt-BR"
_DIR = Path(__file__).parent / "locales"
_dicts: dict[str, dict[str, str]] = {}

current_lang: ContextVar[str] = ContextVar("current_lang", default=DEFAULT)


def pick(value: str | None) -> str:
    """Código suportado mais próximo: 'pt', 'pt-PT' → pt-BR; 'en-US' → en; desconhecido → pt-BR."""
    for part in (value or "").split(","):
        base = part.split(";")[0].strip().lower().split("-")[0]
        if base == "pt":
            return "pt-BR"
        if base in LANGS:
            return base
    return DEFAULT


def _dict(lang: str) -> dict[str, str]:
    if lang not in _dicts:
        f = _DIR / f"{lang}.json"
        _dicts[lang] = json.loads(f.read_text("utf-8")) if f.exists() else {}
    return _dicts[lang]


def tr(text: str, lang: str | None = None, **kw) -> str:
    """tr("Porta inválida: {port}", port=p) — traduz para o idioma atual e preenche as variáveis."""
    lang = lang or current_lang.get()
    out = text if lang == DEFAULT else _dict(lang).get(text, text)
    return out.format(**kw) if kw else out


def tr_ascii(text: str, **kw) -> str:
    """Para scripts RouterOS (o terminal só aceita ASCII): idiomas com outro alfabeto saem em inglês."""
    lang = current_lang.get()
    return tr(text, lang="en" if lang in ("ru", "zh", "ja") else lang, **kw)
