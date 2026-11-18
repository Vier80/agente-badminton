"""Agente Badminton · versione 2.1 (18/11/2026, schede 36 e 37)

Legge i risultati dei tornei BWF e li scrive nel database Notion «🏸 Agente Badminton».
Poi prepara badminton.json per la pagina web badminton.html e lo pubblica sul
repository della mappa (map-q-la28).

Tornei letti, dalla stagione 2026:
  - HSBC BWF World Tour: Super 300, Super 500, Super 750, Super 1000 e World Tour Finals
    (delle Finals anche gli incontri dei gironi, scritti con Turno = "Group Stage");
  - Mondiali individuali (BWF World Championships).
Esclusi: qualificazioni, gare a squadre, campionati e Giochi continentali, juniores,
senior (over), para-badminton.

NOVITÀ della versione 2.1 (solo la parte "pagina web"; Notion funziona come nella 2.0):
  1. badminton.json contiene tutte e 6 le categorie (prima solo Super 300 e 500);
  2. per ogni torneo l'agente legge dal sito BWF il TABELLONE UFFICIALE di ogni specialità
     (ordine delle caselle, teste di serie, passaggi senza gioco, incontri in programma,
     gironi delle World Tour Finals con la classifica) e lo mette in badminton.json, nella
     nuova sezione "tabelloni". Il tabellone NON va in Notion (decisione del 18/11/2026);
  3. i tabelloni dei tornei finiti non vengono riletti ogni giorno: l'agente li riprende dal
     badminton.json già pubblicato. Rilegge solo i tornei in corso, quelli che iniziano entro
     7 giorni (il tabellone esce prima dell'inizio) e quelli non ancora salvati;
  4. nei tornei di badminton.json ci sono anche "id" (il codice BWF del torneo, serve alla
     pagina per l'ELO a inizio torneo) e "attesi" (quanti incontri avrà il torneo);
  5. badminton.json può superare 1 MB: per rileggerlo da GitHub si usa la lettura "grezza"
     (oltre 1 MB l'API di GitHub non restituisce il contenuto nel modo normale);
  6. un tabellone che non si riesce a leggere NON ferma l'agente: compare un "Avviso" nel
     registro e si tiene la versione già pubblicata, se c'è.

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
VERSIONE = "2.1"
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

# Categorie mostrate dalla pagina badminton.html. Versione 2.1: tutte e 6.
CATEGORIE_WEB = set(CATEGORIE.values())

# Incontri attesi per torneo quando il tabellone non è disponibile (solo una stima per
# l'anello della pagina): 5 tabelloni da 32 = 155; Mondiali 5 da 64 = 315;
# Finals 5 × (2 gironi da 6 incontri + 2 semifinali + finale) = 75.
ATTESI_PREDEFINITI = {"Mondiali": 315, "World Tour Finals": 75}
ATTESI_SUPER = 155

# Il tabellone di un torneo esce qualche giorno prima dell'inizio: lo leggo già
# quando mancano al massimo questi giorni.
GIORNI_ANTICIPO_TABELLONE = 7

MAPPA_REPO = "vier80/map-q-la28"
MAPPA_FILE = "badminton.json"
MAPPA_WORKFLOW = "aggiorna.yml"
PAUSE_SECONDS = 1.0         # pausa tra una richiesta e l'altra al sito BWF
PAUSA_NOTION = 0.35         # pausa tra una scrittura e l'altra su Notion

# Una ricerca dell'API Notion restituisce al massimo 10.000 righe (scoperto il 16/11/2026, scheda 28 §3)
LIMITE_NOTION = 10000

API = "https://extranet-lv.bwfbadminton.com/api"
WEB_HEADERS = {
    "User-Agent": f"Mozilla/5.0 (compatible; AgenteBadminton/{VERSIONE}; uso personale)",
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
    "id1": ["ID BWF 1"],
    "p2": ["Player 2"],
    "noc2": ["NOC 2"],
    "id2": ["ID BWF 2"],
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
AVVISI = []                   # problemi che non fermano l'agente (es. un tabellone non letto)


def die(msg):
    print(f"ERRORE: {msg}")
    sys.exit(1)


def avviso(msg):
    """Stampa un avviso e lo ricorda per il riepilogo finale (non fa fallire il lancio)."""
    AVVISI.append(msg)
    print(f"  Avviso: {msg}")


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


# ------------------------------------------------------- tabelloni (novità 2.1)
# Servizi del sito BWF usati dalla pagina «Full Draw» (verificati il 18/11/2026, scheda 36 §3):
#   /vue-tournament-draws                  elenco dei tabelloni di un torneo
#   /vue-tournament-draw-data              caselle di un tabellone a eliminazione
#   /vue-tournament-draw-data-round-robin  un girone delle World Tour Finals
#
# Formato di una casella in badminton.json (una lista, per occupare poco spazio):
#   [colonna, posizione, squadra_a, testa_a, squadra_b, testa_b, vincitore, stato, esito, set, data]
#   - colonna: 0 = primo turno, 1 = turno dopo, ...; posizione: 0 = in alto;
#     nei gironi colonna = giornata (R1 = 0, R2 = 1, R3 = 2) e posizione = ordine;
#   - squadra_a / squadra_b: numero della squadra nell'elenco "squadre" del torneo, -1 se vuota
#     (passaggio senza gioco, oppure avversario non ancora noto);
#   - testa_a / testa_b: testa di serie ("1", "3/4"...) oppure "";
#   - vincitore: 1 = squadra_a, 2 = squadra_b, 0 = non ancora giocato;
#   - stato: "F" = finito, "N" = da giocare (altri valori del sito copiati così come sono);
#   - esito: "" (normale), "walkover", "ritiro", "squalifica", "non disputato";
#   - set: punteggi dal punto di vista di squadra_a, es. "21-10 22-20";
#   - data: giorno dell'incontro (AAAA-MM-GG) o "".
CAMPI_CASELLA = ["colonna", "posizione", "squadra_a", "testa_a", "squadra_b", "testa_b",
                 "vincitore", "stato", "esito", "set", "data"]


def tabellone_params(t, extra=None):
    """Parametri comuni ai servizi dei tabelloni (gli stessi che usa il sito BWF)."""
    p = {"tmtTab": "draw", "tmtId": t["id"], "tmtType": 0}
    if extra:
        p.update(extra)
    return p


class Squadre:
    """Elenco delle squadre (giocatori o coppie) di un torneo: ognuna una volta sola.

    Ogni squadra è [nome, noc, codice BWF]; nelle caselle si scrive solo il suo numero.
    """

    def __init__(self):
        self.lista, self.indice = [], {}

    def numero(self, side):
        names, noc, bwf_id = team(side or {})
        names = re.sub(r"\s+", " ", names).strip()       # "SU Li  Yang" -> "SU Li Yang"
        if not names:
            return -1                                      # lato vuoto
        chiave = bwf_id or names
        if chiave not in self.indice:
            self.indice[chiave] = len(self.lista)
            self.lista.append([names, noc, bwf_id])
        return self.indice[chiave]


def set_testo(m):
    """Punteggio dei set dal punto di vista di team1 (squadra_a), es. "21-10 22-20"."""
    return " ".join(f"{s.get('home')}-{s.get('away')}" for s in (m.get("score") or [])
                    if s.get("home") is not None and s.get("away") is not None)


def casella(col, pos, m, squadre):
    """Una casella del tabellone nel formato compatto di badminton.json (vedi CAMPI_CASELLA)."""
    w = m.get("winner") if m.get("winner") in (1, 2) else 0
    stato = (m.get("matchStatus") or "N").strip() or "N"
    esito = STATUS_IT.get(m.get("scoreStatusValue") or "Normal", "")
    return [col, pos,
            squadre.numero(m.get("team1")), str(m.get("team1seed") or "").strip(),
            squadre.numero(m.get("team2")), str(m.get("team2seed") or "").strip(),
            w, stato, esito if stato == "F" else "", set_testo(m), (m.get("matchTime") or "")[:10]]


def completa_teste(caselle):
    """Le caselle dei turni non ancora giocati spesso non hanno la testa di serie: la riprendo
    dalle altre caselle dello stesso tabellone, dove la stessa squadra compare con la testa."""
    teste = {}
    for c in caselle:
        for i_sq, i_ts in ((2, 3), (4, 5)):
            if c[i_sq] >= 0 and c[i_ts]:
                teste[c[i_sq]] = c[i_ts]
    for c in caselle:
        for i_sq, i_ts in ((2, 3), (4, 5)):
            if c[i_sq] >= 0 and not c[i_ts]:
                c[i_ts] = teste.get(c[i_sq], "")


def nomi_turni(caselle):
    """Nome del turno di ogni colonna (R32, R16, QF, SF, Final...), preso dalle caselle."""
    per_col = {}
    for c, rn in caselle:
        if rn and c not in per_col:
            per_col[c] = rn
    return [per_col.get(i, "") for i in range(max(per_col) + 1)] if per_col else []


def leggi_eliminazione(t, draw_id, squadre):
    """Tabellone a eliminazione diretta: caselle nell'ordine ufficiale del sito."""
    data = bwf("/vue-tournament-draw-data", tabellone_params(t, {"drawId": draw_id, "isPara": 0}))
    risultati = data.get("results") or {}
    if not isinstance(risultati, dict) or not risultati:
        raise ValueError("tabellone vuoto")
    caselle, turni = [], []
    for chiave, valore in risultati.items():
        parti = str(chiave).split("-")
        if len(parti) != 2 or not all(p.isdigit() for p in parti):
            continue                                       # chiave inattesa: la salto
        col, pos = int(parti[0]), int(parti[1])
        m = (valore or {}).get("match") or {}
        caselle.append(casella(col, pos, m, squadre))
        turni.append((col, m.get("roundName") or ""))
    caselle.sort(key=lambda c: (c[0], c[1]))
    completa_teste(caselle)
    primo_turno = sum(1 for c in caselle if c[0] == 0)
    return {"tipo": "E", "misura": primo_turno * 2, "turni": nomi_turni(turni), "c": caselle}


def leggi_girone(t, draw_id, nome, squadre):
    """Un girone delle World Tour Finals: incontri e classifica."""
    data = bwf("/vue-tournament-draw-data-round-robin",
               tabellone_params(t, {"drawId": draw_id, "isPara": 0}))
    caselle, contatore = [], {}
    for m in data.get("results") or []:
        rn = m.get("roundName") or ""
        giornata = int(rn[1:]) - 1 if re.fullmatch(r"R\d+", rn) else 0
        pos = contatore.get(giornata, 0)
        contatore[giornata] = pos + 1
        caselle.append(casella(giornata, pos, m, squadre))
    caselle.sort(key=lambda c: (c[0], c[1]))
    completa_teste(caselle)
    classifica = []
    for s in data.get("standings") or []:
        # [posizione, squadra, giocati, vinti, persi, set vinti, set persi, punti fatti, punti subiti]
        classifica.append([s.get("rank") or 0, squadre.numero(s.get("team")),
                           s.get("played") or 0, s.get("won") or 0, s.get("lost") or 0,
                           s.get("game_for") or 0, s.get("game_against") or 0,
                           s.get("point_for") or 0, s.get("point_against") or 0])
    nome = nome.split(" - ", 1)[1] if " - " in nome else nome     # "MS - Group A" -> "Group A"
    return {"nome": nome, "classifica": classifica, "c": caselle}


def leggi_tabelloni(t):
    """Tutti i tabelloni principali di un torneo (qualificazioni escluse).

    Restituisce {"id", "completo", "squadre", "spec": {"MS": {...}, ...}}.
    - Tabellone a eliminazione: {"tipo": "E", "misura", "turni", "c"}.
    - World Tour Finals: {"tipo": "G", "gironi": [...], "misura", "turni", "c"}
      ("c" = semifinali e finale).
    """
    elenco = bwf("/vue-tournament-draws", tabellone_params(t)).get("results") or []
    squadre, spec = Squadre(), {}
    for d in elenco:
        testo = (d.get("text") or "").strip()
        disc = testo.split(" - ")[0].strip().upper()
        if disc not in DISCIPLINE:
            continue
        gruppo = d.get("type") == 1 or "group" in testo.lower()
        # Qualificazioni: stage "Qualifying", tipo 5 o "qual" nel nome. Attenzione: i gironi
        # delle Finals hanno qualification = 1 ma NON sono qualificazioni.
        if not gruppo and (d.get("type") == 5 or "qual" in testo.lower()
                           or (d.get("stage_name") or "").lower().startswith("qualif")):
            continue
        voce = spec.setdefault(disc, {"tipo": "E"})
        if gruppo:
            voce["tipo"] = "G"
            voce.setdefault("gironi", []).append(leggi_girone(t, d.get("value"), testo, squadre))
        else:
            if "c" in voce:
                avviso(f"{t['name']}: due tabelloni a eliminazione per {disc}, tengo il primo")
                continue
            voce.update({k: v for k, v in leggi_eliminazione(t, d.get("value"), squadre).items()
                         if k != "tipo"})
    for voce in spec.values():
        voce.setdefault("c", [])
        voce.setdefault("misura", 0)
        voce.setdefault("turni", [])
        if voce["tipo"] == "G":
            voce["gironi"].sort(key=lambda g: g["nome"])
    if not spec:
        raise ValueError("nessun tabellone principale pubblicato")
    tutte = [c for v in spec.values() for c in v["c"] + [c for g in v.get("gironi", []) for c in g["c"]]]
    completo = bool(tutte) and all(c[7] == "F" for c in tutte) and len(spec) == len(DISCIPLINE)
    return {"id": t["id"], "completo": completo, "squadre": squadre.lista,
            "spec": {d: spec[d] for d in DISCIPLINE if d in spec}}


def incontri_attesi(tab, categoria):
    """Quanti incontri avrà il torneo: caselle del tabellone tolti i passaggi senza gioco."""
    if not tab:
        return ATTESI_PREDEFINITI.get(categoria, ATTESI_SUPER)
    n = 0
    for v in tab["spec"].values():
        for c in v["c"] + [c for g in v.get("gironi", []) for c in g["c"]]:
            passaggio = c[7] == "F" and (c[2] < 0 or c[4] < 0)
            if not passaggio:
                n += 1
    return n


def tabelloni_aggiornati(tournaments, vecchi, today):
    """Tabelloni da mettere in badminton.json, per nome del torneo.

    Rilegge dal sito BWF solo ciò che può essere cambiato; il resto lo riprende da `vecchi`
    (la sezione "tabelloni" del badminton.json già pubblicato).
    """
    nuovi, letti, ripresi = {}, 0, 0
    limite = (today + timedelta(days=GIORNI_ANTICIPO_TABELLONE)).isoformat()
    print("\nTabelloni ufficiali (ordine e teste di serie):")
    for t in tournaments:
        if t["category"] not in CATEGORIE_WEB or t["cancelled"] or t["start"] > limite:
            continue
        nome = t["name"].replace(",", " ")
        vecchio = vecchi.get(nome) if isinstance(vecchi, dict) else None
        if vecchio and vecchio.get("completo") and vecchio.get("id") == t["id"]:
            nuovi[nome] = vecchio                  # torneo finito e già salvato: non lo rileggo
            ripresi += 1
            continue
        if tempo_scaduto():
            if vecchio:
                nuovi[nome] = vecchio
            avviso(f"{nome}: tabellone non riletto per il limite di tempo")
            continue
        try:
            nuovi[nome] = leggi_tabelloni(t)
            letti += 1
            # Finito da più di 3 giorni: lo considero chiuso anche se qualche casella del sito
            # è rimasta "da giocare" (es. un ritiro registrato male), così non lo rileggo ogni giorno.
            if t["end"] < (today - timedelta(days=3)).isoformat():
                nuovi[nome]["completo"] = True
            spec = nuovi[nome]["spec"]
            print(f"  {nome}: " + ", ".join(
                f"{d} {'gironi + ' if v['tipo'] == 'G' else ''}{v['misura']}" for d, v in spec.items()))
        except (requests.RequestException, ValueError, KeyError, TypeError) as e:
            if vecchio:
                nuovi[nome] = vecchio
            if t["start"] <= today.isoformat():
                avviso(f"{nome}: tabellone non letto ({e})" + ("; tengo quello già pubblicato" if vecchio else ""))
            else:
                print(f"  {nome}: tabellone non ancora pubblicato")
    print(f"  Letti dal sito: {letti}; ripresi da badminton.json già pubblicato: {ripresi}")
    return nuovi


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

    Lettura divisa in 6 ricerche (fette_di_lettura). Se una ricerca arriva a 10.000 righe
    lo script si ferma, anche in prova: oltre il limite non vedrebbe le altre righe e
    scriverebbe DOPPIONI.
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


def gh_headers(accept="application/vnd.github+json"):
    """Intestazioni per l'API di GitHub (senza MAPPA_TOKEN si legge lo stesso: il repo è pubblico)."""
    h = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
    if MAPPA_TOKEN:
        h["Authorization"] = f"Bearer {MAPPA_TOKEN}"
    return h


def leggi_json_pubblicato():
    """Il badminton.json già pubblicato su map-q-la28: (sha, contenuto) oppure (None, None).

    Novità 2.1: il file può superare 1 MB, e oltre 1 MB l'API di GitHub non manda il contenuto
    nel modo normale. Quindi: prima chiedo lo "sha" (serve per sovrascrivere il file), poi il
    contenuto in versione "grezza" (raw), che funziona fino a 100 MB.
    """
    url = f"https://api.github.com/repos/{MAPPA_REPO}/contents/{MAPPA_FILE}"
    r = requests.get(url, headers=gh_headers(), timeout=30)
    if r.status_code == 404:
        return None, None
    if r.status_code != 200:
        raise RuntimeError(f"GitHub ha risposto {r.status_code}: {r.text[:300]}")
    sha = r.json().get("sha")
    r = requests.get(url, headers=gh_headers("application/vnd.github.raw+json"), timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"GitHub non dà il contenuto di {MAPPA_FILE}: {r.status_code}")
    try:
        return sha, json.loads(r.content.decode("utf-8"))
    except ValueError:
        return sha, None


def build_web_data(tournaments, rows, today, tabelloni=None):
    """Prepara badminton.json: calendario della stagione + tutti gli incontri + tabelloni.

    Versione 2.1: tutte le categorie di CATEGORIE_WEB (6) e la sezione "tabelloni".
    """
    tabelloni = tabelloni or {}
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
        tornei.append({"nome": key, "id": t["id"], "categoria": t["category"], "inizio": t["start"],
                       "fine": t["end"], "luogo": t["location"], "url": t["url"], "stato": stato,
                       "incontri": n, "attesi": incontri_attesi(tabelloni.get(key), t["category"])})
    for name, rs in by_name.items():           # tornei in Notion ma non più nel calendario
        if name not in known:
            ds = sorted(r.get("date", "") for r in rs if r.get("date"))
            cat = rs[0].get("category", "")
            tornei.append({"nome": name, "categoria": cat, "inizio": ds[0] if ds else "",
                           "fine": ds[-1] if ds else "", "luogo": "", "url": "", "stato": "concluso",
                           "incontri": len(rs), "attesi": incontri_attesi(tabelloni.get(name), cat)})
    tornei.sort(key=lambda t: (t["inizio"], t["nome"]))
    incontri = [[r.get("tournament", ""), r.get("discipline", ""), r.get("round", ""),
                 r.get("p1", ""), r.get("noc1", ""), r.get("p2", ""), r.get("noc2", ""),
                 r.get("score", ""), r.get("date", "")] for r in rows]
    incontri.sort(key=lambda x: (x[0], x[1], ROUND_ORDER.get(x[2], 9), x[8], x[3]))
    nomi = {t["nome"] for t in tornei}
    return {
        "aggiornato": today.isoformat(),
        "versione": VERSIONE,
        "stagioni": sorted({t["inizio"][:4] for t in tornei if t["inizio"]}),
        "campi": ["torneo", "disciplina", "turno", "vincitore", "noc_vincitore",
                  "sconfitto", "noc_sconfitto", "punteggio", "data"],
        "tornei": tornei,
        "incontri": incontri,
        "campi_casella": CAMPI_CASELLA,
        "tabelloni": {k: v for k, v in sorted(tabelloni.items()) if k in nomi},
    }


def publish_web_data(payload):
    """Scrive badminton.json su map-q-la28 e fa ripartire la pubblicazione della pagina."""
    url = f"https://api.github.com/repos/{MAPPA_REPO}/contents/{MAPPA_FILE}"
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    sha, old = leggi_json_pubblicato()
    if old and {**old, "aggiornato": ""} == {**payload, "aggiornato": ""}:
        print("Pagina web: nessuna novità, non la ripubblico.")
        return
    body = {"message": "Aggiorna i risultati del badminton",
            "content": base64.b64encode(text.encode("utf-8")).decode("ascii")}
    if sha:
        body["sha"] = sha
    r = requests.put(url, headers=gh_headers(), json=body, timeout=120)
    if not r.ok:
        raise RuntimeError(f"GitHub non accetta {MAPPA_FILE}: {r.status_code} {r.text[:300]}")
    r = requests.post(f"https://api.github.com/repos/{MAPPA_REPO}/actions/workflows/{MAPPA_WORKFLOW}/dispatches",
                      headers=gh_headers(), json={"ref": "main"}, timeout=30)
    if r.status_code != 204:
        print(f"  Attenzione: {MAPPA_FILE} salvato, ma la ripubblicazione non è partita ({r.status_code}). "
              "Lancia a mano «Aggiorna mappa LA28» su map-q-la28.")
    else:
        print(f"Pagina web: {MAPPA_FILE} aggiornato ({len(text) // 1024} KB), ripubblicazione avviata.")


# --------------------------------------------------------------------- main
def main():
    if MODALITA not in ("aggiungi", "completa"):
        die(f"MODALITA deve essere 'aggiungi' o 'completa' (ricevuto: '{MODALITA}').")
    if not DRY_RUN and (not NOTION_TOKEN or not DATABASE_ID):
        die("mancano NOTION_TOKEN o NOTION_DATABASE_ID (controlla i Secrets su GitHub).")
    today = date.today()
    print(f"Agente Badminton {VERSIONE} · modalità {MODALITA}" + (" · PROVA: non scrivo nulla" if DRY_RUN else ""))

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

    # 3. tornei uno per uno (risultati -> Notion)
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

    # 4. tabelloni ufficiali (novità 2.1): dal sito BWF solo quelli che possono essere cambiati,
    #    gli altri dal badminton.json già pubblicato
    try:
        _, pubblicato = leggi_json_pubblicato()
    except (RuntimeError, requests.RequestException) as e:
        pubblicato = None
        avviso(f"non riesco a rileggere {MAPPA_FILE} da GitHub ({e}): rileggo tutti i tabelloni")
    vecchi = (pubblicato or {}).get("tabelloni") or {}
    tabelloni = tabelloni_aggiornati(tournaments, vecchi, today)

    # 5. riepilogo e pagina web
    payload = build_web_data(tournaments, rows, today, tabelloni)
    if DRY_RUN:
        with open(MAPPA_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        print(f"\nProva terminata. Righe nuove che scriverei: {da_scrivere}"
              + (f"; righe a cui aggiungerei gli ID BWF: {da_completare}" if MODALITA == "completa" else "")
              + f". {MAPPA_FILE} salvato in locale ({len(payload['incontri'])} incontri, "
              f"{len(payload['tabelloni'])} tornei con tabellone).")
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
    if AVVISI:
        print(f"\nAvvisi ({len(AVVISI)}), non bloccanti:")
        for a in AVVISI:
            print(f"  - {a}")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
