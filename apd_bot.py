"""
APD Notificaciones Bot v2 - Con paginación de distritos, cargos y filtro de estado
"""

import asyncio
import logging
import os
import sqlite3
import hashlib
from datetime import datetime, time
from typing import Optional

import requests
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    ContextTypes, ConversationHandler, MessageHandler, filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

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
APD_URL     = "https://misservicios.abc.gob.ar/actos.publicos.digitales/"

ELIGIENDO_NIVEL, ELIGIENDO_DISTRITO, ELIGIENDO_CARGO, ELIGIENDO_ESTADO = range(4)
DIST_POR_PAGINA = 16

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

NIVELES = ["Inicial","Primaria","Secundaria","Superior","Especial","Adultos","Artística","Técnica","Todos"]

DISTRITOS = [
    "Adolfo Alsina","Adolfo Gonzales Chaves","Alberti","Almirante Brown","Azul",
    "Bahía Blanca","Balcarce","Berazategui","Bolívar","Bragado","Campana",
    "Cañuelas","Capitán Sarmiento","Carlos Casares","Carlos Tejedor","Carmen de Areco",
    "Chascomús","Chivilcoy","Coronel Dorrego","Coronel Pringles","Coronel Rosales",
    "Coronel Suárez","Daireaux","Dolores","Ensenada","Escobar","Esteban Echeverría",
    "Exaltación de la Cruz","Ezeiza","Florencio Varela","General Alvarado",
    "General Belgrano","General Guido","General Juan Madariaga","General La Madrid",
    "General Las Heras","General Lavalle","General Paz","General Pinto",
    "General Pueyrredón","General Rodríguez","General San Martín","General Viamonte",
    "General Villegas","Guaminí","Hipólito Yrigoyen","Hurlingham","Ituzaingó",
    "José C. Paz","Junín","La Costa","La Matanza","La Plata","Lanús","Laprida",
    "Las Flores","Leandro N. Alem","Lincoln","Lobería","Lobos","Lomas de Zamora",
    "Luján","Maipú","Malvinas Argentinas","Mar Chiquita","Marcos Paz","Mercedes",
    "Merlo","Monte","Monte Hermoso","Moreno","Morón","Navarro","Necochea",
    "Nueve de Julio","Olavarría","Patagones","Pehuajó","Pellegrini","Pergamino",
    "Pila","Pilar","Pinamar","Presidente Perón","Puán","Punta Indio","Quilmes",
    "Ramallo","Rauch","Rivadavia","Rojas","Roque Pérez","Saavedra","Saladillo",
    "Salto","Salliqueló","San Andrés de Giles","San Antonio de Areco","San Cayetano",
    "San Fernando","San Isidro","San Miguel","San Nicolás","San Pedro","San Vicente",
    "Suipacha","Tandil","Tapalqué","Tigre","Tordillo","Tornquist","Trenque Lauquen",
    "Tres Arroyos","Tres de Febrero","Tres Lomas","Vicente López","Villa Gesell",
    "Villarino","Zárate","Todos"
]

CARGOS_COMUNES = [
    "Maestro de grado","MG5 - Maestra grado 5ta hora","Maestro de jardín","Secretario","Director",
    "Matemática","Lengua","Historia","Geografía",
    "Inglés","Educación Física","Música","Plástica",
    "Física","Química","Biología","Filosofía",
    "Informática","Tecnología","Preceptor","Otro (escribir)",
]

ESTADOS = ["Publicadas", "Tomadas", "Ambas"]

# ─────────────────────────────────────────────
# BASE DE DATOS
# ─────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS usuarios (
        chat_id INTEGER PRIMARY KEY, username TEXT,
        nivel TEXT DEFAULT 'Todos', distrito TEXT DEFAULT 'Todos',
        cargo TEXT DEFAULT '', estado TEXT DEFAULT 'Publicadas',
        activo INTEGER DEFAULT 1,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP)""")
    # Agregar columna estado si no existe (para bases de datos previas)
    try:
        c.execute("ALTER TABLE usuarios ADD COLUMN estado TEXT DEFAULT 'Publicadas'")
    except:
        pass
    c.execute("""CREATE TABLE IF NOT EXISTS ofertas_vistas (
        oferta_id TEXT PRIMARY KEY, visto_en TEXT DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit(); conn.close()

def get_user(chat_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM usuarios WHERE chat_id=?", (chat_id,))
    row = c.fetchone(); conn.close()
    if row:
        return {
            "chat_id": row[0], "username": row[1], "nivel": row[2],
            "distrito": row[3], "cargo": row[4],
            "estado": row[5] if len(row) > 5 else "Publicadas",
            "activo": row[6] if len(row) > 6 else row[5]
        }
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
        "SELECT chat_id, nivel, distrito, cargo, COALESCE(estado,'Publicadas') FROM usuarios WHERE activo=1"
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
# TECLADO DE DISTRITOS CON PAGINACIÓN
# ─────────────────────────────────────────────
def build_distrito_keyboard(pagina: int):
    inicio = pagina * DIST_POR_PAGINA
    fin = inicio + DIST_POR_PAGINA
    chunk = DISTRITOS[inicio:fin]
    total_paginas = (len(DISTRITOS) + DIST_POR_PAGINA - 1) // DIST_POR_PAGINA

    kb = []
    row = []
    for i, d in enumerate(chunk):
        row.append(InlineKeyboardButton(d[:20], callback_data=f"dist_{d}"))
        if (i + 1) % 2 == 0:
            kb.append(row); row = []
    if row:
        kb.append(row)

    # Fila de navegación
    nav = []
    if pagina > 0:
        nav.append(InlineKeyboardButton("◀ Anterior", callback_data=f"distpag_{pagina-1}"))
    nav.append(InlineKeyboardButton(f"{pagina+1}/{total_paginas}", callback_data="distpag_noop"))
    if fin < len(DISTRITOS):
        nav.append(InlineKeyboardButton("Siguiente ▶", callback_data=f"distpag_{pagina+1}"))
    kb.append(nav)

    return InlineKeyboardMarkup(kb)

# ─────────────────────────────────────────────
# TECLADO DE CARGOS
# ─────────────────────────────────────────────
def build_cargo_keyboard():
    kb = []
    row = []
    for i, c in enumerate(CARGOS_COMUNES):
        row.append(InlineKeyboardButton(c, callback_data=f"cargo_{c}"))
        if (i + 1) % 2 == 0:
            kb.append(row); row = []
    if row:
        kb.append(row)
    kb.append([InlineKeyboardButton("📋 Todos los cargos", callback_data="cargo_Todos")])
    return InlineKeyboardMarkup(kb)

# ─────────────────────────────────────────────
# TECLADO DE ESTADO
# ─────────────────────────────────────────────
def build_estado_keyboard():
    kb = [[InlineKeyboardButton(e, callback_data=f"estado_{e}")] for e in ESTADOS]
    return InlineKeyboardMarkup(kb)

# ─────────────────────────────────────────────
# SCRAPER
# ─────────────────────────────────────────────
def scrape_ofertas():
    headers = {"User-Agent":"Mozilla/5.0","Accept-Language":"es-AR,es;q=0.9"}
    try:
        resp = requests.get(APD_URL, headers=headers, timeout=15, verify=False)
        soup = BeautifulSoup(resp.text, "html.parser")
        return parse_html(soup)
    except Exception as e:
        logger.error(f"Scrape error: {e}"); return []

def parse_html(soup):
    ofertas = []
    tabla = soup.find("table")
    if tabla:
        for fila in tabla.find_all("tr")[1:]:
            celdas = fila.find_all("td")
            if len(celdas) >= 5:
                o = {
                    "ige": celdas[0].get_text(strip=True),
                    "nivel": celdas[1].get_text(strip=True),
                    "cargo": celdas[2].get_text(strip=True),
                    "distrito": celdas[3].get_text(strip=True),
                    "establecimiento": celdas[4].get_text(strip=True),
                    "cierre": celdas[5].get_text(strip=True) if len(celdas)>5 else "",
                    "estado": celdas[6].get_text(strip=True) if len(celdas)>6 else "Publicada",
                    "link": APD_URL,
                }
                o["id"] = hashlib.md5(f"{o['ige']}-{o['cargo']}-{o['distrito']}".encode()).hexdigest()
                ofertas.append(o)
    return ofertas

def coincide(o, nivel, distrito, cargo, estado_filtro):
    if nivel != "Todos" and nivel.lower() not in o.get("nivel","").lower():
        return False
    if distrito != "Todos" and distrito.lower() not in o.get("distrito","").lower():
        return False
    if cargo and cargo != "Todos" and cargo.lower() not in o.get("cargo","").lower():
        return False
    # Filtro de estado
    estado_oferta = o.get("estado","").lower()
    if estado_filtro == "Publicadas" and "publicad" not in estado_oferta:
        return False
    if estado_filtro == "Tomadas" and "tomad" not in estado_oferta:
        return False
    # "Ambas" no filtra
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
    if not (HORA_INICIO <= ahora <= HORA_FIN):
        return
    logger.info("Chequeando APD...")
    ofertas = scrape_ofertas()
    nuevas = [o for o in ofertas if es_nueva(o["id"])]
    if not nuevas: return
    for o in nuevas: mark_vista(o["id"])
    for chat_id, nivel, distrito, cargo, estado_filtro in get_all_active_users():
        for o in nuevas:
            if coincide(o, nivel, distrito, cargo, estado_filtro):
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
        "📢 Soy el bot de *Alertas APD*. Te aviso cuando salen nuevas ofertas "
        "docentes del portal ABC de la Provincia de Buenos Aires.\n\n"
        "Por defecto recibís *todas las ofertas publicadas*. Usá /configurar para filtrar.\n\n"
        "📋 Comandos:\n/configurar · /mis\\_alertas · /pausar · /reanudar · /ayuda",
        parse_mode="Markdown")

async def mis_alertas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = get_user(update.effective_user.id)
    if not d:
        await update.message.reply_text("Primero usá /start."); return
    await update.message.reply_text(
        f"📋 *Tu configuración:*\n\n"
        f"Estado: {'✅ Activo' if d['activo'] else '⏸️ Pausado'}\n"
        f"🏫 Nivel: {d['nivel']}\n"
        f"📍 Distrito: {d['distrito']}\n"
        f"📝 Cargo: {d['cargo'] or 'Todos'}\n"
        f"🔖 Estado oferta: {d.get('estado','Publicadas')}\n\n"
        "Cambiá con /configurar",
        parse_mode="Markdown")

# ── Paso 1: Nivel ──
async def configurar(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    nivel = q.data.replace("nivel_","")
    context.user_data["nivel"] = nivel
    context.user_data["dist_pagina"] = 0
    await q.edit_message_text(
        f"✅ Nivel: *{nivel}*\n\n📍 *Paso 2/4 — ¿En qué distrito?*",
        reply_markup=build_distrito_keyboard(0), parse_mode="Markdown")
    return ELIGIENDO_DISTRITO

# ── Paso 2: Distrito con paginación ──
async def cb_distpag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data == "distpag_noop":
        return ELIGIENDO_DISTRITO
    pagina = int(q.data.replace("distpag_",""))
    context.user_data["dist_pagina"] = pagina
    nivel = context.user_data.get("nivel","Todos")
    await q.edit_message_text(
        f"✅ Nivel: *{nivel}*\n\n📍 *Paso 2/4 — ¿En qué distrito?*",
        reply_markup=build_distrito_keyboard(pagina), parse_mode="Markdown")
    return ELIGIENDO_DISTRITO

async def cb_distrito(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    distrito = q.data.replace("dist_","")
    context.user_data["distrito"] = distrito
    await q.edit_message_text(
        f"✅ Nivel: *{context.user_data['nivel']}*\n"
        f"✅ Distrito: *{distrito}*\n\n"
        "📝 *Paso 3/4 — ¿Qué cargo querés monitorear?*",
        reply_markup=build_cargo_keyboard(), parse_mode="Markdown")
    return ELIGIENDO_CARGO

# ── Paso 3: Cargo ──
async def cb_cargo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    cargo = q.data.replace("cargo_","")
    if cargo == "Otro (escribir)":
        await q.edit_message_text(
            "✏️ Escribí el nombre del cargo que querés buscar:",
            parse_mode="Markdown")
        return ELIGIENDO_CARGO
    context.user_data["cargo"] = "" if cargo == "Todos" else cargo
    nivel = context.user_data.get("nivel","Todos")
    distrito = context.user_data.get("distrito","Todos")
    await q.edit_message_text(
        f"✅ Nivel: *{nivel}*\n✅ Distrito: *{distrito}*\n✅ Cargo: *{cargo}*\n\n"
        "🔖 *Paso 4/4 — ¿Qué estado de oferta querés recibir?*",
        reply_markup=build_estado_keyboard(), parse_mode="Markdown")
    return ELIGIENDO_ESTADO

async def recibir_cargo_texto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cargo = update.message.text.strip()
    context.user_data["cargo"] = "" if cargo.lower() in ("no","todos","-","ninguno") else cargo
    nivel = context.user_data.get("nivel","Todos")
    distrito = context.user_data.get("distrito","Todos")
    await update.message.reply_text(
        f"✅ Nivel: *{nivel}*\n✅ Distrito: *{distrito}*\n✅ Cargo: *{cargo}*\n\n"
        "🔖 *Paso 4/4 — ¿Qué estado de oferta querés recibir?*",
        reply_markup=build_estado_keyboard(), parse_mode="Markdown")
    return ELIGIENDO_ESTADO

# ── Paso 4: Estado ──
async def cb_estado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    estado = q.data.replace("estado_","")
    nivel = context.user_data.get("nivel","Todos")
    distrito = context.user_data.get("distrito","Todos")
    cargo = context.user_data.get("cargo","")
    upsert_user(
        chat_id=update.effective_user.id,
        nivel=nivel, distrito=distrito,
        cargo=cargo, estado=estado, activo=1)
    await q.edit_message_text(
        f"🎉 *¡Configuración guardada!*\n\n"
        f"🏫 Nivel: {nivel}\n"
        f"📍 Distrito: {distrito}\n"
        f"📝 Cargo: {cargo or 'Todos'}\n"
        f"🔖 Estado: {estado}\n\n"
        "Te avisaré cuando aparezcan ofertas que coincidan. "
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
        headers = {"User-Agent":"Mozilla/5.0","Accept-Language":"es-AR,es;q=0.9"}
        resp = requests.get(APD_URL, headers=headers, timeout=15, verify=False)
        codigo = resp.status_code
        largo = len(resp.text)
        soup = BeautifulSoup(resp.text, "html.parser")
        ofertas = parse_html(soup)
        tablas = len(soup.find_all("table"))
        if ofertas:
            muestra = ofertas[0]
            detalle = (
                f"✅ *Portal respondió correctamente*\n\n"
                f"📊 Código HTTP: `{codigo}`\n"
                f"📄 Tamaño respuesta: `{largo} chars`\n"
                f"🗂️ Tablas encontradas: `{tablas}`\n"
                f"📋 Ofertas detectadas: `{len(ofertas)}`\n\n"
                f"*Primera oferta:*\n"
                f"IGE: `{muestra.get('ige','N/D')}`\n"
                f"Nivel: {muestra.get('nivel','N/D')}\n"
                f"Cargo: {muestra.get('cargo','N/D')}\n"
                f"Distrito: {muestra.get('distrito','N/D')}\n"
                f"Estado: {muestra.get('estado','N/D')}\n"
                f"Cierre: {muestra.get('cierre','N/D')}"
            )
        else:
            detalle = (
                f"⚠️ *Portal respondió pero sin ofertas en tabla*\n\n"
                f"📊 Código HTTP: `{codigo}`\n"
                f"📄 Tamaño respuesta: `{largo} chars`\n"
                f"🗂️ Tablas encontradas: `{tablas}`\n\n"
                f"Puede ser porque:\n"
                f"• No hay ofertas publicadas en este momento\n"
                f"• El portal usa JavaScript para cargar los datos\n"
                f"• La estructura HTML es diferente a la esperada\n\n"
                f"Primeras 300 letras del HTML recibido:\n"
                f"`{resp.text[:300].strip()}`"
            )
    except Exception as e:
        detalle = f"❌ *Error al conectar con el portal*\n\n`{str(e)}`"
    await update.message.reply_text(detalle, parse_mode="Markdown")

async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ *¿Cómo funciona?*\n\n"
        "Chequeo el portal APD de la Provincia de Buenos Aires cada 5 minutos "
        "entre las 5:30 y 11:30 hs (lunes a viernes) y te aviso al instante "
        "cuando aparece una oferta que coincide con tus filtros.\n\n"
        "📋 *Comandos:*\n"
        "/configurar — Cambiar nivel, distrito, cargo y estado\n"
        "/mis\\_alertas — Ver tu configuración actual\n"
        "/pausar — Pausar notificaciones\n"
        "/reanudar — Reanudar notificaciones\n\n"
        "🔗 misservicios.abc.gob.ar", parse_mode="Markdown")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("test", test))
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
                CallbackQueryHandler(cb_distpag, pattern="^distpag_"),
                CallbackQueryHandler(cb_distrito, pattern="^dist_"),
            ],
            ELIGIENDO_CARGO: [
                CallbackQueryHandler(cb_cargo, pattern="^cargo_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_cargo_texto),
            ],
            ELIGIENDO_ESTADO: [
                CallbackQueryHandler(cb_estado, pattern="^estado_"),
            ],
        },
        fallbacks=[CommandHandler("cancelar", cancelar)],
    ))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(chequear, "interval", minutes=SCRAPE_INTERVAL_MINUTES, args=[app])
    scheduler.start()

    logger.info("Bot APD v2 corriendo...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
