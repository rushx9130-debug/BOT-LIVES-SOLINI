#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import logging
import asyncio
from datetime import datetime, timedelta
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
DATABASE_URL = os.getenv('DATABASE_URL')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ==================== BASE DE DATOS ====================
class Database:
    def __init__(self):
        self.init_db()

    def get_connection(self):
        return psycopg2.connect(DATABASE_URL)

    def init_db(self):
        conn = self.get_connection()
        cur = conn.cursor()

        # PREMIUM: Usuarios con créditos
        cur.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            chat_id BIGINT,
            credits INT DEFAULT 0,
            expiry_date TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW(),
            is_active BOOLEAN DEFAULT TRUE
        )''')

        # GRUPOS/CANALES AUTORIZADOS
        cur.execute('''CREATE TABLE IF NOT EXISTS authorized_chats (
            chat_id BIGINT PRIMARY KEY,
            chat_title TEXT,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW()
        )''')

        # FREE: Control de búsquedas por usuario-grupo
        cur.execute('''CREATE TABLE IF NOT EXISTS free_users (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            chat_id BIGINT,
            daily_searches_today INT DEFAULT 0,
            last_search TIMESTAMP,
            UNIQUE(user_id, chat_id)
        )''')

        # CONFIG POR GRUPO PARA USUARIOS FREE
        cur.execute('''CREATE TABLE IF NOT EXISTS free_config (
            chat_id BIGINT PRIMARY KEY,
            daily_limit INT DEFAULT 3,
            spam_delay INT DEFAULT 60,
            created_at TIMESTAMP DEFAULT NOW()
        )''')

        # LOG DE BÚSQUEDAS
        cur.execute('''CREATE TABLE IF NOT EXISTS search_logs (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            chat_id BIGINT,
            search_term TEXT,
            search_type VARCHAR(10),
            created_at TIMESTAMP DEFAULT NOW()
        )''')

        conn.commit()
        cur.close()
        conn.close()

    # ===== USUARIOS PREMIUM =====
    def add_premium_user(self, user_id, username, chat_id, credits, days):
        conn = self.get_connection()
        cur = conn.cursor()
        expiry = datetime.now() + timedelta(days=days)
        cur.execute('''INSERT INTO users (user_id, username, chat_id, credits, expiry_date, is_active)
                       VALUES (%s, %s, %s, %s, %s, TRUE)
                       ON CONFLICT (user_id) DO UPDATE
                       SET credits = %s, expiry_date = %s, chat_id = %s, is_active = TRUE''',
                    (user_id, username, chat_id, credits, expiry, credits, expiry, chat_id))
        conn.commit()
        cur.close()
        conn.close()

    def remove_premium_user(self, user_id):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute('UPDATE users SET is_active = FALSE WHERE user_id = %s', (user_id,))
        conn.commit()
        cur.close()
        conn.close()

    def get_premium_user(self, user_id):
        conn = self.get_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute('SELECT * FROM users WHERE user_id = %s', (user_id,))
        user = cur.fetchone()
        cur.close()
        conn.close()
        return user

    def deduct_premium_credits(self, user_id, amount):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute('UPDATE users SET credits = credits - %s WHERE user_id = %s', (amount, user_id))
        conn.commit()
        cur.close()
        conn.close()

    def add_premium_credits(self, user_id, amount):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute('UPDATE users SET credits = credits + %s WHERE user_id = %s', (amount, user_id))
        conn.commit()
        cur.close()
        conn.close()

    # ===== GRUPOS/CANALES =====
    def authorize_chat(self, chat_id, chat_title):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute('''INSERT INTO authorized_chats (chat_id, chat_title, is_active)
                       VALUES (%s, %s, TRUE)
                       ON CONFLICT (chat_id) DO UPDATE SET chat_title = %s, is_active = TRUE''',
                    (chat_id, chat_title, chat_title))
        conn.commit()
        cur.close()
        conn.close()

    def deauthorize_chat(self, chat_id):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute('UPDATE authorized_chats SET is_active = FALSE WHERE chat_id = %s', (chat_id,))
        conn.commit()
        cur.close()
        conn.close()

    def is_chat_authorized(self, chat_id):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute('SELECT is_active FROM authorized_chats WHERE chat_id = %s', (chat_id,))
        result = cur.fetchone()
        cur.close()
        conn.close()
        return result[0] if result else False

    # ===== USUARIOS FREE =====
    def get_free_user(self, user_id, chat_id):
        conn = self.get_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute('SELECT * FROM free_users WHERE user_id = %s AND chat_id = %s', (user_id, chat_id))
        user = cur.fetchone()
        cur.close()
        conn.close()
        return user

    def update_free_search(self, user_id, chat_id, searches_today):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute('''INSERT INTO free_users (user_id, chat_id, daily_searches_today, last_search)
                       VALUES (%s, %s, %s, NOW())
                       ON CONFLICT (user_id, chat_id) DO UPDATE
                       SET daily_searches_today = %s, last_search = NOW()''',
                    (user_id, chat_id, searches_today, searches_today))
        conn.commit()
        cur.close()
        conn.close()

    def reset_free_daily_chat(self, chat_id):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute('UPDATE free_users SET daily_searches_today = 0 WHERE chat_id = %s', (chat_id,))
        conn.commit()
        cur.close()
        conn.close()

    def reset_free_user(self, user_id, chat_id):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute('UPDATE free_users SET daily_searches_today = 0 WHERE user_id = %s AND chat_id = %s', 
                   (user_id, chat_id))
        conn.commit()
        cur.close()
        conn.close()

    # ===== CONFIG FREE =====
    def get_free_config(self, chat_id):
        conn = self.get_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute('SELECT * FROM free_config WHERE chat_id = %s', (chat_id,))
        config = cur.fetchone()
        if not config:
            cur.execute('''INSERT INTO free_config (chat_id, daily_limit, spam_delay)
                          VALUES (%s, 3, 60)''', (chat_id,))
            conn.commit()
            config = {'daily_limit': 3, 'spam_delay': 60}
        cur.close()
        conn.close()
        return config

    def set_free_config(self, chat_id, daily_limit, spam_delay):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute('''INSERT INTO free_config (chat_id, daily_limit, spam_delay)
                       VALUES (%s, %s, %s)
                       ON CONFLICT (chat_id) DO UPDATE
                       SET daily_limit = %s, spam_delay = %s''',
                    (chat_id, daily_limit, spam_delay, daily_limit, spam_delay))
        conn.commit()
        cur.close()
        conn.close()

    # ===== LOGS =====
    def log_search(self, user_id, chat_id, search_term, search_type):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute('''INSERT INTO search_logs (user_id, chat_id, search_term, search_type)
                       VALUES (%s, %s, %s, %s)''',
                    (user_id, chat_id, search_term, search_type))
        conn.commit()
        cur.close()
        conn.close()

    def get_stats(self):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM users WHERE is_active = TRUE')
        premium_users = cur.fetchone()[0]
        cur.execute('SELECT COUNT(*) FROM authorized_chats WHERE is_active = TRUE')
        authorized_chats = cur.fetchone()[0]
        cur.execute('SELECT COUNT(*) FROM free_users')
        free_users = cur.fetchone()[0]
        cur.execute('SELECT COUNT(*) FROM search_logs WHERE DATE(created_at) = CURRENT_DATE')
        searches_today = cur.fetchone()[0]
        cur.close()
        conn.close()
        return {
            'premium_users': premium_users,
            'authorized_chats': authorized_chats,
            'free_users': free_users,
            'searches_today': searches_today
        }


db = Database()


# ==================== COMANDOS USUARIO ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    authorized = db.is_chat_authorized(chat_id)
    premium_user = db.get_premium_user(user_id)

    msg = f"🤖 Bot en: {update.effective_chat.title or 'PM'}\n\n"

    if premium_user and premium_user['is_active']:
        if premium_user['expiry_date'] and datetime.now() <= premium_user['expiry_date']:
            dias = (premium_user['expiry_date'] - datetime.now()).days
            msg += f"💎 PREMIUM: {premium_user['credits']} créditos\n"
            msg += f"📅 Expira en: {dias} días\n\n"
        else:
            msg += "⏰ PREMIUM expirado\n\n"

    if authorized:
        config = db.get_free_config(chat_id)
        msg += f"✅ Grupo autorizado\n"
        msg += f"FREE: {config['daily_limit']}/día, spam {config['spam_delay']}s\n\n"

    msg += "🔍 /live <palabra> - Buscar"
    await update.message.reply_text(msg)


async def creditos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = db.get_premium_user(user_id)

    if not user or not user['is_active']:
        await update.message.reply_text("❌ No eres PREMIUM")
        return

    if user['expiry_date'] and datetime.now() > user['expiry_date']:
        await update.message.reply_text("⏰ Acceso PREMIUM expirado")
        return

    await update.message.reply_text(f"💳 Créditos: {user['credits']}")


async def perfil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = db.get_premium_user(user_id)

    if not user:
        await update.message.reply_text("❌ No registrado")
        return

    if user['expiry_date']:
        dias = (user['expiry_date'] - datetime.now()).days
        expiry_str = f"{dias} días" if dias > 0 else "Expirado"
    else:
        expiry_str = "N/A"

    await update.message.reply_text(
        f"👤 Perfil\n"
        f"ID: {user_id}\n"
        f"💳 Créditos: {user['credits']}\n"
        f"📅 PREMIUM: {expiry_str}\n"
        f"✅ Estado: {'Activo' if user['is_active'] else 'Inactivo'}"
    )


async def live_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    if not context.args:
        await update.message.reply_text("❌ Uso: /live <palabra>")
        return

    search_term = " ".join(context.args)

    # CHECK PREMIUM PRIMERO
    premium_user = db.get_premium_user(user_id)
    if premium_user and premium_user['is_active'] and premium_user['expiry_date']:
        if datetime.now() <= premium_user['expiry_date']:
            # PREMIUM USUARIO
            price = 5
            if premium_user['credits'] < price:
                await update.message.reply_text(f"❌ Créditos insuficientes ({premium_user['credits']}/{price})")
                return

            db.deduct_premium_credits(user_id, price)
            db.log_search(user_id, chat_id, search_term, 'PREMIUM')
            
            await update.message.reply_text(
                f"💎 PREMIUM: Buscando '{search_term}'...\n"
                f"📍 Resultados simulados\n"
                f"💳 Gastaste {price} créditos\n"
                f"💳 Restante: {premium_user['credits'] - price}"
            )
            return

    # CHECK FREE
    authorized = db.is_chat_authorized(chat_id)
    if not authorized:
        await update.message.reply_text("❌ Grupo no autorizado")
        return

    config = db.get_free_config(chat_id)
    free_user = db.get_free_user(user_id, chat_id)

    # Anti-spam
    if free_user and free_user['last_search']:
        elapsed = (datetime.now() - free_user['last_search']).total_seconds()
        if elapsed < config['spam_delay']:
            remaining = int(config['spam_delay'] - elapsed)
            await update.message.reply_text(f"⏳ Espera {remaining}s (anti-spam)")
            return

    # Límite diario
    if free_user and free_user['daily_searches_today'] >= config['daily_limit']:
        await update.message.reply_text(
            f"❌ Límite diario alcanzado ({config['daily_limit']}/día)\n"
            f"Reinicia en 24h"
        )
        return

    # Procesar búsqueda FREE
    searches_today = free_user['daily_searches_today'] + 1 if free_user else 1
    db.update_free_search(user_id, chat_id, searches_today)
    db.log_search(user_id, chat_id, search_term, 'FREE')

    await update.message.reply_text(
        f"🔍 FREE: Buscando '{search_term}'...\n"
        f"📍 Resultados simulados\n"
        f"📊 {searches_today}/{config['daily_limit']} búsquedas hoy"
    )


# ==================== COMANDOS ADMIN ====================
async def adduser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ Solo admin")

    if len(context.args) < 4:
        return await update.message.reply_text("Uso: /adduser user_id chat_id creditos dias")

    try:
        user_id, chat_id, credits, days = map(int, context.args[:4])
        db.add_premium_user(user_id, f"user_{user_id}", chat_id, credits, days)
        await update.message.reply_text(
            f"✅ PREMIUM agregado\n"
            f"User: {user_id}\n"
            f"Créditos: {credits}\n"
            f"Días: {days}"
        )
    except:
        await update.message.reply_text("❌ Error parámetros")


async def removeuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ Solo admin")

    if not context.args:
        return await update.message.reply_text("Uso: /removeuser user_id")

    try:
        user_id = int(context.args[0])
        db.remove_premium_user(user_id)
        await update.message.reply_text(f"✅ Usuario {user_id} desactivado")
    except:
        await update.message.reply_text("❌ Error")


async def authorize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ Solo admin")

    if not context.args:
        return await update.message.reply_text("Uso: /authorize chat_id [nombre]")

    try:
        chat_id = int(context.args[0])
        chat_title = context.args[1] if len(context.args) > 1 else f"Chat_{chat_id}"
        db.authorize_chat(chat_id, chat_title)
        await update.message.reply_text(f"✅ Grupo {chat_id} autorizado")
    except:
        await update.message.reply_text("❌ Error")


async def deauthorize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ Solo admin")

    if not context.args:
        return await update.message.reply_text("Uso: /deauthorize chat_id")

    try:
        chat_id = int(context.args[0])
        db.deauthorize_chat(chat_id)
        await update.message.reply_text(f"✅ Grupo {chat_id} desautorizado")
    except:
        await update.message.reply_text("❌ Error")


async def freeconfig(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ Solo admin")

    if len(context.args) < 3:
        return await update.message.reply_text("Uso: /freeconfig chat_id limite_diario spam_segundos")

    try:
        chat_id, daily_limit, spam_delay = map(int, context.args[:3])
        db.set_free_config(chat_id, daily_limit, spam_delay)
        await update.message.reply_text(
            f"✅ Config FREE actualizada\n"
            f"Grupo: {chat_id}\n"
            f"Límite: {daily_limit}/día\n"
            f"Spam: {spam_delay}s"
        )
    except:
        await update.message.reply_text("❌ Error parámetros")


async def resetfree(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ Solo admin")

    if not context.args:
        return await update.message.reply_text("Uso: /resetfree chat_id")

    try:
        chat_id = int(context.args[0])
        db.reset_free_daily_chat(chat_id)
        await update.message.reply_text(f"✅ Búsquedas FREE reseteadas grupo {chat_id}")
    except:
        await update.message.reply_text("❌ Error")


async def addcredits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ Solo admin")

    if len(context.args) < 2:
        return await update.message.reply_text("Uso: /addcredits user_id cantidad")

    try:
        user_id, amount = map(int, context.args[:2])
        db.add_premium_credits(user_id, amount)
        await update.message.reply_text(f"✅ {amount} créditos agregados a {user_id}")
    except:
        await update.message.reply_text("❌ Error")


async def setprice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ Solo admin")

    if not context.args:
        return await update.message.reply_text("Uso: /setprice precio")

    try:
        price = int(context.args[0])
        # Guardar en config si necesitas persistencia
        await update.message.reply_text(f"✅ Precio PREMIUM: {price} créditos/búsqueda")
    except:
        await update.message.reply_text("❌ Error")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ Solo admin")

    stats = db.get_stats()
    await update.message.reply_text(
        f"📊 ESTADÍSTICAS\n\n"
        f"💎 PREMIUM: {stats['premium_users']} usuarios\n"
        f"✅ Grupos autorizados: {stats['authorized_chats']}\n"
        f"👥 FREE activos: {stats['free_users']}\n"
        f"🔍 Búsquedas hoy: {stats['searches_today']}"
    )


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Usuario
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("live", live_search))
    app.add_handler(CommandHandler("creditos", creditos))
    app.add_handler(CommandHandler("perfil", perfil))

    # Admin
    app.add_handler(CommandHandler("adduser", adduser))
    app.add_handler(CommandHandler("removeuser", removeuser))
    app.add_handler(CommandHandler("authorize", authorize))
    app.add_handler(CommandHandler("deauthorize", deauthorize))
    app.add_handler(CommandHandler("freeconfig", freeconfig))
    app.add_handler(CommandHandler("resetfree", resetfree))
    app.add_handler(CommandHandler("addcredits", addcredits))
    app.add_handler(CommandHandler("setprice", setprice))
    app.add_handler(CommandHandler("stats", stats))

    logger.info("🚀 Bot ULTRA iniciado!")
    app.run_polling()

if __name__ == "__main__":
    main()
