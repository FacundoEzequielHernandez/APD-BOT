"""
APD Alertas Bot - v4
Creado por Facu para su novia maestra.
"""

import asyncio, logging, os, sqlite3, hashlib, ssl
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

# ── SSL para servidores del gobierno ──────────────────────────────────────────
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

# ── Configuración ──────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise ValueError("Falta BOT_TOKEN en las variables de entorno.")

DB_PATH  = "apd_bot.db"
APD_URL  = "http://servicios.abc.gov.ar/actos.publicos.digitales/"
APD_API  = "https://servicios3.abc.gob.ar/valoracion.docente/api/apd.oferta.encabezado/select"
SCRAPE_INTERVAL = 5
HORA_INICIO = time(5, 30)
HORA_FIN    = time(11, 30)
DIST_POR_PAG = 16

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Estados de conversación
(CFG_NIVEL, CFG_DIST, CFG_CARGO, CFG_CARGO_CUSTOM, CFG_ESTADO) = range(5)
(EXP_NIVEL, EXP_DIST, EXP_CARGO, EXP_CARGO_CUSTOM, EXP_ESTADO) = range(5, 10)

# ── Datos de referencia ────────────────────────────────────────────────────────
NIVELES_API = {
    "Inicial":       "inicial",
    "Primaria":      "primaria",
    "Secundaria":    "secundaria",
    "Técnico Prof.": "tecnico profesional",
    "Artística":     "artistica",
    "Adultos/CENS":  "adultos y cens",
    "Especial":      "especial",
    "Ed. Física":    "educacion fisica",
    "Psicología":    "psicologia",
    "Superior":      "superior",
    "Todos":         "Todos",
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
    "jornada escolar de 25 horas- maestro de grado - [prog. res. 2502/22]  (mg5)": "MG5 - 5ta hora",
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
    "orientador educacional (/oe)":  "Orient. educacional",
    "orientador social (/os)":       "Orient. social",
    "fonoaudiologo (/fo)":           "Fonoaudiólogo",
    "bibliotecario (/bi)":           "Bibliotecario",
    "director (xxd)":                "Director",
}

ESTADOS_OPCIONES = ["publicada","designada","desierta","cerrada","anulada","renunciada","finalizada"]
ESTADOS_DISPLAY  = {
    "publicada":  "Publicada (disponible)",
    "designada":  "Designada (tomada)",
    "desierta":   "Desierta (sin postulantes)",
    "cerrada":    "Cerrada",
    "anulada":    "Anulada",
    "renunciada": "Renunciada",
    "finalizada": "Finalizada",
}

# ── Base de datos ──────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS usuarios (
        chat_id INTEGER PRIMARY KEY, username TEXT,
        nivel TEXT DEFAULT 'Todos', distritos TEXT DEFAULT '',
        cargos TEXT DEFAULT '', estados TEXT DEFAULT 'publicada',
        activo INTEGER DEFAULT 1, creado_en TEXT DEFAULT CURRENT_TIMESTAMP)""")
    for col, default in [("distritos","''"),("cargos","''"),("estados","'publicada'")]:
        try: c.execute(f"ALTER TABLE usuarios ADD COLUMN {col} TEXT DEFAULT {default}")
        except: pass
    for viejo, nuevo in [("Publicadas","publicada"),("Tomadas","designada"),("Ambas","")]:
        try: c.execute("UPDATE usuarios SET estados=? WHERE estados=?", (nuevo, viejo))
        except: pass
    c.execute("""CREATE TABLE IF NOT EXISTS ofertas_vistas (
        oferta_id TEXT PRIMARY KEY, visto_en TEXT DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS seguimiento (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER, ige TEXT, cargo TEXT, distrito TEXT, nivel TEXT,
        estado_ultimo TEXT, creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(chat_id, ige))""")
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

def get_all_active():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT chat_id,nivel,distritos,cargos,COALESCE(estados,'publicada') FROM usuarios WHERE activo=1"
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

def get_seguimientos(chat_id):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT ige,cargo,distrito,nivel,estado_ultimo FROM seguimiento WHERE chat_id=?", (chat_id,)).fetchall()
    conn.close(); return rows

def add_seguimiento(chat_id, ige, cargo, distrito, nivel, estado):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("INSERT OR IGNORE INTO seguimiento (chat_id,ige,cargo,distrito,nivel,estado_ultimo) VALUES (?,?,?,?,?,?)",
                     (chat_id, ige, cargo, distrito, nivel, estado))
        conn.commit()
        result = True
    except: result = False
    conn.close(); return result

def remove_seguimiento(chat_id, ige):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM seguimiento WHERE chat_id=? AND ige=?", (chat_id, ige))
    conn.commit(); conn.close()

def get_all_seguimientos():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT id,chat_id,ige,estado_ultimo FROM seguimiento").fetchall()
    conn.close(); return rows

def update_estado_seguimiento(seg_id, nuevo_estado):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE seguimiento SET estado_ultimo=? WHERE id=?", (nuevo_estado, seg_id))
    conn.commit(); conn.close()

# ── Helpers ────────────────────────────────────────────────────────────────────
def csv_to_list(s): return [x.strip() for x in s.split(",") if x.strip()]
def list_to_csv(lst): return ",".join(lst)

def resumen_lista(lst, vacio="Todos"):
    if not lst: return vacio
    if len(lst) == 1: return lst[0]
    return f"{lst[0]} +{len(lst)-1} más"

def formatear_fecha(fecha_str):
    try:
        dt = datetime.strptime(fecha_str, "%Y-%m-%dT%H:%M:%SZ")
        return dt.strftime("%d/%m/%Y %H:%M")
    except: return fecha_str

# ── Teclados ───────────────────────────────────────────────────────────────────
def menu_principal():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Explorar ofertas",    callback_data="menu_explorar"),
         InlineKeyboardButton("🔔 Mis alertas",         callback_data="menu_alertas")],
        [InlineKeyboardButton("📌 Seguimiento",          callback_data="menu_seguimiento"),
         InlineKeyboardButton("⚙️ Configurar alertas",  callback_data="menu_configurar")],
        [InlineKeyboardButton("ℹ️ Acerca del bot",      callback_data="menu_acerca")],
    ])

def build_nivel_keyboard(prefix):
    kb = []; row = []
    for i, n in enumerate(NIVELES):
        row.append(InlineKeyboardButton(n, callback_data=f"{prefix}nivel_{i}"))
        if (i+1) % 3 == 0: kb.append(row); row = []
    if row: kb.append(row)
    return InlineKeyboardMarkup(kb)

def build_dist_keyboard(prefix, pagina, sel):
    inicio = pagina * DIST_POR_PAG
    fin    = inicio + DIST_POR_PAG
    total_pag = (len(DISTRITOS) + DIST_POR_PAG - 1) // DIST_POR_PAG
    kb = []; row = []
    for i, (idx, d) in enumerate(list(enumerate(DISTRITOS))[inicio:fin]):
        tick = "✅ " if d in sel else ""
        row.append(InlineKeyboardButton(f"{tick}{DISTRITOS_DISPLAY[idx][:18]}", callback_data=f"{prefix}dtog_{idx}"))
        if (i+1) % 2 == 0: kb.append(row); row = []
    if row: kb.append(row)
    nav = []
    if pagina > 0: nav.append(InlineKeyboardButton("◀", callback_data=f"{prefix}dpag_{pagina-1}"))
    nav.append(InlineKeyboardButton(f"{pagina+1}/{total_pag}", callback_data=f"{prefix}dpag_noop"))
    if fin < len(DISTRITOS): nav.append(InlineKeyboardButton("▶", callback_data=f"{prefix}dpag_{pagina+1}"))
    kb.append(nav)
    n = len(sel)
    kb.append([InlineKeyboardButton(f"✓ Listo ({n} sel.)" if n else "✓ Listo (Todos)", callback_data=f"{prefix}dist_listo")])
    return InlineKeyboardMarkup(kb)

def build_cargo_keyboard(prefix, sel):
    kb = []; row = []
    for i, c in enumerate(CARGOS_COMUNES):
        tick = "✅ " if c in sel else ""
        row.append(InlineKeyboardButton(f"{tick}{CARGOS_DISPLAY.get(c,c)[:20]}", callback_data=f"{prefix}ctog_{i}"))
        if (i+1) % 2 == 0: kb.append(row); row = []
    if row: kb.append(row)
    kb.append([InlineKeyboardButton("✏️ Cargo personalizado", callback_data=f"{prefix}cargo_custom")])
    n = len(sel)
    kb.append([InlineKeyboardButton(f"✓ Listo ({n} sel.)" if n else "✓ Listo (Todos)", callback_data=f"{prefix}cargo_listo")])
    return InlineKeyboardMarkup(kb)

def build_estado_keyboard(prefix, sel):
    kb = []
    for e in ESTADOS_OPCIONES:
        tick = "✅ " if e in sel else ""
        kb.append([InlineKeyboardButton(f"{tick}{ESTADOS_DISPLAY.get(e,e)}", callback_data=f"{prefix}etog_{e}")])
    n = len(sel)
    kb.append([InlineKeyboardButton(f"✓ Listo ({n} sel.)" if n else "✓ Listo (Todos)", callback_data=f"{prefix}estado_listo")])
    return InlineKeyboardMarkup(kb)

def resumen_filtros(context, prefix):
    nivel     = context.user_data.get(f"{prefix}nivel","Todos")
    distritos = context.user_data.get(f"{prefix}distritos",[])
    cargos    = context.user_data.get(f"{prefix}cargos",[])
    estados   = context.user_data.get(f"{prefix}estados",[])
    return (
        f"🏫 Nivel: *{nivel}*\n"
        f"📍 Distritos: *{resumen_lista(distritos)}*\n"
        f"📝 Cargos: *{resumen_lista(cargos)}*\n"
        f"🔖 Estados: *{resumen_lista(estados,'Todos')}*"
    )

# ── API Solr ───────────────────────────────────────────────────────────────────
def build_fq(nivel, distritos_list, cargos_list, estados_list, solo_futuras=True):
    fq = []
    if solo_futuras: fq.append("finoferta:[NOW TO *]")
    if nivel != "Todos":
        fq.append(f'descnivelmodalidad:"{NIVELES_API.get(nivel, nivel.lower())}"')
    if distritos_list:
        fq.append("(" + " OR ".join(f'descdistrito:"{d}"' for d in distritos_list) + ")")
    if cargos_list:
        fq.append("(" + " OR ".join(f'cargo:"{c}"' for c in cargos_list) + ")")
    if estados_list:
        fq.append("(" + " OR ".join(f'estado:"{e}"' for e in estados_list) + ")")
    return fq

def consultar_api(fq, rows=100, sort="finoferta asc"):
    params = {"q":"*:*","rows":str(rows),"sort":sort,"wt":"json","fq":fq}
    resp = get_session().get(APD_API, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()

def doc_to_oferta(doc):
    return {
        "ige":             doc.get("ige","N/D"),
        "nivel":           doc.get("descnivelmodalidad","N/D"),
        "cargo":           doc.get("cargo","N/D"),
        "distrito":        doc.get("descdistrito","N/D"),
        "establecimiento": doc.get("descestablecimiento", doc.get("clave","N/D")),
        "cierre":          formatear_fecha(doc.get("finoferta","N/D")),
        "estado":          doc.get("estado","N/D"),
        "link":            APD_URL,
        "id": hashlib.md5(f"{doc.get('ige','')}-{doc.get('cargo','')}-{doc.get('descdistrito','')}".encode()).hexdigest(),
    }

def fmt_oferta(o, mostrar_btn_alerta=False, mostrar_btn_seguimiento=False):
    estado_emoji = "🟢" if "publicad" in o.get("estado","").lower() else "🔴"
    texto = (
        f"📚 *OFERTA APD*\n━━━━━━━━━━━━━━━\n"
        f"📋 IGE: `{o.get('ige','N/D')}`\n"
        f"🏫 Nivel: {o.get('nivel','N/D')}\n"
        f"📝 Cargo: {o.get('cargo','N/D')}\n"
        f"📍 Distrito: {o.get('distrito','N/D')}\n"
        f"🏛️ Est.: {o.get('establecimiento','N/D')}\n"
        f"⏰ Cierre: *{o.get('cierre','N/D')}*\n"
        f"{estado_emoji} Estado: {o.get('estado','N/D')}\n"
        f"━━━━━━━━━━━━━━━"
    )
    botones = []
    if mostrar_btn_alerta:
        ige = o.get("ige","")[:20]
        botones.append([InlineKeyboardButton("🔔 Agregar a mis alertas", callback_data=f"alerta_add_{ige}")])
    if mostrar_btn_seguimiento:
        ige = o.get("ige","")[:20]
        botones.append([InlineKeyboardButton("📌 Seguir este cargo", callback_data=f"seg_add_{ige}")])
    markup = InlineKeyboardMarkup(botones) if botones else None
    return texto, markup

# ── Jobs periódicos ────────────────────────────────────────────────────────────
async def chequear_nuevas(application):
    ahora = datetime.now().time()
    if not (HORA_INICIO <= ahora <= HORA_FIN): return
    logger.info("Chequeando nuevas ofertas...")
    try:
        data  = consultar_api(["finoferta:[NOW TO *]"], rows=500)
        docs  = data.get("response",{}).get("docs",[])
        nuevas = [doc_to_oferta(d) for d in docs if es_nueva(
            hashlib.md5(f"{d.get('ige','')}-{d.get('cargo','')}-{d.get('descdistrito','')}".encode()).hexdigest()
        )]
        if not nuevas: return
        for o in nuevas: mark_vista(o["id"])
        for chat_id, nivel, dist_csv, cargo_csv, estado_csv in get_all_active():
            dl = csv_to_list(dist_csv); cl = csv_to_list(cargo_csv); el = csv_to_list(estado_csv)
            fq = build_fq(nivel, dl, cl, el)
            for o in nuevas:
                estado_o  = o.get("estado","").lower().strip()
                nivel_o   = o.get("nivel","").lower().strip()
                dist_o    = o.get("distrito","").lower().strip()
                cargo_o   = o.get("cargo","").lower().strip()
                if nivel != "Todos" and NIVELES_API.get(nivel,"") not in nivel_o: continue
                if dl and not any(d == dist_o for d in dl): continue
                if cl and not any(c == cargo_o for c in cl): continue
                if el and estado_o not in [e.lower() for e in el]: continue
                try:
                    texto, _ = fmt_oferta(o)
                    await application.bot.send_message(chat_id=chat_id, text=f"🆕 *NUEVA OFERTA*\n{texto}",
                        parse_mode="Markdown", disable_web_page_preview=True)
                    await asyncio.sleep(0.1)
                except Exception as e: logger.error(f"Error a {chat_id}: {e}")
    except Exception as e: logger.error(f"Error chequear_nuevas: {e}")

async def chequear_seguimientos(application):
    segs = get_all_seguimientos()
    if not segs: return
    for seg_id, chat_id, ige, estado_ultimo in segs:
        try:
            data = consultar_api([f'ige:"{ige}"'], rows=1, sort="finoferta desc")
            docs = data.get("response",{}).get("docs",[])
            if not docs: continue
            nuevo_estado = docs[0].get("estado","").lower().strip()
            if nuevo_estado and nuevo_estado != estado_ultimo.lower().strip():
                update_estado_seguimiento(seg_id, nuevo_estado)
                o = doc_to_oferta(docs[0])
                texto, _ = fmt_oferta(o)
                await application.bot.send_message(
                    chat_id=chat_id,
                    text=f"🔄 *CAMBIO DE ESTADO*\nIGE `{ige}`: `{estado_ultimo}` → `{nuevo_estado}`\n\n{texto}",
                    parse_mode="Markdown", disable_web_page_preview=True)
        except Exception as e: logger.error(f"Error seguimiento {ige}: {e}")

# ── /start y menú ──────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    upsert_user(chat_id=u.id, username=u.username or u.first_name)
    await update.message.reply_text(
        f"👋 Hola, *{u.first_name}*\\! Bienvenido al bot de *Alertas APD*\\.\n\n"
        "¿Qué querés hacer?",
        parse_mode="MarkdownV2",
        reply_markup=menu_principal())

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    accion = q.data.replace("menu_","")
    if accion == "explorar":
        context.user_data["exp_nivel"]    = "Todos"
        context.user_data["exp_distritos"] = []
        context.user_data["exp_cargos"]   = []
        context.user_data["exp_estados"]  = []
        context.user_data["exp_pagina"]   = 0
        await q.edit_message_text(
            "🔍 *Explorar ofertas*\n\nElegí los filtros para tu búsqueda.\n\n"
            + resumen_filtros(context,"exp_"),
            reply_markup=_explorar_menu(context), parse_mode="Markdown")
    elif accion == "alertas":
        await _mostrar_alertas(q, context)
    elif accion == "seguimiento":
        await _mostrar_seguimiento(q, context)
    elif accion == "configurar":
        await _iniciar_configurar(q, context)
    elif accion == "acerca":
        await _mostrar_acerca(q)

# ── Explorar ───────────────────────────────────────────────────────────────────
def _explorar_menu(context):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏫 Nivel",    callback_data="exp_f_nivel"),
         InlineKeyboardButton("📍 Distritos", callback_data="exp_f_dist")],
        [InlineKeyboardButton("📝 Cargos",   callback_data="exp_f_cargo"),
         InlineKeyboardButton("🔖 Estados",  callback_data="exp_f_estado")],
        [InlineKeyboardButton("🔎 Buscar ahora", callback_data="exp_buscar")],
        [InlineKeyboardButton("« Volver al menú", callback_data="exp_volver")],
    ])

async def explorar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    data = q.data

    if data == "exp_f_nivel":
        await q.edit_message_text(
            "🏫 *Elegí el nivel:*",
            reply_markup=build_nivel_keyboard("exp_"), parse_mode="Markdown")

    elif data.startswith("exp_nivel_"):
        idx = int(data.replace("exp_nivel_",""))
        context.user_data["exp_nivel"] = NIVELES[idx]
        await q.edit_message_text(
            resumen_filtros(context,"exp_"),
            reply_markup=_explorar_menu(context), parse_mode="Markdown")

    elif data == "exp_f_dist":
        sel = context.user_data.get("exp_distritos",[])
        pag = context.user_data.get("exp_pagina",0)
        await q.edit_message_text(
            f"{resumen_filtros(context,'exp_')}\n\n📍 *Elegí distritos:*",
            reply_markup=build_dist_keyboard("exp_",pag,sel), parse_mode="Markdown")

    elif data.startswith("exp_dpag_"):
        if data == "exp_dpag_noop": return
        pag = int(data.replace("exp_dpag_",""))
        context.user_data["exp_pagina"] = pag
        sel = context.user_data.get("exp_distritos",[])
        await q.edit_message_text(
            f"{resumen_filtros(context,'exp_')}\n\n📍 *Elegí distritos:*",
            reply_markup=build_dist_keyboard("exp_",pag,sel), parse_mode="Markdown")

    elif data.startswith("exp_dtog_"):
        idx = int(data.replace("exp_dtog_",""))
        d   = DISTRITOS[idx]
        sel = context.user_data.get("exp_distritos",[])
        if d in sel: sel.remove(d)
        else: sel.append(d)
        context.user_data["exp_distritos"] = sel
        pag = context.user_data.get("exp_pagina",0)
        await q.edit_message_text(
            f"{resumen_filtros(context,'exp_')}\n\n📍 *Elegí distritos:*",
            reply_markup=build_dist_keyboard("exp_",pag,sel), parse_mode="Markdown")

    elif data == "exp_dist_listo":
        await q.edit_message_text(
            resumen_filtros(context,"exp_"),
            reply_markup=_explorar_menu(context), parse_mode="Markdown")

    elif data == "exp_f_cargo":
        sel = context.user_data.get("exp_cargos",[])
        await q.edit_message_text(
            f"{resumen_filtros(context,'exp_')}\n\n📝 *Elegí cargos:*",
            reply_markup=build_cargo_keyboard("exp_",sel), parse_mode="Markdown")

    elif data.startswith("exp_ctog_"):
        idx = int(data.replace("exp_ctog_",""))
        c   = CARGOS_COMUNES[idx]
        sel = context.user_data.get("exp_cargos",[])
        if c in sel: sel.remove(c)
        else: sel.append(c)
        context.user_data["exp_cargos"] = sel
        await q.edit_message_text(
            f"{resumen_filtros(context,'exp_')}\n\n📝 *Elegí cargos:*",
            reply_markup=build_cargo_keyboard("exp_",sel), parse_mode="Markdown")

    elif data == "exp_cargo_custom":
        await q.edit_message_text("✏️ Escribí el nombre del cargo a buscar:")
        context.user_data["exp_esperando_cargo"] = True

    elif data == "exp_cargo_listo":
        await q.edit_message_text(
            resumen_filtros(context,"exp_"),
            reply_markup=_explorar_menu(context), parse_mode="Markdown")

    elif data == "exp_f_estado":
        sel = context.user_data.get("exp_estados",[])
        await q.edit_message_text(
            f"{resumen_filtros(context,'exp_')}\n\n🔖 *Elegí estados:*",
            reply_markup=build_estado_keyboard("exp_",sel), parse_mode="Markdown")

    elif data.startswith("exp_etog_"):
        e   = data.replace("exp_etog_","")
        sel = context.user_data.get("exp_estados",[])
        if e in sel: sel.remove(e)
        else: sel.append(e)
        context.user_data["exp_estados"] = sel
        await q.edit_message_text(
            f"{resumen_filtros(context,'exp_')}\n\n🔖 *Elegí estados:*",
            reply_markup=build_estado_keyboard("exp_",sel), parse_mode="Markdown")

    elif data == "exp_estado_listo":
        await q.edit_message_text(
            resumen_filtros(context,"exp_"),
            reply_markup=_explorar_menu(context), parse_mode="Markdown")

    elif data == "exp_buscar":
        await _ejecutar_busqueda(q, context)

    elif data == "exp_volver":
        await q.edit_message_text("¿Qué querés hacer?", reply_markup=menu_principal())

async def _ejecutar_busqueda(q, context):
    nivel     = context.user_data.get("exp_nivel","Todos")
    distritos = context.user_data.get("exp_distritos",[])
    cargos    = context.user_data.get("exp_cargos",[])
    estados   = context.user_data.get("exp_estados",[])
    fq = build_fq(nivel, distritos, cargos, estados)
    await q.edit_message_text("🔍 Buscando...")
    try:
        data  = consultar_api(fq, rows=20)
        docs  = data.get("response",{}).get("docs",[])
        total = data.get("response",{}).get("numFound",0)
        if not docs:
            await q.message.reply_text(
                "📭 No se encontraron ofertas con esos filtros.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("« Volver a explorar", callback_data="exp_volver_busqueda")
                ]]))
            return
        await q.message.reply_text(
            f"📋 *{total} oferta{'s' if total!=1 else ''} encontrada{'s' if total!=1 else ''}*"
            f"{' (mostrando 20)' if total > 20 else ''}:",
            parse_mode="Markdown")
        for doc in docs:
            o = doc_to_oferta(doc)
            texto, markup = fmt_oferta(o, mostrar_btn_alerta=True, mostrar_btn_seguimiento=True)
            await q.message.reply_text(texto, parse_mode="Markdown",
                disable_web_page_preview=True, reply_markup=markup)
            await asyncio.sleep(0.2)
        await q.message.reply_text(
            "¿Qué más querés hacer?", reply_markup=menu_principal())
    except Exception as e:
        await q.message.reply_text(f"❌ Error: `{e}`", parse_mode="Markdown")

async def exp_cargo_texto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("exp_esperando_cargo"): return
    context.user_data["exp_esperando_cargo"] = False
    cargo = update.message.text.strip()
    sel = context.user_data.get("exp_cargos",[])
    if cargo and cargo not in sel: sel.append(cargo)
    context.user_data["exp_cargos"] = sel
    await update.message.reply_text(
        resumen_filtros(context,"exp_"),
        reply_markup=_explorar_menu(context), parse_mode="Markdown")

# ── Alertar desde explorar ─────────────────────────────────────────────────────
async def alerta_add_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ige = q.data.replace("alerta_add_","")
    try:
        data = consultar_api([f'ige:"{ige}"'], rows=1)
        docs = data.get("response",{}).get("docs",[])
        if not docs:
            await q.message.reply_text("No se encontró la oferta."); return
        doc    = docs[0]
        nivel  = doc.get("descnivelmodalidad","").lower()
        dist   = doc.get("descdistrito","").lower()
        cargo  = doc.get("cargo","").lower()
        estado = doc.get("estado","").lower()
        nivel_key = next((k for k,v in NIVELES_API.items() if v == nivel), "Todos")
        d = get_user(q.from_user.id)
        dl = csv_to_list(d["distritos"]) if d else []
        cl = csv_to_list(d["cargos"])    if d else []
        el = csv_to_list(d["estados"])   if d else []
        if dist  not in dl: dl.append(dist)
        if cargo not in cl: cl.append(cargo)
        if estado not in el: el.append(estado)
        upsert_user(q.from_user.id, nivel=nivel_key,
                    distritos=list_to_csv(dl), cargos=list_to_csv(cl), estados=list_to_csv(el))
        await q.message.reply_text(
            f"✅ Alerta agregada para:\n📝 {cargo}\n📍 {dist}\n🔖 {estado}",
            parse_mode="Markdown")
    except Exception as e:
        await q.message.reply_text(f"❌ Error: `{e}`", parse_mode="Markdown")

# ── Seguimiento desde explorar ─────────────────────────────────────────────────
async def seg_add_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ige = q.data.replace("seg_add_","")
    try:
        data = consultar_api([f'ige:"{ige}"'], rows=1)
        docs = data.get("response",{}).get("docs",[])
        if not docs:
            await q.message.reply_text("No se encontró la oferta."); return
        doc    = docs[0]
        cargo  = doc.get("cargo","")
        dist   = doc.get("descdistrito","")
        nivel  = doc.get("descnivelmodalidad","")
        estado = doc.get("estado","").lower()
        ok = add_seguimiento(q.from_user.id, ige, cargo, dist, nivel, estado)
        if ok:
            await q.message.reply_text(
                f"📌 Seguimiento activado para IGE `{ige}`\n"
                f"📝 {cargo}\n📍 {dist}\n"
                f"Te avisaré si cambia el estado.",
                parse_mode="Markdown")
        else:
            await q.message.reply_text(f"Ya estás siguiendo el IGE `{ige}`.", parse_mode="Markdown")
    except Exception as e:
        await q.message.reply_text(f"❌ Error: `{e}`", parse_mode="Markdown")

# ── Mis alertas ────────────────────────────────────────────────────────────────
async def _mostrar_alertas(q, context):
    d = get_user(q.from_user.id)
    if not d:
        await q.edit_message_text("Primero usá /start."); return
    dl = csv_to_list(d["distritos"])
    cl = csv_to_list(d["cargos"])
    el = csv_to_list(d["estados"])
    estado_bot = "✅ Activas" if d["activo"] else "⏸️ Pausadas"
    await q.edit_message_text(
        f"🔔 *Mis alertas — {estado_bot}*\n\n"
        f"🏫 Nivel: {d['nivel']}\n"
        f"📍 Distritos: {', '.join(dl) if dl else 'Todos'}\n"
        f"📝 Cargos: {', '.join(cl) if cl else 'Todos'}\n"
        f"🔖 Estados: {', '.join(ESTADOS_DISPLAY.get(e,e) for e in el) if el else 'Todos'}\n",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⏸️ Pausar" if d["activo"] else "▶️ Reanudar",
                callback_data="alerta_toggle")],
            [InlineKeyboardButton("⚙️ Cambiar configuración", callback_data="menu_configurar")],
            [InlineKeyboardButton("« Volver", callback_data="alertas_volver")],
        ]))

async def alertas_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data == "alerta_toggle":
        d = get_user(q.from_user.id)
        upsert_user(q.from_user.id, activo=0 if d["activo"] else 1)
        await _mostrar_alertas(q, context)
    elif q.data == "alertas_volver":
        await q.edit_message_text("¿Qué querés hacer?", reply_markup=menu_principal())

# ── Seguimiento ────────────────────────────────────────────────────────────────
async def _mostrar_seguimiento(q, context):
    segs = get_seguimientos(q.from_user.id)
    if not segs:
        await q.edit_message_text(
            "📌 *Seguimiento de ofertas*\n\nNo estás siguiendo ninguna oferta todavía.\n\n"
            "Usá 🔍 Explorar ofertas y tocá *📌 Seguir este cargo* en cualquier resultado.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("« Volver", callback_data="seg_volver")
            ]]))
        return
    texto = "📌 *Ofertas en seguimiento:*\n\n"
    kb = []
    for ige, cargo, distrito, nivel, estado in segs:
        texto += f"• IGE `{ige}` — {cargo[:30]}\n  📍 {distrito} | Estado: {estado}\n\n"
        kb.append([InlineKeyboardButton(f"❌ Dejar de seguir IGE {ige}", callback_data=f"seg_del_{ige}")])
    kb.append([InlineKeyboardButton("« Volver", callback_data="seg_volver")])
    await q.edit_message_text(texto, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def seguimiento_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data.startswith("seg_del_"):
        ige = q.data.replace("seg_del_","")
        remove_seguimiento(q.from_user.id, ige)
        await _mostrar_seguimiento(q, context)
    elif q.data == "seg_volver":
        await q.edit_message_text("¿Qué querés hacer?", reply_markup=menu_principal())
    elif q.data == "exp_volver_busqueda":
        context.user_data["exp_nivel"]    = "Todos"
        context.user_data["exp_distritos"] = []
        context.user_data["exp_cargos"]   = []
        context.user_data["exp_estados"]  = []
        await q.edit_message_text(
            resumen_filtros(context,"exp_"),
            reply_markup=_explorar_menu(context), parse_mode="Markdown")

# ── Configurar alertas ─────────────────────────────────────────────────────────
async def _iniciar_configurar(q, context):
    d = get_user(q.from_user.id)
    prefix = "cfg_"
    context.user_data[f"{prefix}nivel"]     = d["nivel"] if d else "Todos"
    context.user_data[f"{prefix}distritos"] = csv_to_list(d["distritos"]) if d else []
    context.user_data[f"{prefix}cargos"]    = csv_to_list(d["cargos"])    if d else []
    context.user_data[f"{prefix}estados"]   = csv_to_list(d["estados"])   if d else []
    context.user_data[f"{prefix}pagina"]    = 0
    await q.edit_message_text(
        f"⚙️ *Configurar alertas*\n\n{resumen_filtros(context,prefix)}",
        reply_markup=_cfg_menu(context), parse_mode="Markdown")

def _cfg_menu(context):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏫 Nivel",    callback_data="cfg_f_nivel"),
         InlineKeyboardButton("📍 Distritos", callback_data="cfg_f_dist")],
        [InlineKeyboardButton("📝 Cargos",   callback_data="cfg_f_cargo"),
         InlineKeyboardButton("🔖 Estados",  callback_data="cfg_f_estado")],
        [InlineKeyboardButton("💾 Guardar", callback_data="cfg_guardar")],
        [InlineKeyboardButton("« Volver",   callback_data="cfg_volver")],
    ])

async def configurar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    data = q.data
    prefix = "cfg_"

    if data == "cfg_f_nivel":
        await q.edit_message_text("🏫 *Elegí el nivel para tus alertas:*",
            reply_markup=build_nivel_keyboard(prefix), parse_mode="Markdown")

    elif data.startswith("cfg_nivel_"):
        idx = int(data.replace("cfg_nivel_",""))
        context.user_data[f"{prefix}nivel"] = NIVELES[idx]
        await q.edit_message_text(
            f"⚙️ *Configurar alertas*\n\n{resumen_filtros(context,prefix)}",
            reply_markup=_cfg_menu(context), parse_mode="Markdown")

    elif data == "cfg_f_dist":
        sel = context.user_data.get(f"{prefix}distritos",[])
        pag = context.user_data.get(f"{prefix}pagina",0)
        await q.edit_message_text(
            f"{resumen_filtros(context,prefix)}\n\n📍 *Elegí distritos:*",
            reply_markup=build_dist_keyboard(prefix,pag,sel), parse_mode="Markdown")

    elif data.startswith("cfg_dpag_"):
        if data == "cfg_dpag_noop": return
        pag = int(data.replace("cfg_dpag_",""))
        context.user_data[f"{prefix}pagina"] = pag
        sel = context.user_data.get(f"{prefix}distritos",[])
        await q.edit_message_text(
            f"{resumen_filtros(context,prefix)}\n\n📍 *Elegí distritos:*",
            reply_markup=build_dist_keyboard(prefix,pag,sel), parse_mode="Markdown")

    elif data.startswith("cfg_dtog_"):
        idx = int(data.replace("cfg_dtog_",""))
        d   = DISTRITOS[idx]
        sel = context.user_data.get(f"{prefix}distritos",[])
        if d in sel: sel.remove(d)
        else: sel.append(d)
        context.user_data[f"{prefix}distritos"] = sel
        pag = context.user_data.get(f"{prefix}pagina",0)
        await q.edit_message_text(
            f"{resumen_filtros(context,prefix)}\n\n📍 *Elegí distritos:*",
            reply_markup=build_dist_keyboard(prefix,pag,sel), parse_mode="Markdown")

    elif data == "cfg_dist_listo":
        await q.edit_message_text(
            f"⚙️ *Configurar alertas*\n\n{resumen_filtros(context,prefix)}",
            reply_markup=_cfg_menu(context), parse_mode="Markdown")

    elif data == "cfg_f_cargo":
        sel = context.user_data.get(f"{prefix}cargos",[])
        await q.edit_message_text(
            f"{resumen_filtros(context,prefix)}\n\n📝 *Elegí cargos:*",
            reply_markup=build_cargo_keyboard(prefix,sel), parse_mode="Markdown")

    elif data.startswith("cfg_ctog_"):
        idx = int(data.replace("cfg_ctog_",""))
        c   = CARGOS_COMUNES[idx]
        sel = context.user_data.get(f"{prefix}cargos",[])
        if c in sel: sel.remove(c)
        else: sel.append(c)
        context.user_data[f"{prefix}cargos"] = sel
        await q.edit_message_text(
            f"{resumen_filtros(context,prefix)}\n\n📝 *Elegí cargos:*",
            reply_markup=build_cargo_keyboard(prefix,sel), parse_mode="Markdown")

    elif data == "cfg_cargo_custom":
        await q.edit_message_text("✏️ Escribí el nombre del cargo:")
        context.user_data["cfg_esperando_cargo"] = True

    elif data == "cfg_cargo_listo":
        await q.edit_message_text(
            f"⚙️ *Configurar alertas*\n\n{resumen_filtros(context,prefix)}",
            reply_markup=_cfg_menu(context), parse_mode="Markdown")

    elif data == "cfg_f_estado":
        sel = context.user_data.get(f"{prefix}estados",[])
        await q.edit_message_text(
            f"{resumen_filtros(context,prefix)}\n\n🔖 *Elegí estados:*",
            reply_markup=build_estado_keyboard(prefix,sel), parse_mode="Markdown")

    elif data.startswith("cfg_etog_"):
        e   = data.replace("cfg_etog_","")
        sel = context.user_data.get(f"{prefix}estados",[])
        if e in sel: sel.remove(e)
        else: sel.append(e)
        context.user_data[f"{prefix}estados"] = sel
        await q.edit_message_text(
            f"{resumen_filtros(context,prefix)}\n\n🔖 *Elegí estados:*",
            reply_markup=build_estado_keyboard(prefix,sel), parse_mode="Markdown")

    elif data == "cfg_estado_listo":
        await q.edit_message_text(
            f"⚙️ *Configurar alertas*\n\n{resumen_filtros(context,prefix)}",
            reply_markup=_cfg_menu(context), parse_mode="Markdown")

    elif data == "cfg_guardar":
        nivel     = context.user_data.get(f"{prefix}nivel","Todos")
        distritos = context.user_data.get(f"{prefix}distritos",[])
        cargos    = context.user_data.get(f"{prefix}cargos",[])
        estados   = context.user_data.get(f"{prefix}estados",[])
        upsert_user(q.from_user.id,
            nivel=nivel, distritos=list_to_csv(distritos),
            cargos=list_to_csv(cargos), estados=list_to_csv(estados), activo=1)
        await q.edit_message_text(
            f"✅ *Alertas configuradas\\!*\n\n"
            f"🏫 Nivel: {nivel}\n"
            f"📍 Distritos: {', '.join(distritos) if distritos else 'Todos'}\n"
            f"📝 Cargos: {', '.join(cargos) if cargos else 'Todos'}\n"
            f"🔖 Estados: {', '.join(ESTADOS_DISPLAY.get(e,e) for e in estados) if estados else 'Todos'}\n\n"
            "¿Qué querés hacer?",
            parse_mode="MarkdownV2", reply_markup=menu_principal())

    elif data == "cfg_volver":
        await q.edit_message_text("¿Qué querés hacer?", reply_markup=menu_principal())

async def cfg_cargo_texto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("cfg_esperando_cargo"): return
    context.user_data["cfg_esperando_cargo"] = False
    cargo = update.message.text.strip()
    sel = context.user_data.get("cfg_cargos",[])
    if cargo and cargo not in sel: sel.append(cargo)
    context.user_data["cfg_cargos"] = sel
    prefix = "cfg_"
    await update.message.reply_text(
        f"⚙️ *Configurar alertas*\n\n{resumen_filtros(context,prefix)}",
        reply_markup=_cfg_menu(context), parse_mode="Markdown")

# ── Acerca del bot ─────────────────────────────────────────────────────────────
async def _mostrar_acerca(q):
    await q.edit_message_text(
        "💙 *Acerca de este bot*\n\n"
        "Este bot fue creado por un chico con pocos conocimientos de informática "
        "para su novia maestra, que siempre está buscando laburo y que merece "
        "enterarse de las ofertas docentes antes que nadie\\.\n\n"
        "Lo hice entendiendo que los cargos docentes a veces se manejan mal, "
        "que los tiempos son cortos y que la realidad de los docentes no está "
        "para andar pagando servicios innecesarios\\.\n\n"
        "Este bot es y va a seguir siendo *completamente gratuito*\\.\n\n"
        "📬 *¿Tenés sugerencias o encontraste un error?*\n"
        "Escribinos a tbotapd@gmail\\.com\n\n"
        "☕ *¿Querés colaborar para mantener el servidor?*\n"
        "Podés hacerlo al alias: *apdbot*\n\n"
        "_Gracias por usar el bot\\. Ojalá le sirva a muchos docentes_ 🍎",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("« Volver al menú", callback_data="acerca_volver")
        ]]))

async def acerca_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    await q.edit_message_text("¿Qué querés hacer?", reply_markup=menu_principal())

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    # Menú principal
    app.add_handler(CallbackQueryHandler(menu_callback,         pattern="^menu_"))
    # Explorar
    app.add_handler(CallbackQueryHandler(explorar_callback,     pattern="^exp_"))
    # Configurar alertas
    app.add_handler(CallbackQueryHandler(configurar_callback,   pattern="^cfg_"))
    # Alertas toggle/volver
    app.add_handler(CallbackQueryHandler(alertas_callback,      pattern="^alerta_toggle$|^alertas_volver$"))
    # Agregar alerta desde explorar
    app.add_handler(CallbackQueryHandler(alerta_add_callback,   pattern="^alerta_add_"))
    # Seguimiento
    app.add_handler(CallbackQueryHandler(seguimiento_callback,  pattern="^seg_"))
    app.add_handler(CallbackQueryHandler(seg_add_callback,      pattern="^seg_add_"))
    # Acerca
    app.add_handler(CallbackQueryHandler(acerca_callback,       pattern="^acerca_volver$"))
    # Texto libre (cargo personalizado)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _texto_libre))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(chequear_nuevas,       "interval", minutes=SCRAPE_INTERVAL,    args=[app])
    scheduler.add_job(chequear_seguimientos, "interval", minutes=SCRAPE_INTERVAL,    args=[app])
    scheduler.start()

    logger.info("Bot APD v4 corriendo...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

async def _texto_libre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("exp_esperando_cargo"):
        await exp_cargo_texto(update, context)
    elif context.user_data.get("cfg_esperando_cargo"):
        await cfg_cargo_texto(update, context)

if __name__ == "__main__":
    main()
