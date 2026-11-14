"""fitcli.py - CLI para consultar a API wger (exercicios fisicos)."""

import time
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

@app.callback()
def main():
    """CLI de exercicio fisico sobre a API wger."""

@app.command()
def ping():
    """Comando temporario para testar a ligacao a API."""
    dados = get_json("exerciseinfo/", {"language": LANG_EN, "limit": 1})
    print(f"Ligacao OK. A API tem {dados['count']} exercicios.")


if __name__ == "__main__":
    app()