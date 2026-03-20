"""
APD Notificaciones Bot - Réplica gratuita de apdnotificaciones.com
==================================================================
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

ELIGIENDO_NIVEL, ELIGIENDO_DISTRITO, ELIGIENDO_CARGO = range(3)

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

# ─────────────────────────────────────────────
# BASE DE DATOS
# ─────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS usuarios (
        chat_id INTEGER PRIMARY KEY, username TEXT,
        nivel TEXT DEFAULT 'Todos', distrito TEXT DEFAULT 'Todos',
        cargo TEXT DEFAULT '', activo INTEGER DEFAULT 1,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS ofertas_vistas (
        oferta_id TEXT PRIMARY KEY, visto_en TEXT DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit(); conn.close()

def get_user(chat_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM usuarios WHERE chat_id=?", (chat_id,))
    row = c.fetchone(); conn.close()
    if row:
        return {"chat_id":row[0],"username":row[1],"nivel":row[2],"distrito":row[3],"cargo":row[4],"activo":row[5]}
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
    rows = conn.execute("SELECT chat_id,nivel,distrito,cargo FROM usuarios WHERE activo=1").fetchall()
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
                    "link": APD_URL,
                }
                o["id"] = hashlib.md5(f"{o['ige']}-{o['cargo']}-{o['distrito']}".encode()).hexdigest()
                ofertas.append(o)
    return ofertas

def coincide(o, nivel, distrito, cargo):
    if nivel != "Todos" and nivel.lower() not in o.get("nivel","").lower(): return False
    if distrito != "Todos" and distrito.lower() not in o.get("distrito","").lower(): return False
    if cargo and cargo.lower() not in o.get("cargo","").lower(): return False
    return True

def fmt_oferta(o):
    return (
        f"📚 *NUEVA OFERTA APD*\n━━━━━━━━━━━━━━━\n"
        f"📋 IGE: `{o.get('ige','N/D')}`\n"
        f"🏫 Nivel: {o.get('nivel','N/D')}\n"
        f"📝 Cargo: {o.get('cargo','N/D')}\n"
        f"📍 Distrito: {o.get('distrito','N/D')}\n"
        f"🏛️ Est.: {o.get('establecimiento','N/D')}\n"
        f"⏰ Cierre: *{o.get('cierre','N/D')}*\n"
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
    for chat_id, nivel, distrito, cargo in get_all_active_users():
        for o in nuevas:
            if coincide(o, nivel, distrito, cargo):
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
        "Por defecto recibís *todas las ofertas*. Usá /configurar para filtrar.\n\n"
        "📋 Comandos:\n/configurar · /mis\\_alertas · /pausar · /reanudar · /ayuda",
        parse_mode="Markdown")

async def mis_alertas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = get_user(update.effective_user.id)
    if not d:
        await update.message.reply_text("Primero usá /start."); return
    await update.message.reply_text(
        f"📋 *Tu configuración:*\n\n"
        f"Estado: {'✅ Activo' if d['activo'] else '⏸️ Pausado'}\n"
        f"🏫 Nivel: {d['nivel']}\n📍 Distrito: {d['distrito']}\n"
        f"📝 Cargo: {d['cargo'] or 'Todos'}\n\nCambiá con /configurar",
        parse_mode="Markdown")

async def configurar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = []
    row = []
    for i, n in enumerate(NIVELES):
        row.append(InlineKeyboardButton(n, callback_data=f"nivel_{n}"))
        if (i+1) % 3 == 0: kb.append(row); row = []
    if row: kb.append(row)
    await update.message.reply_text(
        "🏫 *Paso 1/3 — ¿Qué nivel querés monitorear?*",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return ELIGIENDO_NIVEL

async def cb_nivel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    nivel = q.data.replace("nivel_","")
    context.user_data["nivel"] = nivel
    distritos_show = ["Todos"] + DISTRITOS[:39]
    kb = []
    row = []
    for i, d in enumerate(distritos_show):
        row.append(InlineKeyboardButton(d[:20], callback_data=f"dist_{d}"))
        if (i+1) % 2 == 0: kb.append(row); row = []
    if row: kb.append(row)
    await q.edit_message_text(
        f"✅ Nivel: *{nivel}*\n\n📍 *Paso 2/3 — ¿En qué distrito?*",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return ELIGIENDO_DISTRITO

async def cb_distrito(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    distrito = q.data.replace("dist_","")
    context.user_data["distrito"] = distrito
    await q.edit_message_text(
        f"✅ Nivel: *{context.user_data['nivel']}*\n✅ Distrito: *{distrito}*\n\n"
        "📝 *Paso 3/3 — ¿Filtrás por cargo?*\n"
        "Escribí el cargo (ej: _Matemática_, _Inglés_) o escribí `no` para todos.",
        parse_mode="Markdown")
    return ELIGIENDO_CARGO

async def recibir_cargo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    cargo = "" if txt.lower() in ("no","todos","-","ninguno") else txt
    nivel = context.user_data.get("nivel","Todos")
    distrito = context.user_data.get("distrito","Todos")
    upsert_user(chat_id=update.effective_user.id, nivel=nivel, distrito=distrito, cargo=cargo, activo=1)
    await update.message.reply_text(
        f"🎉 *Configuración guardada!*\n\n🏫 {nivel}\n📍 {distrito}\n📝 {cargo or 'Todos'}",
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

async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ *¿Cómo funciona?*\n\n"
        "Chequeo el portal APD de la Provincia de Buenos Aires cada 5 minutos "
        "entre las 5:30 y 11:30 hs (lunes a viernes) y te aviso al instante "
        "cuando aparece una oferta que coincide con tus filtros.\n\n"
        "🔗 misservicios.abc.gob.ar", parse_mode="Markdown")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("mis_alertas", mis_alertas))
    app.add_handler(CommandHandler("pausar", pausar))
    app.add_handler(CommandHandler("reanudar", reanudar))
    app.add_handler(CommandHandler("ayuda", ayuda))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("configurar", configurar)],
        states={
            ELIGIENDO_NIVEL:    [CallbackQueryHandler(cb_nivel, pattern="^nivel_")],
            ELIGIENDO_DISTRITO: [CallbackQueryHandler(cb_distrito, pattern="^dist_")],
            ELIGIENDO_CARGO:    [MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_cargo)],
        },
        fallbacks=[CommandHandler("cancelar", cancelar)],
    ))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(chequear, "interval", minutes=SCRAPE_INTERVAL_MINUTES, args=[app])
    scheduler.start()

    logger.info("Bot APD corriendo...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
