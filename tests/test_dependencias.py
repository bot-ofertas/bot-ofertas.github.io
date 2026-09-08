# -*- coding: utf-8 -*-
"""
Todo pacote de terceiros que o codigo importa esta no requirements.txt?

Existe por causa de um caso real, 08/09/2026. Uma limpeza de sistema levou
o Python do PC do Daniel. Reinstalar e rodar
`pip install -r requirements.txt` NAO devolvia o bot ao ar: `pywin32` era
importado por `integrations/whatsapp_desktop_silencioso.py` — o modulo que
de fato envia no grupo — e nunca esteve na lista.

O estrago desse caso e mudo. `_copiar_arquivo_clipboard` embrulha o import
em try/except, entao a falta do pacote nao derruba nada: devolve `False`,
registra um `log.warning("clipboard CF_HDROP: ...")`, a foto nao chega ao
clipboard e o envio e abortado (Regra 5 — foto e legenda saem juntas ou nao
saem). De fora, o sintoma e "o WhatsApp parou de postar" sem erro nenhum.

Faltavam tres: pywin32, pytrends e amazon-creatorsapi.

Rodar:
    python tests/test_dependencias.py
    python -m pytest tests/test_dependencias.py -v
"""
import ast
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# O nome no pip nem sempre e o nome do import. Sem este mapa o teste acusaria
# `import dotenv` como faltando, tendo `python-dotenv` na lista.
APELIDOS = {
    "dotenv": "python-dotenv",
    "telegram": "python-telegram-bot",
    "PIL": "pillow",
    "amazon_paapi": "python-amazon-paapi",
    "amazon_creatorsapi": "amazon-creatorsapi",
    "bs4": "beautifulsoup4",
    "cv2": "opencv-python",
    "yaml": "pyyaml",
    # Todos estes vem de um pacote so.
    "win32api": "pywin32", "win32con": "pywin32", "win32gui": "pywin32",
    "win32clipboard": "pywin32", "win32event": "pywin32",
    "win32process": "pywin32", "win32com": "pywin32", "pythoncom": "pywin32",
}

# Arquivos que NAO fazem parte do bot em producao.
IGNORAR_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__"}


def _modulos_locais() -> set[str]:
    locais = set()
    for nome in os.listdir(BASE):
        caminho = os.path.join(BASE, nome)
        if nome.endswith(".py"):
            locais.add(nome[:-3])
        elif os.path.isdir(caminho) and nome not in IGNORAR_DIRS:
            # Pasta com .py dentro conta como local mesmo sem __init__.py:
            # `n8n/setup_n8n.py` e importado pelos testes via sys.path.
            locais.add(nome)
            for sub in os.listdir(caminho):
                if sub.endswith(".py"):
                    locais.add(sub[:-3])
    return locais


def _imports_de_terceiros() -> dict[str, set[str]]:
    stdlib = set(sys.stdlib_module_names)
    locais = _modulos_locais()
    achados: dict[str, set[str]] = {}
    for raiz, dirs, arquivos in os.walk(BASE):
        dirs[:] = [d for d in dirs if d not in IGNORAR_DIRS]
        for arq in arquivos:
            if not arq.endswith(".py"):
                continue
            caminho = os.path.join(raiz, arq)
            try:
                arvore = ast.parse(open(caminho, encoding="utf-8", errors="replace").read())
            except SyntaxError:
                continue
            for no in ast.walk(arvore):
                if isinstance(no, ast.Import):
                    nomes = [a.name.split(".")[0] for a in no.names]
                elif isinstance(no, ast.ImportFrom):
                    if no.level:          # import relativo: e do proprio projeto
                        continue
                    nomes = [(no.module or "").split(".")[0]]
                else:
                    continue
                for n in nomes:
                    if n and n not in stdlib and n not in locais:
                        achados.setdefault(n, set()).add(
                            os.path.relpath(caminho, BASE))
    return achados


def _declarados() -> set[str]:
    texto = open(os.path.join(BASE, "requirements.txt"), encoding="utf-8").read()
    nomes = set()
    for linha in texto.splitlines():
        linha = linha.split("#")[0].strip()
        if not linha:
            continue
        linha = linha.split(";")[0]        # marcador de plataforma
        nomes.add(re.split(r"[<>=\[]", linha)[0].strip().lower())
    return nomes


def test_todo_import_de_terceiro_esta_no_requirements():
    declarados = _declarados()
    faltando = []
    for mod, onde in sorted(_imports_de_terceiros().items()):
        pip_nome = APELIDOS.get(mod, mod).lower()
        if pip_nome not in declarados:
            faltando.append(f"{mod} (pip: {pip_nome}) em {sorted(onde)[0]}")
    assert not faltando, (
        "pacote importado pelo codigo e ausente do requirements.txt — "
        "reinstalar o Python nao devolveria o bot ao ar:\n  "
        + "\n  ".join(faltando))


def test_pywin32_esta_declarado_e_so_para_windows():
    """O caso que originou este arquivo. E precisa do marcador de
    plataforma: no servidor Linux (deploy/) o pywin32 nem instala, e quem
    envia no WhatsApp la e a Evolution API."""
    texto = open(os.path.join(BASE, "requirements.txt"), encoding="utf-8").read()
    linha = [l for l in texto.splitlines()
             if l.strip().lower().startswith("pywin32")]
    assert linha, "pywin32 saiu do requirements.txt — o WhatsApp para de enviar"
    assert 'sys_platform == "win32"' in linha[0], \
        "pywin32 sem marcador de plataforma quebra a instalacao no Linux"


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    falhas = 0
    for fn in fns:
        try:
            fn()
            print(f"  [OK]   {fn.__name__}")
        except Exception:
            falhas += 1
            print(f"  [FAIL] {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - falhas}/{len(fns)} testes passaram.")
    sys.exit(1 if falhas else 0)
