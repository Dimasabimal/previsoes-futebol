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
TENNIS_SCHEDULE_API_KEY = st.secrets.get("TENNIS_SCHEDULE_API_KEY", "")
MMA_API_KEY = st.secrets.get("MMA_API_KEY", "")
UFC_API_KEY = st.secrets.get("UFC_API_KEY", "")

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
    "mma_vencedor": "Vencedor da luta",
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
        try:
            # Registos antigos podem conter só o dia, sem hora.
            return datetime.strptime(valor, "%d/%m/%Y").replace(tzinfo=TZ)
        except (ValueError, TypeError):
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
    if modalidade_previsao(p) == "MMA":
        valor = p.get("valor_previsao")
        if valor == "Lutador1":
            return p.get("casa")
        if valor == "Lutador2":
            return p.get("fora")
        return None

    if modalidade_previsao(p) == "Ténis":
        valor = p.get("valor_previsao")
        if valor == "Jogador1":
            return p.get("casa")
        if valor == "Jogador2":
            return p.get("fora")
        return None

    # Devolve apenas a equipa realmente associada à previsão.
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
    # Um login real volta a permitir a restauração automática.
    st.session_state["manual_logout"] = False

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

    # Se o utilizador carregou em "Sair", não restaurar a sessão
    # automaticamente mesmo que o navegador ainda entregue o cookie antigo
    # durante o rerun imediato.
    if st.session_state.get("manual_logout"):
        return False

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
    if st.session_state.get("manual_logout"):
        return False

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
    # Bloqueia a restauração automática no rerun imediato.
    st.session_state["manual_logout"] = True

    # Apaga o refresh token persistente.
    if "refresh_token" in cookies:
        del cookies["refresh_token"]

    # Também gravamos um valor vazio para evitar que alguns navegadores
    # reutilizem o valor anterior enquanto o cookie é sincronizado.
    cookies["refresh_token"] = ""
    cookies.save()

    # Remove apenas os dados ligados à autenticação e sessão do utilizador.
    chaves_auth = [
        "access_token",
        "expires_at",
        "user_id",
        "nome",
        "resultados_verificados",
    ]

    for chave in chaves_auth:
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

    # Usa jogos finalizados recentes da competição para criar
    # internamente um perfil simples de golos por equipa.
    # Estes comentários não aparecem na interface.
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
# TÉNIS — UMA ÚNICA FONTE (RAPIDAPI TENNIS API)
# =========================================================

TENNIS_SCHEDULE_HOST = "tennis-api-atp-wta-itf.p.rapidapi.com"
TENNIS_RAPID_BASE = f"https://{TENNIS_SCHEDULE_HOST}/tennis/v2"


def _normalizar_nome_tenis(valor):
    txt = unicodedata.normalize("NFKD", str(valor or "").casefold())
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", txt)


def _cabecalhos_tenis_rapidapi():
    return {
        "X-RapidAPI-Key": TENNIS_SCHEDULE_API_KEY,
        "X-RapidAPI-Host": TENNIS_SCHEDULE_HOST,
        "Accept": "application/json",
    }


def _pedido_tenis_rapidapi(caminho, parametros=None):
    if not TENNIS_SCHEDULE_API_KEY:
        return None, "Falta TENNIS_SCHEDULE_API_KEY nos Secrets."
    try:
        resposta = requests.get(
            f"{TENNIS_RAPID_BASE}{caminho}",
            headers=_cabecalhos_tenis_rapidapi(),
            params=parametros or {},
            timeout=20,
        )
    except requests.RequestException:
        return None, "Não foi possível contactar a API de ténis."
    if resposta.status_code != 200:
        return None, f"Tennis API: HTTP {resposta.status_code}."
    try:
        return resposta.json(), None
    except (ValueError, TypeError):
        return None, "A API devolveu dados inválidos."


def _dados_tenis_resposta(dados):
    if isinstance(dados, dict):
        item = dados.get("data")
        return item if isinstance(item, dict) else dados
    return {}


def _dt_tenis_rapidapi(valor):
    if not valor:
        return None
    try:
        dt = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return None
        return dt.astimezone(TZ)
    except (ValueError, TypeError):
        return None


def _ranking_jogador_tenis(item):
    # A API pode omitir rankings nos encontros futuros.
    valor = item.get("rank") or item.get("ranking")
    if isinstance(valor, dict):
        valor = valor.get("rank") or valor.get("position")
    try:
        numero = int(valor)
        return numero if numero > 0 else None
    except (TypeError, ValueError):
        return None


@st.cache_data(ttl=7200, show_spinner=False)
def buscar_jogos_tenis(circuito):
    """Obtém ATP/WTA apenas da Tennis API; não cruza fornecedores."""
    diag = {"fonte": "Tennis API (RapidAPI)", "circuito": circuito,
            "chave_configurada": bool(TENNIS_SCHEDULE_API_KEY),
            "pedidos": [], "encontros_disponiveis": 0}
    if not TENNIS_SCHEDULE_API_KEY:
        return [], "Falta TENNIS_SCHEDULE_API_KEY nos Secrets.", diag

    tour = "atp" if circuito.upper() == "ATP" else "wta"
    hoje = datetime.now(TZ).date()
    base = {
        "group": "singles",
        "dateFrom": hoje.isoformat(),
        "dateTo": (hoje + timedelta(days=10)).isoformat(),
        "rank": "2,3,4,7",  # Tour, Masters, Grand Slam e finais
        "sortBy": "startTime",
        "limit": 100,
        "page": 1,
        "include": "fixtureDetails,metadata",
    }
    pesquisas = []
    if tour == "atp":
        pesquisas.append(("Paris Masters", {**base, "tournament": "Paris"}))
    pesquisas.append(("Torneios principais", base))

    vistos = set()
    jogos = []
    erros = []
    for descricao, params in pesquisas:
        payload, erro = _pedido_tenis_rapidapi(
            f"/upcoming/matches/{tour}", params
        )
        if erro:
            diag["pedidos"].append({"pesquisa": descricao, "erro": erro})
            erros.append(erro)
            continue
        lista = payload.get("matches", []) if isinstance(payload, dict) else []
        if not isinstance(lista, list):
            lista = []
        diag["pedidos"].append({"pesquisa": descricao,
                                "recebidos": len(lista),
                                "total_api": payload.get("total")})
        for item in lista:
            if not isinstance(item, dict):
                continue
            if item.get("doubles") is True or item.get("awaitingParticipants") or item.get("isBye"):
                continue
            p1, p2 = item.get("player1") or {}, item.get("player2") or {}
            if not isinstance(p1, dict) or not isinstance(p2, dict):
                continue
            nome1, nome2 = p1.get("name"), p2.get("name")
            id1, id2 = p1.get("id"), p2.get("id")
            torneio = item.get("tournament") or {}
            if not isinstance(torneio, dict):
                continue
            nome_torneio = torneio.get("name") or "ATP/WTA"
            rank = torneio.get("rankId")
            try:
                rank = int(rank)
            except (TypeError, ValueError):
                rank = None
            if rank is not None and rank not in (2, 3, 4, 7):
                continue
            inicio = _dt_tenis_rapidapi(item.get("startTime") or item.get("date"))
            if inicio is None or not nome1 or not nome2 or not id1 or not id2:
                continue
            if inicio.date() < hoje or inicio.date() > hoje + timedelta(days=10):
                continue
            event_id = item.get("liveMatchId")
            if not event_id:
                # Sem ID duradouro não é possível corrigir depois sem ambiguidades.
                continue
            event_id = str(event_id)
            if event_id in vistos:
                continue
            vistos.add(event_id)
            fonte_hora = str(item.get("startTimeSource") or "schedule").lower()
            jogos.append({
                "evento_id": event_id,
                "jogo": f"{nome1} vs {nome2}",
                "casa": nome1, "fora": nome2,
                "liga": nome_torneio,
                "data": inicio.strftime("%d/%m/%Y %H:%M"),
                "data_obj": inicio,
                "fonte_api": "tennis_rapidapi_wta" if circuito == "WTA" else "tennis_rapidapi_atp",
                "modalidade": "Ténis",
                "hora_confirmada": True,
                "horario_previsto": True,
                "origem_horario": fonte_hora,
                "estado_api": "scheduled",
                "circuito": circuito,
                "rank_casa": _ranking_jogador_tenis(p1),
                "rank_fora": _ranking_jogador_tenis(p2),
            })
    jogos.sort(key=lambda j: j["data_obj"])
    for j in jogos:
        j.pop("data_obj", None)
    diag["encontros_disponiveis"] = len(jogos)
    if not jogos and erros:
        return [], "; ".join(dict.fromkeys(erros)), diag
    return jogos[:100], None, diag


def estado_previsao_tenis(jogo):
    """Nunca assume que hora prevista garante que o encontro não começou."""
    dt = parse_data_jogo(jogo.get("data"))
    if dt is None:
        return False, "Horário desconhecido."
    agora = datetime.now(TZ)
    if jogo.get("estado_api") in {"live", "inplay", "ended", "finished", "completed"}:
        return False, "O encontro já começou ou terminou."
    # Reserva de 1h, pois horários de ténis podem ser antecipados.
    if agora >= dt - timedelta(hours=1):
        return False, "Previsões fechadas por segurança antes da hora prevista."
    return True, "Horário previsto pela Tennis API; pode sofrer alterações."


def dificuldade_tenis(jogo, valor):
    rank1, rank2 = jogo.get("rank_casa"), jogo.get("rank_fora")
    if rank1 is None or rank2 is None:
        return "Média", 1.5
    escolhido = rank1 if valor == "Jogador1" else rank2
    adversario = rank2 if valor == "Jogador1" else rank1
    if escolhido <= max(10, adversario * .5):
        return "Fácil", 1.0
    if escolhido <= adversario * 1.15:
        return "Média", 1.5
    if escolhido <= adversario * 2:
        return "Difícil", 2.0
    return "Muito difícil", 3.0


@st.cache_data(ttl=7200, show_spinner=False)
def buscar_resultado_tenis(evento_id, circuito="ATP", nome1="", nome2=""):
    """Usa o ID estável do mesmo fornecedor. Nunca assume vencedor por posição
    enquanto o jogo não constar no arquivo de resultados completos."""
    if not evento_id or not TENNIS_SCHEDULE_API_KEY:
        return None
    tour = "wta" if str(circuito).upper() == "WTA" else "atp"
    payload, erro = _pedido_tenis_rapidapi(
        f"/matches/{evento_id}", {"type": tour, "source": "game"}
    )
    if erro or not payload:
        return None
    item = _dados_tenis_resposta(payload)
    if item.get("source") != "game" or not item.get("result"):
        return {"status": "PENDING"}
    vencedor = item.get("player1") or {}
    perdedor = item.get("player2") or {}
    if not isinstance(vencedor, dict) or not isinstance(perdedor, dict):
        return None
    nome_vencedor = vencedor.get("name") or ""
    nome_perdedor = perdedor.get("name") or ""
    nomes_registados = {_normalizar_nome_tenis(nome1), _normalizar_nome_tenis(nome2)}
    nomes_api = {_normalizar_nome_tenis(nome_vencedor), _normalizar_nome_tenis(nome_perdedor)}
    if not all(nomes_registados) or nomes_api != nomes_registados:
        return None
    indice = 0 if _normalizar_nome_tenis(nome_vencedor) == _normalizar_nome_tenis(nome1) else 1
    return {"status": "FINISHED", "winner_index": indice,
            "winner_name": nome_vencedor, "placar": str(item["result"])}


# =========================================================
# UFC / MMA — UFCALENDAR FIGHT API
# =========================================================

UFC_API_BASE = "https://api.ufcalendar.com/v1"


def headers_ufc():
    return {
        "Authorization": f"Bearer {UFC_API_KEY}",
        "Accept": "application/json",
    }


@st.cache_data(ttl=21600, show_spinner=False)
def buscar_lutas_mma():
    if not UFC_API_KEY:
        return [], (
            "UFC_API_KEY não está configurada "
            "nos Secrets do Streamlit."
        )

    # 1) Próximos eventos UFC
    try:
        r = requests.get(
            f"{UFC_API_BASE}/events",
            headers=headers_ufc(),
            params={
                "org": "ufc",
                "status": "upcoming",
                "limit": 5,
            },
            timeout=20,
        )
    except requests.RequestException as erro:
        return [], f"Erro de ligação à Fight API: {erro}"

    if r.status_code != 200:
        return [], f"Erro Fight API: {r.status_code}"

    try:
        dados = r.json()
    except Exception:
        return [], "A Fight API devolveu uma resposta inválida."

    eventos = dados.get("data") or []
    if not isinstance(eventos, list):
        eventos = []

    agora = datetime.now(TZ)
    lutas = []

    # 2) Buscar o card completo de cada evento.
    # Limitamos a 5 eventos e usamos cache de 6h para poupar o plano gratuito.
    for evento in eventos[:5]:
        if not isinstance(evento, dict):
            continue

        slug = evento.get("slug")
        if not slug:
            continue

        try:
            revent = requests.get(
                f"{UFC_API_BASE}/events/{slug}",
                headers=headers_ufc(),
                timeout=20,
            )
        except requests.RequestException:
            continue

        if revent.status_code != 200:
            continue

        try:
            detalhe = revent.json().get("data") or {}
        except Exception:
            continue

        titulo_evento = (
            detalhe.get("title")
            or evento.get("title")
            or evento.get("short_title")
            or "UFC"
        )

        valor_data = (
            detalhe.get("starts_at")
            or evento.get("starts_at")
        )

        if not valor_data:
            continue

        try:
            dt = datetime.fromisoformat(
                str(valor_data).replace("Z", "+00:00")
            )
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo("UTC"))
            dt = dt.astimezone(TZ)
        except Exception:
            continue

        # Não mostrar eventos já passados.
        if dt < agora - timedelta(hours=8):
            continue

        card = detalhe.get("card") or []

        for luta in card:
            if not isinstance(luta, dict):
                continue

            # Se já tem resultado, não é previsão futura.
            if luta.get("result"):
                continue

            fighter_a = luta.get("fighter_a") or {}
            fighter_b = luta.get("fighter_b") or {}

            nome1 = fighter_a.get("name")
            nome2 = fighter_b.get("name")

            if not nome1 or not nome2:
                continue

            fight_id = luta.get("id")
            if fight_id is None:
                continue

            peso = luta.get("weight_class") or ""
            titulo = bool(luta.get("is_title"))
            main_event = bool(luta.get("is_main"))

            # A UFCalendar inclui no próprio card o ranking oficial
            # e a sequência atual de cada lutador.
            rank1 = fighter_a.get("rank")
            rank2 = fighter_b.get("rank")
            streak1 = fighter_a.get("streak")
            streak2 = fighter_b.get("streak")

            lutas.append({
                "evento_id": str(fight_id),
                "jogo": f"{nome1} vs {nome2}",
                "casa": nome1,
                "fora": nome2,
                "liga": titulo_evento,
                "data": dt.strftime("%d/%m/%Y %H:%M"),
                "data_obj": dt,
                "fonte_api": "ufcalendar",
                "modalidade": "MMA",
                "categoria": peso,
                "is_title": titulo,
                "is_main": main_event,
                "rank_casa": rank1,
                "rank_fora": rank2,
                "streak_casa": streak1,
                "streak_fora": streak2,
            })

    lutas.sort(
        key=lambda x: (
            x["data_obj"],
            not x.get("is_main", False),
        )
    )

    for luta in lutas:
        luta.pop("data_obj", None)

    return lutas[:100], None


def _rank_mma_valido(valor):
    try:
        if valor is None:
            return None
        return int(valor)
    except (TypeError, ValueError):
        return None


def _streak_mma_valido(valor):
    try:
        if valor is None:
            return 0
        return int(valor)
    except (TypeError, ValueError):
        return 0


def dificuldade_mma(luta, valor):
    """
    Dificuldade baseada apenas em dados desportivos:
    ranking oficial UFC + sequência atual.
    Não usa odds.
    """

    if valor == "Lutador1":
        rank_escolhido = _rank_mma_valido(
            luta.get("rank_casa")
        )
        rank_adversario = _rank_mma_valido(
            luta.get("rank_fora")
        )
        streak_escolhido = _streak_mma_valido(
            luta.get("streak_casa")
        )
        streak_adversario = _streak_mma_valido(
            luta.get("streak_fora")
        )
    else:
        rank_escolhido = _rank_mma_valido(
            luta.get("rank_fora")
        )
        rank_adversario = _rank_mma_valido(
            luta.get("rank_casa")
        )
        streak_escolhido = _streak_mma_valido(
            luta.get("streak_fora")
        )
        streak_adversario = _streak_mma_valido(
            luta.get("streak_casa")
        )

    score = 0

    # Ranking: 0 = campeão, números menores = melhor posição.
    if (
        rank_escolhido is not None
        and rank_adversario is not None
    ):
        diferenca = rank_escolhido - rank_adversario

        if diferenca >= 8:
            score += 3
        elif diferenca >= 4:
            score += 2
        elif diferenca >= 2:
            score += 1
        elif diferenca <= -8:
            score -= 2
        elif diferenca <= -4:
            score -= 1

    elif (
        rank_escolhido is None
        and rank_adversario is not None
    ):
        score += 2

    elif (
        rank_escolhido is not None
        and rank_adversario is None
    ):
        score -= 1

    # Sequência: streak positivo = vitórias; negativo = derrotas.
    diferenca_streak = streak_adversario - streak_escolhido

    if diferenca_streak >= 4:
        score += 2
    elif diferenca_streak >= 2:
        score += 1
    elif diferenca_streak <= -4:
        score -= 1

    if score >= 4:
        return "Muito difícil", 2.5
    if score >= 2:
        return "Difícil", 2.0
    if score <= -2:
        return "Fácil", 1.0

    return "Média", 1.5




def texto_rank_mma(rank):
    rank = _rank_mma_valido(rank)
    if rank is None:
        return "Não ranqueado"
    if rank == 0:
        return "Campeão"
    return f"#{rank}"


def texto_streak_mma(streak):
    streak = _streak_mma_valido(streak)
    if streak > 0:
        return f"{streak} vitórias seguidas"
    if streak < 0:
        return f"{abs(streak)} derrotas seguidas"
    return "Sem sequência"



@st.cache_data(ttl=1800, show_spinner=False)
def buscar_resultado_mma(evento_id):
    if not UFC_API_KEY or not evento_id:
        return None

    try:
        r = requests.get(
            f"{UFC_API_BASE}/fights/{evento_id}",
            headers=headers_ufc(),
            timeout=20,
        )
    except requests.RequestException:
        return None

    if r.status_code != 200:
        return None

    try:
        luta = r.json().get("data") or {}
    except Exception:
        return None

    resultado = luta.get("result")
    if not resultado:
        return {"status": "PENDING"}

    fighter_a = luta.get("fighter_a") or {}
    fighter_b = luta.get("fighter_b") or {}

    id_a = fighter_a.get("id")
    id_b = fighter_b.get("id")
    winner_id = resultado.get("winner_fighter_id")

    if winner_id is None:
        return None

    vencedor_indice = None
    vencedor_nome = ""

    if str(winner_id) == str(id_a):
        vencedor_indice = 0
        vencedor_nome = fighter_a.get("name") or ""
    elif str(winner_id) == str(id_b):
        vencedor_indice = 1
        vencedor_nome = fighter_b.get("name") or ""
    else:
        # Draw / no contest
        return None

    metodo = resultado.get("method") or ""
    round_n = resultado.get("round")
    tempo = resultado.get("time") or ""

    partes = [metodo]
    if round_n is not None:
        partes.append(f"R{round_n}")
    if tempo:
        partes.append(str(tempo))

    placar = " · ".join(p for p in partes if p)

    return {
        "status": "FINISHED",
        "winner_index": vencedor_indice,
        "winner_name": vencedor_nome,
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
    pendentes_mma = []

    for p in pendentes:
        fonte = p.get("fonte_api")
        modalidade = modalidade_previsao(p)

        if modalidade == "MMA" or fonte in {"api_mma", "ufcalendar"}:
            if jogo_comecou(p):
                pendentes_mma.append(p)
        elif modalidade == "Ténis" or fonte in {"api_tennis", "tennis_rapidapi_atp", "tennis_rapidapi_wta"}:
            # Só consultar a API depois da hora prevista do encontro,
            # para poupar o limite mensal.
            if fonte in {"tennis_rapidapi_atp", "tennis_rapidapi_wta"} and jogo_comecou(p):
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
    # Limitar consultas por execução para proteger a quota gratuita.
    for previsao in pendentes_tenis[:4]:
        resultado = buscar_resultado_tenis(
            previsao.get("evento_id"),
            "WTA" if previsao.get("fonte_api") == "tennis_rapidapi_wta" else "ATP",
            previsao.get("casa") or "",
            previsao.get("fora") or "",
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

    # MMA / UFC
    for previsao in pendentes_mma:
        resultado = buscar_resultado_mma(
            previsao.get("evento_id")
        )

        if not resultado:
            continue

        if resultado.get("status") != "FINISHED":
            continue

        valor = previsao.get("valor_previsao")
        escolhido = 0 if valor == "Lutador1" else 1
        acertou = escolhido == resultado.get("winner_index")

        r = atualizar_previsao_admin(
            previsao["id"],
            resultado.get("placar") or "",
            resultado.get("winner_name") or "",
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
    if tipo == "mma_vencedor":
        return {
            jogo.get("casa", "Lutador 1"): "Lutador1",
            jogo.get("fora", "Lutador 2"): "Lutador2",
        }

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
    if tipo == "mma_vencedor":
        if valor == "Lutador1":
            return f"{jogo.get('casa')} vence"
        return f"{jogo.get('fora')} vence"

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
    st.write(f"**Melhor fim de semana:** {formatar_pontos(melhor_fds)} pontos")
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


# O arranque da app não deve aguardar dezenas de pedidos a APIs.
# Atualização de resultados fica disponível sob ação explícita do utilizador.
with st.sidebar:
    st.caption("Resultados e desempenho")
    if st.button("🔄 Verificar resultados concluídos", key="atualizar_resultados_manual"):
        with st.spinner("A verificar resultados..."):
            try:
                n_atualizados = atualizar_resultados_automaticamente()
                st.success(f"{n_atualizados} previsões atualizadas.")
            except Exception:
                st.error("Não foi possível concluir a verificação de resultados.")

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

# Evita chamar o componente de cookies em todos os reruns.
if str(cookies.get("concluidas_vistas") or "") != str(concluidas_agora):
    cookies["concluidas_vistas"] = str(concluidas_agora)
    cookies.save()


# =========================================================
# TABS PRINCIPAIS
# =========================================================

PAGINAS = {
    "⚽ Prever": "tab_prever",
    "📋 Minhas previsões": "tab_minhas",
    "📊 Estatísticas": "tab_stats",
    "📜 Histórico": "tab_historico",
    "🏆 Ranking": "tab_ranking",
    "⚖️ Comparar": "tab_comparar",
    "👥 Perfis": "tab_perfis",
}

# Ao contrário de st.tabs, a navegação condicional só executa a página aberta.
pagina_ativa = st.radio(
    "Navegação",
    options=list(PAGINAS),
    index=0,
    horizontal=True,
    label_visibility="collapsed",
    key="pagina_ativa",
)



# =========================================================
# TAB — PREVER
# =========================================================

if pagina_ativa == "⚽ Prever":

    st.subheader("Nova previsão")

    modalidade = st.selectbox(
        "Modalidade",
        ["⚽ Futebol", "🎾 Ténis", "🥋 MMA/UFC"],
        key="modalidade_prever",
    )

    if modalidade == "🎾 Ténis":
        circuito = st.selectbox(
            "Circuito",
            ["ATP", "WTA"],
            key="circuito_tenis",
        )

        with st.spinner("A carregar encontros de ténis..."):
            jogos_tenis, erro_tenis, diag_tenis = buscar_jogos_tenis(circuito)

        if erro_tenis:
            if "limitar pedidos" in erro_tenis.lower():
                st.warning(
                    "⏳ A API Tennis está a receber pedidos demasiado depressa. "
                    "Espera alguns segundos e tenta novamente."
                )
            else:
                st.error(erro_tenis)
            jogos_tenis = []

        st.caption("A partir desta versão, as novas previsões de ténis usam uma única API. "
                   "As previsões antigas continuam no histórico e não são recalculadas automaticamente nesta migração.")
        with st.expander("🔧 Diagnóstico do calendário ATP/WTA"):
            st.json(diag_tenis)

        if not jogos_tenis and not erro_tenis:
            st.info(
                "Não encontrei encontros futuros de "
                f"{circuito} nos próximos 7 dias."
            )




        if not jogos_tenis:
            with st.expander("🔧 Diagnóstico ATP/WTA (sem mostrar a chave)"):
                st.json(diag_tenis)

        if jogos_tenis:
            indice_tenis = st.selectbox(
                "Escolhe o encontro",
                options=range(len(jogos_tenis)),
                format_func=lambda i: (
                    f"{'✅' if estado_previsao_tenis(jogos_tenis[i])[0] else '🔒'} "
                    f"{jogos_tenis[i]['data']} — "
                    f"{jogos_tenis[i]['jogo']} "
                    f"({jogos_tenis[i]['liga']})"
                ),
                key="indice_tenis_novo",
            )

            jogo = jogos_tenis[indice_tenis]
            previsao_aberta, aviso_horario = estado_previsao_tenis(jogo)
            if previsao_aberta:
                st.caption(f"✅ {aviso_horario}")
            else:
                st.warning(f"🔒 {aviso_horario}")
            st.caption("🕒 Horário previsto pela Tennis API; pode mudar conforme os encontros anteriores.")
            if "paris" in jogo.get("liga", "").lower():
                st.caption("O horário previsto de cada encontro deve ser confirmado na ordem de jogos oficial, não na hora de abertura da sessão.")
                st.markdown("[Consultar programação oficial do Rolex Paris Masters](https://tickets.rolexparismasters.com/en/programme-previsionnel)")

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
                disabled=not previsao_aberta,
            ):
                aberto_agora, motivo_agora = estado_previsao_tenis(jogo)
                if not aberto_agora:
                    st.error(motivo_agora)
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
                            "tennis_rapidapi_wta" if circuito == "WTA" else "tennis_rapidapi_atp",
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
                        st.error("Não foi possível guardar a previsão de ténis.")
                        if r is not None:
                            try:
                                detalhe = r.json()
                                codigo = detalhe.get("code", "") if isinstance(detalhe, dict) else ""
                                mensagem = detalhe.get("message", "") if isinstance(detalhe, dict) else ""
                                st.warning(f"Erro {r.status_code} ({codigo}): {mensagem[:300]}")
                            except Exception:
                                st.warning(f"Erro HTTP {r.status_code}. Consulta os logs do Streamlit.")

    elif modalidade == "🥋 MMA/UFC":
        with st.spinner("A carregar o próximo card UFC..."):
            lutas_mma, erro_mma = buscar_lutas_mma()

        if erro_mma:
            st.error(erro_mma)
            lutas_mma = []

        if not lutas_mma and not erro_mma:
            st.info(
                "Não encontrei lutas UFC futuras "
                "nos próximos 60 dias."
            )

        if lutas_mma:
            indice_mma = st.selectbox(
                "Escolhe a luta",
                options=range(len(lutas_mma)),
                format_func=lambda i: (
                    f"{'⭐ ' if lutas_mma[i].get('is_main') else ''}"
                    f"{'🏆 ' if lutas_mma[i].get('is_title') else ''}"
                    f"{lutas_mma[i]['data']} — "
                    f"{lutas_mma[i]['jogo']} "
                    f"({lutas_mma[i]['categoria']})"
                ),
                key="indice_mma_novo",
            )

            luta = lutas_mma[indice_mma]

            etiquetas = []
            if luta.get("is_main"):
                etiquetas.append("⭐ Main Event")
            if luta.get("is_title"):
                etiquetas.append("🏆 Luta de título")

            if etiquetas:
                st.markdown(" • ".join(etiquetas))

            st.caption(
                f"🥋 {luta.get('categoria') or 'Categoria não indicada'}"
            )

            col_mma_a, col_mma_b = st.columns(2)

            with col_mma_a:
                st.markdown(f"**{luta['casa']}**")
                st.write(
                    f"Ranking: {texto_rank_mma(luta.get('rank_casa'))}"
                )
                st.caption(
                    texto_streak_mma(luta.get("streak_casa"))
                )

            with col_mma_b:
                st.markdown(f"**{luta['fora']}**")
                st.write(
                    f"Ranking: {texto_rank_mma(luta.get('rank_fora'))}"
                )
                st.caption(
                    texto_streak_mma(luta.get("streak_fora"))
                )

            existentes_mma = [
                p for p in minhas
                if (
                    modalidade_previsao(p) == "MMA"
                    and str(p.get("evento_id"))
                    == str(luta["evento_id"])
                )
            ]

            ja_tem_mma = any(
                p.get("tipo_previsao") == "mma_vencedor"
                for p in existentes_mma
            )

            opcoes_mma = opcoes_para_tipo(
                "mma_vencedor",
                luta,
            )

            label_mma = st.selectbox(
                "Quem vence?",
                list(opcoes_mma.keys()),
                key=f"mma_valor_{luta['evento_id']}",
            )

            valor_mma = opcoes_mma[label_mma]

            confianca_mma = st.select_slider(
                "Confiança",
                options=[1, 2, 3, 4, 5],
                value=3,
                key=f"mma_conf_{luta['evento_id']}",
            )

            dificuldade_mma_nome, multiplicador_mma = (
                dificuldade_mma(luta, valor_mma)
            )

            texto_mma = texto_previsao(
                "mma_vencedor",
                valor_mma,
                luta,
            )

            st.info(
                f"🥋 {texto_mma}\n\n"
                f"Dificuldade: **{dificuldade_mma_nome} "
                f"×{multiplicador_mma:g}** • "
                f"Se acertar: **"
                f"{formatar_pontos(confianca_mma * multiplicador_mma)} "
                f"pontos** • "
                f"Se errar: **-{confianca_mma} pontos**\n\n"
                f"_Dificuldade calculada pelo ranking oficial e "
                f"pela sequência recente dos lutadores._"
            )

            if st.button(
                "Guardar previsão de MMA",
                type="primary",
                key=f"guardar_mma_{luta['evento_id']}",
            ):
                if jogo_comecou(luta):
                    st.error("A luta já começou.")
                elif ja_tem_mma:
                    st.warning(
                        "Já tens uma previsão para esta luta."
                    )
                else:
                    lutador_previsto = (
                        luta["casa"]
                        if valor_mma == "Lutador1"
                        else luta["fora"]
                    )

                    registo_mma = {
                        "user_id":
                            st.session_state["user_id"],
                        "utilizador":
                            st.session_state["nome"],
                        "modalidade":
                            "MMA",
                        "evento_id":
                            luta["evento_id"],
                        "match_id":
                            None,
                        "fonte_api":
                            "ufcalendar",
                        "jogo":
                            luta["jogo"],
                        "casa":
                            luta["casa"],
                        "fora":
                            luta["fora"],
                        "liga":
                            luta["liga"],
                        "data":
                            luta["data"],
                        "previsao":
                            texto_mma,
                        "tipo_previsao":
                            "mma_vencedor",
                        "valor_previsao":
                            valor_mma,
                        "equipa_previsao":
                            lutador_previsto,
                        "confianca":
                            confianca_mma,
                        "dificuldade":
                            dificuldade_mma_nome,
                        "multiplicador":
                            multiplicador_mma,
                        "placar":
                            "",
                        "resultado":
                            "Pendente",
                        "resultado_real":
                            None,
                    }

                    r = guardar_previsao(registo_mma)

                    if (
                        r is not None
                        and r.status_code in [200, 201]
                    ):
                        st.success(
                            "✅ Previsão de MMA guardada!"
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

if pagina_ativa == "📋 Minhas previsões":

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
            ["Todas", "Futebol", "Ténis", "MMA"],
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

if pagina_ativa == "📊 Estatísticas":
    mostrar_estatisticas_completas(minhas, prefixo="minhas")


# =========================================================
# TAB — HISTÓRICO DOS ÚLTIMOS 7 DIAS
# =========================================================

if pagina_ativa == "📜 Histórico":
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

if pagina_ativa == "🏆 Ranking":
    modalidade_ranking = st.radio(
        "Modalidade",
        ["Geral", "Futebol", "Ténis", "MMA"],
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

if pagina_ativa == "⚖️ Comparar":
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

if pagina_ativa == "👥 Perfis":
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

