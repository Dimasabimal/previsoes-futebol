"""Regras do jogo de previsões de ténis (sem dinheiro).
Usadas pela aplicação e pelo verificador agendado.
"""
import re

NOVOS_TIPOS_TENIS = (
    "tenis_total_sets",
    "tenis_jogador_set",
    "tenis_ambos_sets",
    "tenis_total_jogos",
)

def analisar_placar_sets(placar):
    """Devolve sets e jogos só quando há placar final completo.
    Não contabiliza encontros interrompidos, WO ou super tie-breaks ambíguos.
    """
    texto = str(placar or "").strip()
    if not texto or re.search(
        r"(?i)\b(?:ret(?:ired)?|w/?o|walkover|abn|abandoned|unfinished|"
        r"cancelled|suspended|def(?:ault)?|withdrawn|injury)\b", texto
    ):
        return None
    fichas = texto.split()
    if len(fichas) < 2 or len(fichas) > 5:
        return None
    sets = []
    jogos_total = 0
    jogos_confiaveis = True
    for ficha in fichas:
        encontrado = re.fullmatch(r"(\d{1,2})-(\d{1,2})(?:\(\d{1,2}\))?", ficha)
        if not encontrado:
            return None
        a, b = int(encontrado.group(1)), int(encontrado.group(2))
        if a == b or max(a, b) < 6:
            return None
        # Um set 10-8 pode ser um super tie-break, não 18 jogos.
        if max(a, b) >= 10:
            jogos_confiaveis = False
        sets.append((a, b))
        jogos_total += a + b
    vencedor = sum(a > b for a, b in sets)
    perdedor = sum(b > a for a, b in sets)
    if vencedor <= perdedor or vencedor not in (2, 3):
        return None
    return {
        "sets": len(sets),
        "sets_vencedor": vencedor,
        "sets_perdedor": perdedor,
        "jogos": jogos_total if jogos_confiaveis else None,
    }


def avaliar_tenis(previsao, resultado):
    """(placar, texto_resultado, acertou) ou None se não verificável."""
    tipo = previsao.get("tipo_previsao")
    valor = str(previsao.get("valor_previsao") or "")
    placar = resultado.get("placar") or ""
    vencedor = resultado.get("winner_name") or ""
    indice = resultado.get("winner_index")
    if tipo == "tenis_vencedor":
        if indice not in (0, 1) or valor not in ("Jogador1", "Jogador2"):
            return None
        return placar, vencedor, (0 if valor == "Jogador1" else 1) == indice

    dados = analisar_placar_sets(placar)
    if not dados:
        return None
    if tipo == "tenis_total_sets":
        if valor not in ("2", "3", "4", "5"):
            return None
        return placar, f"{dados['sets']} sets", int(valor) == dados["sets"]
    if tipo == "tenis_jogador_set":
        if valor not in ("Jogador1", "Jogador2") or indice not in (0, 1):
            return None
        ganhou = (dados["sets_vencedor"] if
                  (0 if valor == "Jogador1" else 1) == indice
                  else dados["sets_perdedor"]) >= 1
        nome = previsao.get("casa") if valor == "Jogador1" else previsao.get("fora")
        return placar, f"{nome}: {'ganhou' if ganhou else 'não ganhou'} um set", ganhou
    if tipo == "tenis_ambos_sets":
        if valor not in ("Sim", "Não"):
            return None
        ambos = dados["sets_perdedor"] >= 1
        return placar, "Ambos ganharam um set" if ambos else "Só um jogador ganhou sets", (valor == "Sim") == ambos
    if tipo == "tenis_total_jogos":
        total = dados["jogos"]
        if total is None or not re.fullmatch(r"\d+\+", valor):
            return None
        return placar, f"{total} jogos", total >= int(valor[:-1])
    return None
