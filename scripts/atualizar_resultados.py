"""Atualização agendada das previsões desportivas entre amigos.

Executado pelo GitHub Actions; não executa a interface Streamlit.
Só atualiza registos quando o resultado é final e inequívoco.
"""
import os
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

TZ = ZoneInfo("Europe/Lisbon")
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "previsoes-amigos-resultados/1.0"})
UPDATED = 0
WARNINGS = []
BASE = os.getenv("SUPABASE_URL", "").rstrip("/")
ADMIN_KEY = os.getenv("SUPABASE_SECRET_KEY", "")


def warning(message):
    WARNINGS.append(message)
    print("AVISO:", message)


def json_get(url, *, headers=None, params=None):
    try:
        r = SESSION.get(url, headers=headers, params=params, timeout=18)
        if not r.ok:
            warning(f"{url.split('/')[2]}: HTTP {r.status_code}")
            return None
        return r.json()
    except (requests.RequestException, ValueError) as exc:
        warning(f"Consulta falhou: {type(exc).__name__}")
        return None


def admin_headers():
    return {
        "apikey": ADMIN_KEY,
        "Content-Type": "application/json",
    }


def pending_rows():
    data = json_get(
        f"{BASE}/rest/v1/previsoes",
        headers=admin_headers(),
        params={"select": "*", "resultado": "eq.Pendente", "limit": "500"},
    )
    return data if isinstance(data, list) else []


def started(row):
    try:
        dt = datetime.strptime(str(row.get("data")), "%d/%m/%Y %H:%M")
        return dt.replace(tzinfo=TZ) < datetime.now(TZ) - timedelta(hours=2)
    except (TypeError, ValueError):
        return False


def update_prediction(row, score, result_text, correct):
    global UPDATED
    rid = row["id"]
    payload = {
        "placar": str(score),
        "resultado_real": str(result_text),
        "resultado": "Acertou" if correct else "Errou",
    }
    try:
        response = SESSION.patch(
            f"{BASE}/rest/v1/previsoes",
            headers={**admin_headers(), "Prefer": "return=minimal"},
            params={"id": f"eq.{rid}", "resultado": "eq.Pendente"},
            json=payload,
            timeout=18,
        )
        if response.status_code in (200, 204):
            UPDATED += 1
            print(f"Previsão {rid} atualizada")
        else:
            warning(f"Supabase PATCH: HTTP {response.status_code}")
    except requests.RequestException:
        warning("Erro de ligação ao Supabase ao atualizar previsão")


def goals(partida):
    score = partida.get("score") or {}
    reg = score.get("regularTime") or {}
    full = score.get("fullTime") or {}
    half = score.get("halfTime") or {}
    h = reg.get("home")
    a = reg.get("away")
    if h is None or a is None:
        h, a = full.get("home"), full.get("away")
    return h, a, half.get("home"), half.get("away")


def threshold(actual, value):
    if str(value) == "0":
        return actual == 0
    try:
        digits = re.findall(r"\d+", str(value))
        return bool(digits) and actual >= int(digits[-1])
    except (ValueError, TypeError):
        return False


def assess_football(prediction, partida):
    h, a, hh, ha = goals(partida)
    if h is None or a is None:
        return None
    try:
        h, a = int(h), int(a)
    except (TypeError, ValueError):
        return None
    typ = prediction.get("tipo_previsao") or "resultado"
    val = prediction.get("valor_previsao") or prediction.get("previsao")
    score = f"{h}-{a}"
    result = "Casa" if h > a else "Fora" if a > h else "Empate"
    if typ == "resultado":
        return score, result, val == result
    if typ == "dupla_possibilidade":
        matched = {
            "CasaEmpate": ("Casa", "Empate"),
            "ForaEmpate": ("Fora", "Empate"),
            "CasaFora": ("Casa", "Fora"),
        }
        if val not in matched:
            return None
        return score, result, result in matched[val]
    if typ == "golos_tempo_regulamentar":
        return score, f"{h + a} golos", threshold(h + a, val)
    if typ == "golos_intervalo":
        if hh is None or ha is None:
            return None
        total = int(hh) + int(ha)
        return score, f"{total} golos ao intervalo", threshold(total, val)
    if typ in ("golos_equipa_casa", "golos_equipa_fora"):
        total = h if typ.endswith("casa") else a
        return score, f"{total} golos", threshold(total, val)
    if typ == "ambas_marcam":
        value = "Sim" if h > 0 and a > 0 else "Não"
        return score, value, val == value
    return None


def football_data(rows):
    key = os.getenv("FOOTBALL_API_KEY")
    if not rows or not key:
        return
    ids = sorted({str(r["match_id"]) for r in rows if r.get("match_id")})
    for start in range(0, len(ids), 40):
        data = json_get(
            "https://api.football-data.org/v4/matches",
            headers={"X-Auth-Token": key},
            params={"ids": ",".join(ids[start:start + 40])},
        )
        matches = data.get("matches", []) if isinstance(data, dict) else []
        indexed = {
            str(m.get("id")): m
            for m in matches
            if isinstance(m, dict) and m.get("status") == "FINISHED"
        }
        for row in rows:
            match = indexed.get(str(row.get("match_id")))
            if match:
                evaluation = assess_football(row, match)
                if evaluation:
                    update_prediction(row, *evaluation)


def brazil(rows):
    key = os.getenv("BRASILEIRAO_API_KEY")
    if not key:
        return
    headers = {
        "X-RapidAPI-Key": key,
        "X-RapidAPI-Host": "brasileirao-serie-a.p.rapidapi.com",
    }
    by_id = defaultdict(list)
    for row in rows:
        by_id[str(row.get("match_id"))].append(row)
    for identifier, predictions in list(by_id.items())[:12]:
        data = json_get(
            f"https://brasileirao-serie-a.p.rapidapi.com/matches/{identifier}",
            headers=headers,
        )
        item = data.get("data") if isinstance(data, dict) else None
        if not isinstance(item, dict):
            continue
        if str(item.get("status", "")).lower() != "finished":
            continue
        score = item.get("score") or {}
        halftime = item.get("halfTime") or item.get("halftime") or {}
        match = {
            "score": {
                "regularTime": {
                    "home": score.get("home"), "away": score.get("away")
                },
                "halfTime": {
                    "home": halftime.get("home"), "away": halftime.get("away")
                },
            }
        }
        for row in predictions:
            evaluation = assess_football(row, match)
            if evaluation:
                update_prediction(row, *evaluation)


def ninjas(rows):
    key = os.getenv("API_NINJAS_KEY")
    if not rows or not key:
        return
    by_id = defaultdict(list)
    for row in rows:
        if row.get("evento_id"):
            by_id[row["evento_id"]].append(row)
    for game_id, predictions in list(by_id.items())[:8]:
        data = json_get(
            "https://api.api-ninjas.com/v1/soccermatches",
            headers={"X-Api-Key": key},
            params={"game_id": game_id},
        )
        if not isinstance(data, list):
            continue
        item = next(
            (r for r in data if str(r.get("game_id")) == str(game_id)),
            None,
        )
        if not item or item.get("status") != "final":
            continue
        # Não corrigir resultados de prolongamento/penáltis sem golos aos 90'.
        if item.get("decided_in") in ("extra_time", "penalties"):
            continue
        h, a = item.get("home_score"), item.get("away_score")
        if not isinstance(h, int) or not isinstance(a, int):
            continue
        period = item.get("period_scores") or {}
        hp = period.get("home") or []
        ap = period.get("away") or []
        match = {
            "score": {
                "regularTime": {"home": h, "away": a},
                "halfTime": {
                    "home": hp[0] if hp else None,
                    "away": ap[0] if ap else None,
                },
            }
        }
        for row in predictions:
            evaluation = assess_football(row, match)
            if evaluation:
                update_prediction(row, *evaluation)


def normalize(text):
    t = unicodedata.normalize("NFKD", str(text or "").casefold())
    return re.sub(r"[^a-z0-9]", "", "".join(
        c for c in t if not unicodedata.combining(c)
    ))


def tennis(rows):
    key = os.getenv("TENNIS_SCHEDULE_API_KEY")
    if not rows or not key:
        return
    groups = defaultdict(list)
    for row in rows:
        try:
            date = datetime.strptime(row["data"], "%d/%m/%Y %H:%M").date()
            tour = "wta" if row.get("fonte_api") == "tennis_rapidapi_wta" else "atp"
            groups[(tour, date.isoformat())].append(row)
        except (KeyError, ValueError):
            continue
    headers = {
        "X-RapidAPI-Key": key,
        "X-RapidAPI-Host": "tennis-api-atp-wta-itf.p.rapidapi.com",
    }
    for (tour, date), predictions in list(groups.items())[:8]:
        data = json_get(
            f"https://tennis-api-atp-wta-itf.p.rapidapi.com/tennis/v2/{tour}/results/{date}",
            headers=headers,
        )
        if not isinstance(data, dict):
            continue
        records = data.get("data", [])
        if isinstance(records, dict):
            records = records.get("singles", [])
        if not isinstance(records, list):
            continue
        for row in predictions:
            pnames = {normalize(row.get("casa")), normalize(row.get("fora"))}
            candidates = []
            for item in records:
                if not isinstance(item, dict) or not item.get("result"):
                    continue
                player1 = item.get("player1") or {}
                player2 = item.get("player2") or {}
                if not isinstance(player1, dict) or not isinstance(player2, dict):
                    continue
                names = {normalize(player1.get("name")), normalize(player2.get("name"))}
                if pnames == names and all(names):
                    candidates.append(item)
            # Correspondência única de participantes e dia; caso contrário, não atribuir.
            if len(candidates) != 1:
                continue
            item = candidates[0]
            winner = (item.get("player1") or {}).get("name")
            chosen = row.get("casa") if row.get("valor_previsao") == "Jogador1" else row.get("fora")
            if not winner or not chosen:
                continue
            update_prediction(
                row,
                item["result"],
                winner,
                normalize(winner) == normalize(chosen),
            )


def ufc(rows):
    key = os.getenv("UFC_API_KEY")
    if not rows or not key:
        return
    ids = defaultdict(list)
    for row in rows:
        if row.get("evento_id"):
            ids[row["evento_id"]].append(row)
    for event_id, predictions in list(ids.items())[:6]:
        data = json_get(
            f"https://api.ufcalendar.com/v1/fights/{event_id}",
            headers={"Authorization": f"Bearer {key}"},
        )
        item = data.get("data") if isinstance(data, dict) else None
        if not isinstance(item, dict):
            continue
        outcome = item.get("result") or {}
        winner_id = outcome.get("winner_fighter_id")
        fighter_a = item.get("fighter_a") or {}
        fighter_b = item.get("fighter_b") or {}
        if winner_id is None:
            continue
        if str(winner_id) == str(fighter_a.get("id")):
            winner_name, idx = fighter_a.get("name"), 0
        elif str(winner_id) == str(fighter_b.get("id")):
            winner_name, idx = fighter_b.get("name"), 1
        else:
            continue
        score = str(outcome.get("method") or "Resultado final")
        for row in predictions:
            if row.get("valor_previsao") not in ("Lutador1", "Lutador2"):
                continue
            selected = 0 if row.get("valor_previsao") == "Lutador1" else 1
            update_prediction(row, score, winner_name, selected == idx)


def main():
    if not BASE or not ADMIN_KEY:
        print("GitHub Secrets SUPABASE_URL e SUPABASE_SECRET_KEY em falta.")
        return 2
    pending = [r for r in pending_rows() if started(r)]
    print(f"Previsões pendentes com início há mais de 2 horas: {len(pending)}")
    groups = defaultdict(list)
    for row in pending:
        source = row.get("fonte_api")
        if source in ("tennis_rapidapi_atp", "tennis_rapidapi_wta"):
            groups["tennis"].append(row)
        elif source in ("ufcalendar", "api_mma"):
            groups["ufc"].append(row)
        elif source == "rapidapi_brasileirao" or row.get("liga") == "Brasileirão Série A":
            groups["brazil"].append(row)
        elif source == "api_ninjas_football":
            groups["ninjas"].append(row)
        elif source == "football_data" or source is None:
            groups["football"].append(row)
    football_data(groups["football"])
    brazil(groups["brazil"])
    ninjas(groups["ninjas"])
    tennis(groups["tennis"])
    ufc(groups["ufc"])
    print(f"Previsões atualizadas: {UPDATED}; avisos: {len(WARNINGS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
