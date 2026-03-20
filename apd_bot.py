"""
APD Notificaciones Bot v3 - Selección múltiple de distritos, cargos y estados
"""

import asyncio
import logging
import os
import sqlite3
import hashlib
import ssl
from datetime import datetime, time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    ContextTypes, ConversationHandler, MessageHandler, filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ─────────────────────────────────────────────
# SSL para servidores del gobierno
# ─────────────────────────────────────────────
class SSLAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)

def get_session():
    s = requests.Session()
    s.mount("https://", SSLAdapter())
    s.mount("http://", SSLAdapter())
    return s

# ─────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise ValueError("Falta BOT_TOKEN en las variables de entorno.")

DB_PATH = "apd_bot.db"
SCRAPE_INTERVAL_MINUTES = 5
HORA_INICIO = time(5, 30)
HORA_FIN    = time(11, 30)
APD_URL     = "http://servicios.abc.gov.ar/actos.publicos.digitales/"
APD_API     = "https://servicios3.abc.gob.ar/valoracion.docente/api/apd.oferta.encabezado/select"

(ELIGIENDO_NIVEL, ELIGIENDO_DISTRITO, ELIGIENDO_CARGO,
 ELIGIENDO_CARGO_CUSTOM, ELIGIENDO_ESTADO) = range(5)

DIST_POR_PAGINA = 16

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

NIVELES_API = {
    "Inicial":           "inicial",
    "Primaria":          "primaria",
    "Secundaria":        "secundaria",
    "Técnico Prof.":     "tecnico profesional",
    "Artística":         "artistica",
    "Adultos/CENS":      "adultos y cens",
    "Especial":          "especial",
    "Ed. Física":        "educacion fisica",
    "Psicología":        "psicologia",
    "Superior":          "superior",
    "Todos":             "Todos",
}
NIVELES = list(NIVELES_API.keys())

DISTRITOS = [
    "adolfo alsina","adolfo gonzales chaves","alberti","almirante brown","azul",
    "bahia blanca","balcarce","berazategui","bolivar","bragado","campana",
    "canuelas","capitan sarmiento","carlos casares","carlos tejedor","carmen de areco",
    "chascomus","chivilcoy","coronel dorrego","coronel pringles","coronel rosales",
    "coronel suarez","daireaux","dolores","ensenada","escobar","esteban echeverria",
    "exaltacion de la cruz","ezeiza","florencio varela","general alvarado",
    "general belgrano","general guido","general madariaga","general la madrid",
    "general las heras","general lavalle","general paz","general pinto",
    "general pueyrredon","general rodriguez","general san martin","general viamonte",
    "general villegas","guamini","hipolito irigoyen","hurlingham","ituzaingo",
    "jose c. paz","junin","partido de la costa","la matanza","la plata",
    "lanus","laprida","las flores","leandro n alem","lincoln","loberia","lobos",
    "lomas de zamora","lujan","maipu","malvinas argentinas","mar chiquita",
    "marcos paz","mercedes","merlo","monte","monte hermoso","moreno","moron",
    "navarro","necochea","9 de julio","olavarria","patagones","pehuajo",
    "pellegrini","pergamino","pila","pilar","pinamar","presidente peron",
    "puan","punta indio","quilmes","ramallo","rauch","rivadavia","rojas",
    "roque perez","saavedra","saladillo","salto","salliquelo",
    "san andres de giles","san antonio de areco","san cayetano","san fernando",
    "san isidro","san miguel","san nicolas","san pedro","san vicente",
    "suipacha","tandil","tapalque","tigre","tordillo","tornquist","trenque lauquen",
    "tres arroyos","tres de febrero","tres lomas","vicente lopez","villa gesell",
    "villarino","zarate",
]
DISTRITOS_DISPLAY = [d.title() for d in DISTRITOS]

CARGOS_COMUNES = [
    "maestro de grado (/mg)",
    "jornada escolar de 25 horas- maestro de grado - [prog. res. 2502/22]  (mg5)",
    "maestra de infantes (/mi)",
    "preceptor (/pr)",
    "ingles (igs)",
    "prof. de c.e.f. (/ef)",
    "educacion fisica (efc)",
    "matematica (mtm)",
    "practicas del lenguaje (plg)",
    "historia (htr)",
    "geografia (ggf)",
    "biologia (blg)",
    "filosofia (fia)",
    "orientador educacional (/oe)",
    "orientador social (/os)",
    "fonoaudiologo (/fo)",
    "bibliotecario (/bi)",
    "director (xxd)",
]
CARGOS_DISPLAY = {
    "maestro de grado (/mg)":        "Maestro de grado",
    "jornada escolar de 25 horas- maestro de grado - [prog. res. 2502/22]  (mg5)": "MG5 - Maestra grado 5ta hora",
    "maestra de infantes (/mi)":     "Maestra de infantes",
    "preceptor (/pr)":               "Preceptor",
    "ingles (igs)":                  "Inglés",
    "prof. de c.e.f. (/ef)":        "Ed. Física (EF)",
    "educacion fisica (efc)":        "Ed. Física (EFC)",
    "matematica (mtm)":              "Matemática",
    "practicas del lenguaje (plg)":  "Prácticas del lenguaje",
    "historia (htr)":                "Historia",
    "geografia (ggf)":               "Geografía",
    "biologia (blg)":                "Biología",
    "filosofia (fia)":               "Filosofía",
    "orientador educacional (/oe)":  "Orientador educacional",
    "orientador social (/os)":       "Orientador social",
    "fonoaudiologo (/fo)":           "Fonoaudiólogo",
    "bibliotecario (/bi)":           "Bibliotecario",
    "director (xxd)":                "Director",
}

ESTADOS_OPCIONES = ["publicada", "designada", "desierta", "cerrada", "anulada", "renunciada", "finalizada"]
ESTADOS_DISPLAY = {
    "publicada":   "Publicada (disponible)",
    "designada":   "Designada (tomada)",
    "desierta":    "Desierta (sin postulantes)",
    "cerrada":     "Cerrada",
    "anulada":     "Anulada",
    "renunciada":  "Renunciada",
    "finalizada":  "Finalizada",
}

# ─────────────────────────────────────────────
# BASE DE DATOS
# ─────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS usuarios (
        chat_id INTEGER PRIMARY KEY, username TEXT,
        nivel TEXT DEFAULT 'Todos',
        distritos TEXT DEFAULT '',
        cargos TEXT DEFAULT '',
        estados TEXT DEFAULT 'publicada',
        activo INTEGER DEFAULT 1,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP)""")
    # Agregar columnas si no existen (bases antiguas)
    for col, default in [("distritos","''"),("cargos","''"),("estados","'publicada'")]:
        try:
            c.execute(f"ALTER TABLE usuarios ADD COLUMN {col} TEXT DEFAULT {default}")
        except:
            pass
    # Migrar valores viejos de estados
    for viejo, nuevo in [("Publicadas","publicada"),("Tomadas","designada"),("Ambas","")]:
        try:
            c.execute("UPDATE usuarios SET estados=? WHERE estados=?", (nuevo, viejo))
        except:
            pass
    c.execute("""CREATE TABLE IF NOT EXISTS ofertas_vistas (
        oferta_id TEXT PRIMARY KEY, visto_en TEXT DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit(); conn.close()

def get_user(chat_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT chat_id,username,nivel,distritos,cargos,estados,activo FROM usuarios WHERE chat_id=?", (chat_id,))
    row = c.fetchone(); conn.close()
    if row:
        return {"chat_id":row[0],"username":row[1],"nivel":row[2],
                "distritos":row[3] or "","cargos":row[4] or "",
                "estados":row[5] or "publicada","activo":row[6]}
    return None

def upsert_user(chat_id, username=None, **kwargs):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    exists = c.execute("SELECT chat_id FROM usuarios WHERE chat_id=?", (chat_id,)).fetchone()
    if not exists:
        c.execute("INSERT INTO usuarios (chat_id, username) VALUES (?,?)", (chat_id, username))
    if kwargs:
        sets = ", ".join(f"{k}=?" for k in kwargs)
        c.execute(f"UPDATE usuarios SET {sets} WHERE chat_id=?", [*kwargs.values(), chat_id])
    conn.commit(); conn.close()

def get_all_active_users():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT chat_id,nivel,distritos,cargos,COALESCE(estados,'Publicadas') FROM usuarios WHERE activo=1"
    ).fetchall()
    conn.close(); return rows

def mark_vista(oid):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR IGNORE INTO ofertas_vistas (oferta_id) VALUES (?)", (oid,))
    conn.commit(); conn.close()

def es_nueva(oid):
    conn = sqlite3.connect(DB_PATH)
    r = conn.execute("SELECT 1 FROM ofertas_vistas WHERE oferta_id=?", (oid,)).fetchone()
    conn.close(); return r is None

# ─────────────────────────────────────────────
# HELPERS listas guardadas como CSV
# ─────────────────────────────────────────────
def csv_to_list(s): return [x.strip() for x in s.split(",") if x.strip()]
def list_to_csv(lst): return ",".join(lst)

def resumen_lista(lst, vacio="Todos"):
    if not lst: return vacio
    if len(lst) == 1: return lst[0]
    return f"{lst[0]} +{len(lst)-1} más"

# ─────────────────────────────────────────────
# TECLADOS CON CHECKBOXES
# ─────────────────────────────────────────────
def build_distrito_keyboard(pagina, seleccionados):
    inicio = pagina * DIST_POR_PAGINA
    fin    = inicio + DIST_POR_PAGINA
    chunk  = list(enumerate(DISTRITOS))[inicio:fin]
    total_paginas = (len(DISTRITOS) + DIST_POR_PAGINA - 1) // DIST_POR_PAGINA
    kb = []
    row = []
    for i, (idx, d) in enumerate(chunk):
        tick = "✅ " if d in seleccionados else ""
        label = DISTRITOS_DISPLAY[idx][:18]
        row.append(InlineKeyboardButton(f"{tick}{label}", callback_data=f"dtog_{idx}"))
        if (i+1) % 2 == 0: kb.append(row); row = []
    if row: kb.append(row)
    nav = []
    if pagina > 0:
        nav.append(InlineKeyboardButton("◀", callback_data=f"dpag_{pagina-1}"))
    nav.append(InlineKeyboardButton(f"{pagina+1}/{total_paginas}", callback_data="dpag_noop"))
    if fin < len(DISTRITOS):
        nav.append(InlineKeyboardButton("▶", callback_data=f"dpag_{pagina+1}"))
    kb.append(nav)
    n = len(seleccionados)
    label = f"✓ Listo ({n} seleccionado{'s' if n!=1 else ''})" if n else "✓ Listo (Todos)"
    kb.append([InlineKeyboardButton(label, callback_data="dist_listo")])
    return InlineKeyboardMarkup(kb)

def build_cargo_keyboard(seleccionados):
    kb = []
    row = []
    for i, c in enumerate(CARGOS_COMUNES):
        tick = "✅ " if c in seleccionados else ""
        label = CARGOS_DISPLAY.get(c, c)[:20]
        row.append(InlineKeyboardButton(f"{tick}{label}", callback_data=f"ctog_{i}"))
        if (i+1) % 2 == 0: kb.append(row); row = []
    if row: kb.append(row)
    kb.append([InlineKeyboardButton("✏️ Escribir cargo personalizado", callback_data="cargo_custom")])
    n = len(seleccionados)
    label = f"✓ Listo ({n} seleccionado{'s' if n!=1 else ''})" if n else "✓ Listo (Todos)"
    kb.append([InlineKeyboardButton(label, callback_data="cargo_listo")])
    return InlineKeyboardMarkup(kb)

def build_estado_keyboard(seleccionados):
    kb = []
    for e in ESTADOS_OPCIONES:
        tick = "✅ " if e in seleccionados else ""
        label = ESTADOS_DISPLAY.get(e, e)
        kb.append([InlineKeyboardButton(f"{tick}{label}", callback_data=f"etog_{e}")])
    n = len(seleccionados)
    label = f"✓ Listo ({n} seleccionado{'s' if n!=1 else ''})" if n else "✓ Listo (Todos)"
    kb.append([InlineKeyboardButton(label, callback_data="estado_listo")])
    return InlineKeyboardMarkup(kb)

def texto_resumen(context):
    nivel     = context.user_data.get("nivel","Todos")
    distritos = context.user_data.get("sel_distritos",[])
    cargos    = context.user_data.get("sel_cargos",[])
    estados   = context.user_data.get("sel_estados",[])
    return (
        f"🏫 Nivel: *{nivel}*\n"
        f"📍 Distritos: *{resumen_lista(distritos)}*\n"
        f"📝 Cargos: *{resumen_lista(cargos)}*\n"
        f"🔖 Estados: *{resumen_lista(estados,'Ambos')}*"
    )

# ─────────────────────────────────────────────
# SCRAPER
# ─────────────────────────────────────────────
def formatear_fecha(fecha_str):
    try:
        dt = datetime.strptime(fecha_str, "%Y-%m-%dT%H:%M:%SZ")
        return dt.strftime("%d/%m/%Y %H:%M")
    except:
        return fecha_str

def build_fq(nivel, distritos_list, cargos_list, estados_list):
    """Construye filtros Solr para reducir resultados en la API."""
    fq = ["finoferta:[NOW TO *]"]
    if nivel != "Todos":
        nivel_api = NIVELES_API.get(nivel, nivel.lower())
        fq.append(f'descnivelmodalidad:"{nivel_api}"')
    if distritos_list:
        dist_query = " OR ".join(f'descdistrito:"{d}"' for d in distritos_list)
        fq.append(f"({dist_query})")
    if cargos_list:
        cargo_query = " OR ".join(f'cargo:"{c}"' for c in cargos_list)
        fq.append(f"({cargo_query})")
    if estados_list:
        estado_query = " OR ".join(f'estado:"{e}"' for e in estados_list)
        fq.append(f"({estado_query})")
    return fq

def scrape_ofertas():
    """Consulta la API Solr — sin filtros de usuario, para detectar nuevas."""
    params = {
        "q": "*:*", "rows": "500", "sort": "finoferta asc",
        "wt": "json",
        "fq": "finoferta:[NOW TO *]",
    }
    try:
        session = get_session()
        resp = session.get(APD_API, params=params, timeout=15)
        resp.raise_for_status()
        docs = resp.json().get("response",{}).get("docs",[])
        ofertas = []
        for doc in docs:
            o = {
                "ige":             doc.get("ige","N/D"),
                "nivel":           doc.get("descnivelmodalidad","N/D"),
                "cargo":           doc.get("cargo","N/D"),
                "distrito":        doc.get("descdistrito","N/D"),
                "establecimiento": doc.get("descestablecimiento", doc.get("clave","N/D")),
                "cierre":          formatear_fecha(doc.get("finoferta","N/D")),
                "estado":          doc.get("estado","N/D"),
                "link":            APD_URL,
            }
            o["id"] = hashlib.md5(f"{o['ige']}-{o['cargo']}-{o['distrito']}".encode()).hexdigest()
            ofertas.append(o)
        return ofertas
    except Exception as e:
        logger.error(f"Scrape error: {e}"); return []

def scrape_ofertas_filtradas(nivel, distritos_list, cargos_list, estados_list):
    """Consulta la API con filtros Solr — para /ofertas."""
    fq = build_fq(nivel, distritos_list, cargos_list, estados_list)
    params = {
        "q": "*:*", "rows": "100", "sort": "finoferta asc",
        "wt": "json", "fq": fq,
    }
    try:
        session = get_session()
        resp = session.get(APD_API, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        docs  = data.get("response",{}).get("docs",[])
        total = data.get("response",{}).get("numFound",0)
        ofertas = []
        for doc in docs:
            o = {
                "ige":             doc.get("ige","N/D"),
                "nivel":           doc.get("descnivelmodalidad","N/D"),
                "cargo":           doc.get("cargo","N/D"),
                "distrito":        doc.get("descdistrito","N/D"),
                "establecimiento": doc.get("descestablecimiento", doc.get("clave","N/D")),
                "cierre":          formatear_fecha(doc.get("finoferta","N/D")),
                "estado":          doc.get("estado","N/D"),
                "link":            APD_URL,
            }
            o["id"] = hashlib.md5(f"{o['ige']}-{o['cargo']}-{o['distrito']}".encode()).hexdigest()
            ofertas.append(o)
        return ofertas, total
    except Exception as e:
        logger.error(f"Scrape filtrado error: {e}"); return [], 0

def coincide(o, nivel, distritos_list, cargos_list, estados_list):
    nivel_o    = o.get("nivel","").lower().strip()
    distrito_o = o.get("distrito","").lower().strip()
    cargo_o    = o.get("cargo","").lower().strip()
    estado_o   = o.get("estado","").lower().strip()

    # Nivel: comparación exacta contra valor API
    if nivel != "Todos":
        nivel_api = NIVELES_API.get(nivel, nivel.lower())
        if nivel_api not in nivel_o:
            return False

    # Distritos: matching exacto (los guardamos en minúsculas igual que la API)
    if distritos_list:
        if not any(d.lower() == distrito_o for d in distritos_list):
            return False

    # Cargos: matching exacto (los guardamos tal como los devuelve la API)
    if cargos_list:
        if not any(c.lower() == cargo_o for c in cargos_list):
            return False

    # Estados: comparación directa con valor API (insensible a mayúsculas)
    if estados_list:
        if estado_o not in [e.lower().strip() for e in estados_list]:
            return False

    return True

def fmt_oferta(o):
    estado_emoji = "🟢" if "publicad" in o.get("estado","").lower() else "🔴"
    return (
        f"📚 *NUEVA OFERTA APD*\n━━━━━━━━━━━━━━━\n"
        f"📋 IGE: `{o.get('ige','N/D')}`\n"
        f"🏫 Nivel: {o.get('nivel','N/D')}\n"
        f"📝 Cargo: {o.get('cargo','N/D')}\n"
        f"📍 Distrito: {o.get('distrito','N/D')}\n"
        f"🏛️ Est.: {o.get('establecimiento','N/D')}\n"
        f"⏰ Cierre: *{o.get('cierre','N/D')}*\n"
        f"{estado_emoji} Estado: {o.get('estado','N/D')}\n"
        f"━━━━━━━━━━━━━━━\n[📌 Ver en ABC]({o.get('link',APD_URL)})"
    )

# ─────────────────────────────────────────────
# JOB PERIÓDICO
# ─────────────────────────────────────────────
async def chequear(application):
    ahora = datetime.now().time()
    if not (HORA_INICIO <= ahora <= HORA_FIN): return
    logger.info("Chequeando APD...")
    ofertas = scrape_ofertas()
    nuevas  = [o for o in ofertas if es_nueva(o["id"])]
    if not nuevas: return
    for o in nuevas: mark_vista(o["id"])
    for chat_id, nivel, distritos_csv, cargos_csv, estados_csv in get_all_active_users():
        distritos_list = csv_to_list(distritos_csv)
        cargos_list    = csv_to_list(cargos_csv)
        estados_list   = csv_to_list(estados_csv)
        for o in nuevas:
            if coincide(o, nivel, distritos_list, cargos_list, estados_list):
                try:
                    await application.bot.send_message(
                        chat_id=chat_id, text=fmt_oferta(o),
                        parse_mode="Markdown", disable_web_page_preview=True)
                    await asyncio.sleep(0.1)
                except Exception as e:
                    logger.error(f"Error a {chat_id}: {e}")

# ─────────────────────────────────────────────
# HANDLERS
# ─────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    upsert_user(chat_id=u.id, username=u.username or u.first_name)
    await update.message.reply_text(
        f"👋 Hola, *{u.first_name}*!\n\n"
        "📢 Soy el bot de *Alertas APD*. Te aviso cuando aparecen nuevas ofertas "
        "docentes en el portal ABC de la Provincia de Buenos Aires.\n\n"
        "Por defecto recibís *todas las ofertas publicadas*. "
        "Usá /configurar para elegir distritos, cargos y estados.\n\n"
        "📋 Comandos:\n/ofertas · /configurar · /mis\\_alertas · /pausar · /reanudar · /test · /ayuda",
        parse_mode="Markdown")

async def mis_alertas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = get_user(update.effective_user.id)
    if not d:
        await update.message.reply_text("Primero usá /start."); return
    distritos = csv_to_list(d["distritos"])
    cargos    = csv_to_list(d["cargos"])
    estados   = csv_to_list(d["estados"])
    await update.message.reply_text(
        f"📋 *Tu configuración:*\n\n"
        f"Estado bot: {'✅ Activo' if d['activo'] else '⏸️ Pausado'}\n"
        f"🏫 Nivel: {d['nivel']}\n"
        f"📍 Distritos: {', '.join(distritos) if distritos else 'Todos'}\n"
        f"📝 Cargos: {', '.join(cargos) if cargos else 'Todos'}\n"
        f"🔖 Estados: {', '.join(ESTADOS_DISPLAY.get(e,e) for e in estados) if estados else 'Todos'}\n\n"
        "Cambiá con /configurar", parse_mode="Markdown")

# ── Paso 1: Nivel ──
async def configurar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Cargar config previa como punto de partida
    d = get_user(update.effective_user.id)
    if d:
        context.user_data["sel_distritos"] = csv_to_list(d["distritos"])
        context.user_data["sel_cargos"]    = csv_to_list(d["cargos"])
        context.user_data["sel_estados"]   = csv_to_list(d["estados"])
    else:
        context.user_data["sel_distritos"] = []
        context.user_data["sel_cargos"]    = []
        context.user_data["sel_estados"]   = []
    kb = []
    row = []
    for i, n in enumerate(NIVELES):
        row.append(InlineKeyboardButton(n, callback_data=f"nivel_{n}"))
        if (i+1) % 3 == 0: kb.append(row); row = []
    if row: kb.append(row)
    await update.message.reply_text(
        "🏫 *Paso 1/4 — ¿Qué nivel querés monitorear?*",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return ELIGIENDO_NIVEL

async def cb_nivel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    context.user_data["nivel"] = q.data.replace("nivel_","")
    context.user_data["dist_pagina"] = 0
    sel = context.user_data.get("sel_distritos",[])
    await q.edit_message_text(
        f"{texto_resumen(context)}\n\n"
        "📍 *Paso 2/4 — Elegí uno o varios distritos*\n"
        "_Tocá para marcar/desmarcar. Sin selección = todos._",
        reply_markup=build_distrito_keyboard(0, sel), parse_mode="Markdown")
    return ELIGIENDO_DISTRITO

# ── Paso 2: Distritos (multi) ──
async def cb_dpag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data == "dpag_noop": return ELIGIENDO_DISTRITO
    pagina = int(q.data.replace("dpag_",""))
    context.user_data["dist_pagina"] = pagina
    sel = context.user_data.get("sel_distritos",[])
    await q.edit_message_text(
        f"{texto_resumen(context)}\n\n"
        "📍 *Paso 2/4 — Elegí uno o varios distritos*\n"
        "_Tocá para marcar/desmarcar. Sin selección = todos._",
        reply_markup=build_distrito_keyboard(pagina, sel), parse_mode="Markdown")
    return ELIGIENDO_DISTRITO

async def cb_dtog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    idx = int(q.data.replace("dtog_",""))
    d = DISTRITOS[idx]
    sel = context.user_data.get("sel_distritos",[])
    if d in sel: sel.remove(d)
    else: sel.append(d)
    context.user_data["sel_distritos"] = sel
    pagina = context.user_data.get("dist_pagina",0)
    await q.edit_message_text(
        f"{texto_resumen(context)}\n\n"
        "📍 *Paso 2/4 — Elegí uno o varios distritos*\n"
        "_Tocá para marcar/desmarcar. Sin selección = todos._",
        reply_markup=build_distrito_keyboard(pagina, sel), parse_mode="Markdown")
    return ELIGIENDO_DISTRITO

async def cb_dist_listo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    sel = context.user_data.get("sel_cargos",[])
    await q.edit_message_text(
        f"{texto_resumen(context)}\n\n"
        "📝 *Paso 3/4 — Elegí uno o varios cargos*\n"
        "_Tocá para marcar/desmarcar. Sin selección = todos._",
        reply_markup=build_cargo_keyboard(sel), parse_mode="Markdown")
    return ELIGIENDO_CARGO

# ── Paso 3: Cargos (multi) ──
async def cb_ctog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    idx = int(q.data.replace("ctog_",""))
    c = CARGOS_COMUNES[idx]
    sel = context.user_data.get("sel_cargos",[])
    if c in sel: sel.remove(c)
    else: sel.append(c)
    context.user_data["sel_cargos"] = sel
    await q.edit_message_text(
        f"{texto_resumen(context)}\n\n"
        "📝 *Paso 3/4 — Elegí uno o varios cargos*\n"
        "_Tocá para marcar/desmarcar. Sin selección = todos._",
        reply_markup=build_cargo_keyboard(sel), parse_mode="Markdown")
    return ELIGIENDO_CARGO

async def cb_cargo_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    await q.edit_message_text(
        "✏️ Escribí el nombre del cargo que querés agregar\n"
        "_(ej: Orientador educacional, Fonoaudiología)_",
        parse_mode="Markdown")
    return ELIGIENDO_CARGO_CUSTOM

async def recibir_cargo_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text.strip()
    sel = context.user_data.get("sel_cargos",[])
    if texto and texto not in sel:
        sel.append(texto)
    context.user_data["sel_cargos"] = sel
    await update.message.reply_text(
        f"{texto_resumen(context)}\n\n"
        "📝 *Paso 3/4 — Elegí uno o varios cargos*\n"
        "_Tocá para marcar/desmarcar. Sin selección = todos._",
        reply_markup=build_cargo_keyboard(sel), parse_mode="Markdown")
    return ELIGIENDO_CARGO

async def cb_cargo_listo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    sel = context.user_data.get("sel_estados",[])
    await q.edit_message_text(
        f"{texto_resumen(context)}\n\n"
        "🔖 *Paso 4/4 — ¿Qué estado de oferta querés recibir?*\n"
        "_Sin selección = ambos._",
        reply_markup=build_estado_keyboard(sel), parse_mode="Markdown")
    return ELIGIENDO_ESTADO

# ── Paso 4: Estados (multi) ──
async def cb_etog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    e = q.data.replace("etog_","")
    sel = context.user_data.get("sel_estados",[])
    if e in sel: sel.remove(e)
    else: sel.append(e)
    context.user_data["sel_estados"] = sel
    await q.edit_message_text(
        f"{texto_resumen(context)}\n\n"
        "🔖 *Paso 4/4 — ¿Qué estado de oferta querés recibir?*\n"
        "_Sin selección = ambos._",
        reply_markup=build_estado_keyboard(sel), parse_mode="Markdown")
    return ELIGIENDO_ESTADO

async def cb_estado_listo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    nivel     = context.user_data.get("nivel","Todos")
    distritos = context.user_data.get("sel_distritos",[])
    cargos    = context.user_data.get("sel_cargos",[])
    estados   = context.user_data.get("sel_estados",[])
    upsert_user(
        chat_id=update.effective_user.id,
        nivel=nivel,
        distritos=list_to_csv(distritos),
        cargos=list_to_csv(cargos),
        estados=list_to_csv(estados),
        activo=1)
    await q.edit_message_text(
        f"🎉 *¡Configuración guardada!*\n\n"
        f"🏫 Nivel: {nivel}\n"
        f"📍 Distritos: {', '.join(distritos) if distritos else 'Todos'}\n"
        f"📝 Cargos: {', '.join(cargos) if cargos else 'Todos'}\n"
        f"🔖 Estados: {', '.join(ESTADOS_DISPLAY.get(e,e) for e in estados) if estados else 'Todos'}\n"
        "Te avisaré cuando aparezcan ofertas que coincidan.\n"
        "Los APD tienen cierres a las 7:30 y 10:30 hs (lunes a viernes).",
        parse_mode="Markdown")
    return ConversationHandler.END

async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelado."); return ConversationHandler.END

async def pausar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    upsert_user(chat_id=update.effective_user.id, activo=0)
    await update.message.reply_text("⏸️ Pausado. Usá /reanudar cuando quieras.")

async def reanudar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    upsert_user(chat_id=update.effective_user.id, activo=1)
    await update.message.reply_text("✅ Reactivado. ¡Te sigo avisando!")

async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Consultando el portal APD ahora mismo...")
    try:
        params = {"q":"*:*","rows":"5","sort":"finoferta asc","wt":"json",
                  "fq":"finoferta:[NOW TO *]"}
        resp = get_session().get(APD_API, params=params, timeout=15)
        resp.raise_for_status()
        data  = resp.json()
        docs  = data.get("response",{}).get("docs",[])
        total = data.get("response",{}).get("numFound",0)
        if docs:
            m = docs[0]
            detalle = (
                f"✅ *API respondió correctamente*\n\n"
                f"📋 Ofertas publicadas activas: `{total}`\n\n"
                f"*Próxima en cerrar:*\n"
                f"IGE: `{m.get('ige','N/D')}`\n"
                f"Nivel: {m.get('descnivelmodalidad','N/D')}\n"
                f"Cargo: {m.get('cargo','N/D')}\n"
                f"Distrito: {m.get('descdistrito','N/D')}\n"
                f"Estado: {m.get('estado','N/D')}\n"
                f"Cierre: {formatear_fecha(m.get('finoferta','N/D'))}"
            )
        else:
            detalle = "⚠️ No hay ofertas publicadas activas en este momento."
    except Exception as e:
        detalle = f"❌ *Error al conectar con el portal*\n\n`{str(e)}`"
    await update.message.reply_text(detalle, parse_mode="Markdown")

async def debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = get_user(update.effective_user.id)
    if not d:
        await update.message.reply_text("Primero usá /start."); return

    distritos_list = csv_to_list(d["distritos"])
    cargos_list    = csv_to_list(d["cargos"])
    estados_list   = csv_to_list(d["estados"])

    await update.message.reply_text(
        f"🔧 *Config en DB:*\n"
        f"Nivel: `{d['nivel']}`\n"
        f"Distritos: `{d['distritos'] or '(vacío=todos)'}`\n"
        f"Cargos: `{d['cargos'] or '(vacío=todos)'}`\n"
        f"Estados: `{d['estados'] or '(vacío=todos)'}`",
        parse_mode="Markdown")

    try:
        params = {"q":"*:*","rows":"5","sort":"finoferta asc","wt":"json",
                  "fq":"finoferta:[NOW TO *]"}
        resp = get_session().get(APD_API, params=params, timeout=15)
        resp.raise_for_status()
        docs = resp.json().get("response",{}).get("docs",[])
        total = resp.json().get("response",{}).get("numFound",0)

        if not docs:
            await update.message.reply_text("⚠️ La API no devolvió ofertas activas."); return

        msg = f"📡 *API devuelve {total} ofertas activas. Primeras 5:*\n\n"
        for doc in docs:
            distrito_api = doc.get("descdistrito","")
            cargo_api    = doc.get("cargo","")
            nivel_api    = doc.get("descnivelmodalidad","")
            estado_api   = doc.get("estado","")

            # Simular coincide
            o = {"nivel": nivel_api, "distrito": distrito_api,
                 "cargo": cargo_api, "estado": estado_api}
            match = coincide(o, d["nivel"], distritos_list, cargos_list, estados_list)

            msg += (
                f"{'✅' if match else '❌'} IGE {doc.get('ige','?')}\n"
                f"  distrito=`{distrito_api}`\n"
                f"  cargo=`{cargo_api[:40]}`\n"
                f"  nivel=`{nivel_api}` estado=`{estado_api}`\n\n"
            )

        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: `{e}`", parse_mode="Markdown")

async def ofertas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = get_user(update.effective_user.id)
    if not d:
        await update.message.reply_text("Primero usá /start."); return
    distritos_list = csv_to_list(d["distritos"])
    cargos_list    = csv_to_list(d["cargos"])
    estados_list   = csv_to_list(d["estados"])
    nivel          = d["nivel"]
    await update.message.reply_text("🔍 Buscando ofertas según tu configuración...")
    try:
        coincidentes, total = scrape_ofertas_filtradas(nivel, distritos_list, cargos_list, estados_list)
        if not coincidentes:
            await update.message.reply_text(
                f"📭 No hay ofertas que coincidan con tu configuración (total en sistema: {total}).\n\n"
                "Podés cambiar los filtros con /configurar")
            return
        await update.message.reply_text(
            f"📋 *{total} oferta{'s' if total!=1 else ''}* coinciden con tu configuración"
            f"{' (mostrando primeras 20)' if total > 20 else ''}:",
            parse_mode="Markdown")
        for o in coincidentes[:20]:
            await update.message.reply_text(
                fmt_oferta(o), parse_mode="Markdown", disable_web_page_preview=True)
            await asyncio.sleep(0.3)
        if total > 20:
            await update.message.reply_text(
                f"_...y {total-20} ofertas más. Afinás los filtros con /configurar para ver menos resultados._",
                parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error al consultar el portal:\n`{str(e)}`", parse_mode="Markdown")

async def debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Diagnóstico completo: config del usuario + lo que devuelve la API + filtros aplicados."""
    d = get_user(update.effective_user.id)
    if not d:
        await update.message.reply_text("Primero usá /start."); return

    distritos_list = csv_to_list(d["distritos"])
    cargos_list    = csv_to_list(d["cargos"])
    estados_list   = csv_to_list(d["estados"])
    nivel          = d["nivel"]

    msg = (
        f"🔧 *DEBUG — Config guardada en DB:*\n"
        f"nivel: `{repr(nivel)}`\n"
        f"distritos raw: `{repr(d['distritos'])}`\n"
        f"cargos raw: `{repr(d['cargos'])}`\n"
        f"estados raw: `{repr(d['estados'])}`\n"
        f"distritos list: `{distritos_list}`\n"
        f"cargos list: `{cargos_list}`\n"
        f"estados list: `{estados_list}`\n"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

    # Consultar API
    try:
        params = {"q":"*:*","rows":"5","sort":"finoferta asc","wt":"json",
                  "fq":"finoferta:[NOW TO *]"}
        resp = get_session().get(APD_API, params=params, timeout=15)
        resp.raise_for_status()
        data  = resp.json()
        docs  = data.get("response",{}).get("docs",[])
        total = data.get("response",{}).get("numFound",0)

        msg2 = f"🌐 *API — Total con cierre futuro: `{total}`*\n\n"
        for i, doc in enumerate(docs[:3]):
            dist  = doc.get("descdistrito","?")
            niv   = doc.get("descnivelmodalidad","?")
            cargo = doc.get("cargo","?")
            est   = doc.get("estado","?")
            o = {
                "nivel": niv, "distrito": dist,
                "cargo": cargo, "estado": est,
                "ige": doc.get("ige","?"),
                "establecimiento": doc.get("descestablecimiento","?"),
                "cierre": formatear_fecha(doc.get("finoferta","?")),
                "link": APD_URL,
            }
            pasa = coincide(o, nivel, distritos_list, cargos_list, estados_list)
            msg2 += (
                f"*Oferta {i+1}:*\n"
                f"  distrito: `{dist}`\n"
                f"  nivel: `{niv}`\n"
                f"  cargo: `{cargo}`\n"
                f"  estado: `{est}`\n"
                f"  ¿pasa filtro?: `{pasa}`\n\n"
            )
        await update.message.reply_text(msg2, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error API: `{e}`", parse_mode="Markdown")

async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ *¿Cómo funciona?*\n\n"
        "Chequeo el portal APD cada 5 minutos entre las 5:30 y 11:30 hs "
        "(lunes a viernes) y te aviso cuando aparece una oferta que coincide "
        "con tus filtros.\n\n"
        "📋 *Comandos:*\n"
        "/ofertas — Ver ofertas activas ahora según tu config\n"
        "/configurar — Elegir nivel, distritos, cargos y estados\n"
        "/mis\\_alertas — Ver tu configuración actual\n"
        "/pausar — Pausar notificaciones\n"
        "/reanudar — Reanudar notificaciones\n"
        "/test — Probar conexión con el portal\n\n"
        "🔗 servicios.abc.gov.ar/actos.publicos.digitales",
        parse_mode="Markdown")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("test", test))
    app.add_handler(CommandHandler("debug", debug))
    app.add_handler(CommandHandler("ofertas", ofertas))
    app.add_handler(CommandHandler("mis_alertas", mis_alertas))
    app.add_handler(CommandHandler("pausar", pausar))
    app.add_handler(CommandHandler("reanudar", reanudar))
    app.add_handler(CommandHandler("ayuda", ayuda))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("configurar", configurar)],
        states={
            ELIGIENDO_NIVEL: [
                CallbackQueryHandler(cb_nivel, pattern="^nivel_"),
            ],
            ELIGIENDO_DISTRITO: [
                CallbackQueryHandler(cb_dpag,      pattern="^dpag_"),
                CallbackQueryHandler(cb_dtog,      pattern="^dtog_"),
                CallbackQueryHandler(cb_dist_listo,pattern="^dist_listo$"),
            ],
            ELIGIENDO_CARGO: [
                CallbackQueryHandler(cb_ctog,       pattern="^ctog_"),
                CallbackQueryHandler(cb_cargo_custom,pattern="^cargo_custom$"),
                CallbackQueryHandler(cb_cargo_listo, pattern="^cargo_listo$"),
            ],
            ELIGIENDO_CARGO_CUSTOM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_cargo_custom),
            ],
            ELIGIENDO_ESTADO: [
                CallbackQueryHandler(cb_etog,        pattern="^etog_"),
                CallbackQueryHandler(cb_estado_listo, pattern="^estado_listo$"),
            ],
        },
        fallbacks=[CommandHandler("cancelar", cancelar)],
    ))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(chequear, "interval", minutes=SCRAPE_INTERVAL_MINUTES, args=[app])
    scheduler.start()

    logger.info("Bot APD v3 corriendo...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
