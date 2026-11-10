"""Agente Badminton

Legge i risultati dei tornei BWF World Tour Super 300 e Super 500 e li scrive
in un database Notion. Poi prepara badminton.json per la pagina web e lo
pubblica sul repository della mappa (map-q-la28).

Variabili (i "Secrets" di GitHub):
  NOTION_TOKEN        token della connessione Notion (la stessa dello squash)
  NOTION_DATABASE_ID  link o codice del database "Agente Badminton"
  MAPPA_TOKEN         token GitHub che può scrivere su map-q-la28 (facoltativo:
                      senza, Notion si aggiorna lo stesso ma la pagina web no)
  DRY_RUN=true        prova: stampa i dati trovati senza scrivere nulla
"""
import base64
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta

import requests

# ------------------------------------------------------------- impostazioni
ANNO_INIZIO = 2026          # da quale stagione partire
CATEGORIE = {11: "Super 500", 12: "Super 300"}   # codici BWF delle categorie
MAPPA_REPO = "vier80/map-q-la28"
MAPPA_FILE = "badminton.json"
MAPPA_WORKFLOW = "aggiorna.yml"
PAUSE_SECONDS = 1.0         # pausa tra una richiesta e l'altra al sito BWF

API = "https://extranet-lv.bwfbadminton.com/api"
WEB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AgenteBadminton/1.0; uso personale)",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://bwfworldtour.bwfbadminton.com",
    "Referer": "https://bwfworldtour.bwfbadminton.com/",
}

NOTION_TOKEN = os.getenv("NOTION_TOKEN", "").strip()
_raw_id = os.getenv("NOTION_DATABASE_ID", "").strip().replace("-", "")
_found = re.search(r"[0-9a-fA-F]{32}", _raw_id)
DATABASE_ID = _found.group(0) if _found else _raw_id
MAPPA_TOKEN = os.getenv("MAPPA_TOKEN", "").strip()
DRY_RUN = os.getenv("DRY_RUN", "").strip().lower() == "true"

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

# Nomi delle colonne nel database (maiuscole/minuscole non contano).
# La colonna "titolo" viene riconosciuta da sola, qualunque nome abbia.
COLUMNS = {
    "p1": ["Player 1"],
    "noc1": ["NOC 1"],
    "p2": ["Player 2"],
    "noc2": ["NOC 2"],
    "gender": ["Gender"],
    "discipline": ["Disciplina"],
    "score": ["Score"],
    "round": ["Turno"],
    "tournament": ["Torneo"],
    "category": ["Categoria"],
    "date": ["Data"],
    "source": ["Fonte", "Fonte (link)", "Link"],
    "id": ["ID univoco", "ID"],
}

# Turni del tabellone principale (le qualificazioni vengono saltate)
ROUNDS = {
    "R64": "Round of 64",
    "R32": "Round of 32",
    "R16": "Round of 16",
    "QF": "Quarter Final",
    "SF": "Semi Final",
    "Final": "Final",
    "F": "Final",
}
GENDER = {"MS": "Men", "MD": "Men", "WS": "Women", "WD": "Women", "XD": "Mixed"}
STATUS_IT = {"Retired": "ritiro", "Walkover": "walkover", "Disqualified": "squalifica",
             "No Match": "non disputato"}


def die(msg):
    print(f"ERRORE: {msg}")
    sys.exit(1)


# ------------------------------------------------------------- sito BWF
def bwf(path, params):
    """Chiede dati al servizio pubblico del sito BWF (risposta in JSON)."""
    for attempt in range(3):
        try:
            r = requests.get(API + path, params=params, headers=WEB_HEADERS, timeout=30)
            r.raise_for_status()
            time.sleep(PAUSE_SECONDS)
            return r.json()
        except (requests.RequestException, ValueError):
            if attempt == 2:
                raise
            time.sleep(5)


def tournaments_of_year(year):
    """Tornei Super 300 e Super 500 dell'anno, in ordine di data."""
    params = [("startDate", f"{year}-01-01"), ("endDate", f"{year}-12-31"),
              ("page", 1), ("perPage", 100), ("drawCount", 1), ("activeTab", 1)]
    params += [("category[]", c) for c in CATEGORIE]
    data = bwf("/vue-tournaments-search", params)
    out = []
    for t in data.get("results", {}).get("data", []):
        cat = next((v for v in CATEGORIE.values() if v in (t.get("category") or "")), None)
        if not cat or not t.get("code"):
            continue
        status = ((t.get("status") or {}).get("code") or "normal").lower()
        out.append({
            "id": t["id"],
            "code": t["code"],
            "name": t["name"].strip(),
            "category": cat,
            "start": t["start_date"][:10],
            "end": t["end_date"][:10],
            "location": t.get("location") or "",
            "country": t.get("country") or "",
            "url": t.get("url") or "",
            "cancelled": status != "normal" or "cancel" in t["name"].lower(),
        })
    return sorted(out, key=lambda t: t["start"])


def days(t, today):
    d = date.fromisoformat(t["start"])
    end = min(date.fromisoformat(t["end"]), today)
    while d <= end:
        yield d.isoformat()
        d += timedelta(days=1)


def team(side):
    players = side.get("players") or []
    names = " / ".join(p.get("nameDisplay", "").strip() for p in players)
    codes = []
    for p in players:
        c = (p.get("countryCode") or "").strip()
        if c and c not in codes:
            codes.append(c)
    noc = (side.get("countryCode") or "").strip() or "/".join(codes)
    return names, noc


def parse_match(m, t):
    """Trasforma un incontro del sito in una riga per Notion (None se va saltato)."""
    if m.get("matchStatus") != "F" or m.get("isTeamMatch"):
        return None                                   # non concluso
    if "qual" in (m.get("drawName") or "").lower() or (m.get("roundName") or "").lower().startswith("qual"):
        return None                                   # qualificazioni
    rnd = ROUNDS.get(m.get("roundName"))
    disc = (m.get("eventName") or "").upper()
    if not rnd or disc not in GENDER:
        return None
    w = m.get("winner")
    if w not in (1, 2):
        return None
    home, away = team(m.get("team1") or {}), team(m.get("team2") or {})
    if not home[0] or not away[0]:
        return None
    sets = [(s.get("home"), s.get("away")) for s in (m.get("score") or [])
            if s.get("home") is not None and s.get("away") is not None]
    if w == 2:                                        # il vincitore va sempre per primo
        home, away = away, home
        sets = [(b, a) for a, b in sets]
    status = m.get("scoreStatusValue") or "Normal"
    games = ", ".join(f"{a}-{b}" for a, b in sets)
    if status == "Normal":
        g1 = sum(1 for a, b in sets if a > b)
        g2 = sum(1 for a, b in sets if b > a)
        score = f"{g1}-{g2} ({games})"
    else:
        score = STATUS_IT.get(status, status.lower()) + (f" ({games})" if games else "")
    day = (m.get("matchTime") or "")[:10]
    return {
        "id": f"BWF|{t['id']}|{disc}|{m.get('roundName')}|{m.get('id')}",
        "p1": home[0], "noc1": home[1], "p2": away[0], "noc2": away[1],
        "gender": GENDER[disc], "discipline": disc, "score": score, "round": rnd,
        "tournament": t["name"].replace(",", " "), "category": t["category"], "date": day,
        "source": (t["url"].rstrip("/") + "/results/" + day) if t["url"] and day else t["url"],
    }


def tournament_matches(t, today):
    found = {}
    for d in days(t, today):
        data = bwf("/tournaments/day-matches",
                   {"tournamentCode": t["code"], "date": d, "order": 2, "court": 0})
        for m in data if isinstance(data, list) else []:
            row = parse_match(m, t)
            if row:
                found[row["id"]] = row
    return list(found.values())


# ------------------------------------------------------------------- Notion
def notion(method, path, **kwargs):
    r = None
    for attempt in range(4):
        r = requests.request(method, "https://api.notion.com/v1" + path,
                             headers=NOTION_HEADERS, timeout=30, **kwargs)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(float(r.headers.get("Retry-After", 2)) + attempt)
            continue
        break
    if not r.ok:
        raise RuntimeError(f"Notion ha risposto {r.status_code}: {r.text[:400]}")
    return r.json()


def read_schema():
    props = notion("GET", f"/databases/{DATABASE_ID}")["properties"]
    by_name = {n.strip().lower(): (n, p["type"]) for n, p in props.items()}
    mapping, missing = {}, []
    for key, options in COLUMNS.items():
        hit = next((by_name[o.lower()] for o in options if o.lower() in by_name), None)
        if hit:
            mapping[key] = hit
        else:
            missing.append(options[0])
    titles = [(n, p["type"]) for n, p in props.items() if p["type"] == "title"]
    mapping["title"] = titles[0]
    if missing:
        die("nel database non trovo queste colonne: " + ", ".join(missing)
            + ". Colonne presenti: " + ", ".join(props.keys()))
    return mapping


def plain(prop):
    t = prop.get("type")
    if t in ("title", "rich_text"):
        return "".join(p.get("plain_text", "") for p in prop.get(t) or [])
    if t == "select":
        return (prop.get("select") or {}).get("name", "")
    if t == "date":
        return (prop.get("date") or {}).get("start", "") or ""
    if t == "url":
        return prop.get("url") or ""
    return ""


def existing_rows(mapping):
    """Tutte le righe già presenti nel database (servono anche per la pagina web)."""
    rows, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        data = notion("POST", f"/databases/{DATABASE_ID}/query", json=body)
        for page in data["results"]:
            p = page["properties"]
            row = {key: plain(p.get(name, {})) for key, (name, _) in mapping.items() if key != "title"}
            if row.get("id"):
                rows.append(row)
        if not data.get("has_more"):
            return rows
        cursor = data["next_cursor"]


def notion_value(ptype, value):
    value = "" if value is None else str(value)
    if ptype == "title":
        return {"title": [{"text": {"content": value[:2000]}}]}
    if ptype == "rich_text":
        return {"rich_text": [{"text": {"content": value[:2000]}}]}
    if ptype == "select":
        return {"select": {"name": value.replace(",", " ")[:100]} if value else None}
    if ptype == "date":
        return {"date": {"start": value} if value else None}
    if ptype == "url":
        return {"url": value or None}
    raise RuntimeError(f"Tipo di colonna non gestito: {ptype}. Usa testo, selezione, data o URL.")


def create_row(mapping, m):
    values = dict(m)
    values["title"] = f"{m['p1']} vs {m['p2']}"
    props = {name: notion_value(ptype, values.get(key, "")) for key, (name, ptype) in mapping.items()}
    notion("POST", "/pages", json={"parent": {"database_id": DATABASE_ID}, "properties": props})


# ---------------------------------------------------------- pagina web
ROUND_ORDER = {"Final": 0, "Semi Final": 1, "Quarter Final": 2, "Round of 16": 3,
               "Round of 32": 4, "Round of 64": 5}


def build_web_data(tournaments, rows, today):
    """Prepara badminton.json: calendario della stagione + tutti gli incontri."""
    known = {t["name"].replace(",", " "): t for t in tournaments}
    by_name = {}
    for r in rows:
        name = r.get("tournament", "")
        by_name.setdefault(name, []).append(r)
    tornei = []
    for t in tournaments:
        key = t["name"].replace(",", " ")
        n = len(by_name.get(key, []))
        if t["cancelled"]:
            stato = "annullato"
        elif t["start"] > today.isoformat():
            stato = "da giocare"
        elif t["end"] >= today.isoformat():
            stato = "in corso"
        else:
            stato = "concluso"
        tornei.append({"nome": key, "categoria": t["category"], "inizio": t["start"], "fine": t["end"],
                       "luogo": t["location"], "url": t["url"], "stato": stato, "incontri": n})
    for name, rs in by_name.items():           # tornei in Notion ma non più nel calendario
        if name not in known:
            ds = sorted(r.get("date", "") for r in rs if r.get("date"))
            tornei.append({"nome": name, "categoria": rs[0].get("category", ""), "inizio": ds[0] if ds else "",
                           "fine": ds[-1] if ds else "", "luogo": "", "url": "", "stato": "concluso",
                           "incontri": len(rs)})
    tornei.sort(key=lambda t: (t["inizio"], t["nome"]))
    incontri = [[r.get("tournament", ""), r.get("discipline", ""), r.get("round", ""),
                 r.get("p1", ""), r.get("noc1", ""), r.get("p2", ""), r.get("noc2", ""),
                 r.get("score", ""), r.get("date", "")] for r in rows]
    incontri.sort(key=lambda x: (x[0], x[1], ROUND_ORDER.get(x[2], 9), x[8], x[3]))
    return {
        "aggiornato": today.isoformat(),
        "stagioni": sorted({t["inizio"][:4] for t in tornei if t["inizio"]}),
        "campi": ["torneo", "disciplina", "turno", "vincitore", "noc_vincitore",
                  "sconfitto", "noc_sconfitto", "punteggio", "data"],
        "tornei": tornei,
        "incontri": incontri,
    }


def publish_web_data(payload):
    """Scrive badminton.json su map-q-la28 e fa ripartire la pubblicazione della pagina."""
    gh = {"Authorization": f"Bearer {MAPPA_TOKEN}", "Accept": "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28"}
    url = f"https://api.github.com/repos/{MAPPA_REPO}/contents/{MAPPA_FILE}"
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    r = requests.get(url, headers=gh, timeout=30)
    sha, old = None, None
    if r.status_code == 200:
        sha = r.json()["sha"]
        try:
            old = json.loads(base64.b64decode(r.json()["content"]).decode("utf-8"))
        except ValueError:
            old = None
    elif r.status_code != 404:
        raise RuntimeError(f"GitHub ha risposto {r.status_code}: {r.text[:300]}")
    if old and {**old, "aggiornato": ""} == {**payload, "aggiornato": ""}:
        print("Pagina web: nessuna novità, non la ripubblico.")
        return
    body = {"message": "Aggiorna i risultati del badminton",
            "content": base64.b64encode(text.encode("utf-8")).decode("ascii")}
    if sha:
        body["sha"] = sha
    r = requests.put(url, headers=gh, json=body, timeout=60)
    if not r.ok:
        raise RuntimeError(f"GitHub non accetta {MAPPA_FILE}: {r.status_code} {r.text[:300]}")
    r = requests.post(f"https://api.github.com/repos/{MAPPA_REPO}/actions/workflows/{MAPPA_WORKFLOW}/dispatches",
                      headers=gh, json={"ref": "main"}, timeout=30)
    if r.status_code != 204:
        print(f"  Attenzione: {MAPPA_FILE} salvato, ma la ripubblicazione non è partita ({r.status_code}). "
              "Lancia a mano «Aggiorna mappa LA28» su map-q-la28.")
    else:
        print(f"Pagina web: {MAPPA_FILE} aggiornato, ripubblicazione avviata.")


# --------------------------------------------------------------------- main
def main():
    if not DRY_RUN and (not NOTION_TOKEN or not DATABASE_ID):
        die("mancano NOTION_TOKEN o NOTION_DATABASE_ID (controlla i Secrets su GitHub).")
    today = date.today()
    mapping, rows = None, []
    if not DRY_RUN:
        mapping = read_schema()
        rows = existing_rows(mapping)
        print(f"Righe già presenti in Notion: {len(rows)}")
    else:
        print("MODALITÀ PROVA: non scrivo nulla su Notion né sulla pagina web.")
    done = {r["id"] for r in rows}

    tournaments = []
    for year in range(ANNO_INIZIO, today.year + 1):
        try:
            tournaments += tournaments_of_year(year)
        except requests.RequestException as e:
            die(f"il sito BWF non risponde per il calendario {year}: {e}")
    print(f"Tornei Super 300 e 500 in calendario: {len(tournaments)}")

    errors = added = 0
    for t in tournaments:
        if t["cancelled"] or t["start"] > today.isoformat():
            continue
        prefix = f"BWF|{t['id']}|"
        finals = sum(1 for i in done if i.startswith(prefix) and "|Final|" in i)
        if finals >= 5 and t["end"] < (today - timedelta(days=2)).isoformat():
            continue                    # torneo già caricato tutto: non lo rileggo
        print(f"\n{t['category']} · {t['name']} ({t['start']} – {t['end']})")
        try:
            matches = tournament_matches(t, today)
        except requests.RequestException as e:
            print(f"  Sito non raggiungibile: {e}")
            errors += 1
            continue
        new = [m for m in matches if m["id"] not in done]
        print(f"  Incontri conclusi: {len(matches)}, nuovi: {len(new)}")
        if DRY_RUN:
            for m in new[:8]:
                print(f"  [{m['discipline']}] {m['round']} | {m['p1']} ({m['noc1']}) b. "
                      f"{m['p2']} ({m['noc2']}) | {m['score']} | {m['date']}")
            rows += new
            continue
        for m in new:
            try:
                create_row(mapping, m)
                done.add(m["id"])
                rows.append(m)
                added += 1
                time.sleep(0.35)
            except RuntimeError as e:
                print(f"  Errore su {m['p1']} vs {m['p2']}: {e}")
                errors += 1

    payload = build_web_data(tournaments, rows, today)
    if DRY_RUN:
        with open(MAPPA_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        print(f"\nProva terminata: {len(rows)} incontri trovati (mostrati i primi 8 per torneo).")
    else:
        print(f"\nFatto. Righe aggiunte: {added}. Errori: {errors}.")
        if MAPPA_TOKEN:
            try:
                publish_web_data(payload)
            except (RuntimeError, requests.RequestException) as e:
                print(f"Pagina web non aggiornata: {e}")
                errors += 1
        else:
            print("Pagina web: manca MAPPA_TOKEN, non la aggiorno.")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
