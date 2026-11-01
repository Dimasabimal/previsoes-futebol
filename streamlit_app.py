import streamlit as st
import requests
import re
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict
from st_cookie_manager import EncryptedCookieManager

# =========================================================
# CONFIGURAÇÃO
# =========================================================

st.set_page_config(
    page_title="Previsões de Futebol",
    page_icon="⚽",
    layout="wide"
)

SUPABASE_URL = st.secrets["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
SUPABASE_SECRET_KEY = st.secrets["SUPABASE_SECRET_KEY"]
FOOTBALL_API_KEY = st.secrets["FOOTBALL_API_KEY"]

# Cookie encriptado para manter a sessão iniciada entre visitas.
# A chave privada do servidor é usada apenas como palavra-passe de encriptação.
cookies = EncryptedCookieManager(
    prefix="previsoes_futebol/",
    password=SUPABASE_SECRET_KEY,
)

if not cookies.ready():
    st.stop()

TZ = ZoneInfo("Europe/Lisbon")

LIGAS = {
    "Liga Portuguesa": "PPL",
    "Premier League": "PL",
    "La Liga": "PD",
    "Serie A": "SA",
    "Bundesliga": "BL1",
    "Champions League": "CL",
}

NOMES_TIPOS = {
    "resultado": "Resultado do jogo",
    "golos_tempo_regulamentar": "Golos — tempo regulamentar",
    "golos_intervalo": "Golos — intervalo",
    "golos_equipa_casa": "Golos — equipa da casa",
    "golos_equipa_fora": "Golos — equipa visitante",
    "ambas_marcam": "Ambas as equipas marcam",
    "dupla_possibilidade": "Dupla possibilidade",
}

MINIMO_RANKING_PRECISAO = 10


# =========================================================
# FUNÇÕES GERAIS
# =========================================================

def limpar_username(username):
    username = username.strip().lower()
    username = unicodedata.normalize("NFKD", username)
    username = "".join(
        c for c in username
        if not unicodedata.combining(c)
    )
    return re.sub(r"[^a-z0-9_]", "", username)


def username_para_email(username):
    return f"{limpar_username(username)}@users.example.com"


def parse_data_jogo(valor):
    if not valor:
        return None
    try:
        return datetime.strptime(
            valor,
            "%d/%m/%Y %H:%M"
        ).replace(tzinfo=TZ)
    except Exception:
        return None


def parse_created_at(valor):
    if not valor:
        return None
    try:
        return datetime.fromisoformat(
            valor.replace("Z", "+00:00")
        ).astimezone(TZ)
    except Exception:
        return None


def jogo_comecou(previsao_ou_jogo):
    data = parse_data_jogo(
        previsao_ou_jogo.get("data")
    )
    if data is None:
        return False
    return datetime.now(TZ) >= data


def calcular_pontos(previsao):
    resultado = previsao.get("resultado", "Pendente")
    try:
        confianca = int(previsao.get("confianca", 0) or 0)
    except Exception:
        confianca = 0

    try:
        multiplicador = float(previsao.get("multiplicador", 1) or 1)
    except Exception:
        multiplicador = 1.0

    if resultado == "Acertou":
        return confianca * multiplicador
    if resultado == "Errou":
        return -confianca
    return 0


def formatar_pontos(valor):
    try:
        numero = float(valor)
    except Exception:
        numero = 0.0

    if numero.is_integer():
        return f"{int(numero):+d}"
    return f"{numero:+.1f}"


def inicio_semana(agora=None):
    agora = agora or datetime.now(TZ)
    base = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    return base - timedelta(days=base.weekday())


def filtrar_periodo(lista, periodo):
    agora = datetime.now(TZ)
    if periodo == "Total":
        return list(lista)

    if periodo == "Esta semana":
        inicio = inicio_semana(agora)
        return [p for p in lista if (parse_data_jogo(p.get("data")) or parse_created_at(p.get("created_at"))) and (parse_data_jogo(p.get("data")) or parse_created_at(p.get("created_at"))) >= inicio]

    if periodo == "Este mês":
        inicio = agora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return [p for p in lista if (parse_data_jogo(p.get("data")) or parse_created_at(p.get("created_at"))) and (parse_data_jogo(p.get("data")) or parse_created_at(p.get("created_at"))) >= inicio]

    return list(lista)


def valor_minimo_golos(valor):
    if valor == "0":
        return 0
    if isinstance(valor, str) and valor.endswith("+"):
        try:
            return int(valor[:-1])
        except Exception:
            return None
    return None


def verificar_meta_golos(golos, valor):
    if golos is None:
        return None
    if valor == "0":
        return golos == 0
    minimo = valor_minimo_golos(valor)
    if minimo is not None:
        return golos >= minimo
    return False


def categoria_estatistica(tipo):
    if tipo in [
        "golos_equipa_casa",
        "golos_equipa_fora",
    ]:
        return "Golos — equipa"
    return NOMES_TIPOS.get(tipo, tipo or "Sem tipo")


def estatisticas_basicas(lista):
    acertos = sum(
        1 for p in lista
        if p.get("resultado") == "Acertou"
    )
    erros = sum(
        1 for p in lista
        if p.get("resultado") == "Errou"
    )
    pendentes = sum(
        1 for p in lista
        if p.get("resultado", "Pendente") == "Pendente"
    )
    concluidas = acertos + erros
    taxa = (
        acertos / concluidas * 100
        if concluidas else 0
    )
    pontos = sum(calcular_pontos(p) for p in lista)
    return {
        "acertos": acertos,
        "erros": erros,
        "pendentes": pendentes,
        "concluidas": concluidas,
        "taxa": taxa,
        "pontos": pontos,
        "total": len(lista),
    }


def sequencias(lista):
    concluidas = [
        p for p in lista
        if p.get("resultado") in ["Acertou", "Errou"]
    ]
    concluidas.sort(
        key=lambda p: parse_created_at(
            p.get("created_at")
        ) or datetime.min.replace(tzinfo=TZ)
    )

    atual = 0
    melhor = 0

    for p in concluidas:
        if p.get("resultado") == "Acertou":
            atual += 1
            melhor = max(melhor, atual)
        else:
            atual = 0

    return atual, melhor


def melhor_categoria(lista):
    grupos = defaultdict(list)
    for p in lista:
        if p.get("resultado") in ["Acertou", "Errou"]:
            grupos[categoria_estatistica(
                p.get("tipo_previsao")
            )].append(p)

    ranking = []
    for nome, itens in grupos.items():
        e = estatisticas_basicas(itens)
        ranking.append((e["taxa"], e["concluidas"], nome))
    ranking.sort(reverse=True)

    return ranking[0][2] if ranking else "—"


def melhor_liga(lista):
    grupos = defaultdict(list)
    for p in lista:
        if p.get("resultado") in ["Acertou", "Errou"]:
            grupos[p.get("liga", "Sem liga")].append(p)

    ranking = []
    for nome, itens in grupos.items():
        e = estatisticas_basicas(itens)
        ranking.append((e["taxa"], e["concluidas"], nome))
    ranking.sort(reverse=True)

    return ranking[0][2] if ranking else "—"


def equipa_associada_previsao(p):
    """Devolve apenas a equipa realmente associada à previsão."""
    tipo = p.get("tipo_previsao")
    valor = p.get("valor_previsao")

    if tipo == "resultado":
        if valor == "Casa":
            return p.get("casa")
        if valor == "Fora":
            return p.get("fora")
        return None

    if tipo == "dupla_possibilidade":
        if valor == "CasaEmpate":
            return p.get("casa")
        if valor == "ForaEmpate":
            return p.get("fora")
        return None

    if tipo == "golos_equipa_casa":
        return p.get("equipa_previsao") or p.get("casa")

    if tipo == "golos_equipa_fora":
        return p.get("equipa_previsao") or p.get("fora")

    return None


def melhor_equipa(lista):
    grupos = defaultdict(list)

    for p in lista:
        if p.get("resultado") not in ["Acertou", "Errou"]:
            continue

        equipa = equipa_associada_previsao(p)
        if equipa:
            grupos[equipa].append(p)

    ranking = []
    for nome, itens in grupos.items():
        e = estatisticas_basicas(itens)
        ranking.append((e["taxa"], e["concluidas"], nome))

    ranking.sort(reverse=True)
    return ranking[0][2] if ranking else "—"


# =========================================================
# AUTENTICAÇÃO
# =========================================================

def criar_conta(username, password):
    url = f"{SUPABASE_URL}/auth/v1/signup"
    headers = {
        "apikey": SUPABASE_KEY,
        "Content-Type": "application/json",
    }
    dados = {
        "email": username_para_email(username),
        "password": password,
        "data": {
            "nome": username,
            "username": limpar_username(username),
        },
    }
    try:
        return requests.post(
            url,
            headers=headers,
            json=dados,
            timeout=20,
        )
    except requests.RequestException as erro:
        st.error(f"Erro de ligação: {erro}")
        return None


def fazer_login(username, password):
    url = (
        f"{SUPABASE_URL}/auth/v1/token"
        "?grant_type=password"
    )
    headers = {
        "apikey": SUPABASE_KEY,
        "Content-Type": "application/json",
    }
    dados = {
        "email": username_para_email(username),
        "password": password,
    }
    try:
        return requests.post(
            url,
            headers=headers,
            json=dados,
            timeout=20,
        )
    except requests.RequestException as erro:
        st.error(f"Erro de ligação: {erro}")
        return None


def fazer_refresh(refresh_token):
    url = (
        f"{SUPABASE_URL}/auth/v1/token"
        "?grant_type=refresh_token"
    )
    headers = {
        "apikey": SUPABASE_KEY,
        "Content-Type": "application/json",
    }
    try:
        return requests.post(
            url,
            headers=headers,
            json={"refresh_token": refresh_token},
            timeout=20,
        )
    except requests.RequestException:
        return None


def aplicar_sessao(dados, nome_fallback=None):
    user = dados.get("user") or {}
    metadata = user.get("user_metadata", {}) or {}

    st.session_state["access_token"] = dados["access_token"]
    st.session_state["expires_at"] = dados.get("expires_at")
    st.session_state["user_id"] = user.get("id")
    st.session_state["nome"] = (
        metadata.get("nome")
        or metadata.get("username")
        or nome_fallback
        or "Utilizador"
    )

    refresh_token = dados.get("refresh_token")
    if refresh_token:
        cookies["refresh_token"] = refresh_token
        cookies.save()


def tentar_restaurar_sessao():
    if "access_token" in st.session_state:
        return True

    refresh_token = cookies.get("refresh_token")
    if not refresh_token:
        return False

    r = fazer_refresh(refresh_token)

    if r is not None and r.status_code == 200:
        aplicar_sessao(r.json())
        return True

    if "refresh_token" in cookies:
        del cookies["refresh_token"]
        cookies.save()

    return False


def garantir_sessao_valida():
    if "access_token" not in st.session_state:
        return tentar_restaurar_sessao()

    expires_at = st.session_state.get("expires_at")
    if not expires_at:
        return True

    agora_ts = datetime.now(ZoneInfo("UTC")).timestamp()

    # Renova dois minutos antes do fim do token de acesso.
    if agora_ts < float(expires_at) - 120:
        return True

    refresh_token = cookies.get("refresh_token")
    if not refresh_token:
        return False

    r = fazer_refresh(refresh_token)
    if r is not None and r.status_code == 200:
        aplicar_sessao(r.json())
        return True

    return False


def terminar_sessao():
    if "refresh_token" in cookies:
        del cookies["refresh_token"]
        cookies.save()

    for chave in list(st.session_state.keys()):
        st.session_state.pop(chave, None)

    st.rerun()


# =========================================================
# SUPABASE
# =========================================================

def headers_utilizador():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": (
            f"Bearer {st.session_state['access_token']}"
        ),
        "Content-Type": "application/json",
    }


def headers_admin():
    return {
        "apikey": SUPABASE_SECRET_KEY,
        "Content-Type": "application/json",
    }


def carregar_previsoes():
    url = (
        f"{SUPABASE_URL}/rest/v1/previsoes"
        "?select=*"
        "&order=created_at.desc"
    )
    try:
        r = requests.get(
            url,
            headers=headers_utilizador(),
            timeout=20,
        )
        if r.status_code == 200:
            return r.json()
        st.error(
            f"Erro ao carregar previsões: {r.status_code}"
        )
    except requests.RequestException as erro:
        st.error(f"Erro ao carregar dados: {erro}")
    return []


def guardar_previsao(registo):
    url = f"{SUPABASE_URL}/rest/v1/previsoes"
    headers = headers_utilizador()
    headers["Prefer"] = "return=representation"
    try:
        return requests.post(
            url,
            headers=headers,
            json=registo,
            timeout=20,
        )
    except requests.RequestException as erro:
        st.error(f"Erro ao guardar: {erro}")
        return None


def atualizar_previsao_utilizador(previsao_id, dados):
    url = (
        f"{SUPABASE_URL}/rest/v1/previsoes"
        f"?id=eq.{previsao_id}"
    )
    headers = headers_utilizador()
    headers["Prefer"] = "return=minimal"
    try:
        return requests.patch(
            url,
            headers=headers,
            json=dados,
            timeout=20,
        )
    except requests.RequestException:
        return None


def apagar_previsao_utilizador(previsao_id):
    url = (
        f"{SUPABASE_URL}/rest/v1/previsoes"
        f"?id=eq.{previsao_id}"
    )
    headers = headers_utilizador()
    headers["Prefer"] = "return=minimal"
    try:
        return requests.delete(
            url,
            headers=headers,
            timeout=20,
        )
    except requests.RequestException:
        return None


# =========================================================
# API DE FUTEBOL
# =========================================================

def buscar_jogos(nome_liga):
    codigo = LIGAS[nome_liga]
    url = (
        "https://api.football-data.org/v4/"
        f"competitions/{codigo}/matches"
    )
    headers = {"X-Auth-Token": FOOTBALL_API_KEY}

    try:
        r = requests.get(
            url,
            headers=headers,
            timeout=20,
        )
    except requests.RequestException as erro:
        return [], f"Erro de ligação: {erro}"

    if r.status_code != 200:
        return [], f"Erro da API: {r.status_code}"

    agora = datetime.now(TZ)
    jogos = []

    for partida in r.json().get("matches", []):
        if partida.get("status") not in [
            "SCHEDULED",
            "TIMED",
        ]:
            continue

        data_utc = datetime.strptime(
            partida["utcDate"],
            "%Y-%m-%dT%H:%M:%SZ",
        ).replace(tzinfo=ZoneInfo("UTC"))

        data_pt = data_utc.astimezone(TZ)

        if data_pt <= agora:
            continue

        casa = partida.get(
            "homeTeam", {}
        ).get("name", "Casa")

        fora = partida.get(
            "awayTeam", {}
        ).get("name", "Fora")

        jogos.append({
            "match_id": partida["id"],
            "jogo": f"{casa} vs {fora}",
            "casa": casa,
            "fora": fora,
            "liga": nome_liga,
            "data": data_pt.strftime(
                "%d/%m/%Y %H:%M"
            ),
            "data_obj": data_pt,
        })

    jogos.sort(key=lambda x: x["data_obj"])

    for jogo in jogos:
        jogo.pop("data_obj", None)

    return jogos[:40], None


def buscar_classificacao(nome_liga):
    codigo = LIGAS[nome_liga]
    url = (
        "https://api.football-data.org/v4/"
        f"competitions/{codigo}/standings"
    )
    headers = {"X-Auth-Token": FOOTBALL_API_KEY}

    try:
        r = requests.get(url, headers=headers, timeout=20)
    except requests.RequestException:
        return {}

    if r.status_code != 200:
        return {}

    posicoes = {}
    for bloco in r.json().get("standings", []):
        if bloco.get("type") not in ["TOTAL", None]:
            continue
        for linha in bloco.get("table", []):
            equipa = linha.get("team", {}).get("name")
            posicao = linha.get("position")
            if equipa and posicao:
                posicoes[equipa] = int(posicao)
        if posicoes:
            break

    return posicoes


def dificuldade_previsao(tipo, valor, jogo, posicoes):
    # Para mercados que não representam diretamente a força de uma equipa,
    # usamos dificuldade neutra.
    if tipo not in ["resultado", "dupla_possibilidade"]:
        return "Fácil", 1.0

    casa = jogo.get("casa")
    fora = jogo.get("fora")
    pc = posicoes.get(casa)
    pf = posicoes.get(fora)

    if tipo == "resultado" and valor == "Empate":
        return "Difícil", 2.0

    if tipo == "dupla_possibilidade" and valor == "CasaFora":
        return "Média", 1.5

    # Se não houver classificação disponível, mantemos uma dificuldade neutra.
    if not pc or not pf:
        return "Média", 1.5

    if tipo == "resultado":
        if valor == "Casa":
            delta = pc - pf
        else:
            delta = pf - pc

        if delta <= -5:
            return "Fácil", 1.0
        if delta <= 1:
            return "Média", 1.5
        if delta <= 6:
            return "Difícil", 2.0
        return "Muito difícil", 3.0

    # Dupla possibilidade: lado + empate.
    if valor == "CasaEmpate":
        delta = pc - pf
    else:
        delta = pf - pc

    if delta <= -5:
        return "Fácil", 1.0
    if delta <= 2:
        return "Média", 1.5
    return "Difícil", 2.0


# =========================================================
# RESULTADOS AUTOMÁTICOS
# =========================================================

def carregar_pendentes_admin():
    url = (
        f"{SUPABASE_URL}/rest/v1/previsoes"
        "?select=*"
        "&resultado=eq.Pendente"
    )
    try:
        r = requests.get(
            url,
            headers=headers_admin(),
            timeout=20,
        )
        if r.status_code == 200:
            return r.json()
    except requests.RequestException:
        pass
    return []


def buscar_resultados_api(match_ids):
    resultados = {}
    headers = {"X-Auth-Token": FOOTBALL_API_KEY}

    for match_id in match_ids:
        url = (
            "https://api.football-data.org/"
            f"v4/matches/{match_id}"
        )
        try:
            r = requests.get(
                url,
                headers=headers,
                timeout=20,
            )
            if r.status_code == 200:
                partida = r.json()
                resultados[partida["id"]] = partida
        except requests.RequestException:
            continue

    return resultados


def atualizar_previsao_admin(
    previsao_id,
    placar,
    resultado_real,
    resultado,
):
    url = (
        f"{SUPABASE_URL}/rest/v1/previsoes"
        f"?id=eq.{previsao_id}"
    )
    headers = headers_admin()
    headers["Prefer"] = "return=minimal"
    dados = {
        "placar": placar,
        "resultado_real": resultado_real,
        "resultado": resultado,
    }
    try:
        return requests.patch(
            url,
            headers=headers,
            json=dados,
            timeout=20,
        )
    except requests.RequestException:
        return None


def obter_golos_regulamentares(partida):
    score = partida.get("score", {})

    regular = score.get("regularTime", {}) or {}
    casa = regular.get("home")
    fora = regular.get("away")

    if casa is not None and fora is not None:
        return casa, fora

    full = score.get("fullTime", {}) or {}
    return full.get("home"), full.get("away")


def obter_golos_intervalo(partida):
    intervalo = (
        partida.get("score", {})
        .get("halfTime", {})
        or {}
    )
    return (
        intervalo.get("home"),
        intervalo.get("away"),
    )


def avaliar_previsao(previsao, partida):
    tipo = previsao.get("tipo_previsao")
    valor = previsao.get("valor_previsao")

    if not tipo:
        antiga = previsao.get("previsao")
        if antiga in ["Casa", "Empate", "Fora"]:
            tipo = "resultado"
            valor = antiga
        else:
            return None

    golos_casa, golos_fora = (
        obter_golos_regulamentares(partida)
    )

    if golos_casa is None or golos_fora is None:
        return None

    placar = f"{golos_casa}-{golos_fora}"

    if tipo == "resultado":
        if golos_casa > golos_fora:
            real = "Casa"
        elif golos_fora > golos_casa:
            real = "Fora"
        else:
            real = "Empate"

        return placar, real, valor == real

    if tipo == "dupla_possibilidade":
        if golos_casa > golos_fora:
            real = "Casa"
        elif golos_fora > golos_casa:
            real = "Fora"
        else:
            real = "Empate"

        acertou = (
            (valor == "CasaEmpate" and real in ["Casa", "Empate"])
            or (valor == "ForaEmpate" and real in ["Fora", "Empate"])
            or (valor == "CasaFora" and real in ["Casa", "Fora"])
        )
        return placar, real, acertou

    if tipo == "golos_tempo_regulamentar":
        total = golos_casa + golos_fora
        return (
            placar,
            f"{total} golos",
            verificar_meta_golos(total, valor),
        )

    if tipo == "golos_intervalo":
        casa_int, fora_int = obter_golos_intervalo(
            partida
        )
        if casa_int is None or fora_int is None:
            return None

        total = casa_int + fora_int
        return (
            placar,
            f"{total} golos ao intervalo",
            verificar_meta_golos(total, valor),
        )

    if tipo == "golos_equipa_casa":
        return (
            placar,
            f"{golos_casa} golos",
            verificar_meta_golos(golos_casa, valor),
        )

    if tipo == "golos_equipa_fora":
        return (
            placar,
            f"{golos_fora} golos",
            verificar_meta_golos(golos_fora, valor),
        )

    if tipo == "ambas_marcam":
        real = (
            "Sim"
            if golos_casa >= 1 and golos_fora >= 1
            else "Não"
        )
        return placar, real, valor == real

    return None


def atualizar_resultados_automaticamente():
    pendentes = carregar_pendentes_admin()
    if not pendentes:
        return 0

    match_ids = list({
        p["match_id"]
        for p in pendentes
        if p.get("match_id") is not None
    })

    jogos_api = buscar_resultados_api(match_ids)
    atualizados = 0

    for previsao in pendentes:
        partida = jogos_api.get(
            previsao.get("match_id")
        )

        if not partida:
            continue

        if partida.get("status") != "FINISHED":
            continue

        avaliacao = avaliar_previsao(
            previsao,
            partida,
        )

        if avaliacao is None:
            continue

        placar, resultado_real, acertou = avaliacao

        r = atualizar_previsao_admin(
            previsao["id"],
            placar,
            resultado_real,
            "Acertou" if acertou else "Errou",
        )

        if (
            r is not None
            and r.status_code in [200, 204]
        ):
            atualizados += 1

    return atualizados


# =========================================================
# CONTRADIÇÕES
# =========================================================

def verificar_contradicao(
    existentes,
    novo_tipo,
    novo_valor,
):
    for p in existentes:
        tipo = p.get("tipo_previsao")
        valor = p.get("valor_previsao")

        if (
            novo_tipo == "golos_tempo_regulamentar"
            and novo_valor == "0"
        ):
            if (
                tipo == "resultado"
                and valor in ["Casa", "Fora"]
            ):
                return True, (
                    "0 golos contradiz uma previsão "
                    "de vitória."
                )

            if (
                tipo == "golos_intervalo"
                and (valor_minimo_golos(valor) or 0) >= 1
            ):
                return True, (
                    "0 golos no jogo contradiz golos "
                    "previstos ao intervalo."
                )

            if (
                tipo in [
                    "golos_equipa_casa",
                    "golos_equipa_fora",
                ]
                and (valor_minimo_golos(valor) or 0) >= 1
            ):
                return True, (
                    "0 golos no jogo contradiz golos "
                    "previstos para uma equipa."
                )

            if tipo == "ambas_marcam" and valor == "Sim":
                return True, (
                    "0 golos contradiz "
                    "'Ambas marcam: Sim'."
                )

        if (
            novo_tipo == "resultado"
            and novo_valor in ["Casa", "Fora"]
            and tipo == "golos_tempo_regulamentar"
            and valor == "0"
        ):
            return True, (
                "Uma equipa não pode vencer "
                "com 0 golos no jogo."
            )

        if (
            novo_tipo == "golos_intervalo"
            and (valor_minimo_golos(novo_valor) or 0) >= 1
            and tipo == "golos_tempo_regulamentar"
            and valor == "0"
        ):
            return True, (
                "Não podes prever golos ao intervalo "
                "com 0 golos no jogo."
            )

        if (
            novo_tipo in [
                "golos_equipa_casa",
                "golos_equipa_fora",
            ]
            and (valor_minimo_golos(novo_valor) or 0) >= 1
            and tipo == "golos_tempo_regulamentar"
            and valor == "0"
        ):
            return True, (
                "Não podes prever golos para uma equipa "
                "com 0 golos no jogo."
            )

        if (
            novo_tipo == "ambas_marcam"
            and novo_valor == "Sim"
        ):
            if (
                tipo == "golos_tempo_regulamentar"
                and valor == "0"
            ):
                return True, (
                    "'Ambas marcam: Sim' contradiz "
                    "0 golos no jogo."
                )

            if (
                tipo in [
                    "golos_equipa_casa",
                    "golos_equipa_fora",
                ]
                and valor == "0"
            ):
                return True, (
                    "'Ambas marcam: Sim' contradiz "
                    "0 golos para uma equipa."
                )

        if (
            novo_tipo in [
                "golos_equipa_casa",
                "golos_equipa_fora",
            ]
            and novo_valor == "0"
            and tipo == "ambas_marcam"
            and valor == "Sim"
        ):
            return True, (
                "0 golos para uma equipa contradiz "
                "'Ambas marcam: Sim'."
            )

        # Resultado exato vs dupla possibilidade incompatível.
        if novo_tipo == "resultado":
            if novo_valor == "Casa" and tipo == "dupla_possibilidade" and valor == "ForaEmpate":
                return True, "'Casa vence' contradiz 'Fora ou Empate'."
            if novo_valor == "Fora" and tipo == "dupla_possibilidade" and valor == "CasaEmpate":
                return True, "'Fora vence' contradiz 'Casa ou Empate'."
            if novo_valor == "Empate" and tipo == "dupla_possibilidade" and valor == "CasaFora":
                return True, "'Empate' contradiz 'Casa ou Fora'."

        if novo_tipo == "dupla_possibilidade":
            if novo_valor == "CasaEmpate" and tipo == "resultado" and valor == "Fora":
                return True, "'Casa ou Empate' contradiz 'Fora vence'."
            if novo_valor == "ForaEmpate" and tipo == "resultado" and valor == "Casa":
                return True, "'Fora ou Empate' contradiz 'Casa vence'."
            if novo_valor == "CasaFora" and tipo == "resultado" and valor == "Empate":
                return True, "'Casa ou Fora' contradiz 'Empate'."
            if novo_valor == "CasaFora" and tipo == "golos_tempo_regulamentar" and valor == "0":
                return True, "'Casa ou Fora' contradiz 0 golos no jogo."

        if novo_tipo == "golos_tempo_regulamentar" and novo_valor == "0":
            if tipo == "dupla_possibilidade" and valor == "CasaFora":
                return True, "0 golos no jogo contradiz 'Casa ou Fora'."

    return False, ""


# =========================================================
# OPÇÕES DE PREVISÃO
# =========================================================

def opcoes_para_tipo(tipo, jogo):
    if tipo == "resultado":
        return {
            f"{jogo['casa']} vence": "Casa",
            "Empate": "Empate",
            f"{jogo['fora']} vence": "Fora",
        }

    if tipo == "dupla_possibilidade":
        return {
            f"{jogo['casa']} ou Empate": "CasaEmpate",
            f"{jogo['fora']} ou Empate": "ForaEmpate",
            f"{jogo['casa']} ou {jogo['fora']}": "CasaFora",
        }

    if tipo == "golos_tempo_regulamentar":
        return {
            "0 golos": "0",
            "1 ou mais": "1+",
            "2 ou mais": "2+",
            "3 ou mais": "3+",
            "4 ou mais": "4+",
            "5 ou mais": "5+",
        }

    if tipo == "golos_intervalo":
        return {
            "0 golos": "0",
            "1 ou mais": "1+",
            "2 ou mais": "2+",
            "3 ou mais": "3+",
        }

    if tipo in [
        "golos_equipa_casa",
        "golos_equipa_fora",
    ]:
        return {
            "0 golos": "0",
            "1 ou mais": "1+",
            "2 ou mais": "2+",
            "3 ou mais": "3+",
            "4 ou mais": "4+",
        }

    if tipo == "ambas_marcam":
        return {"Sim": "Sim", "Não": "Não"}

    return {}


def texto_previsao(tipo, valor, jogo):
    if tipo == "resultado":
        if valor == "Casa":
            return f"{jogo['casa']} vence"
        if valor == "Fora":
            return f"{jogo['fora']} vence"
        return "Empate"

    if tipo == "dupla_possibilidade":
        if valor == "CasaEmpate":
            return f"{jogo['casa']} ou Empate"
        if valor == "ForaEmpate":
            return f"{jogo['fora']} ou Empate"
        return f"{jogo['casa']} ou {jogo['fora']}"

    if tipo == "golos_tempo_regulamentar":
        nome = next(
            (
                k for k, v in opcoes_para_tipo(
                    tipo, jogo
                ).items()
                if v == valor
            ),
            valor,
        )
        return f"Tempo regulamentar: {nome}"

    if tipo == "golos_intervalo":
        nome = next(
            (
                k for k, v in opcoes_para_tipo(
                    tipo, jogo
                ).items()
                if v == valor
            ),
            valor,
        )
        return f"Intervalo: {nome}"

    if tipo == "golos_equipa_casa":
        nome = next(
            (
                k for k, v in opcoes_para_tipo(
                    tipo, jogo
                ).items()
                if v == valor
            ),
            valor,
        )
        return f"{jogo['casa']}: {nome}"

    if tipo == "golos_equipa_fora":
        nome = next(
            (
                k for k, v in opcoes_para_tipo(
                    tipo, jogo
                ).items()
                if v == valor
            ),
            valor,
        )
        return f"{jogo['fora']}: {nome}"

    if tipo == "ambas_marcam":
        return f"Ambas as equipas marcam: {valor}"

    return str(valor)


def linhas_desempenho_equipa(lista):
    grupos = defaultdict(list)

    for p in lista:
        equipa = equipa_associada_previsao(p)
        if equipa:
            grupos[equipa].append(p)

    linhas = []
    for nome, itens in grupos.items():
        e = estatisticas_basicas(itens)
        if e["concluidas"] == 0:
            continue
        linhas.append({
            "Equipa": nome,
            "Taxa": f"{e['taxa']:.1f}%",
            "Concluídas": e["concluidas"],
            "Pontos": e["pontos"],
            "_taxa": e["taxa"],
        })

    linhas.sort(
        key=lambda x: (x["_taxa"], x["Concluídas"]),
        reverse=True,
    )

    for x in linhas:
        x.pop("_taxa", None)

    return linhas


def mostrar_estatisticas_completas(lista, prefixo=""):
    stats = estatisticas_basicas(lista)
    streak_atual, melhor_streak = sequencias(lista)

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Previsões", stats["total"])
    m2.metric("Acertos", stats["acertos"])
    m3.metric("Erros", stats["erros"])
    m4.metric("Pendentes", stats["pendentes"])
    m5.metric("Taxa", f"{stats['taxa']:.1f}%")
    m6.metric("Pontos", formatar_pontos(stats["pontos"]))

    st.subheader("Resumo do perfil")
    r1, r2, r3, r4, r5 = st.columns(5)
    r1.metric("Melhor tipo", melhor_categoria(lista))
    r2.metric("Melhor liga", melhor_liga(lista))
    r3.metric("Melhor equipa", melhor_equipa(lista))
    r4.metric("Sequência atual", streak_atual)
    r5.metric("Melhor sequência", melhor_streak)

    st.subheader("🧠 Desempenho por confiança")
    linhas_conf = []
    for nivel in range(5, 0, -1):
        itens = [
            p for p in lista
            if int(p.get("confianca", 0) or 0) == nivel
            and p.get("resultado") in ["Acertou", "Errou"]
        ]
        if not itens:
            continue
        e = estatisticas_basicas(itens)
        linhas_conf.append({
            "Confiança": f"{nivel}/5",
            "Taxa de acerto": f"{e['taxa']:.1f}%",
            "Acertos": e["acertos"],
            "Concluídas": e["concluidas"],
            "Pontos": e["pontos"],
        })

    if linhas_conf:
        st.dataframe(linhas_conf, hide_index=True, use_container_width=True)
    else:
        st.info("Ainda sem previsões concluídas.")

    st.subheader("🎯 Desempenho por tipo")
    grupos_tipo = defaultdict(list)
    for p in lista:
        grupos_tipo[categoria_estatistica(p.get("tipo_previsao"))].append(p)

    linhas_tipo = []
    for nome, itens in grupos_tipo.items():
        e = estatisticas_basicas(itens)
        linhas_tipo.append({
            "Tipo": nome,
            "Taxa": f"{e['taxa']:.1f}%",
            "Acertos": e["acertos"],
            "Erros": e["erros"],
            "Pendentes": e["pendentes"],
            "Pontos": e["pontos"],
            "_taxa": e["taxa"],
        })
    linhas_tipo.sort(key=lambda x: x["_taxa"], reverse=True)
    for x in linhas_tipo:
        x.pop("_taxa", None)
    if linhas_tipo:
        st.dataframe(linhas_tipo, hide_index=True, use_container_width=True)

    st.subheader("🏟️ Desempenho por liga")
    grupos_liga = defaultdict(list)
    for p in lista:
        grupos_liga[p.get("liga", "Sem liga")].append(p)

    linhas_liga = []
    for nome, itens in grupos_liga.items():
        e = estatisticas_basicas(itens)
        linhas_liga.append({
            "Liga": nome,
            "Taxa": f"{e['taxa']:.1f}%",
            "Acertos": e["acertos"],
            "Concluídas": e["concluidas"],
            "Pontos": e["pontos"],
            "_taxa": e["taxa"],
        })
    linhas_liga.sort(key=lambda x: x["_taxa"], reverse=True)
    for x in linhas_liga:
        x.pop("_taxa", None)
    if linhas_liga:
        st.dataframe(linhas_liga, hide_index=True, use_container_width=True)

    st.subheader("👕 Desempenho por equipa")
    linhas_equipa = linhas_desempenho_equipa(lista)
    if linhas_equipa:
        st.dataframe(
            linhas_equipa[:15],
            hide_index=True,
            use_container_width=True,
        )
    else:
        st.caption("Ainda não existem previsões suficientes associadas a uma equipa específica.")

    st.subheader("📅 Histórico mensal")
    meses = defaultdict(list)
    for p in lista:
        criado = parse_created_at(p.get("created_at"))
        if criado:
            meses[criado.strftime("%Y-%m")].append(p)

    historico = []
    for mes in sorted(meses):
        e = estatisticas_basicas(meses[mes])
        historico.append({
            "Mês": mes,
            "Taxa": e["taxa"],
            "Pontos": e["pontos"],
            "Concluídas": e["concluidas"],
        })

    if historico:
        st.dataframe(
            [
                {
                    "Mês": x["Mês"],
                    "Taxa": f"{x['Taxa']:.1f}%",
                    "Pontos": x["Pontos"],
                    "Concluídas": x["Concluídas"],
                }
                for x in historico
            ],
            hide_index=True,
            use_container_width=True,
        )
        st.line_chart({x["Mês"]: x["Taxa"] for x in historico})

    st.subheader("⏱️ Forma recente")
    agora = datetime.now(TZ)
    for dias in [7, 30]:
        itens = []
        for p in lista:
            criado = parse_created_at(p.get("created_at"))
            if criado and criado >= agora - timedelta(days=dias):
                itens.append(p)
        e = estatisticas_basicas(itens)
        st.write(
            f"**Últimos {dias} dias:** "
            f"{e['taxa']:.1f}% • {formatar_pontos(e['pontos'])} pontos • "
            f"{e['concluidas']} concluídas"
        )

    st.subheader("🏅 Recordes pessoais")
    melhor_mes = "—"
    if historico:
        info = max(historico, key=lambda x: (x["Taxa"], x["Concluídas"]))
        melhor_mes = f"{info['Mês']} ({info['Taxa']:.1f}%)"

    fins_semana = defaultdict(list)
    for p in lista:
        criado = parse_created_at(p.get("created_at"))
        if criado and criado.weekday() in [5, 6]:
            chave = (criado.isocalendar().year, criado.isocalendar().week)
            fins_semana[chave].append(p)

    melhor_fds = 0
    for itens in fins_semana.values():
        melhor_fds = max(melhor_fds, sum(calcular_pontos(p) for p in itens))

    st.write(f"**Melhor sequência:** {melhor_streak}")
    st.write(f"**Melhor mês:** {melhor_mes}")
    st.write(f"**Melhor fim de semana:** {melhor_fds:+d} pontos")
    st.write(f"**Melhor liga:** {melhor_liga(lista)}")
    st.write(f"**Melhor categoria:** {melhor_categoria(lista)}")


# =========================================================
# INTERFACE — LOGIN
# =========================================================

st.title("⚽ Previsões de Futebol")
st.caption("Jogo de previsões entre amigos.")

# Recupera e renova automaticamente a sessão guardada no navegador.
garantir_sessao_valida()

if "access_token" not in st.session_state:
    tab_login, tab_criar = st.tabs([
        "Entrar",
        "Criar conta",
    ])

    with tab_login:
        username_login = st.text_input(
            "Nome de utilizador",
            key="username_login",
        )
        password_login = st.text_input(
            "Palavra-passe",
            type="password",
            key="password_login",
        )

        if st.button(
            "Entrar",
            type="primary",
        ):
            if not limpar_username(
                username_login
            ) or not password_login:
                st.warning("Preenche os dois campos.")
            else:
                r = fazer_login(
                    username_login,
                    password_login,
                )

                if (
                    r is not None
                    and r.status_code == 200
                ):
                    dados = r.json()
                    user = dados["user"]

                    aplicar_sessao(
                        dados,
                        nome_fallback=username_login,
                    )
                    st.rerun()
                else:
                    st.error(
                        "Nome de utilizador ou "
                        "palavra-passe incorretos."
                    )

    with tab_criar:
        novo_username = st.text_input(
            "Nome de utilizador",
            key="novo_username",
        )
        nova_password = st.text_input(
            "Palavra-passe",
            type="password",
            key="nova_password",
        )
        repetir_password = st.text_input(
            "Repetir palavra-passe",
            type="password",
        )

        if st.button(
            "Criar conta",
            type="primary",
        ):
            if len(
                limpar_username(novo_username)
            ) < 3:
                st.warning(
                    "O nome deve ter pelo menos "
                    "3 caracteres."
                )
            elif len(nova_password) < 6:
                st.warning(
                    "A palavra-passe deve ter pelo "
                    "menos 6 caracteres."
                )
            elif nova_password != repetir_password:
                st.warning(
                    "As palavras-passe não são iguais."
                )
            else:
                r = criar_conta(
                    novo_username,
                    nova_password,
                )

                if (
                    r is not None
                    and r.status_code in [200, 201]
                ):
                    dados = r.json()

                    if dados.get("access_token"):
                        aplicar_sessao(
                            dados,
                            nome_fallback=novo_username.strip(),
                        )
                        st.rerun()
                    else:
                        st.success(
                            "Conta criada. Agora faz login."
                        )
                else:
                    st.error(
                        "Não foi possível criar a conta."
                    )

    st.stop()


# =========================================================
# UTILIZADOR AUTENTICADO
# =========================================================

col_nome, col_sair = st.columns([5, 1])

with col_nome:
    st.success(
        f"👤 Olá, {st.session_state['nome']}!"
    )

with col_sair:
    if st.button("Sair"):
        terminar_sessao()


# Atualização automática uma vez por sessão
if "resultados_verificados" not in st.session_state:
    atualizar_resultados_automaticamente()
    st.session_state[
        "resultados_verificados"
    ] = True


previsoes = carregar_previsoes()

minhas = [
    p for p in previsoes
    if str(p.get("user_id"))
    == str(st.session_state["user_id"])
]

# Aviso simples de novos resultados desde a última visita.
concluidas_agora = sum(1 for p in minhas if p.get("resultado") in ["Acertou", "Errou"])
try:
    concluidas_antes = int(cookies.get("concluidas_vistas") or 0)
except Exception:
    concluidas_antes = 0

if concluidas_agora > concluidas_antes and concluidas_antes > 0:
    st.info(
        f"✅ {concluidas_agora - concluidas_antes} das tuas previsões "
        "foram atualizadas desde a última visita."
    )

cookies["concluidas_vistas"] = str(concluidas_agora)
cookies.save()


# =========================================================
# TABS PRINCIPAIS
# =========================================================

tab_prever, tab_minhas, tab_stats, tab_historico, tab_ranking, tab_comparar, tab_perfis = st.tabs([
    "⚽ Prever",
    "📋 Minhas previsões",
    "📊 Estatísticas",
    "📜 Histórico",
    "🏆 Ranking",
    "⚖️ Comparar",
    "👥 Perfis",
])


# =========================================================
# TAB — PREVER
# =========================================================

with tab_prever:

    st.subheader("Nova previsão")

    liga = st.selectbox(
        "Competição",
        list(LIGAS.keys()),
        key="liga_prever",
    )

    if st.button(
        "Carregar jogos",
        key="carregar_jogos_liga",
    ):
        with st.spinner(
            "A carregar jogos..."
        ):
            jogos_novos, erro = buscar_jogos(
                liga
            )

        if erro:
            st.error(erro)
        else:
            st.session_state[
                "jogos"
            ] = jogos_novos
            st.session_state[
                "classificacao"
            ] = buscar_classificacao(liga)
            st.session_state[
                "liga_carregada"
            ] = liga
            st.session_state.pop(
                "indice_jogo_novo",
                None,
            )

    if (
        st.session_state.get(
            "liga_carregada"
        ) == liga
    ):
        jogos = st.session_state.get(
            "jogos", []
        )
    else:
        jogos = []

    if not jogos:
        st.info(
            "Carrega os jogos desta competição."
        )

    if jogos:
        indice_jogo = st.selectbox(
            "Escolhe o jogo",
            options=range(len(jogos)),
            format_func=lambda i: (
                f"{jogos[i]['data']} — "
                f"{jogos[i]['jogo']}"
            ),
            key="indice_jogo_novo",
        )

        jogo = jogos[indice_jogo]
        bloqueado = jogo_comecou(jogo)

        existentes = [
            p for p in minhas
            if str(p.get("match_id"))
            == str(jogo["match_id"])
        ]

        tipos_ja_feitos = {
            p.get("tipo_previsao")
            for p in existentes
        }

        st.caption(
            f"{len(existentes)} previsões já feitas "
            f"neste jogo."
        )

        if tipos_ja_feitos:
            st.write(
                "**Já preenchidos:** "
                + ", ".join(
                    categoria_estatistica(t)
                    for t in tipos_ja_feitos
                    if t
                )
            )

        if bloqueado:
            st.error(
                "🔒 O jogo já começou. "
                "As previsões estão fechadas."
            )
        else:
            tipos_ui = {
                "Resultado do jogo": "resultado",
                "Dupla possibilidade": "dupla_possibilidade",
                "Golos — tempo regulamentar":
                    "golos_tempo_regulamentar",
                "Golos — intervalo":
                    "golos_intervalo",
                f"Golos do {jogo['casa']}":
                    "golos_equipa_casa",
                f"Golos do {jogo['fora']}":
                    "golos_equipa_fora",
                "Ambas as equipas marcam":
                    "ambas_marcam",
            }

            label_tipo = st.selectbox(
                "Tipo de previsão",
                list(tipos_ui.keys()),
                key=f"tipo_{jogo['match_id']}",
            )

            tipo = tipos_ui[label_tipo]
            opcoes = opcoes_para_tipo(
                tipo,
                jogo,
            )

            label_valor = st.selectbox(
                "Escolha",
                list(opcoes.keys()),
                key=(
                    f"valor_{jogo['match_id']}_"
                    f"{tipo}"
                ),
            )

            valor = opcoes[label_valor]

            confianca = st.select_slider(
                "Confiança",
                options=[1, 2, 3, 4, 5],
                value=3,
                key=(
                    f"confianca_{jogo['match_id']}"
                ),
            )

            texto = texto_previsao(
                tipo,
                valor,
                jogo,
            )

            posicoes = st.session_state.get("classificacao", {})
            dificuldade, multiplicador = dificuldade_previsao(
                tipo, valor, jogo, posicoes
            )

            st.info(
                f"🎯 {texto}\n\n"
                f"Dificuldade: **{dificuldade} ×{multiplicador:g}** • "
                f"Se acertar: **{formatar_pontos(confianca * multiplicador)} pontos** • "
                f"Se errar: **-{confianca} pontos**"
            )

            if st.button(
                "Guardar previsão",
                type="primary",
                key=(
                    f"guardar_{jogo['match_id']}"
                ),
            ):
                if jogo_comecou(jogo):
                    st.error(
                        "O jogo já começou."
                    )
                elif tipo in tipos_ja_feitos:
                    st.warning(
                        "Já tens uma previsão "
                        "deste tipo neste jogo."
                    )
                else:
                    contradicao, mensagem = (
                        verificar_contradicao(
                            existentes,
                            tipo,
                            valor,
                        )
                    )

                    if contradicao:
                        st.warning(
                            f"⚠️ {mensagem}"
                        )
                    else:
                        equipa_previsao = None
                        if tipo == "golos_equipa_casa":
                            equipa_previsao = jogo["casa"]
                        elif tipo == "golos_equipa_fora":
                            equipa_previsao = jogo["fora"]

                        registo = {
                            "user_id":
                                st.session_state[
                                    "user_id"
                                ],
                            "utilizador":
                                st.session_state["nome"],
                            "match_id":
                                jogo["match_id"],
                            "jogo":
                                jogo["jogo"],
                            "casa":
                                jogo["casa"],
                            "fora":
                                jogo["fora"],
                            "liga":
                                jogo["liga"],
                            "data":
                                jogo["data"],
                            "previsao":
                                texto,
                            "tipo_previsao":
                                tipo,
                            "valor_previsao":
                                valor,
                            "equipa_previsao":
                                equipa_previsao,
                            "confianca":
                                confianca,
                            "dificuldade":
                                dificuldade,
                            "multiplicador":
                                multiplicador,
                            "placar":
                                "",
                            "resultado":
                                "Pendente",
                            "resultado_real":
                                None,
                        }

                        r = guardar_previsao(
                            registo
                        )

                        if (
                            r is not None
                            and r.status_code
                            in [200, 201]
                        ):
                            st.success(
                                "✅ Previsão guardada!"
                            )
                            st.rerun()
                        else:
                            st.error(
                                "Não foi possível guardar."
                            )
                            if r is not None:
                                st.code(r.text)


# =========================================================
# TAB — MINHAS PREVISÕES
# =========================================================

with tab_minhas:

    st.subheader("Filtros")

    ligas_filtro = sorted({
        p.get("liga")
        for p in minhas
        if p.get("liga")
    })

    tipos_filtro = sorted({
        categoria_estatistica(
            p.get("tipo_previsao")
        )
        for p in minhas
        if p.get("tipo_previsao")
    })

    c1, c2, c3, c4 = st.columns(4)

    with c1:
        filtro_liga = st.selectbox(
            "Liga",
            ["Todas"] + ligas_filtro,
        )

    with c2:
        filtro_tipo = st.selectbox(
            "Tipo",
            ["Todos"] + tipos_filtro,
        )

    with c3:
        filtro_estado = st.selectbox(
            "Estado",
            [
                "Todos",
                "Pendente",
                "Acertou",
                "Errou",
            ],
        )

    with c4:
        filtro_periodo = st.selectbox(
            "Período",
            [
                "Tudo",
                "Últimos 7 dias",
                "Últimos 30 dias",
            ],
        )

    filtradas = []

    agora = datetime.now(TZ)

    for p in minhas:
        if (
            filtro_liga != "Todas"
            and p.get("liga") != filtro_liga
        ):
            continue

        if (
            filtro_tipo != "Todos"
            and categoria_estatistica(
                p.get("tipo_previsao")
            ) != filtro_tipo
        ):
            continue

        if (
            filtro_estado != "Todos"
            and p.get(
                "resultado",
                "Pendente",
            ) != filtro_estado
        ):
            continue

        criado = parse_created_at(
            p.get("created_at")
        )

        if (
            filtro_periodo == "Últimos 7 dias"
            and criado
            and criado < agora - timedelta(days=7)
        ):
            continue

        if (
            filtro_periodo == "Últimos 30 dias"
            and criado
            and criado < agora - timedelta(days=30)
        ):
            continue

        filtradas.append(p)

    if not filtradas:
        st.info(
            "Nenhuma previsão com estes filtros."
        )
    else:
        grupos = defaultdict(list)

        for p in filtradas:
            grupos[p.get("match_id")].append(p)

        for match_id, lista in grupos.items():
            primeira = lista[0]

            titulo = (
                f"{primeira.get('jogo', '')} "
                f"({len(lista)} previsões)"
            )

            with st.expander(titulo):
                st.caption(
                    f"{primeira.get('liga', '')} • "
                    f"{primeira.get('data', '')}"
                )

                for p in lista:
                    estado = p.get(
                        "resultado",
                        "Pendente",
                    )

                    icone = {
                        "Acertou": "✅",
                        "Errou": "❌",
                        "Pendente": "⏳",
                    }.get(estado, "⏳")

                    st.write(
                        f"### {icone} "
                        f"{categoria_estatistica(p.get('tipo_previsao'))}"
                    )

                    st.write(
                        "**Previsão:**",
                        p.get("previsao", ""),
                    )

                    st.write(
                        "**Confiança:**",
                        f"{p.get('confianca', '')}/5",
                    )
                    st.write(
                        "**Dificuldade:**",
                        f"{p.get('dificuldade', 'Fácil')} ×{float(p.get('multiplicador', 1) or 1):g}",
                    )

                    if estado != "Pendente":
                        st.write(
                            "**Pontos:**",
                            formatar_pontos(calcular_pontos(p)),
                        )

                    if p.get("placar"):
                        st.write(
                            "**Placar final:**",
                            p.get("placar"),
                        )

                    if not jogo_comecou(p):
                        col_editar, col_apagar = (
                            st.columns(2)
                        )

                        with col_editar:
                            with st.popover(
                                "✏️ Editar"
                            ):
                                opcoes = opcoes_para_tipo(
                                    p.get(
                                        "tipo_previsao"
                                    ),
                                    p,
                                )

                                labels = list(
                                    opcoes.keys()
                                )

                                atual_valor = p.get(
                                    "valor_previsao"
                                )

                                atual_label = next(
                                    (
                                        k
                                        for k, v
                                        in opcoes.items()
                                        if v == atual_valor
                                    ),
                                    labels[0]
                                    if labels
                                    else "",
                                )

                                novo_label = st.selectbox(
                                    "Nova previsão",
                                    labels,
                                    index=(
                                        labels.index(
                                            atual_label
                                        )
                                        if atual_label
                                        in labels
                                        else 0
                                    ),
                                    key=(
                                        f"edit_val_"
                                        f"{p['id']}"
                                    ),
                                )

                                nova_conf = st.select_slider(
                                    "Nova confiança",
                                    options=[
                                        1, 2, 3, 4, 5
                                    ],
                                    value=int(
                                        p.get(
                                            "confianca",
                                            3,
                                        ) or 3
                                    ),
                                    key=(
                                        f"edit_conf_"
                                        f"{p['id']}"
                                    ),
                                )

                                if st.button(
                                    "Guardar alterações",
                                    key=(
                                        f"confirm_edit_"
                                        f"{p['id']}"
                                    ),
                                ):
                                    if jogo_comecou(p):
                                        st.error(
                                            "O jogo já começou."
                                        )
                                    else:
                                        novo_valor = (
                                            opcoes[
                                                novo_label
                                            ]
                                        )

                                        outros = [
                                            x for x in minhas
                                            if (
                                                str(
                                                    x.get(
                                                        "match_id"
                                                    )
                                                )
                                                ==
                                                str(
                                                    p.get(
                                                        "match_id"
                                                    )
                                                )
                                                and
                                                x.get("id")
                                                != p.get("id")
                                            )
                                        ]

                                        contradicao, msg = (
                                            verificar_contradicao(
                                                outros,
                                                p.get(
                                                    "tipo_previsao"
                                                ),
                                                novo_valor,
                                            )
                                        )

                                        if contradicao:
                                            st.warning(msg)
                                        else:
                                            novo_texto = (
                                                texto_previsao(
                                                    p.get(
                                                        "tipo_previsao"
                                                    ),
                                                    novo_valor,
                                                    p,
                                                )
                                            )

                                            r = (
                                                atualizar_previsao_utilizador(
                                                    p["id"],
                                                    {
                                                        "valor_previsao":
                                                            novo_valor,
                                                        "previsao":
                                                            novo_texto,
                                                        "confianca":
                                                            nova_conf,
                                                    },
                                                )
                                            )

                                            if (
                                                r is not None
                                                and r.status_code
                                                in [200, 204]
                                            ):
                                                st.success(
                                                    "Alterado."
                                                )
                                                st.rerun()
                                            else:
                                                st.error(
                                                    "Não foi possível editar."
                                                )

                        with col_apagar:
                            if st.button(
                                "🗑️ Apagar",
                                key=(
                                    f"apagar_{p['id']}"
                                ),
                            ):
                                if jogo_comecou(p):
                                    st.error(
                                        "O jogo já começou."
                                    )
                                else:
                                    r = (
                                        apagar_previsao_utilizador(
                                            p["id"]
                                        )
                                    )
                                    if (
                                        r is not None
                                        and r.status_code
                                        in [200, 204]
                                    ):
                                        st.success(
                                            "Previsão apagada."
                                        )
                                        st.rerun()
                                    else:
                                        st.error(
                                            "Não foi possível apagar."
                                        )
                    else:
                        st.caption(
                            "🔒 Previsão fechada."
                        )

                    st.divider()


# =========================================================
# TAB — ESTATÍSTICAS
# =========================================================

with tab_stats:
    mostrar_estatisticas_completas(minhas, prefixo="minhas")


# =========================================================
# TAB — HISTÓRICO DOS ÚLTIMOS 7 DIAS
# =========================================================

with tab_historico:
    st.subheader("📜 Histórico dos últimos 7 dias")
    st.caption("Todos podem consultar o histórico recente dos participantes.")

    nomes_historico = sorted({
        p.get("utilizador")
        for p in previsoes
        if p.get("utilizador")
    })

    if not nomes_historico:
        st.info("Ainda não existem previsões no histórico.")
    else:
        nome_historico = st.selectbox(
            "Escolhe um utilizador",
            nomes_historico,
            key="historico_utilizador",
        )

        agora = datetime.now(TZ)
        inicio = agora - timedelta(days=7)

        historico_recente = []
        for p in previsoes:
            if p.get("utilizador") != nome_historico:
                continue

            # As previsões dos outros só ficam visíveis depois do início do jogo.
            if (
                str(p.get("user_id")) != str(st.session_state["user_id"])
                and not jogo_comecou(p)
            ):
                continue

            data_jogo = parse_data_jogo(p.get("data"))
            if data_jogo and inicio <= data_jogo <= agora:
                historico_recente.append(p)

        historico_recente.sort(
            key=lambda p: parse_data_jogo(p.get("data"))
            or datetime.min.replace(tzinfo=TZ),
            reverse=True,
        )

        if not historico_recente:
            st.info("Este utilizador não tem jogos previstos nos últimos 7 dias.")
        else:
            resumo = estatisticas_basicas(historico_recente)
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Previsões", resumo["total"])
            c2.metric("Acertos", resumo["acertos"])
            c3.metric("Taxa", f"{resumo['taxa']:.1f}%")
            c4.metric("Pontos", formatar_pontos(resumo["pontos"]))

            grupos_hist = defaultdict(list)
            for p in historico_recente:
                grupos_hist[p.get("match_id")].append(p)

            for _, lista in grupos_hist.items():
                primeira = lista[0]
                with st.expander(
                    f"{primeira.get('data', '')} — {primeira.get('jogo', '')}"
                ):
                    st.caption(primeira.get("liga", ""))

                    for p in lista:
                        estado = p.get("resultado", "Pendente")
                        icone = {
                            "Acertou": "✅",
                            "Errou": "❌",
                            "Pendente": "⏳",
                        }.get(estado, "⏳")

                        st.write(
                            f"**{icone} {categoria_estatistica(p.get('tipo_previsao'))}**"
                        )
                        st.write(f"Previsão: {p.get('previsao', '')}")
                        st.write(f"Confiança: {p.get('confianca', '')}/5")
                        st.write(
                            f"Dificuldade: {p.get('dificuldade', 'Fácil')} "
                            f"×{float(p.get('multiplicador', 1) or 1):g}"
                        )
                        st.write(f"Resultado: {estado}")
                        if estado != "Pendente":
                            st.write(f"Pontos: {formatar_pontos(calcular_pontos(p))}")
                        if p.get("placar"):
                            st.write(f"Placar: {p.get('placar')}")
                        st.divider()


# =========================================================
# TAB — RANKING
# =========================================================

with tab_ranking:
    periodo_ranking = st.radio(
        "Período",
        ["Total", "Esta semana", "Este mês"],
        horizontal=True,
        key="periodo_ranking",
    )

    previsoes_ranking = filtrar_periodo(previsoes, periodo_ranking)

    grupos_amigos = defaultdict(list)
    for p in previsoes_ranking:
        grupos_amigos[p.get("utilizador", "Sem nome")].append(p)

    elegiveis = []
    insuficientes = []

    for nome, itens in grupos_amigos.items():
        e = estatisticas_basicas(itens)
        linha = {"nome": nome, **e}
        if e["concluidas"] >= MINIMO_RANKING_PRECISAO:
            elegiveis.append(linha)
        else:
            insuficientes.append(linha)

    elegiveis.sort(
        key=lambda x: (x["taxa"], x["concluidas"], x["acertos"]),
        reverse=True,
    )
    insuficientes.sort(
        key=lambda x: (x["taxa"], x["concluidas"]),
        reverse=True,
    )

    st.subheader("🏆 Ranking por precisão")
    tabela_precisao = []

    for pos, pessoa in enumerate(elegiveis, start=1):
        tabela_precisao.append({
            "Posição": pos,
            "Utilizador": pessoa["nome"],
            "Precisão": f"{pessoa['taxa']:.1f}%",
            "Acertos": pessoa["acertos"],
            "Concluídas": pessoa["concluidas"],
            "Estado": "Ranking",
        })

    for pessoa in insuficientes:
        tabela_precisao.append({
            "Posição": "—",
            "Utilizador": pessoa["nome"],
            "Precisão": f"{pessoa['taxa']:.1f}%",
            "Acertos": pessoa["acertos"],
            "Concluídas": pessoa["concluidas"],
            "Estado": f"Amostra insuficiente (<{MINIMO_RANKING_PRECISAO})",
        })

    if tabela_precisao:
        st.dataframe(
            tabela_precisao,
            hide_index=True,
            use_container_width=True,
        )
    else:
        st.info("Ainda não existem previsões concluídas.")

    st.subheader("⭐ Ranking por pontos")
    ranking_pontos = []

    for nome, itens in grupos_amigos.items():
        e = estatisticas_basicas(itens)
        ranking_pontos.append({"nome": nome, **e})

    ranking_pontos.sort(
        key=lambda x: (x["pontos"], x["acertos"], x["taxa"]),
        reverse=True,
    )

    tabela_pontos = []
    for pos, pessoa in enumerate(ranking_pontos, start=1):
        tabela_pontos.append({
            "Posição": pos,
            "Utilizador": pessoa["nome"],
            "Pontos": pessoa["pontos"],
            "Acertos": pessoa["acertos"],
            "Erros": pessoa["erros"],
        })

    if tabela_pontos:
        st.dataframe(
            tabela_pontos,
            hide_index=True,
            use_container_width=True,
        )

    st.subheader("🌟 Melhor previsão da semana")
    semana = filtrar_periodo(previsoes, "Esta semana")
    acertos_semana = [p for p in semana if p.get("resultado") == "Acertou"]
    if acertos_semana:
        melhor_prev = max(
            acertos_semana,
            key=lambda p: (
                float(p.get("multiplicador", 1) or 1),
                int(p.get("confianca", 0) or 0),
            ),
        )
        st.success(
            f"{melhor_prev.get('utilizador')} — {melhor_prev.get('previsao')} • "
            f"{melhor_prev.get('dificuldade', 'Fácil')} "
            f"×{float(melhor_prev.get('multiplicador', 1) or 1):g} • "
            f"{formatar_pontos(calcular_pontos(melhor_prev))} pontos"
        )
    else:
        st.info("Ainda não há previsões certas esta semana.")

    st.subheader("🥇 Melhor por categoria")

    categorias = sorted({
        categoria_estatistica(p.get("tipo_previsao"))
        for p in previsoes
        if p.get("tipo_previsao")
    })

    linhas_categoria = []
    for categoria in categorias:
        melhor = None

        for nome, itens in grupos_amigos.items():
            filtrados = [
                p for p in itens
                if categoria_estatistica(p.get("tipo_previsao")) == categoria
            ]
            e = estatisticas_basicas(filtrados)
            if e["concluidas"] == 0:
                continue

            candidato = (e["taxa"], e["concluidas"], nome)
            if melhor is None or candidato > melhor:
                melhor = candidato

        if melhor:
            linhas_categoria.append({
                "Categoria": categoria,
                "Melhor": melhor[2],
                "Taxa": f"{melhor[0]:.1f}%",
                "Concluídas": melhor[1],
            })

    if linhas_categoria:
        st.dataframe(
            linhas_categoria,
            hide_index=True,
            use_container_width=True,
        )


# =========================================================
# TAB — COMPARAR E CONSENSO
# =========================================================

with tab_comparar:
    nomes_comparar = sorted({
        p.get("utilizador") for p in previsoes if p.get("utilizador")
    })

    st.subheader("⚖️ Comparar utilizadores")
    if len(nomes_comparar) < 2:
        st.info("São necessários pelo menos dois utilizadores.")
    else:
        c1, c2 = st.columns(2)
        with c1:
            nome_a = st.selectbox("Utilizador A", nomes_comparar, key="comp_a")
        with c2:
            opcoes_b = [n for n in nomes_comparar if n != nome_a]
            nome_b = st.selectbox("Utilizador B", opcoes_b, key="comp_b")

        a = [p for p in previsoes if p.get("utilizador") == nome_a]
        b = [p for p in previsoes if p.get("utilizador") == nome_b]
        ea = estatisticas_basicas(a)
        eb = estatisticas_basicas(b)
        sa, ma = sequencias(a)
        sb, mb = sequencias(b)

        st.dataframe([
            {"Métrica": "Precisão", nome_a: f"{ea['taxa']:.1f}%", nome_b: f"{eb['taxa']:.1f}%"},
            {"Métrica": "Pontos", nome_a: formatar_pontos(ea['pontos']), nome_b: formatar_pontos(eb['pontos'])},
            {"Métrica": "Concluídas", nome_a: ea['concluidas'], nome_b: eb['concluidas']},
            {"Métrica": "Melhor liga", nome_a: melhor_liga(a), nome_b: melhor_liga(b)},
            {"Métrica": "Melhor tipo", nome_a: melhor_categoria(a), nome_b: melhor_categoria(b)},
            {"Métrica": "Sequência atual", nome_a: sa, nome_b: sb},
            {"Métrica": "Melhor sequência", nome_a: ma, nome_b: mb},
        ], hide_index=True, use_container_width=True)

    st.divider()
    st.subheader("🤝 Consenso entre amigos")
    jogos_comecados = {}
    for p in previsoes:
        if jogo_comecou(p):
            jogos_comecados[p.get("match_id")] = p

    if not jogos_comecados:
        st.info("Ainda não há jogos começados com previsões.")
    else:
        ids = list(jogos_comecados.keys())
        id_escolhido = st.selectbox(
            "Jogo",
            ids,
            format_func=lambda mid: f"{jogos_comecados[mid].get('data', '')} — {jogos_comecados[mid].get('jogo', '')}",
            key="consenso_jogo",
        )
        itens = [p for p in previsoes if p.get("match_id") == id_escolhido]
        contagem = defaultdict(int)
        for p in itens:
            contagem[p.get("previsao", "Sem descrição")] += 1
        tabela_consenso = [
            {"Previsão": nome, "Pessoas": total}
            for nome, total in sorted(contagem.items(), key=lambda x: x[1], reverse=True)
        ]
        st.dataframe(tabela_consenso, hide_index=True, use_container_width=True)


# =========================================================
# TAB — PERFIS
# =========================================================

with tab_perfis:
    nomes = sorted({
        p.get("utilizador")
        for p in previsoes
        if p.get("utilizador")
    })

    if not nomes:
        st.info("Ainda não existem perfis.")
    else:
        nome_escolhido = st.selectbox(
            "Escolhe um amigo",
            nomes,
            key="perfil_amigo",
        )

        perfil = [
            p for p in previsoes
            if p.get("utilizador") == nome_escolhido
        ]

        st.subheader(f"👤 {nome_escolhido}")
        mostrar_estatisticas_completas(
            perfil,
            prefixo=f"perfil_{nome_escolhido}",
        )

        st.subheader("🏅 Conquistas")
        e_perfil = estatisticas_basicas(perfil)
        atual, melhor_seq = sequencias(perfil)
        badges = []
        if e_perfil["concluidas"] >= 10:
            badges.append("🎯 10 previsões concluídas")
        if melhor_seq >= 5:
            badges.append("🔥 5 acertos seguidos")
        conf5 = [p for p in perfil if int(p.get("confianca", 0) or 0) == 5 and p.get("resultado") in ["Acertou", "Errou"]]
        if len(conf5) >= 5 and estatisticas_basicas(conf5)["taxa"] >= 70:
            badges.append("🧠 70%+ com confiança 5/5")
        if e_perfil["concluidas"] >= 20:
            badges.append("🏟️ Veterano — 20 previsões concluídas")
        if badges:
            for badge in badges:
                st.write(badge)
        else:
            st.caption("Ainda sem conquistas desbloqueadas.")

