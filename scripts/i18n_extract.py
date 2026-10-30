"""Lista as frases do painel que precisam de tradução.

Uso: python scripts/i18n_extract.py            → mostra quantas frases faltam em cada idioma
     python scripts/i18n_extract.py --keys      → imprime todas as frases (JSON)

O texto em português é a chave. Telas: static/i18n/<código>.js · servidor: app/locales/<código>.json.
O manual de cada idioma fica inteiro no arquivo do idioma (I18N.manual).
"""
import ast
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LANGS = ("en", "es", "fr", "de", "it", "ru", "zh", "ja")
ATTRS = {"title", "placeholder", "alt", "aria-label"}
EXPRS = {"x-text", "x-html", ":title", ":placeholder", ":alt", ":aria-label"}
LITERAL = re.compile(r"'((?:[^'\\]|\\.)*)'")
JS_ESC = {"n": "\n", "'": "'", '"': '"', "\\": "\\"}


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def js_unescape(s: str) -> str:
    return re.sub(r"\\(.)", lambda m: JS_ESC.get(m.group(1), m.group(1)), s)


def has_text(s: str) -> bool:
    return bool(re.search(r"[A-Za-zÀ-ÿ]{2}", s))


def expr_literals(expr: str):
    """Mesma regra do i18n.js: literais comparados (=== 'x') são valores, não texto."""
    for m in LITERAL.finditer(expr):
        before, after = expr[:m.start()], expr[m.end():]
        if re.search(r"[=!]==?\s*$", before) or re.match(r"\s*[=!]==?", after):
            continue
        lit = js_unescape(m.group(1))
        if has_text(lit):
            yield lit


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.keys, self.skip, self.in_manual = [], 0, 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style"):
            self.skip += 1
        if tag == "article" and "help" in (a.get("class") or "").split():
            self.in_manual = 1
        elif self.in_manual and tag == "article":
            self.in_manual += 1
        if self.in_manual:
            return
        for k, v in attrs:
            if v and k in ATTRS and has_text(v):
                self.keys.append(norm(v))
            if v and k in EXPRS:
                self.keys.extend(expr_literals(v))

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1
        if tag == "article" and self.in_manual:
            self.in_manual -= 1

    def handle_data(self, data):
        if not self.skip and not self.in_manual and has_text(data):
            self.keys.append(norm(data))


def html_keys(path: Path) -> list[str]:
    p = Page()
    p.feed(path.read_text("utf-8"))
    return p.keys


def js_keys(path: Path) -> list[str]:
    src = path.read_text("utf-8")
    return [js_unescape(m.group(1)) for m in re.finditer(r"(?<![\w.])t\('((?:[^'\\]|\\.)*)'", src)]


def py_calls(names: set[str], files) -> list[str]:
    out = []
    for f in files:
        for node in ast.walk(ast.parse(f.read_text("utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in names
                    and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                out.append(node.args[0].value)
    return out


def server_labels() -> list[str]:
    """Textos fixos que a API envia e a tela traduz: eventos, modelos de porta e ações da auditoria."""
    out = []
    tree = ast.parse((ROOT / "app/services/alerts.py").read_text("utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "EVENT_LABELS":
            out += [v.value for v in node.value.values]
    tree = ast.parse((ROOT / "app/services/nat_templates.py").read_text("utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value in ("category", "name", "warning") and isinstance(v, ast.Constant):
                    out.append(v.value)
    for f in (ROOT / "app").rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text("utf-8"))):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "audit" and len(node.args) >= 3
                    and isinstance(node.args[2], ast.Constant)):
                out.append(node.args[2].value)
            if isinstance(node, ast.IfExp) and isinstance(node.body, ast.Constant) and isinstance(node.orelse, ast.Constant):
                for v in (node.body.value, node.orelse.value):  # label = "MikroTik remoto criado" if ... else "..."
                    if isinstance(v, str) and " " in v and "criado" in v:
                        out.append(v)
    return out


def unique(seq):
    seen, out = set(), []
    for s in seq:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def ui_keys() -> list[str]:
    return unique(html_keys(ROOT / "static/index.html") + html_keys(ROOT / "static/login.html")
                  + js_keys(ROOT / "static/app.js") + server_labels())


def server_keys() -> list[str]:
    return unique(py_calls({"tr", "tr_ascii"}, sorted((ROOT / "app").rglob("*.py"))))


def load_ui(lang: str) -> dict:
    f = ROOT / f"static/i18n/{lang}.js"
    if not f.exists():
        return {}
    m = re.search(r"I18N\.dict = (\{.*?\n\});", f.read_text("utf-8"), re.S)
    return json.loads(m.group(1)) if m else {}


def load_server(lang: str) -> dict:
    f = ROOT / f"app/locales/{lang}.json"
    return json.loads(f.read_text("utf-8")) if f.exists() else {}


if __name__ == "__main__":
    if "--keys" in sys.argv:
        print(json.dumps({"ui": ui_keys(), "server": server_keys()}, ensure_ascii=False, indent=1))
        sys.exit()
    ui, server = ui_keys(), server_keys()
    print(f"frases: {len(ui)} nas telas, {len(server)} no servidor")
    for lang in LANGS:
        u, s = load_ui(lang), load_server(lang)
        miss_u = [k for k in ui if k not in u]
        miss_s = [k for k in server if k not in s]
        print(f"  {lang}: faltam {len(miss_u)} (telas) e {len(miss_s)} (servidor)")
        for k in (miss_u + miss_s)[:5]:
            print("     -", k[:90].replace("\n", "\\n"))
