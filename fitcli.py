"""fitcli.py - CLI para consultar a API wger (exercicios fisicos)."""

import json
import re
import time
from functools import lru_cache
from typing import Optional

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


teste = 23

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

if __name__ == "__main__":
    app()