import streamlit as st
import requests
import re
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict, Counter
from st_cookie_manager import EncryptedCookieManager

# =========================================================
# CONFIGURAÇÃO
# =========================================================

st.set_page_config(
    page_title="Previsões Desportivas",
    page_icon="⚽",
    layout="wide"
)

SUPABASE_URL = st.secrets["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
SUPABASE_SECRET_KEY = st.secrets["SUPABASE_SECRET_KEY"]
FOOTBALL_API_KEY = st.secrets["FOOTBALL_API_KEY"]
BRASILEIRAO_API_KEY = st.secrets.get("BRASILEIRAO_API_KEY", "")
TENNIS_API_KEY = st.secrets.get("TENNIS_API_KEY", "")

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
    "Brasileirão Série A": "API_FOOTBALL_BR",
}

NOMES_TIPOS = {
    "resultado": "Resultado do jogo",
    "golos_tempo_regulamentar": "Golos — tempo regulamentar",
    "golos_intervalo": "Golos — intervalo",
    "golos_equipa_casa": "Golos — equipa da casa",
    "golos_equipa_fora": "Golos — equipa visitante",
    "ambas_marcam": "Ambas as equipas marcam",
    "dupla_possibilidade": "Dupla possibilidade",
    "tenis_vencedor": "Vencedor do encontro",
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



def modalidade_previsao(p):
    return p.get("modalidade") or "Futebol"


def chave_evento(p):
    return (
        modalidade_previsao(p),
        p.get("fonte_api") or "",
        p.get("evento_id") or p.get("match_id"),
    )



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
    if modalidade_previsao(p) == "Ténis":
        valor = p.get("valor_previsao")
        if valor == "Jogador1":
            return p.get("casa")
        if valor == "Jogador2":
            return p.get("fora")
        return None

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



BRASILEIRAO_RAPIDAPI_HOST = "brasileirao-serie-a.p.rapidapi.com"
BRASILEIRAO_RAPIDAPI_BASE = (
    f"https://{BRASILEIRAO_RAPIDAPI_HOST}"
)


def fonte_api_liga(nome_liga):
    if nome_liga == "Brasileirão Série A":
        return "rapidapi_brasileirao"
    return "football_data"


def headers_brasileirao():
    return {
        "X-RapidAPI-Key": BRASILEIRAO_API_KEY,
        "X-RapidAPI-Host": BRASILEIRAO_RAPIDAPI_HOST,
    }


def erro_rapidapi(dados, status_code):
    if status_code != 200:
        return f"Erro Brasileirão API: {status_code}"

    if isinstance(dados, dict) and dados.get("error"):
        erro = dados.get("error") or {}
        return (
            erro.get("message")
            or erro.get("code")
            or "Erro na API do Brasileirão."
        )

    return None


@st.cache_data(ttl=300, show_spinner=False)
def buscar_jogos_brasileirao():
    if not BRASILEIRAO_API_KEY:
        return [], (
            "BRASILEIRAO_API_KEY não está configurada "
            "nos Secrets do Streamlit."
        )

    agora = datetime.now(TZ)
    data_inicio = agora.date()
    data_fim = (agora + timedelta(days=45)).date()

    url = f"{BRASILEIRAO_RAPIDAPI_BASE}/matches"
    params = {
        "season": str(agora.year),
        "from": data_inicio.isoformat(),
        "to": data_fim.isoformat(),
        "limit": 200,
    }

    try:
        r = requests.get(
            url,
            headers=headers_brasileirao(),
            params=params,
            timeout=20,
        )
    except requests.RequestException as erro:
        return [], f"Erro de ligação à API do Brasileirão: {erro}"

    try:
        dados = r.json()
    except Exception:
        return [], (
            "A API do Brasileirão devolveu uma resposta inválida."
        )

    erro = erro_rapidapi(dados, r.status_code)
    if erro:
        return [], erro

    jogos = []

    for item in dados.get("data", []):
        status = str(item.get("status", "")).lower()

        if status in {"finished", "cancelled"}:
            continue

        data_iso = item.get("kickoff")
        if not data_iso:
            continue

        try:
            data_pt = datetime.fromisoformat(
                data_iso.replace("Z", "+00:00")
            ).astimezone(TZ)
        except Exception:
            continue

        if data_pt <= agora:
            continue

        casa = (
            item.get("homeTeam", {}).get("name")
            or "Casa"
        )
        fora = (
            item.get("awayTeam", {}).get("name")
            or "Fora"
        )

        match_id = item.get("id")
        if match_id is None:
            continue

        jogos.append({
            "match_id": match_id,
            "jogo": f"{casa} vs {fora}",
            "casa": casa,
            "fora": fora,
            "liga": "Brasileirão Série A",
            "data": data_pt.strftime("%d/%m/%Y %H:%M"),
            "data_obj": data_pt,
            "fonte_api": "rapidapi_brasileirao",
        })

    jogos.sort(key=lambda x: x["data_obj"])

    for jogo in jogos:
        jogo.pop("data_obj", None)

    return jogos[:40], None


@st.cache_data(ttl=600, show_spinner=False)
def buscar_classificacao_brasileirao():
    if not BRASILEIRAO_API_KEY:
        return {}

    url = f"{BRASILEIRAO_RAPIDAPI_BASE}/standings"
    params = {
        "season": str(datetime.now(TZ).year),
    }

    try:
        r = requests.get(
            url,
            headers=headers_brasileirao(),
            params=params,
            timeout=20,
        )
    except requests.RequestException:
        return {}

    if r.status_code != 200:
        return {}

    try:
        dados = r.json()
    except Exception:
        return {}

    if dados.get("error"):
        return {}

    posicoes = {}

    for linha in dados.get("data", []):
        equipa = (
            linha.get("team", {}).get("name")
            or linha.get("teamName")
        )
        posicao = (
            linha.get("position")
            or linha.get("rank")
        )

        if equipa and posicao is not None:
            try:
                posicoes[equipa] = int(posicao)
            except Exception:
                pass

    return posicoes


@st.cache_data(ttl=600, show_spinner=False)
def buscar_perfil_golos_brasileirao():
    """
    Perfil recente para dificuldade de:
    - ambas marcam
    - total de golos
    - golos por equipa

    O endpoint de lista fornece o placar final.
    Se não houver informação de intervalo no item, a dificuldade
    de golos ao intervalo cai para a regra neutra.
    """
    if not BRASILEIRAO_API_KEY:
        return {}

    agora = datetime.now(TZ)
    data_fim = agora.date()
    data_inicio = (agora - timedelta(days=90)).date()

    url = f"{BRASILEIRAO_RAPIDAPI_BASE}/matches"
    params = {
        "season": str(agora.year),
        "from": data_inicio.isoformat(),
        "to": data_fim.isoformat(),
        "status": "finished",
        "limit": 200,
    }

    try:
        r = requests.get(
            url,
            headers=headers_brasileirao(),
            params=params,
            timeout=20,
        )
    except requests.RequestException:
        return {}

    if r.status_code != 200:
        return {}

    try:
        dados = r.json()
    except Exception:
        return {}

    if dados.get("error"):
        return {}

    perfis = defaultdict(list)
    partidas = dados.get("data", [])

    partidas.sort(
        key=lambda p: p.get("kickoff", ""),
        reverse=True,
    )

    for item in partidas:
        casa = (
            item.get("homeTeam", {}).get("name")
        )
        fora = (
            item.get("awayTeam", {}).get("name")
        )

        if not casa or not fora:
            continue

        score = item.get("score", {}) or {}
        gc = score.get("home")
        gf = score.get("away")

        if gc is None or gf is None:
            continue

        try:
            gc = int(gc)
            gf = int(gf)
        except Exception:
            continue

        # Algumas respostas podem trazer halftime/halfTime.
        half = (
            item.get("halfTime")
            or item.get("halftime")
            or score.get("halfTime")
            or score.get("halftime")
            or {}
        )

        hc = (
            half.get("home")
            if isinstance(half, dict)
            else None
        )
        hf = (
            half.get("away")
            if isinstance(half, dict)
            else None
        )

        total = gc + gf
        ambas = gc >= 1 and gf >= 1

        total_intervalo = None
        if hc is not None and hf is not None:
            try:
                total_intervalo = int(hc) + int(hf)
            except Exception:
                total_intervalo = None

        registo_casa = {
            "marcados": gc,
            "total": total,
            "intervalo_total": total_intervalo,
            "ambas": ambas,
        }

        registo_fora = {
            "marcados": gf,
            "total": total,
            "intervalo_total": total_intervalo,
            "ambas": ambas,
        }

        if len(perfis[casa]) < 8:
            perfis[casa].append(registo_casa)

        if len(perfis[fora]) < 8:
            perfis[fora].append(registo_fora)

    return dict(perfis)


def _resultado_brasileirao_normalizado(item):
    """
    Converte a resposta da RapidAPI para a estrutura usada
    pelo avaliador já existente da app.
    """
    if not item:
        return None

    status = str(item.get("status", "")).lower()

    score = item.get("score", {}) or {}
    gc = score.get("home")
    gf = score.get("away")

    half_home = None
    half_away = None

    half = (
        item.get("halfTime")
        or item.get("halftime")
        or score.get("halfTime")
        or score.get("halftime")
        or {}
    )

    if isinstance(half, dict):
        half_home = half.get("home")
        half_away = half.get("away")

    # Se o detalhe não trouxer placar do intervalo, tentamos
    # reconstruí-lo pelos eventos de golo até aos 45 minutos.
    if half_home is None or half_away is None:
        home_id = item.get("homeTeam", {}).get("id")
        away_id = item.get("awayTeam", {}).get("id")

        if home_id is not None and away_id is not None:
            h = 0
            a = 0
            encontrou_eventos_golo = False

            for evento in item.get("events", []) or []:
                if str(evento.get("type", "")).lower() != "goal":
                    continue

                minuto = evento.get("minute")
                try:
                    minuto = int(minuto)
                except Exception:
                    continue

                if minuto > 45:
                    continue

                encontrou_eventos_golo = True
                team_id = evento.get("teamId")

                if team_id == home_id:
                    h += 1
                elif team_id == away_id:
                    a += 1

            if encontrou_eventos_golo:
                half_home = h
                half_away = a

    return {
        "id": item.get("id"),
        "status": (
            "FINISHED"
            if status == "finished"
            else status.upper()
        ),
        "score": {
            "regularTime": {
                "home": gc,
                "away": gf,
            },
            "fullTime": {
                "home": gc,
                "away": gf,
            },
            "halfTime": {
                "home": half_home,
                "away": half_away,
            },
        },
    }


def buscar_resultados_brasileirao(match_ids):
    resultados = {}

    if not match_ids or not BRASILEIRAO_API_KEY:
        return resultados

    for match_id in match_ids:
        url = (
            f"{BRASILEIRAO_RAPIDAPI_BASE}"
            f"/matches/{match_id}"
        )

        try:
            r = requests.get(
                url,
                headers=headers_brasileirao(),
                timeout=20,
            )
        except requests.RequestException:
            continue

        if r.status_code != 200:
            continue

        try:
            dados = r.json()
        except Exception:
            continue

        if dados.get("error"):
            continue

        item = dados.get("data")
        normalizado = _resultado_brasileirao_normalizado(
            item
        )

        if normalizado and normalizado.get("id") is not None:
            resultados[
                normalizado["id"]
            ] = normalizado

    return resultados


@st.cache_data(ttl=300, show_spinner=False)
def buscar_jogos(nome_liga):
    if nome_liga == "Brasileirão Série A":
        return buscar_jogos_brasileirao()

    codigo = LIGAS[nome_liga]

    agora = datetime.now(TZ)
    data_inicio = agora.date()
    data_fim = (agora + timedelta(days=45)).date()

    url = (
        "https://api.football-data.org/v4/"
        f"competitions/{codigo}/matches"
        f"?dateFrom={data_inicio.isoformat()}"
        f"&dateTo={data_fim.isoformat()}"
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
        detalhe = ""
        try:
            detalhe = r.json().get("message", "")
        except Exception:
            pass

        mensagem = f"Erro da API: {r.status_code}"
        if detalhe:
            mensagem += f" — {detalhe}"
        return [], mensagem

    jogos = []

    estados_ignorados = {
        "FINISHED",
        "CANCELLED",
        "AWARDED",
    }

    for partida in r.json().get("matches", []):
        if partida.get("status") in estados_ignorados:
            continue

        utc_date = partida.get("utcDate")
        if not utc_date:
            continue

        try:
            data_utc = datetime.strptime(
                utc_date,
                "%Y-%m-%dT%H:%M:%SZ",
            ).replace(tzinfo=ZoneInfo("UTC"))
        except ValueError:
            continue

        data_pt = data_utc.astimezone(TZ)

        if data_pt <= agora:
            continue

        casa = (
            partida.get("homeTeam", {}).get("name")
            or "Casa"
        )

        fora = (
            partida.get("awayTeam", {}).get("name")
            or "Fora"
        )

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
            "fonte_api": "football_data",
        })

    jogos.sort(key=lambda x: x["data_obj"])

    for jogo in jogos:
        jogo.pop("data_obj", None)

    return jogos[:40], None


@st.cache_data(ttl=300, show_spinner=False)
@st.cache_data(ttl=600, show_spinner=False)
def buscar_classificacao(nome_liga):
    if nome_liga == "Brasileirão Série A":
        return buscar_classificacao_brasileirao()

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


@st.cache_data(ttl=600, show_spinner=False)
def buscar_perfil_golos_liga(nome_liga):
    if nome_liga == "Brasileirão Série A":
        return buscar_perfil_golos_brasileirao()

    """
    Usa jogos FINALIZADOS recentes da competição para criar
    um perfil simples de golos de cada equipa.

    Guardamos, para cada equipa, até aos 8 jogos mais recentes:
    - golos marcados
    - total de golos do jogo
    - total de golos ao intervalo
    - se ambas marcaram
    """
    codigo = LIGAS[nome_liga]

    agora = datetime.now(TZ)
    data_fim = agora.date()
    data_inicio = (agora - timedelta(days=90)).date()

    url = (
        "https://api.football-data.org/v4/"
        f"competitions/{codigo}/matches"
        f"?dateFrom={data_inicio.isoformat()}"
        f"&dateTo={data_fim.isoformat()}"
        "&status=FINISHED"
    )

    headers = {"X-Auth-Token": FOOTBALL_API_KEY}

    try:
        r = requests.get(
            url,
            headers=headers,
            timeout=20,
        )
    except requests.RequestException:
        return {}

    if r.status_code != 200:
        return {}

    perfis = defaultdict(list)

    partidas = r.json().get("matches", [])

    # Mais recentes primeiro.
    partidas.sort(
        key=lambda p: p.get("utcDate", ""),
        reverse=True,
    )

    for partida in partidas:
        casa = (
            partida.get("homeTeam", {}).get("name")
        )
        fora = (
            partida.get("awayTeam", {}).get("name")
        )

        if not casa or not fora:
            continue

        score = partida.get("score", {}) or {}

        full = score.get("fullTime", {}) or {}
        gc = full.get("home")
        gf = full.get("away")

        if gc is None or gf is None:
            regular = score.get("regularTime", {}) or {}
            gc = regular.get("home")
            gf = regular.get("away")

        if gc is None or gf is None:
            continue

        half = score.get("halfTime", {}) or {}
        hc = half.get("home")
        hf = half.get("away")

        total = gc + gf
        ambas = gc >= 1 and gf >= 1

        total_intervalo = None
        if hc is not None and hf is not None:
            total_intervalo = hc + hf

        registo_casa = {
            "marcados": gc,
            "total": total,
            "intervalo_total": total_intervalo,
            "ambas": ambas,
        }

        registo_fora = {
            "marcados": gf,
            "total": total,
            "intervalo_total": total_intervalo,
            "ambas": ambas,
        }

        if len(perfis[casa]) < 8:
            perfis[casa].append(registo_casa)

        if len(perfis[fora]) < 8:
            perfis[fora].append(registo_fora)

    return dict(perfis)


def dificuldade_por_probabilidade(probabilidade):
    """
    Converte uma frequência estimada (0 a 1) em dificuldade.

    70%+   -> Fácil x1
    55-69% -> Média x1.5
    35-54% -> Difícil x2
    <35%   -> Muito difícil x3
    """
    if probabilidade is None:
        return "Média", 1.5

    if probabilidade >= 0.70:
        return "Fácil", 1.0

    if probabilidade >= 0.55:
        return "Média", 1.5

    if probabilidade >= 0.35:
        return "Difícil", 2.0

    return "Muito difícil", 3.0


def frequencia_evento(lista, funcao):
    valores = []

    for item in lista:
        try:
            resultado = funcao(item)
        except Exception:
            resultado = None

        if resultado is not None:
            valores.append(1 if resultado else 0)

    if not valores:
        return None

    return sum(valores) / len(valores)


def media_probabilidades(*valores):
    validos = [
        valor
        for valor in valores
        if valor is not None
    ]

    if not validos:
        return None

    return sum(validos) / len(validos)


def dificuldade_previsao(
    tipo,
    valor,
    jogo,
    posicoes,
    perfis_golos=None,
):
    casa = jogo.get("casa")
    fora = jogo.get("fora")

    # =====================================================
    # RESULTADO / DUPLA POSSIBILIDADE
    # =====================================================

    if tipo in [
        "resultado",
        "dupla_possibilidade",
    ]:
        pc = posicoes.get(casa)
        pf = posicoes.get(fora)

        if tipo == "resultado" and valor == "Empate":
            return "Difícil", 2.0

        if (
            tipo == "dupla_possibilidade"
            and valor == "CasaFora"
        ):
            return "Média", 1.5

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

        if valor == "CasaEmpate":
            delta = pc - pf
        else:
            delta = pf - pc

        if delta <= -5:
            return "Fácil", 1.0

        if delta <= 2:
            return "Média", 1.5

        return "Difícil", 2.0

    # =====================================================
    # MERCADOS DE GOLOS
    # =====================================================

    perfis_golos = perfis_golos or {}

    jogos_casa = perfis_golos.get(casa, [])
    jogos_fora = perfis_golos.get(fora, [])

    # Se ainda não houver amostra suficiente, usamos Médio,
    # em vez de marcar tudo automaticamente como Fácil.
    if not jogos_casa and not jogos_fora:
        return "Média", 1.5

    if tipo == "ambas_marcam":
        freq_casa = frequencia_evento(
            jogos_casa,
            lambda x: x.get("ambas"),
        )

        freq_fora = frequencia_evento(
            jogos_fora,
            lambda x: x.get("ambas"),
        )

        prob_sim = media_probabilidades(
            freq_casa,
            freq_fora,
        )

        if prob_sim is None:
            return "Média", 1.5

        prob = (
            prob_sim
            if valor == "Sim"
            else 1 - prob_sim
        )

        return dificuldade_por_probabilidade(prob)

    if tipo == "golos_tempo_regulamentar":
        minimo = valor_minimo_golos(valor)

        def evento_total(item):
            total = item.get("total")

            if total is None:
                return None

            if valor == "0":
                return total == 0

            return total >= minimo

        freq_casa = frequencia_evento(
            jogos_casa,
            evento_total,
        )

        freq_fora = frequencia_evento(
            jogos_fora,
            evento_total,
        )

        return dificuldade_por_probabilidade(
            media_probabilidades(
                freq_casa,
                freq_fora,
            )
        )

    if tipo == "golos_intervalo":
        minimo = valor_minimo_golos(valor)

        def evento_intervalo(item):
            total = item.get("intervalo_total")

            if total is None:
                return None

            if valor == "0":
                return total == 0

            return total >= minimo

        freq_casa = frequencia_evento(
            jogos_casa,
            evento_intervalo,
        )

        freq_fora = frequencia_evento(
            jogos_fora,
            evento_intervalo,
        )

        return dificuldade_por_probabilidade(
            media_probabilidades(
                freq_casa,
                freq_fora,
            )
        )

    if tipo in [
        "golos_equipa_casa",
        "golos_equipa_fora",
    ]:
        minimo = valor_minimo_golos(valor)

        jogos_equipa = (
            jogos_casa
            if tipo == "golos_equipa_casa"
            else jogos_fora
        )

        def evento_equipa(item):
            marcados = item.get("marcados")

            if marcados is None:
                return None

            if valor == "0":
                return marcados == 0

            return marcados >= minimo

        prob = frequencia_evento(
            jogos_equipa,
            evento_equipa,
        )

        return dificuldade_por_probabilidade(prob)

    return "Média", 1.5



# =========================================================
# API DE TÉNIS
# =========================================================

TENNIS_API_BASE = "https://api.tennis/v1"


def headers_tennis():
    # O dashboard atual da api.tennis mostra X-API-Key.
    # Também enviamos Bearer para manter compatibilidade com a
    # documentação pública mais recente.
    return {
        "X-API-Key": TENNIS_API_KEY,
        "Authorization": f"Bearer {TENNIS_API_KEY}",
        "Accept": "application/json",
    }


def _lista_tennis(dados):
    if isinstance(dados, list):
        return dados

    if isinstance(dados, dict):
        for chave in [
            "matches",
            "data",
            "results",
            "response",
            "items",
            "events",
        ]:
            valor = dados.get(chave)

            if isinstance(valor, list):
                return valor

            # Alguns serviços embrulham a lista dentro de data/results.
            if isinstance(valor, dict):
                for subchave in [
                    "matches",
                    "items",
                    "results",
                    "events",
                    "data",
                ]:
                    subvalor = valor.get(subchave)
                    if isinstance(subvalor, list):
                        return subvalor

        # Alguns endpoints devolvem um único encontro.
        if any(
            chave in dados
            for chave in [
                "match_id",
                "id",
                "players",
                "player1",
                "event_id",
            ]
        ):
            return [dados]

    return []


def _nome_torneio_tennis(item):
    torneio = item.get("tournament")

    if isinstance(torneio, dict):
        nome = (
            torneio.get("name")
            or torneio.get("title")
        )
        if nome:
            return str(nome)

    if torneio:
        return str(torneio)

    edicao = item.get("edition")
    if isinstance(edicao, dict):
        nome = edicao.get("name")
        if nome:
            return str(nome)

    return "Torneio"


def _players_tennis(item):
    # Formato real da api.tennis:
    # "sides": [
    #   {"side": 1, "players": [{"id": ..., "name": ...}]},
    #   {"side": 2, "players": [{"id": ..., "name": ...}]}
    # ]
    sides = item.get("sides")

    if isinstance(sides, list) and len(sides) >= 2:
        lados = {}

        for lado in sides:
            if not isinstance(lado, dict):
                continue

            numero = lado.get("side")
            jogadores = lado.get("players") or []

            jogador = None

            # Singles: normalmente há apenas 1 jogador por lado.
            if isinstance(jogadores, list) and jogadores:
                candidato = jogadores[0]
                if isinstance(candidato, dict):
                    jogador = dict(candidato)
                elif candidato:
                    jogador = {"name": str(candidato)}

            # Alguns registos podem trazer equipa/entry em vez de player.
            if jogador is None:
                equipa = lado.get("team")
                if isinstance(equipa, dict):
                    jogador = dict(equipa)
                elif equipa:
                    jogador = {"name": str(equipa)}

            if jogador is not None:
                jogador["_side"] = numero
                jogador["_winner"] = bool(lado.get("winner"))
                lados[numero] = jogador

        if 1 in lados and 2 in lados:
            return lados[1], lados[2]

    # Fallback para outros formatos possíveis.
    jogadores = item.get("players")

    if isinstance(jogadores, list) and len(jogadores) >= 2:
        p1 = jogadores[0]
        p2 = jogadores[1]

        if not isinstance(p1, dict):
            p1 = {"name": str(p1)}
        if not isinstance(p2, dict):
            p2 = {"name": str(p2)}

        return p1, p2

    p1 = (
        item.get("player1")
        or item.get("home")
        or item.get("first_player")
    )
    p2 = (
        item.get("player2")
        or item.get("away")
        or item.get("second_player")
    )

    if p1 and p2:
        if not isinstance(p1, dict):
            p1 = {"name": str(p1)}
        if not isinstance(p2, dict):
            p2 = {"name": str(p2)}
        return p1, p2

    return None, None


def _rank_tennis(player):
    if not isinstance(player, dict):
        return None

    for chave in ["rank", "ranking", "position"]:
        valor = player.get(chave)
        if isinstance(valor, dict):
            valor = valor.get("position") or valor.get("rank")
        try:
            if valor is not None:
                return int(valor)
        except Exception:
            pass

    return None


def _parse_data_tennis(item):
    candidatos = [
        item.get("start_time"),
        item.get("scheduled_at"),
        item.get("scheduled"),
        item.get("start_at"),
        item.get("datetime"),
        item.get("start"),
        item.get("start_date"),
        item.get("event_date"),
        item.get("date"),
        item.get("timestamp"),
        item.get("start_timestamp"),
    ]

    for valor in candidatos:
        if valor is None or valor == "":
            continue

        # Unix timestamp em segundos ou milissegundos.
        if isinstance(valor, (int, float)):
            try:
                numero = float(valor)
                if numero > 10_000_000_000:
                    numero /= 1000
                return datetime.fromtimestamp(
                    numero,
                    tz=ZoneInfo("UTC"),
                ).astimezone(TZ)
            except Exception:
                pass

        # Data isolada + hora isolada.
        if (
            isinstance(valor, str)
            and len(valor) == 10
            and (
                item.get("time")
                or item.get("event_time")
                or item.get("start_time_local")
            )
        ):
            hora = (
                item.get("time")
                or item.get("event_time")
                or item.get("start_time_local")
            )
            valor = f"{valor}T{hora}"

        try:
            valor_str = str(valor)

            # Quando a API devolve apenas YYYY-MM-DD,
            # não há hora exata. Usamos 12:00 para evitar
            # eliminar jogos de hoje por estarem a "00:00".
            if (
                len(valor_str) == 10
                and valor_str[4] == "-"
                and valor_str[7] == "-"
            ):
                dt = datetime.fromisoformat(
                    valor_str + "T12:00:00"
                ).replace(tzinfo=TZ)
            else:
                dt = datetime.fromisoformat(
                    valor_str.replace("Z", "+00:00")
                )
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=TZ)

            return dt.astimezone(TZ)
        except Exception:
            pass

        for formato in [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M",
            "%d/%m/%Y %H:%M",
        ]:
            try:
                return datetime.strptime(
                    str(valor),
                    formato,
                ).replace(tzinfo=TZ)
            except Exception:
                pass

    return None


def _tour_tennis(item):
    circuito = str(item.get("circuit") or "").strip().lower()

    # Valores reais observados na api.tennis.
    if circuito in {"atp", "challenger"}:
        return "ATP"

    if circuito == "wta":
        return "WTA"

    # ITF/Fed/Davis/etc. não entram quando o utilizador escolhe ATP/WTA.
    if circuito in {
        "itf",
        "fed",
        "davis",
        "utr",
        "exhibition",
    }:
        return circuito.upper()

    valores = [
        item.get("category"),
        item.get("gender"),
        item.get("event_type"),
        item.get("level_name"),
    ]

    torneio = item.get("tournament")
    if isinstance(torneio, dict):
        valores.extend([
            torneio.get("name"),
            torneio.get("category"),
            torneio.get("gender"),
        ])

    texto = " ".join(
        str(v) for v in valores if v
    ).lower()

    if "wta" in texto or "women" in texto or "female" in texto:
        return "WTA"

    if (
        "atp" in texto
        or "challenger" in texto
        or "men" in texto
        or "male" in texto
    ):
        return "ATP"

    return None


def _status_tennis(item):
    status = item.get("status")
    if isinstance(status, dict):
        status = (
            status.get("name")
            or status.get("short")
            or status.get("status")
        )
    return str(status or "").strip().lower()


@st.cache_data(ttl=3600, show_spinner=False)
def buscar_jogos_tenis(circuito):
    if not TENNIS_API_KEY:
        return [], (
            "TENNIS_API_KEY não está configurada "
            "nos Secrets do Streamlit."
        )

    url = f"{TENNIS_API_BASE}/matches"

    agora = datetime.now(TZ)
    limite = agora + timedelta(days=30)

    # A documentação usa:
    # atp = ATP Tour
    # ch = Challenger
    # wta = WTA
    circuit_param = "atp,ch" if circuito == "ATP" else "wta"
    circuitos_aceites = {"atp", "ch"} if circuito == "ATP" else {"wta"}

    params = {
        "from": agora.date().isoformat(),
        "to": limite.date().isoformat(),
        "circuit": circuit_param,
        "doubles": "false",
        "limit": 100,
    }

    try:
        r = requests.get(
            url,
            headers=headers_tennis(),
            params=params,
            timeout=20,
        )
    except requests.RequestException as erro:
        return [], f"Erro de ligação à API Tennis: {erro}"

    if r.status_code != 200:
        return [], f"Erro API Tennis: {r.status_code}"

    try:
        dados = r.json()
    except Exception:
        return [], "A API Tennis devolveu uma resposta inválida."

    itens = _lista_tennis(dados)
    jogos = []

    for item in itens:
        if not isinstance(item, dict):
            continue

        circuito_item = str(
            item.get("circuit") or ""
        ).strip().lower()

        if circuito_item not in circuitos_aceites:
            continue

        if item.get("doubles") is True:
            continue

        # Não exigimos status=scheduled.
        # A documentação indica também "unknown" para jogos ainda sem estado final.
        status = _status_tennis(item)
        if status in {
            "completed",
            "finished",
            "final",
            "retired",
            "walkover",
            "defaulted",
            "abandoned",
            "cancelled",
            "canceled",
        }:
            continue

        dt = _parse_data_tennis(item)

        if dt is None:
            continue

        if dt < agora - timedelta(hours=12):
            continue

        if dt > limite:
            continue

        p1, p2 = _players_tennis(item)

        if not p1 or not p2:
            continue

        nome1 = (
            p1.get("name")
            or p1.get("full_name")
            or p1.get("player_name")
        )
        nome2 = (
            p2.get("name")
            or p2.get("full_name")
            or p2.get("player_name")
        )

        if not nome1 or not nome2:
            continue

        evento_id = (
            item.get("id")
            or item.get("match_id")
            or item.get("event_id")
            or item.get("key")
        )

        if evento_id is None:
            continue

        jogos.append({
            "evento_id": str(evento_id),
            "jogo": f"{nome1} vs {nome2}",
            "casa": nome1,
            "fora": nome2,
            "liga": _nome_torneio_tennis(item),
            "data": dt.strftime("%d/%m/%Y"),
            "data_obj": dt,
            "fonte_api": "api_tennis",
            "modalidade": "Ténis",
            "circuito": circuito,
            "rank_casa": _rank_tennis(p1),
            "rank_fora": _rank_tennis(p2),
        })

    jogos.sort(key=lambda x: x["data_obj"])

    for jogo in jogos:
        jogo.pop("data_obj", None)

    return jogos[:50], None


def dificuldade_tenis(jogo, valor):
    rank1 = jogo.get("rank_casa")
    rank2 = jogo.get("rank_fora")

    if rank1 is None or rank2 is None:
        return "Média", 1.5

    escolhido = rank1 if valor == "Jogador1" else rank2
    adversario = rank2 if valor == "Jogador1" else rank1

    # Ranking menor = jogador mais bem classificado.
    if escolhido <= max(10, adversario * 0.50):
        return "Fácil", 1.0

    if escolhido <= adversario * 1.15:
        return "Média", 1.5

    if escolhido <= adversario * 2.0:
        return "Difícil", 2.0

    return "Muito difícil", 3.0


@st.cache_data(ttl=600, show_spinner=False)
def buscar_resultado_tenis(evento_id):
    if not TENNIS_API_KEY or not evento_id:
        return None

    url = f"{TENNIS_API_BASE}/matches/{evento_id}"

    try:
        r = requests.get(
            url,
            headers=headers_tennis(),
            timeout=20,
        )
    except requests.RequestException:
        return None

    if r.status_code != 200:
        return None

    try:
        dados = r.json()
    except Exception:
        return None

    lista = _lista_tennis(dados)
    if not lista:
        return None

    item = lista[0]
    status = _status_tennis(item)

    terminado = status in {
        "finished",
        "final",
        "completed",
        "complete",
        "retired",
        "walkover",
        "ended",
        "closed",
    }

    if not terminado:
        return {"status": "PENDING"}

    p1, p2 = _players_tennis(item)
    if not p1 or not p2:
        return None

    nome1 = (
        p1.get("name")
        or p1.get("full_name")
        or p1.get("player_name")
    )
    nome2 = (
        p2.get("name")
        or p2.get("full_name")
        or p2.get("player_name")
    )

    vencedor_indice = None

    # Campo real observado na resposta.
    winner_side = item.get("winner_side")
    try:
        if winner_side is not None:
            vencedor_indice = int(winner_side) - 1
    except Exception:
        pass

    # Fallback pelo campo winner dentro de sides.
    if vencedor_indice is None:
        if p1.get("_winner") is True:
            vencedor_indice = 0
        elif p2.get("_winner") is True:
            vencedor_indice = 1

    if vencedor_indice not in [0, 1]:
        return None

    placar = ""

    score = item.get("score")
    if score is not None:
        if isinstance(score, str):
            placar = score
        elif isinstance(score, (dict, list)):
            placar = str(score)

    if not placar:
        sets = item.get("sets")
        if isinstance(sets, list):
            partes = []
            for parcial in sets:
                if isinstance(parcial, dict):
                    a = (
                        parcial.get("side1")
                        or parcial.get("player1")
                        or parcial.get("home")
                    )
                    b = (
                        parcial.get("side2")
                        or parcial.get("player2")
                        or parcial.get("away")
                    )
                    if a is not None and b is not None:
                        partes.append(f"{a}-{b}")
                elif (
                    isinstance(parcial, (list, tuple))
                    and len(parcial) >= 2
                ):
                    partes.append(
                        f"{parcial[0]}-{parcial[1]}"
                    )
            placar = ", ".join(partes)

    return {
        "status": "FINISHED",
        "winner_index": vencedor_indice,
        "winner_name": (
            nome1 if vencedor_indice == 0 else nome2
        ),
        "placar": placar,
    }



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

    pendentes_football_data = []
    pendentes_brasileirao = []
    pendentes_tenis = []

    for p in pendentes:
        fonte = p.get("fonte_api")
        modalidade = modalidade_previsao(p)

        if modalidade == "Ténis" or fonte == "api_tennis":
            # Só consultar a API depois da hora prevista do encontro,
            # para poupar o limite mensal.
            if jogo_comecou(p):
                pendentes_tenis.append(p)
        elif (
            fonte == "rapidapi_brasileirao"
            or p.get("liga") == "Brasileirão Série A"
        ):
            pendentes_brasileirao.append(p)
        else:
            pendentes_football_data.append(p)

    ids_football_data = list({
        p["match_id"]
        for p in pendentes_football_data
        if p.get("match_id") is not None
    })

    ids_brasileirao = list({
        p["match_id"]
        for p in pendentes_brasileirao
        if p.get("match_id") is not None
    })

    resultados_fd = buscar_resultados_api(
        ids_football_data
    )

    resultados_br = buscar_resultados_brasileirao(
        ids_brasileirao
    )

    atualizados = 0

    # Futebol
    for previsao in pendentes_football_data + pendentes_brasileirao:
        fonte = previsao.get("fonte_api")

        if (
            fonte == "rapidapi_brasileirao"
            or previsao.get("liga") == "Brasileirão Série A"
        ):
            partida = resultados_br.get(
                previsao.get("match_id")
            )
        else:
            partida = resultados_fd.get(
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

    # Ténis
    for previsao in pendentes_tenis:
        resultado = buscar_resultado_tenis(
            previsao.get("evento_id")
        )

        if not resultado:
            continue

        if resultado.get("status") != "FINISHED":
            continue

        valor = previsao.get("valor_previsao")
        escolhido = 0 if valor == "Jogador1" else 1
        acertou = escolhido == resultado.get("winner_index")

        placar = resultado.get("placar") or ""
        resultado_real = resultado.get("winner_name") or ""

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
    if tipo == "tenis_vencedor":
        return {
            jogo.get("casa", "Jogador 1"): "Jogador1",
            jogo.get("fora", "Jogador 2"): "Jogador2",
        }

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
    if tipo == "tenis_vencedor":
        if valor == "Jogador1":
            return f"{jogo.get('casa')} vence"
        return f"{jogo.get('fora')} vence"

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

st.title("🏆 Previsões Desportivas")
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
                    cookies.save()
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
                        cookies.save()
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

    modalidade = st.selectbox(
        "Modalidade",
        ["⚽ Futebol", "🎾 Ténis"],
        key="modalidade_prever",
    )

    if modalidade == "🎾 Ténis":
        circuito = st.selectbox(
            "Circuito",
            ["ATP", "WTA"],
            key="circuito_tenis",
        )

        with st.spinner("A carregar encontros de ténis..."):
            jogos_tenis, erro_tenis = buscar_jogos_tenis(circuito)

        if erro_tenis:
            st.error(erro_tenis)
            jogos_tenis = []

        if not jogos_tenis and not erro_tenis:
            st.info(
                "Não encontrei encontros futuros de "
                f"{circuito} nos próximos 30 dias."
            )




        if jogos_tenis:
            indice_tenis = st.selectbox(
                "Escolhe o encontro",
                options=range(len(jogos_tenis)),
                format_func=lambda i: (
                    f"{jogos_tenis[i]['data']} — "
                    f"{jogos_tenis[i]['jogo']} "
                    f"({jogos_tenis[i]['liga']})"
                ),
                key="indice_tenis_novo",
            )

            jogo = jogos_tenis[indice_tenis]

            existentes = [
                p for p in minhas
                if (
                    modalidade_previsao(p) == "Ténis"
                    and str(p.get("evento_id"))
                    == str(jogo["evento_id"])
                )
            ]

            ja_tem = any(
                p.get("tipo_previsao") == "tenis_vencedor"
                for p in existentes
            )

            opcoes = opcoes_para_tipo(
                "tenis_vencedor",
                jogo,
            )

            label_valor = st.selectbox(
                "Quem vence?",
                list(opcoes.keys()),
                key=f"tenis_valor_{jogo['evento_id']}",
            )

            valor = opcoes[label_valor]

            confianca = st.select_slider(
                "Confiança",
                options=[1, 2, 3, 4, 5],
                value=3,
                key=f"tenis_conf_{jogo['evento_id']}",
            )

            dificuldade, multiplicador = (
                dificuldade_tenis(jogo, valor)
            )

            texto = texto_previsao(
                "tenis_vencedor",
                valor,
                jogo,
            )

            rank1 = jogo.get("rank_casa")
            rank2 = jogo.get("rank_fora")

            detalhe_rank = ""
            if rank1 and rank2:
                detalhe_rank = (
                    f"\n\nRanking: {jogo['casa']} #{rank1} • "
                    f"{jogo['fora']} #{rank2}"
                )

            st.info(
                f"🎯 {texto}\n\n"
                f"Dificuldade: **{dificuldade} ×{multiplicador:g}** • "
                f"Se acertar: **{formatar_pontos(confianca * multiplicador)} pontos** • "
                f"Se errar: **-{confianca} pontos**"
                f"{detalhe_rank}"
            )

            if st.button(
                "Guardar previsão de ténis",
                type="primary",
                key=f"guardar_tenis_{jogo['evento_id']}",
            ):
                if jogo_comecou(jogo):
                    st.error(
                        "O encontro já começou."
                    )
                elif ja_tem:
                    st.warning(
                        "Já tens uma previsão para "
                        "este encontro."
                    )
                else:
                    equipa_previsao = (
                        jogo["casa"]
                        if valor == "Jogador1"
                        else jogo["fora"]
                    )

                    registo = {
                        "user_id":
                            st.session_state["user_id"],
                        "utilizador":
                            st.session_state["nome"],
                        "modalidade":
                            "Ténis",
                        "evento_id":
                            jogo["evento_id"],
                        "match_id":
                            None,
                        "fonte_api":
                            "api_tennis",
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
                            "tenis_vencedor",
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

                    r = guardar_previsao(registo)

                    if (
                        r is not None
                        and r.status_code in [200, 201]
                    ):
                        st.success(
                            "✅ Previsão de ténis guardada!"
                        )
                        st.rerun()
                    else:
                        st.error(
                            "Não foi possível guardar."
                        )
                        if r is not None:
                            st.code(r.text)

    else:
        liga = st.selectbox(
            "Competição",
            list(LIGAS.keys()),
            key="liga_prever",
        )

        # Os jogos são carregados automaticamente quando a competição muda.
        with st.spinner("A carregar jogos..."):
            jogos, erro = buscar_jogos(liga)
            classificacao_atual = buscar_classificacao(liga)
            perfil_golos_atual = buscar_perfil_golos_liga(liga)

        st.session_state["classificacao"] = classificacao_atual
        st.session_state["perfil_golos"] = perfil_golos_atual

        if erro:
            st.error(erro)
            jogos = []

        if not jogos and not erro:
            st.info(
                "Não encontrei jogos futuros desta competição "
                "nos próximos 45 dias."
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

            fonte_jogo = jogo.get(
                "fonte_api",
                fonte_api_liga(jogo["liga"]),
            )

            existentes = [
                p for p in minhas
                if (
                    str(p.get("match_id"))
                    == str(jogo["match_id"])
                    and (
                        p.get("fonte_api")
                        or (
                            "rapidapi_brasileirao"
                            if p.get("liga") == "Brasileirão Série A"
                            else "football_data"
                        )
                    )
                    == fonte_jogo
                )
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
                perfis_golos = st.session_state.get("perfil_golos", {})

                dificuldade, multiplicador = dificuldade_previsao(
                    tipo,
                    valor,
                    jogo,
                    posicoes,
                    perfis_golos,
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
                                "fonte_api":
                                    jogo.get(
                                        "fonte_api",
                                        fonte_api_liga(jogo["liga"]),
                                    ),
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

    c0, c1, c2, c3, c4 = st.columns(5)

    with c0:
        filtro_modalidade = st.selectbox(
            "Modalidade",
            ["Todas", "Futebol", "Ténis"],
            key="filtro_modalidade_minhas",
        )

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
            filtro_modalidade != "Todas"
            and modalidade_previsao(p) != filtro_modalidade
        ):
            continue

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
            grupos[chave_evento(p)].append(p)

        for chave_jogo, lista in grupos.items():
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
                grupos_hist[chave_evento(p)].append(p)

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
    modalidade_ranking = st.radio(
        "Modalidade",
        ["Geral", "Futebol", "Ténis"],
        horizontal=True,
        key="modalidade_ranking",
    )

    periodo_ranking = st.radio(
        "Período",
        ["Total", "Esta semana", "Este mês"],
        horizontal=True,
        key="periodo_ranking",
    )

    previsoes_ranking = filtrar_periodo(
        previsoes,
        periodo_ranking,
    )

    if modalidade_ranking != "Geral":
        previsoes_ranking = [
            p for p in previsoes_ranking
            if modalidade_previsao(p) == modalidade_ranking
        ]

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
    if modalidade_ranking != "Geral":
        semana = [
            p for p in semana
            if modalidade_previsao(p) == modalidade_ranking
        ]
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

