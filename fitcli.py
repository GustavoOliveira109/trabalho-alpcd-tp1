"""fitcli.py - CLI para consultar a API wger (exercicios fisicos)."""

import json
import re
import time
from functools import lru_cache
from typing import Optional
from collections import Counter

import requests
import typer

BASE_URL = "https://wger.de/api/v2/"
HEADERS = {"User-Agent": "fitcli-ALPCD/1.0 (TP1 ALPCD 2026/27)"}
LANG_EN = 2
PAGE_SIZE = 100
TIMEOUT = 20

app = typer.Typer(help="CLI de exercicio fisico sobre a API wger.")
session = requests.Session()
session.headers.update(HEADERS)


def erro(msg: str):
    typer.secho(f"Erro: {msg}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def get_json(url: str, params: Optional[dict] = None) -> dict:
    """GET + verificacao do status code + retry se 429."""
    if not url.startswith("http"):
        url = BASE_URL + url

    for _ in range(3):
        try:
            resp = session.get(url, params=params, timeout=TIMEOUT)
        except requests.RequestException as e:
            erro(f"Falha de ligacao a API: {e}")

        if resp.status_code == 429:
            time.sleep(int(resp.headers.get("Retry-After", 2)))
            continue

        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            erro(f"A API respondeu com erro: {e}")
        return resp.json()

    erro("Demasiados pedidos (429). Tente novamente mais tarde.")


def get_all(endpoint: str, params: Optional[dict] = None) -> list:
    """Paginacao: segue o campo 'next' ate ser null."""
    params = dict(params or {})
    params.setdefault("limit", PAGE_SIZE)

    resultados = []
    pagina = get_json(endpoint, params)
    resultados.extend(pagina["results"])
    while pagina.get("next"):
        pagina = get_json(pagina["next"])
        resultados.extend(pagina["results"])
    return resultados

# ---------------------------------------------------------------------------
# Categorias e musculos (traduzir nomes -> ids)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=None)
def get_categories() -> list:
    return get_all("exercisecategory/")


@lru_cache(maxsize=None)
def get_muscles() -> list:
    return get_all("muscle/")


def resolve_term(termo: str) -> dict:
    """
    Transforma texto (ex.: 'biceps', 'chest') em ids de categorias e/ou musculos.
    1) correspondencia EXATA (ignora maiusculas)
    2) se nao houver nenhuma, correspondencia parcial
    """
    t = termo.strip().lower()
    cats, musc = [], []

    for c in get_categories():
        if c["name"].lower() == t:
            cats.append(c["id"])
    for m in get_muscles():
        if t in (m["name"].lower(), (m.get("name_en") or "").lower()):
            musc.append(m["id"])

    if not cats and not musc:
        for c in get_categories():
            if t in c["name"].lower():
                cats.append(c["id"])
        for m in get_muscles():
            if t in m["name"].lower() or t in (m.get("name_en") or "").lower():
                musc.append(m["id"])

    return {"categories": cats, "muscles": musc}


# ---------------------------------------------------------------------------
# Tratar um exercicio (JSON "em caixas" -> dicionario simples)
# ---------------------------------------------------------------------------

def english_translation(ex: dict) -> Optional[dict]:
    """Devolve a traducao em ingles (language == 2) ou None."""
    for t in ex.get("translations", []):
        if t.get("language") == LANG_EN:
            return t
    return None


def build_record(ex: dict) -> Optional[dict]:
    """Converte um exercicio da API num dicionario simples (None se nao houver ingles)."""
    tr = english_translation(ex)
    if tr is None or not tr.get("name"):
        return None
    return {
        "id": ex["id"],
        "name": tr["name"],
        "category": (ex.get("category") or {}).get("name"),
        "equipment": [e["name"] for e in ex.get("equipment", [])],
        "muscles": [m.get("name_en") or m["name"] for m in ex.get("muscles", [])],
    }


def imprimir_json(dados):
    # ensure_ascii=False mantem acentos e letras normais
    print(json.dumps(dados, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Logica das alineas a) e b)
# ---------------------------------------------------------------------------

def latest_exercises(n: int) -> list:
    """
    A API devolve os mais ANTIGOS primeiro. Por isso:
      1. pedimos 1 resultado so para saber o 'count' (total);
      2. vamos buscar as paginas do FIM (offset = count - PAGE_SIZE);
      3. ordenamos por 'created' decrescente e cortamos em N.
    """
    base = {"language": LANG_EN}
    count = get_json("exerciseinfo/", {**base, "limit": 1})["count"]

    recolhidos = []
    offset = max(count - PAGE_SIZE, 0)
    while True:
        pagina = get_json("exerciseinfo/", {**base, "limit": PAGE_SIZE, "offset": offset})
        recolhidos.extend(e for e in pagina["results"] if english_translation(e))
        if len(recolhidos) >= n or offset == 0:
            break
        offset = max(offset - PAGE_SIZE, 0)

    recolhidos.sort(key=lambda e: e["created"], reverse=True)
    return recolhidos[:n]


def search_exercises(termo: str, n: Optional[int] = None) -> list:
    """Exercicios (em ingles) de um musculo e/ou categoria."""
    ids = resolve_term(termo)
    if not ids["categories"] and not ids["muscles"]:
        erro(f"'{termo}' nao corresponde a nenhum musculo nem categoria.")

    encontrados = {}    # id -> exercicio (o dicionario evita duplicados)
    for cid in ids["categories"]:
        for ex in get_all("exerciseinfo/", {"language": LANG_EN, "category": cid}):
            if (ex.get("category") or {}).get("id") == cid:
                encontrados[ex["id"]] = ex
    for mid in ids["muscles"]:
        for ex in get_all("exerciseinfo/", {"language": LANG_EN, "muscles": mid}):
            if mid in [m["id"] for m in ex.get("muscles", [])]:
                encontrados[ex["id"]] = ex

    registos = [r for r in (build_record(e) for e in encontrados.values()) if r]
    return registos if n is None else registos[:n]

# ---------------------------------------------------------------------------
# Alinea c) - tipo de esforco (regex)
# ---------------------------------------------------------------------------

def strip_html(texto: str) -> str:
    """Remove as tags HTML (<p>, <li>...) e espacos a mais."""
    texto = re.sub(r"<[^>]+>", " ", texto or "")      # tags -> espaco
    texto = re.sub(r"&nbsp;|&amp;", " ", texto)        # entidades comuns
    return re.sub(r"\s+", " ", texto).strip()          # espacos repetidos


# Palavras no NOME que indicam exercicio isolado (um so musculo/articulacao)
ISOLATED_NAME = re.compile(
    r"\b(curls?|extensions?|raises?|flyes?|flys?|kickbacks?|crunch(es)?|"
    r"shrugs?|pushdowns?|pull-?overs?|skullcrushers?|skull crushers?|"
    r"calf|concentration|preacher|sit-?ups?)\b",
    re.IGNORECASE,
)
# Palavras no NOME que indicam exercicio composto (varias articulacoes)
COMPOUND_NAME = re.compile(
    r"\b(squats?|deadlifts?|press(es)?|rows?|pull-?ups?|chin-?ups?|dips?|"
    r"lunges?|cleans?|snatch(es)?|thrusts?|push-?ups?|burpees?|"
    r"step-?ups?|good ?mornings?)\b",
    re.IGNORECASE,
)
# Palavras no NOME que indicam cardio
CARDIO_NAME = re.compile(
    r"\b(running|run|jogging|cycling|treadmill|elliptical|jump(ing)? rope|"
    r"skipping|swimming|stair ?(climber|master)|rowing machine|jumping jacks?)\b",
    re.IGNORECASE,
)
# Frases explicitas na DESCRICAO
COMPOUND_DESC = re.compile(r"\b(compound|multi-?joint|multiple muscle)", re.IGNORECASE)
ISOLATED_DESC = re.compile(r"\b(isolat(e|es|ed|ion)|single[- ]joint)", re.IGNORECASE)


def classify_effort(ex: dict) -> str:
    """
    Devolve 'cardio', 'compound', 'isolated' ou 'other'.
    Regras, da mais forte para a mais fraca:
      1. categoria Cardio (ou nome tipico de cardio)      -> cardio
      2. palavras no nome (isolado primeiro, depois composto)
      3. frases explicitas na descricao
      4. numero de musculos envolvidos
    """
    cat = (ex.get("category") or {}).get("name", "")
    tr = english_translation(ex)
    nome = tr["name"] if tr else ""
    desc = strip_html(tr.get("description", "")) if tr else ""

    # 1. cardio
    if cat.lower() == "cardio" or CARDIO_NAME.search(nome):
        return "cardio"

    # 2. nome ("Triceps Pushdown" tem 'press' mas e isolado, por isso
    #    testamos o isolado primeiro)
    if ISOLATED_NAME.search(nome):
        return "isolated"
    if COMPOUND_NAME.search(nome):
        return "compound"

    # 3. descricao
    if COMPOUND_DESC.search(desc):
        return "compound"
    if ISOLATED_DESC.search(desc):
        return "isolated"

    # 4. numero de musculos
    principais = len(ex.get("muscles", []))
    total = principais + len(ex.get("muscles_secondary", []))
    if total == 0:
        return "other"
    if principais >= 2 or total >= 3:
        return "compound"
    return "isolated"


def exercise_type_by_id(exercise_id: int) -> str:
    """Vai buscar um exercicio pelo id e classifica-o."""
    ex = get_json(f"exerciseinfo/{exercise_id}/")
    return classify_effort(ex)

# ---------------------------------------------------------------------------
# Alinea d) - equipamento mencionado nas descricoes (regex)
# ---------------------------------------------------------------------------

# (padrao regex, nome normalizado)
EQUIPMENT_PATTERNS = [
    (r"barbells?", "barbell"),
    (r"dumb-?bells?", "dumbbell"),
    (r"cables?", "cable"),
    (r"machines?", "machine"),
    (r"kettle-?bells?", "kettlebell"),
    (r"body-?weight|body weight", "bodyweight"),
    (r"(?:resistance |exercise )?bands?", "band"),
    (r"benche?s?", "bench"),
    (r"ez[- ]?bars?|curl bars?", "ez bar"),
    (r"medicine balls?", "medicine ball"),
    (r"(?:swiss|stability|exercise) balls?", "swiss ball"),
    (r"pull-?up bars?", "pull-up bar"),
]
# \b = fronteira de palavra; IGNORECASE = ignora maiusculas
EQUIPMENT_REGEX = [
    (re.compile(rf"\b(?:{padrao})\b", re.IGNORECASE), nome)
    for padrao, nome in EQUIPMENT_PATTERNS
]


def count_equipment_in_texts(textos: list) -> list:
    """Conta mencoes de equipamento num conjunto de textos, por ordem decrescente."""
    contagem = Counter()
    for texto in textos:
        for regex, nome in EQUIPMENT_REGEX:
            contagem[nome] += len(regex.findall(texto))
    ordenado = sorted(contagem.items(), key=lambda par: par[1], reverse=True)
    return [{nome: n} for nome, n in ordenado if n > 0]


def equipment_for_category(categoria: str) -> list:
    """Le as descricoes dos exercicios de uma categoria e conta o equipamento."""
    ids = resolve_term(categoria)
    if not ids["categories"]:
        nomes = ", ".join(c["name"] for c in get_categories())
        erro(f"Categoria '{categoria}' nao existe. Categorias validas: {nomes}")

    descricoes = []
    for cid in ids["categories"]:
        for ex in get_all("exerciseinfo/", {"language": LANG_EN, "category": cid}):
            tr = english_translation(ex)
            if tr:
                descricoes.append(strip_html(tr.get("description", "")))
    return count_equipment_in_texts(descricoes)

@app.command()
def top(n: int = typer.Argument(..., min=1, help="Quantos exercicios mostrar")):
    """a) Lista os N exercicios mais recentes (JSON)."""
    registos = [build_record(e) for e in latest_exercises(n)]
    saida = [
        {"name": r["name"], "category": r["category"], "equipment": r["equipment"]}
        for r in registos if r
    ]
    imprimir_json(saida)


@app.command()
def search(
    termo: str = typer.Argument(..., help="Musculo ou categoria (ex.: biceps, chest)"),
    n: int = typer.Argument(..., min=1, help="Numero maximo de resultados"),
):
    """b) Pesquisa exercicios por musculo e/ou categoria (JSON)."""
    imprimir_json(search_exercises(termo, n))

@app.command("type")
def type_cmd(exercise_id: int = typer.Argument(..., help="ID do exercicio")):
    """c) Diz se o exercicio e compound, isolated, cardio ou other."""
    print(exercise_type_by_id(exercise_id))

@app.command()
def equipment(categoria: str = typer.Argument(..., help="Categoria (ex.: chest)")):
    """d) Conta o equipamento mencionado nas descricoes de uma categoria (JSON)."""
    imprimir_json(equipment_for_category(categoria))

if __name__ == "__main__":
    app()
