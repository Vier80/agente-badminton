"""Agente Badminton · versione 2.0 (17/11/2026, scheda 32)

Legge i risultati dei tornei BWF e li scrive nel database Notion «🏸 Agente Badminton».
Poi prepara badminton.json per la pagina web badminton.html e lo pubblica sul
repository della mappa (map-q-la28).

Tornei letti, dalla stagione 2026:
  - HSBC BWF World Tour: Super 300, Super 500, Super 750, Super 1000 e World Tour Finals
    (delle Finals anche gli incontri dei gironi, scritti con Turno = "Group Stage");
  - Mondiali individuali (BWF World Championships).
Esclusi: qualificazioni, gare a squadre, campionati e Giochi continentali, juniores,
senior (over), para-badminton.

NOVITÀ della versione 2.0:
  1. nuove categorie: Super 750, Super 1000, World Tour Finals, Mondiali;
  2. nuove colonne ID BWF 1 (vincitore) e ID BWF 2 (sconfitto) con il codice BWF del giocatore;
     nei doppi i due codici in ordine crescente, separati da "/" (es. "65267/89426"),
     così la stessa coppia ha sempre lo stesso codice, in qualunque ordine la dia il sito;
  3. lettura del database divisa in 6 ricerche (una per Disciplina + una di controllo),
     perché l'API di Notion non restituisce più di 10.000 righe per ricerca (schede 29 e 30);
  4. modalità "completa": scrive ID BWF 1 / ID BWF 2 nelle righe già presenti che non li hanno.
     Solo nelle celle vuote: nessun dato viene sovrascritto o cancellato;
  5. un torneo con stato "finished" non viene più scambiato per annullato (era il caso
     dell'All England 2026); annullato = la parola "cancel" nello stato o nel nome;
  6. la prova (DRY_RUN) legge anche Notion, senza scrivere, e dice quante righe scriverebbe;
  7. limite di tempo: passati TEMPO_MAX_MINUTI smette di scrivere e chiude in ordine;
     il lancio successivo riprende da dove si era fermato, senza doppioni.
La pagina web badminton.html per ora mostra solo Super 300 e 500 (vedi CATEGORIE_WEB):
il file badminton.json resta quindi identico a prima.

Variabili (i "Secrets" e gli input di GitHub):
  NOTION_TOKEN        token della connessione Notion (la stessa dello squash)
  NOTION_DATABASE_ID  link o codice del database "Agente Badminton"
  MAPPA_TOKEN         token GitHub che può scrivere su map-q-la28 (facoltativo:
                      senza, Notion si aggiorna lo stesso ma la pagina web no)
  DRY_RUN=true        prova: legge BWF e Notion e stampa cosa farebbe, senza scrivere nulla
  MODALITA            "aggiungi" (predefinita) = solo incontri nuovi;
                      "completa" = in più scrive gli ID BWF nelle righe già presenti
  TEMPO_MAX_MINUTI    dopo quanti minuti smettere di scrivere (predefinito 110)
"""
import base64
import json
import os
import re
import sys
import time
from datetime import date, timedelta

import requests

# ------------------------------------------------------------- impostazioni
ANNO_INIZIO = 2026          # da quale stagione partire

# Codici BWF delle categorie da chiedere al sito (elenco /vue-tournament-categories,
# verificato il 17/11/2026): 2 = Grade 1 individuali (Mondiali), 8 = World Tour Finals,
# 9 = Super 1000, 10 = Super 750, 11 = Super 500, 12 = Super 300.
CODICI_CATEGORIA = [2, 8, 9, 10, 11, 12]

# Nome della categoria sul sito BWF -> valore della colonna "Categoria" in Notion.
# (Le opzioni nuove della colonna Categoria le crea Notion da solo alla prima scrittura.)
CATEGORIE = {
    "HSBC BWF World Tour Finals": "World Tour Finals",
    "HSBC BWF World Tour Super 1000": "Super 1000",
    "HSBC BWF World Tour Super 750": "Super 750",
    "HSBC BWF World Tour Super 500": "Super 500",
    "HSBC BWF World Tour Super 300": "Super 300",
    "Grade 1 - Individual Tournaments": "Mondiali",
}
# Nella categoria "Grade 1 - Individual Tournaments" si tengono solo i Mondiali senior
# (es. "BWF World Championships 2026"), non i Mondiali over, juniores o para.
PAROLE_MONDIALI = "world championships"
PAROLE_ESCLUSE = ("senior", "junior", "para")

# Categorie mostrate dalla pagina badminton.html (per ora solo Super 300 e 500, come prima)
CATEGORIE_WEB = {"Super 500", "Super 300"}

MAPPA_REPO = "vier80/map-q-la28"
MAPPA_FILE = "badminton.json"
MAPPA_WORKFLOW = "aggiorna.yml"
PAUSE_SECONDS = 1.0         # pausa tra una richiesta e l'altra al sito BWF
PAUSA_NOTION = 0.35         # pausa tra una scrittura e l'altra su Notion

# Una ricerca dell'API Notion restituisce al massimo 10.000 righe (scoperto il 16/11/2026, scheda 28 §3)
LIMITE_NOTION = 10000

API = "https://extranet-lv.bwfbadminton.com/api"
WEB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AgenteBadminton/2.0; uso personale)",
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
MODALITA = (os.getenv("MODALITA", "").strip().lower() or "aggiungi")
try:
    TEMPO_MAX_MINUTI = float(os.getenv("TEMPO_MAX_MINUTI", "").strip() or 110)
except ValueError:
    TEMPO_MAX_MINUTI = 110.0

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
    "id1": ["ID BWF 1"],          # novità 2.0
    "p2": ["Player 2"],
    "noc2": ["NOC 2"],
    "id2": ["ID BWF 2"],          # novità 2.0
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
# Gironi delle World Tour Finals: sul sito drawName = "MS - Group A", roundName = "R1", "R2", "R3"
GIRONE = "Group Stage"
DISCIPLINE = ["MS", "WS", "MD", "WD", "XD"]
GENDER = {"MS": "Men", "MD": "Men", "WS": "Women", "WD": "Women", "XD": "Mixed"}
STATUS_IT = {"Retired": "ritiro", "Walkover": "walkover", "Disqualified": "squalifica",
             "No Match": "non disputato"}

INIZIO = time.monotonic()     # per il limite di tempo


def die(msg):
    print(f"ERRORE: {msg}")
    sys.exit(1)


def tempo_scaduto():
    """True se sono passati più di TEMPO_MAX_MINUTI dall'avvio dello script."""
    return (time.monotonic() - INIZIO) / 60 > TEMPO_MAX_MINUTI


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


def categoria_di(t):
    """Valore di "Categoria" in Notion per un torneo del calendario BWF (None = torneo da saltare)."""
    nome_cat = (t.get("category") or "").replace("–", "-").replace("—", "-")
    nome_cat = re.sub(r"\s+", " ", nome_cat).strip()        # "Grade 1 – Individual…" -> "Grade 1 - Individual…"
    cat = CATEGORIE.get(nome_cat)
    if cat == "Mondiali":
        nome = (t.get("name") or "").lower()
        if PAROLE_MONDIALI not in nome or any(p in nome for p in PAROLE_ESCLUSE):
            return None                                     # altri eventi di Grade 1 (es. Mondiali over)
    return cat


def tournaments_of_year(year):
    """Tornei dell'anno nelle categorie scelte, in ordine di data."""
    params = [("startDate", f"{year}-01-01"), ("endDate", f"{year}-12-31"),
              ("page", 1), ("perPage", 100), ("drawCount", 1), ("activeTab", 1)]
    params += [("category[]", c) for c in CODICI_CATEGORIA]
    data = bwf("/vue-tournaments-search", params)
    out = []
    for t in data.get("results", {}).get("data", []):
        cat = categoria_di(t)
        if not cat or not t.get("code"):
            continue
        # Stato del torneo: "normal", "finished", ... Annullato solo se "cancel" compare nello stato
        # o nel nome (la versione 1.0 scartava tutto ciò che non era "normal", anche "finished").
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
            "status": status,
            "cancelled": "cancel" in status or "cancel" in t["name"].lower(),
        })
    return sorted(out, key=lambda t: t["start"])


def days(t, today):
    d = date.fromisoformat(t["start"])
    end = min(date.fromisoformat(t["end"]), today)
    while d <= end:
        yield d.isoformat()
        d += timedelta(days=1)


def team(side):
    """Nomi, NOC e codice BWF di un lato dell'incontro (giocatore o coppia).

    Codice BWF: nel singolo il codice del giocatore (es. "65267"); nel doppio i due codici
    in ordine crescente separati da "/" (es. "65267/89426"). Vuoto se il sito non dà il
    codice di tutti i giocatori.
    """
    players = side.get("players") or []
    names = " / ".join(p.get("nameDisplay", "").strip() for p in players)
    codes = []
    for p in players:
        c = (p.get("countryCode") or "").strip()
        if c and c not in codes:
            codes.append(c)
    noc = (side.get("countryCode") or "").strip() or "/".join(codes)
    ids = [str(p.get("id") or "").strip() for p in players]
    if players and all(ids):
        ids.sort(key=lambda x: (len(x), x))      # ordine numerico anche se i codici sono testo
        bwf_id = "/".join(ids)
    else:
        bwf_id = ""
    return names, noc, bwf_id


def parse_match(m, t):
    """Trasforma un incontro del sito in una riga per Notion (None se va saltato)."""
    if m.get("matchStatus") != "F" or m.get("isTeamMatch"):
        return None                                   # non concluso, oppure gara a squadre
    draw = (m.get("drawName") or "")
    round_name = m.get("roundName") or ""
    if "qual" in draw.lower() or round_name.lower().startswith("qual"):
        return None                                   # qualificazioni
    if "group" in draw.lower():
        rnd = GIRONE                                  # gironi delle World Tour Finals (R1, R2, R3)
    else:
        rnd = ROUNDS.get(round_name)
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
        # ID univoco invariato rispetto alla 1.0 (per i gironi roundName è "R1", "R2", "R3")
        "id": f"BWF|{t['id']}|{disc}|{round_name}|{m.get('id')}",
        "p1": home[0], "noc1": home[1], "id1": home[2],
        "p2": away[0], "noc2": away[1], "id2": away[2],
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


def fette_di_lettura(mapping):
    """Le ricerche in cui è divisa la lettura del database: [(etichetta, filtro), ...].

    - 5 ricerche, una per Disciplina (MS, WS, MD, WD, XD): circa 900 righe l'una per stagione,
      quindi lontane dal limite di 10.000 righe fino al 2028 e oltre;
    - 1 ricerca "di controllo" per le righe con Disciplina vuota o scritta in modo diverso.
      Di norma trova 0 righe, ma garantisce che nessuna riga resti fuori.
    Una pagina trovata da due ricerche viene contata una volta sola.
    """
    nome, tipo = mapping["discipline"]
    fette = [(d, {"property": nome, tipo: {"equals": d}}) for d in DISCIPLINE]
    altre = {"or": [
        {"property": nome, tipo: {"is_empty": True}},
        {"and": [{"property": nome, tipo: {"does_not_equal": d}} for d in DISCIPLINE]},
    ]}
    fette.append(("altre (Disciplina vuota o diversa)", altre))
    return fette


def existing_rows(mapping):
    """Tutte le righe già presenti nel database (servono anche per la pagina web).

    Versione 2.0: lettura divisa in 6 ricerche (fette_di_lettura). Se una ricerca arriva
    a 10.000 righe lo script si ferma, anche in prova: oltre il limite non vedrebbe le altre
    righe e scriverebbe DOPPIONI.
    """
    rows, viste, conteggi, senza_id = [], set(), [], 0
    for etichetta, filtro in fette_di_lettura(mapping):
        lette, cursor = 0, None
        while True:
            body = {"page_size": 100, "filter": filtro}
            if cursor:
                body["start_cursor"] = cursor
            data = notion("POST", f"/databases/{DATABASE_ID}/query", json=body)
            for page in data["results"]:
                lette += 1
                if page["id"] in viste:          # già letta da un'altra ricerca
                    continue
                viste.add(page["id"])
                p = page["properties"]
                row = {key: plain(p.get(name, {})) for key, (name, _) in mapping.items() if key != "title"}
                row["page"] = page["id"]         # serve alla modalità "completa"
                if row.get("id"):
                    rows.append(row)
                else:
                    senza_id += 1
            if not data.get("has_more"):
                break
            cursor = data["next_cursor"]
        if lette >= LIMITE_NOTION:
            die(f"la ricerca '{etichetta}' ha restituito {lette} righe, cioè il limite dell'API Notion: "
                "alcune righe potrebbero non essere state lette e l'agente scriverebbe DOPPIONI. "
                "Bisogna dividere ancora la lettura (per esempio Disciplina e anno).")
        conteggi.append((etichetta, lette))
    print(f"Righe lette dal db Notion: {len(viste)} in {len(conteggi)} ricerche "
          f"(la più grande: {max(n for _, n in conteggi)} righe; limite di Notion {LIMITE_NOTION})")
    print("  " + " · ".join(f"{e} {n}" for e, n in conteggi))
    if senza_id:
        print(f"Nota: {senza_id} righe in Notion non hanno 'ID Univoco': lo script le lascia stare.")
    return rows


def notion_value(ptype, value):
    value = "" if value is None else str(value)
    if ptype == "title":
        return {"title": [{"text": {"content": value[:2000]}}]}
    if ptype == "rich_text":
        return {"rich_text": [{"text": {"content": value[:2000]}}] if value else []}
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


def id_da_completare(riga, m):
    """Le colonne ID BWF da scrivere in una riga già presente: solo quelle VUOTE in Notion
    per cui il sito dà un codice. Restituisce {"id1": codice, ...} (vuoto = niente da fare)."""
    return {k: m[k] for k in ("id1", "id2") if not (riga.get(k) or "").strip() and m.get(k)}


def completa_riga(mapping, riga, valori):
    """Scrive in una riga esistente SOLO le colonne ID BWF vuote (regola: nessun dato sovrascritto)."""
    props = {mapping[k][0]: notion_value(mapping[k][1], v) for k, v in valori.items()}
    notion("PATCH", f"/pages/{riga['page']}", json={"properties": props})


# ---------------------------------------------------------- pagina web
ROUND_ORDER = {"Final": 0, "Semi Final": 1, "Quarter Final": 2, "Round of 16": 3,
               "Round of 32": 4, "Round of 64": 5, GIRONE: 6}


def build_web_data(tournaments, rows, today):
    """Prepara badminton.json: calendario della stagione + tutti gli incontri.

    Versione 2.0: solo le categorie della pagina (CATEGORIE_WEB, oggi Super 300 e 500),
    così badminton.html continua a funzionare senza modifiche.
    """
    tournaments = [t for t in tournaments if t["category"] in CATEGORIE_WEB]
    rows = [r for r in rows if r.get("category", "") in CATEGORIE_WEB]
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
    if MODALITA not in ("aggiungi", "completa"):
        die(f"MODALITA deve essere 'aggiungi' o 'completa' (ricevuto: '{MODALITA}').")
    if not DRY_RUN and (not NOTION_TOKEN or not DATABASE_ID):
        die("mancano NOTION_TOKEN o NOTION_DATABASE_ID (controlla i Secrets su GitHub).")
    today = date.today()
    print(f"Agente Badminton 2.0 · modalità {MODALITA}" + (" · PROVA: non scrivo nulla" if DRY_RUN else ""))

    # 1. righe già presenti in Notion (in prova solo se ci sono i Secrets; sempre in sola lettura)
    mapping, rows = None, []
    if NOTION_TOKEN and DATABASE_ID:
        mapping = read_schema()
        rows = existing_rows(mapping)      # si ferma da sola se una ricerca arriva a 10.000 righe
    else:
        print("PROVA senza Secrets di Notion: non leggo il database.")
    done = {r["id"] for r in rows}
    per_uid = {}                           # ID Univoco -> righe Notion con quell'ID (di solito una)
    for r in rows:
        per_uid.setdefault(r["id"], []).append(r)

    # 2. calendario dei tornei
    tournaments = []
    for year in range(ANNO_INIZIO, today.year + 1):
        try:
            tournaments += tournaments_of_year(year)
        except requests.RequestException as e:
            die(f"il sito BWF non risponde per il calendario {year}: {e}")
    per_cat = {}
    for t in tournaments:
        per_cat[t["category"]] = per_cat.get(t["category"], 0) + 1
    print(f"Tornei in calendario: {len(tournaments)} ("
          + ", ".join(f"{c} {n}" for c, n in sorted(per_cat.items())) + ")")

    # 3. tornei uno per uno
    errors = added = completate = 0
    da_scrivere = da_completare = 0       # conteggi per la prova
    fermato = False
    for t in tournaments:
        if fermato:
            break
        if t["cancelled"] or t["start"] > today.isoformat():
            continue
        prefix = f"BWF|{t['id']}|"
        finals = sum(1 for i in done if i.startswith(prefix) and "|Final|" in i)
        concluso = finals >= 5 and t["end"] < (today - timedelta(days=2)).isoformat()
        if concluso:
            if MODALITA != "completa":
                continue                   # torneo già caricato tutto: non lo rileggo
            # in "completa" lo rileggo solo se qualche sua riga non ha ancora gli ID BWF
            if not any(i.startswith(prefix) and any(not (r.get("id1") and r.get("id2")) for r in per_uid[i])
                       for i in per_uid):
                continue
        print(f"\n{t['category']} · {t['name']} ({t['start']} – {t['end']})")
        try:
            matches = tournament_matches(t, today)
        except requests.RequestException as e:
            print(f"  Sito non raggiungibile: {e}")
            errors += 1
            continue
        new = [m for m in matches if m["id"] not in done]
        senza_id = sum(1 for m in matches if not m["id1"] or not m["id2"])
        vecchie = []                       # (riga Notion, valori da scrivere) per la modalità completa
        if MODALITA == "completa":
            for m in matches:
                for riga in per_uid.get(m["id"], []):
                    valori = id_da_completare(riga, m)
                    if valori:
                        vecchie.append((riga, valori))
        print(f"  Incontri conclusi: {len(matches)}, nuovi: {len(new)}"
              + (f", righe a cui aggiungere gli ID BWF: {len(vecchie)}" if MODALITA == "completa" else "")
              + (f" · ATTENZIONE {senza_id} incontri senza codice BWF" if senza_id else ""))

        if DRY_RUN:
            for m in new[:8]:
                print(f"  [{m['discipline']}] {m['round']} | {m['p1']} ({m['noc1']}, {m['id1'] or '—'}) b. "
                      f"{m['p2']} ({m['noc2']}, {m['id2'] or '—'}) | {m['score']} | {m['date']}")
            for riga, valori in vecchie[:3]:
                print(f"  completerei «{riga.get('p1')} vs {riga.get('p2')}»: "
                      + ", ".join(f"{COLUMNS[k][0]} = {v}" for k, v in valori.items()))
            da_scrivere += len(new)
            da_completare += len(vecchie)
            rows += new
            continue

        for m in new:
            if tempo_scaduto():
                fermato = True
                break
            try:
                create_row(mapping, m)
                done.add(m["id"])
                rows.append(m)
                added += 1
                time.sleep(PAUSA_NOTION)
            except RuntimeError as e:
                print(f"  Errore su {m['p1']} vs {m['p2']}: {e}")
                errors += 1
        for riga, valori in vecchie:
            if fermato or tempo_scaduto():
                fermato = True
                break
            try:
                completa_riga(mapping, riga, valori)
                riga.update(valori)        # così il torneo non risulta più "da completare"
                completate += 1
                time.sleep(PAUSA_NOTION)
            except RuntimeError as e:
                print(f"  Errore nel completare «{riga.get('p1')} vs {riga.get('p2')}»: {e}")
                errors += 1

    # 4. riepilogo e pagina web
    payload = build_web_data(tournaments, rows, today)
    if DRY_RUN:
        with open(MAPPA_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        print(f"\nProva terminata. Righe nuove che scriverei: {da_scrivere}"
              + (f"; righe a cui aggiungerei gli ID BWF: {da_completare}" if MODALITA == "completa" else "")
              + f". {MAPPA_FILE} salvato in locale ({len(payload['incontri'])} incontri Super 300/500).")
    else:
        print(f"\nFatto. Righe aggiunte: {added}."
              + (f" Righe completate con gli ID BWF: {completate}." if MODALITA == "completa" else "")
              + f" Errori: {errors}.")
        if fermato:
            print(f"Limite di tempo ({TEMPO_MAX_MINUTI:.0f} minuti) raggiunto: mi sono fermato in ordine. "
                  "Rilancia l'agente con la stessa modalità: riprende da dove si era fermato, senza doppioni.")
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
