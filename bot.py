#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bot Telegram - Sistema de Búsqueda con Créditos
Autor: Tu Nombre
Descripción: Bot con sistema de créditos, auto-registro de usuarios y comandos
"""

import os
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters
from telegram.constants import ParseMode

load_dotenv()

# ==================== CONFIGURACIÓN ====================
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))
DATABASE_URL = os.getenv('DATABASE_URL')
PRICE_PER_SEARCH = int(os.getenv('PRICE_PER_SEARCH', '5'))
INITIAL_CREDITS = 100  # Créditos iniciales para nuevos usuarios

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== BASE DE DATOS ====================
class Database:
    def __init__(self):
        self.init_db()

    def get_connection(self):
        """Obtiene conexión a PostgreSQL"""
        return psycopg2.connect(DATABASE_URL)

    def init_db(self):
        """Inicializa las tablas de la base de datos"""
        conn = self.get_connection()
        cur = conn.cursor()
        
        # Tabla de usuarios
        cur.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            credits INT DEFAULT 100,
            expiry_date TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW(),
            is_active BOOLEAN DEFAULT TRUE
        )''')

        # Tabla de búsquedas (logs)
        cur.execute('''CREATE TABLE IF NOT EXISTS searches (
            id SERIAL PRIMARY KEY,
            user_id BIGINT REFERENCES users(user_id),
            search_term TEXT,
            results_count INT,
            credits_used INT,
            created_at TIMESTAMP DEFAULT NOW()
        )''')

        # Tabla de configuración
        cur.execute('''CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        )''')

        # Insertar precio por defecto
        cur.execute("""
            INSERT INTO config (key, value) 
            VALUES ('price_per_search', %s) 
            ON CONFLICT (key) DO UPDATE SET value = %s
        """, (str(PRICE_PER_SEARCH), str(PRICE_PER_SEARCH)))
        
        conn.commit()
        cur.close()
        conn.close()
        logger.info("✓ Base de datos inicializada")

    def user_exists(self, user_id):
        """Verifica si un usuario existe"""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute('SELECT user_id FROM users WHERE user_id = %s', (user_id,))
        exists = cur.fetchone() is not None
        cur.close()
        conn.close()
        return exists

    def register_user(self, user_id, username, first_name, last_name, credits=100, days=30):
        """Registra un nuevo usuario en la base de datos"""
        conn = self.get_connection()
        cur = conn.cursor()
        expiry = datetime.now() + timedelta(days=days)
        
        try:
            cur.execute('''
                INSERT INTO users (user_id, username, first_name, last_name, credits, expiry_date, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, TRUE)
                ON CONFLICT (user_id) DO UPDATE 
                SET credits = %s, expiry_date = %s, is_active = TRUE
            ''', (user_id, username, first_name, last_name, credits, expiry, credits, expiry))
            conn.commit()
            logger.info(f"✓ Usuario registrado: {user_id} (@{username})")
            return True
        except Exception as e:
            logger.error(f"Error al registrar usuario: {e}")
            return False
        finally:
            cur.close()
            conn.close()

    def get_user(self, user_id):
        """Obtiene información del usuario"""
        conn = self.get_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute('SELECT * FROM users WHERE user_id = %s', (user_id,))
        user = cur.fetchone()
        cur.close()
        conn.close()
        return user

    def deduct_credits(self, user_id, amount):
        """Deduce créditos de un usuario"""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute('UPDATE users SET credits = credits - %s WHERE user_id = %s', 
                   (amount, user_id))
        conn.commit()
        cur.close()
        conn.close()

    def add_credits(self, user_id, amount):
        """Agrega créditos a un usuario"""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute('UPDATE users SET credits = credits + %s WHERE user_id = %s', 
                   (amount, user_id))
        conn.commit()
        cur.close()
        conn.close()

    def remove_user(self, user_id):
        """Desactiva un usuario"""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute('UPDATE users SET is_active = FALSE WHERE user_id = %s', 
                   (user_id,))
        conn.commit()
        cur.close()
        conn.close()

    def set_price(self, price):
        """Actualiza el precio por búsqueda"""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute("UPDATE config SET value = %s WHERE key = 'price_per_search'", 
                   (str(price),))
        conn.commit()
        cur.close()
        conn.close()

    def get_price(self):
        """Obtiene el precio actual por búsqueda"""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT value FROM config WHERE key = 'price_per_search'")
        result = cur.fetchone()
        cur.close()
        conn.close()
        return int(result[0]) if result else PRICE_PER_SEARCH

    def log_search(self, user_id, search_term, results_count):
        """Registra una búsqueda en la base de datos"""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO searches (user_id, search_term, results_count, credits_used)
            VALUES (%s, %s, %s, %s)
        ''', (user_id, search_term, results_count, self.get_price()))
        conn.commit()
        cur.close()
        conn.close()

    def get_stats(self):
        """Obtiene estadísticas del sistema"""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM users WHERE is_active = TRUE')
        user_count = cur.fetchone()[0]
        cur.execute('SELECT COUNT(*) FROM searches WHERE DATE(created_at) = CURRENT_DATE')
        search_count = cur.fetchone()[0]
        cur.close()
        conn.close()
        return user_count, search_count

db = Database()

# ==================== COMANDOS DE USUARIO ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /start - Registro automático y bienvenida"""
    user = update.effective_user
    user_id = user.id
    username = user.username or "Sin usuario"
    first_name = user.first_name or "Usuario"
    last_name = user.last_name or ""

    # Verificar si el usuario ya está registrado
    existing_user = db.get_user(user_id)

    if not existing_user:
        # Registrar nuevo usuario
        db.register_user(
            user_id=user_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            credits=INITIAL_CREDITS,
            days=30
        )
        user_info = db.get_user(user_id)
        
        # Mensaje de bienvenida para nuevo usuario
        welcome_msg = f"""
🎉 <b>¡Bienvenido {first_name}!</b>

Has sido registrado automáticamente en el sistema.

👤 <b>Tu Información:</b>
🔑 ID: <code>{user_id}</code>
👤 Usuario: @{username}
💳 Créditos Iniciales: {INITIAL_CREDITS}
📅 Acceso por: 30 días

📋 <b>Comandos Disponibles:</b>
/cmds - Ver todos los comandos
/creditos - Ver tus créditos
/perfil - Ver tu información
/live - Buscar en el canal

¿Necesitas ayuda? Escribe /cmds
        """
    else:
        # Usuario ya existe
        expiry = datetime.fromisoformat(str(existing_user['expiry_date']))
        
        if not existing_user['is_active']:
            await update.message.reply_text(
                "❌ Tu acceso ha sido desactivado.\n"
                "Contacta al administrador.",
                parse_mode=ParseMode.HTML
            )
            return

        if datetime.now() > expiry:
            await update.message.reply_text(
                "⏰ Tu acceso ha expirado.\n"
                "Contacta al administrador para renovar.",
                parse_mode=ParseMode.HTML
            )
            return

        welcome_msg = f"""
👋 <b>¡Bienvenido de vuelta {first_name}!</b>

🔑 ID: <code>{user_id}</code>
💳 Créditos: {existing_user['credits']}
📅 Expira: {expiry.strftime('%d/%m/%Y')}

Escribe /cmds para ver los comandos disponibles
        """

    await update.message.reply_text(welcome_msg, parse_mode=ParseMode.HTML)

async def cmds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /cmds - Muestra todos los comandos disponibles"""
    user_id = update.effective_user.id
    user = db.get_user(user_id)

    if not user:
        await update.message.reply_text(
            "❌ No estás registrado. Usa /start primero.",
            parse_mode=ParseMode.HTML
        )
        return

    commands_msg = f"""
📋 <b>COMANDOS DISPONIBLES</b>

🔍 <b>COMANDOS DE BÚSQUEDA:</b>
/live &lt;palabra&gt; - Busca en el canal
   Costo: {db.get_price()} créditos por búsqueda
   Ejemplo: /live python

👤 <b>COMANDOS DE USUARIO:</b>
/start - Inicia el bot (auto-registra)
/creditos - Ver créditos disponibles
/perfil - Ver información de tu cuenta
/cmds - Ver este menú de comandos

💬 <b>TU INFORMACIÓN ACTUAL:</b>
🔑 ID: <code>{user_id}</code>
👤 Usuario: @{user['username']}
💳 Créditos: {user['credits']}
📅 Acceso hasta: {datetime.fromisoformat(str(user['expiry_date'])).strftime('%d/%m/%Y')}

{"✅ Estado: ACTIVO" if user['is_active'] else "❌ Estado: INACTIVO"}

{"" if user_id != ADMIN_ID else f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚙️ <b>COMANDOS ADMIN:</b>
/adduser &lt;id&gt; &lt;créditos&gt; &lt;días&gt; - Agregar usuario
   Ejemplo: /adduser 123456789 100 30

/removeuser &lt;id&gt; - Desactivar usuario
   Ejemplo: /removeuser 123456789

/setprice &lt;precio&gt; - Cambiar precio por búsqueda
   Ejemplo: /setprice 10

/addcredits &lt;id&gt; &lt;cantidad&gt; - Agregar créditos
   Ejemplo: /addcredits 123456789 50

/stats - Ver estadísticas del sistema
"""}
    """

    await update.message.reply_text(commands_msg, parse_mode=ParseMode.HTML)

async def creditos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /creditos - Ver créditos disponibles"""
    user_id = update.effective_user.id
    user = db.get_user(user_id)

    if not user:
        await update.message.reply_text(
            "❌ No estás registrado. Usa /start",
            parse_mode=ParseMode.HTML
        )
        return

    price = db.get_price()
    searches_available = user['credits'] // price

    creditos_msg = f"""
💳 <b>TUS CRÉDITOS</b>

💰 Créditos disponibles: <b>{user['credits']}</b>
🔍 Búsquedas disponibles: <b>{searches_available}</b>
💵 Costo por búsqueda: <b>{price} créditos</b>

{"✅ Tienes suficientes créditos para buscar" if searches_available > 0 else "❌ Insuficientes créditos. Contacta al admin"}

Usa /live &lt;palabra&gt; para buscar
    """

    await update.message.reply_text(creditos_msg, parse_mode=ParseMode.HTML)

async def perfil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /perfil - Ver información del usuario"""
    user_id = update.effective_user.id
    user = db.get_user(user_id)

    if not user:
        await update.message.reply_text(
            "❌ No estás registrado. Usa /start",
            parse_mode=ParseMode.HTML
        )
        return

    expiry = datetime.fromisoformat(str(user['expiry_date']))
    dias_restantes = (expiry - datetime.now()).days

    perfil_msg = f"""
👤 <b>TU PERFIL</b>

🔑 ID Telegram: <code>{user_id}</code>
👤 Usuario: <b>@{user['username']}</b>
📝 Nombre: <b>{user['first_name']} {user['last_name']}</b>
💳 Créditos: <b>{user['credits']}</b>
📅 Acceso expira en: <b>{dias_restantes} días</b>
📆 Fecha expiración: {expiry.strftime('%d/%m/%Y %H:%M')}
📝 Miembro desde: {datetime.fromisoformat(str(user['created_at'])).strftime('%d/%m/%Y')}
✅ Estado: <b>{"ACTIVO" if user['is_active'] else "INACTIVO"}</b>
    """

    await update.message.reply_text(perfil_msg, parse_mode=ParseMode.HTML)

async def live_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /live - Buscar en el canal"""
    user_id = update.effective_user.id
    user = db.get_user(user_id)

    if not user:
        await update.message.reply_text(
            "❌ No estás registrado. Usa /start",
            parse_mode=ParseMode.HTML
        )
        return

    if not user['is_active']:
        await update.message.reply_text(
            "❌ Tu acceso ha sido desactivado.",
            parse_mode=ParseMode.HTML
        )
        return

    expiry = datetime.fromisoformat(str(user['expiry_date']))
    if datetime.now() > expiry:
        await update.message.reply_text(
            "⏰ Tu acceso ha expirado. Contacta al administrador.",
            parse_mode=ParseMode.HTML
        )
        return

    price = db.get_price()
    if user['credits'] < price:
        await update.message.reply_text(
            f"❌ Créditos insuficientes.\n"
            f"Necesitas: {price} créditos\n"
            f"Tienes: {user['credits']} créditos\n\n"
            f"Contacta al administrador para agregar créditos.",
            parse_mode=ParseMode.HTML
        )
        return

    if not context.args:
        await update.message.reply_text(
            "❌ Uso correcto: /live &lt;palabra clave&gt;\n"
            "Ejemplo: /live python",
            parse_mode=ParseMode.HTML
        )
        return

    search_term = ' '.join(context.args)

    await update.message.reply_text(
        f"🔍 Buscando '{search_term}' en el canal...",
        parse_mode=ParseMode.HTML
    )

    try:
        # Aquí irá la lógica para buscar en el canal
        # Por ahora simulamos la búsqueda
        
        db.deduct_credits(user_id, price)
        db.log_search(user_id, search_term, 0)

        remaining_credits = user['credits'] - price

        search_result = f"""
✅ <b>Búsqueda Completada</b>

🔍 Término: <b>{search_term}</b>
📍 Resultados: Se está procesando...
💳 Créditos usados: <b>{price}</b>
💰 Créditos restantes: <b>{remaining_credits}</b>

Puedes hacer {remaining_credits // price} búsquedas más con tus créditos actuales.
        """

        await update.message.reply_text(search_result, parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error(f"Error en búsqueda: {e}")
        # Devolver créditos en caso de error
        db.add_credits(user_id, price)
        await update.message.reply_text(
            f"❌ Error en la búsqueda: {str(e)}",
            parse_mode=ParseMode.HTML
        )

# ==================== COMANDOS ADMIN ====================

async def adduser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /adduser - Agregar usuario (SOLO ADMIN)"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(
            "❌ Solo el administrador puede usar este comando.",
            parse_mode=ParseMode.HTML
        )
        return

    if len(context.args) < 3:
        await update.message.reply_text(
            "❌ Uso: /adduser &lt;user_id&gt; &lt;créditos&gt; &lt;días&gt;\n"
            "Ejemplo: /adduser 123456789 100 30",
            parse_mode=ParseMode.HTML
        )
        return

    try:
        user_id = int(context.args[0])
        credits = int(context.args[1])
        days = int(context.args[2])

        db.register_user(
            user_id=user_id,
            username=f"user_{user_id}",
            first_name="Agregado",
            last_name="por Admin",
            credits=credits,
            days=days
        )

        await update.message.reply_text(
            f"✅ <b>Usuario Agregado</b>\n"
            f"🔑 ID: <code>{user_id}</code>\n"
            f"💳 Créditos: {credits}\n"
            f"📅 Acceso: {days} días",
            parse_mode=ParseMode.HTML
        )

    except ValueError:
        await update.message.reply_text(
            "❌ Los argumentos deben ser números.",
            parse_mode=ParseMode.HTML
        )

async def removeuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /removeuser - Eliminar usuario (SOLO ADMIN)"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(
            "❌ Solo el administrador.",
            parse_mode=ParseMode.HTML
        )
        return

    if not context.args:
        await update.message.reply_text(
            "❌ Uso: /removeuser &lt;user_id&gt;",
            parse_mode=ParseMode.HTML
        )
        return

    try:
        user_id = int(context.args[0])
        db.remove_user(user_id)
        await update.message.reply_text(
            f"✅ Usuario {user_id} desactivado.",
            parse_mode=ParseMode.HTML
        )
    except ValueError:
        await update.message.reply_text(
            "❌ ID inválido.",
            parse_mode=ParseMode.HTML
        )

async def setprice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /setprice - Cambiar precio (SOLO ADMIN)"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(
            "❌ Solo el administrador.",
            parse_mode=ParseMode.HTML
        )
        return

    if not context.args:
        await update.message.reply_text(
            "❌ Uso: /setprice &lt;nuevo_precio&gt;",
            parse_mode=ParseMode.HTML
        )
        return

    try:
        new_price = int(context.args[0])
        db.set_price(new_price)
        await update.message.reply_text(
            f"✅ Precio actualizado a {new_price} créditos por búsqueda.",
            parse_mode=ParseMode.HTML
        )
    except ValueError:
        await update.message.reply_text(
            "❌ Precio inválido.",
            parse_mode=ParseMode.HTML
        )

async def addcredits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /addcredits - Agregar créditos (SOLO ADMIN)"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(
            "❌ Solo el administrador.",
            parse_mode=ParseMode.HTML
        )
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ Uso: /addcredits &lt;user_id&gt; &lt;cantidad&gt;",
            parse_mode=ParseMode.HTML
        )
        return

    try:
        user_id = int(context.args[0])
        amount = int(context.args[1])
        db.add_credits(user_id, amount)
        user = db.get_user(user_id)
        await update.message.reply_text(
            f"✅ Se agregaron {amount} créditos a usuario {user_id}\n"
            f"Créditos actuales: {user['credits']}",
            parse_mode=ParseMode.HTML
        )
    except ValueError:
        await update.message.reply_text(
            "❌ Argumentos inválidos.",
            parse_mode=ParseMode.HTML
        )

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /stats - Ver estadísticas (SOLO ADMIN)"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(
            "❌ Solo el administrador.",
            parse_mode=ParseMode.HTML
        )
        return

    user_count, search_count = db.get_stats()
    price = db.get_price()

    stats_msg = f"""
📊 <b>ESTADÍSTICAS DEL SISTEMA</b>

👥 Usuarios activos: <b>{user_count}</b>
🔍 Búsquedas hoy: <b>{search_count}</b>
💳 Precio por búsqueda: <b>{price} créditos</b>
🤖 Bot Token: {"✅ Conectado" if BOT_TOKEN else "❌ No configurado"}
🗄️ Base de datos: {"✅ PostgreSQL Conectado" if DATABASE_URL else "❌ No configurado"}
    """

    await update.message.reply_text(stats_msg, parse_mode=ParseMode.HTML)

# ==================== MAIN ====================

def main():
    """Inicia el bot"""
    app = Application.builder().token(BOT_TOKEN).build()

    # Comandos de usuario
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('cmds', cmds))
    app.add_handler(CommandHandler('creditos', creditos))
    app.add_handler(CommandHandler('perfil', perfil))
    app.add_handler(CommandHandler('live', live_search))

    # Comandos admin
    app.add_handler(CommandHandler('adduser', adduser))
    app.add_handler(CommandHandler('removeuser', removeuser))
    app.add_handler(CommandHandler('setprice', setprice))
    app.add_handler(CommandHandler('addcredits', addcredits))
    app.add_handler(CommandHandler('stats', stats))

    logger.info("=" * 50)
    logger.info("🤖 Bot Telegram iniciado correctamente")
    logger.info("=" * 50)
    app.run_polling()

if __name__ == '__main__':
    main()
