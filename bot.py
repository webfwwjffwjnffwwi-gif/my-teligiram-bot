import logging
import sqlite3
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

# Logging sozlamalari
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# =========================================================
# SOZLAMALAR
# =========================================================

TOKEN = "8586495198:AAERZVqpmEOo1bB-PvRak6rvpDEn6c0ZpZ8"
ADMIN_ID = 8528296825  # Telegram ID
REQUIRED_CHANNEL = "@Animelar_olami_uz_01"  # Obona kanali

PAGE_SIZE = 5

# =========================================================
# HOLATLAR
# =========================================================

ADD_ANIME_NAME, ADD_ANIME_GENRE, ADD_ANIME_DESCRIPTION = range(3)
ADD_EPISODE_NUMBER, ADD_EPISODE_VIDEO = range(3, 5)
BROADCAST_MESSAGE = 5

# =========================================================
# DATABASE
# =========================================================

def db_connect():
    return sqlite3.connect("anime.db")

def init_database():
    connection = db_connect()
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS anime (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            genre TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            anime_id INTEGER NOT NULL,
            episode_number INTEGER NOT NULL,
            file_id TEXT NOT NULL,
            FOREIGN KEY (anime_id) REFERENCES anime(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            first_name TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            anime_id INTEGER NOT NULL,
            UNIQUE(user_id, anime_id)
        )
    """)

    connection.commit()
    connection.close()

# =========================================================
# HELPERLAR
# =========================================================

def save_user(user):
    connection = db_connect()
    cursor = connection.cursor()
    cursor.execute("""
        INSERT OR IGNORE INTO users (telegram_id, username, first_name)
        VALUES (?, ?, ?)
    """, (user.id, user.username, user.first_name))
    connection.commit()
    connection.close()

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

async def check_sub(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if is_admin(user_id):
        return True
    try:
        member = await context.bot.get_chat_member(chat_id=REQUIRED_CHANNEL, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        logger.error(f"O'bona tekshirishda xatolik: {e}")
        return False

async def send_sub_request(update: Update):
    channel_username = REQUIRED_CHANNEL.replace("@", "")
    keyboard = [
        [InlineKeyboardButton("📢 Kanalimizga a'zo bo'ling", url=f"https://t.me/{channel_username}")],
        [InlineKeyboardButton("✅ Tekshirish", callback_data="check_subscription")]
    ]
    text = "⚠️ **Botdan foydalanish uchun avval rasmiy kanalimizga a'zo bo'ling:**"
    
    if update.callback_query:
        try:
            await update.callback_query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        except Exception:
            await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# =========================================================
# START & SEARCH
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user)

    if not await check_sub(user.id, context):
        await send_sub_request(update)
        return

    keyboard = [
        [InlineKeyboardButton("🎌 Anime katalogi", callback_data="user_anime_list_page_0")],
        [InlineKeyboardButton("⭐️ Saralanganlar", callback_data="user_favorites")]
    ]
    await update.message.reply_text(
        f"Salom, {user.first_name}! 👋\n\n🎌 Anime botimizga xush kelibsiz!\n\n"
        "🔎 Anime nomini yozib yuborsangiz, uni bazadan qidirib beraman.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def search_anime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await check_sub(user.id, context):
        await send_sub_request(update)
        return

    query_text = update.message.text.strip()
    
    connection = db_connect()
    cursor = connection.cursor()
    cursor.execute("SELECT id, name FROM anime WHERE name LIKE ? LIMIT 10", (f"%{query_text}%",))
    results = cursor.fetchall()
    connection.close()

    if not results:
        await update.message.reply_text("❌ Kechirasiz, bunday nomli anime topilmadi.")
        return

    keyboard = [
        [InlineKeyboardButton(name, callback_data=f"user_anime_{anime_id}")]
        for anime_id, name in results
    ]
    await update.message.reply_text(
        f"🔍 \"{query_text}\" bo'yicha topilgan animelar:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# =========================================================
# ADMIN PANEL & ACTIONS
# =========================================================

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Siz admin emassiz.")
        return
    await send_admin_panel(update)

async def send_admin_panel(update: Update):
    keyboard = [
        [InlineKeyboardButton("➕ Anime qo‘shish", callback_data="add_anime")],
        [InlineKeyboardButton("📺 Qism qo‘shish", callback_data="add_episode")],
        [InlineKeyboardButton("📢 Xabar tarqatish", callback_data="broadcast")],
        [InlineKeyboardButton("📋 Anime ro‘yxati", callback_data="admin_anime_list")],
        [InlineKeyboardButton("🗑 Anime o‘chirish", callback_data="delete_anime")],
        [InlineKeyboardButton("📊 Statistika", callback_data="statistics")],
    ]
    text = "⚙️ **ADMIN PANEL**\n\nKerakli bo‘limni tanlang:"
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def delete_anime_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    connection = db_connect()
    cursor = connection.cursor()
    cursor.execute("SELECT id, name FROM anime ORDER BY id DESC")
    anime_list = cursor.fetchall()
    connection.close()

    if not anime_list:
        keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="back_admin")]]
        await query.edit_message_text("❌ O‘chirish uchun anime topilmadi.", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    keyboard = [
        [InlineKeyboardButton(f"❌ {name}", callback_data=f"confirm_delete_{anime_id}")]
        for anime_id, name in anime_list
    ]
    keyboard.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="back_admin")])

    await query.edit_message_text("🗑 O‘chirmoqchi bo‘lgan animeni tanlang:\n\n⚠️ *Diqqat:* Animeni o'chirsangiz, uning barcha qismlari ham bazadan o'chib ketadi!", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def process_delete_anime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    anime_id = int(query.data.split("_")[-1])

    connection = db_connect()
    cursor = connection.cursor()
    cursor.execute("SELECT name FROM anime WHERE id = ?", (anime_id,))
    anime = cursor.fetchone()

    if anime:
        anime_name = anime[0]
        cursor.execute("DELETE FROM anime WHERE id = ?", (anime_id,))
        cursor.execute("DELETE FROM episodes WHERE anime_id = ?", (anime_id,))
        cursor.execute("DELETE FROM favorites WHERE anime_id = ?", (anime_id,))
        connection.commit()
        text = f"✅ **{anime_name}** va uning barcha qismlari muvaffaqiyatli o‘chirildi!"
    else:
        text = "❌ Anime topilmadi."

    connection.close()

    keyboard = [[InlineKeyboardButton("⬅️ Admin panel", callback_data="back_admin")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def admin_anime_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    connection = db_connect()
    cursor = connection.cursor()
    cursor.execute("SELECT id, name FROM anime ORDER BY id DESC")
    anime_list = cursor.fetchall()
    connection.close()

    if not anime_list:
        text = "📋 Hozircha bazada animelar yo‘q."
    else:
        text = "📋 **BAZADAGI ANIMELAR RO'YXATI:**\n\n"
        for anime_id, name in anime_list:
            text += f"🆔 ID: `{anime_id}` | 🎌 {name}\n"

    keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="back_admin")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    connection = db_connect()
    cursor = connection.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    users_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM anime")
    anime_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM episodes")
    episodes_count = cursor.fetchone()[0]
    connection.close()

    text = (
        "📊 **BOT STATISTIKASI:**\n\n"
        f"👤 Foydalanuvchilar: **{users_count}** ta\n"
        f"🎌 Animelar soni: **{anime_count}** ta\n"
        f"📺 Jami qismlar: **{episodes_count}** ta"
    )
    keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="back_admin")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return ConversationHandler.END

    await query.edit_message_text("📢 **Xabar tarqatish bo'limi.**\n\nBarcha foydalanuvchilarga yubormoqchi bo'lgan matningizni kiriting:", parse_mode="Markdown")
    return BROADCAST_MESSAGE

async def broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_text = update.message.text

    connection = db_connect()
    cursor = connection.cursor()
    cursor.execute("SELECT telegram_id FROM users")
    users = cursor.fetchall()
    connection.close()

    count = 0
    for (user_id,) in users:
        try:
            await context.bot.send_message(chat_id=user_id, text=message_text)
            count += 1
        except Exception:
            pass

    await update.message.reply_text(f"✅ Xabar **{count}** ta foydalanuvchiga muvaffaqiyatli yuborildi.", parse_mode="Markdown")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Amal bekor qilindi.\n\n/admin orqali panelni ochishingiz mumkin.")
    return ConversationHandler.END

# =========================================================
# CONVERSATIONS (ANIME & EPISODE ADD)
# =========================================================

async def add_anime_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("➕ **ANIME QO‘SHISH**\n\nAnime nomini yozing:", parse_mode="Markdown")
    return ADD_ANIME_NAME

async def add_anime_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["anime_name"] = update.message.text.strip()
    await update.message.reply_text("🎭 Anime janrini yozing:")
    return ADD_ANIME_GENRE

async def add_anime_genre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["anime_genre"] = update.message.text.strip()
    await update.message.reply_text("📝 Anime haqida qisqacha tavsif yozing:")
    return ADD_ANIME_DESCRIPTION

async def add_anime_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = context.user_data["anime_name"]
    genre = context.user_data["anime_genre"]
    description = update.message.text.strip()

    connection = db_connect()
    cursor = connection.cursor()
    cursor.execute("INSERT INTO anime (name, description, genre) VALUES (?, ?, ?)", (name, description, genre))
    connection.commit()
    connection.close()

    context.user_data.clear()
    await update.message.reply_text(f"✅ **ANIME QO‘SHILDI:** {name}", parse_mode="Markdown")
    return ConversationHandler.END

async def add_episode_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    connection = db_connect()
    cursor = connection.cursor()
    cursor.execute("SELECT id, name FROM anime ORDER BY id DESC")
    anime_list = cursor.fetchall()
    connection.close()

    if not anime_list:
        await query.edit_message_text("❌ Avval anime qo‘shing.")
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton(name, callback_data=f"episode_anime_{anime_id}")]
        for anime_id, name in anime_list
    ]
    await query.edit_message_text("📺 **QISM QO‘SHISH**\n\nQaysi animega qism qo‘shamiz?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ADD_EPISODE_NUMBER

async def choose_episode_anime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    anime_id = int(query.data.split("_")[-1])
    context.user_data["episode_anime_id"] = anime_id

    connection = db_connect()
    cursor = connection.cursor()
    cursor.execute("SELECT name FROM anime WHERE id = ?", (anime_id,))
    anime = cursor.fetchone()
    connection.close()

    context.user_data["episode_anime_name"] = anime[0]
    await query.edit_message_text(f"📺 **Anime:** {anime[0]}\n\nQism raqamini yozing:", parse_mode="Markdown")
    return ADD_EPISODE_NUMBER

async def episode_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("❌ Faqat musbat raqam yozing.")
        return ADD_EPISODE_NUMBER

    context.user_data["episode_number"] = int(text)
    await update.message.reply_text("📺 Videoni yuboring.")
    return ADD_EPISODE_VIDEO

async def episode_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.video:
        await update.message.reply_text("❌ Iltimos, video fayl yuboring.")
        return ADD_EPISODE_VIDEO

    video_file_id = update.message.video.file_id
    anime_id = context.user_data["episode_anime_id"]
    ep_num = context.user_data["episode_number"]

    connection = db_connect()
    cursor = connection.cursor()
    cursor.execute("INSERT INTO episodes (anime_id, episode_number, file_id) VALUES (?, ?, ?)", (anime_id, ep_num, video_file_id))
    connection.commit()
    connection.close()

    context.user_data.clear()
    await update.message.reply_text("✅ **QISM SAQLANDI!**", parse_mode="Markdown")
    return ConversationHandler.END

# =========================================================
# KATALOG VA FOYDALANUVCHI BO'LIMLARI
# =========================================================

async def user_anime_list_paged(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    query = update.callback_query
    if query:
        await query.answer()

    connection = db_connect()
    cursor = connection.cursor()
    cursor.execute("SELECT id, name FROM anime ORDER BY name")
    anime_list = cursor.fetchall()
    connection.close()

    if not anime_list:
        if query:
            await query.edit_message_text("🎌 Hozircha anime qo‘shilmagan.")
        return

    total_items = len(anime_list)
    start_offset = page * PAGE_SIZE
    end_offset = start_offset + PAGE_SIZE
    current_items = anime_list[start_offset:end_offset]

    keyboard = [
        [InlineKeyboardButton(name, callback_data=f"user_anime_{anime_id}")]
        for anime_id, name in current_items
    ]

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Orqaga", callback_data=f"user_anime_list_page_{page - 1}"))
    if end_offset < total_items:
        nav_row.append(InlineKeyboardButton("Keyingi ➡️", callback_data=f"user_anime_list_page_{page + 1}"))

    if nav_row:
        keyboard.append(nav_row)

    await query.edit_message_text(f"🎌 **ANIME KATALOGI** (Sahifa {page + 1}):", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def user_anime_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    anime_id = int(query.data.split("_")[-1])

    connection = db_connect()
    cursor = connection.cursor()
    cursor.execute("SELECT name, description, genre FROM anime WHERE id = ?", (anime_id,))
    anime = cursor.fetchone()

    cursor.execute("SELECT episode_number FROM episodes WHERE anime_id = ? ORDER BY episode_number", (anime_id,))
    episodes = cursor.fetchall()

    cursor.execute("SELECT id FROM favorites WHERE user_id = ? AND anime_id = ?", (user_id, anime_id))
    is_fav = cursor.fetchone()
    connection.close()

    if not anime:
        await query.edit_message_text("❌ Anime topilmadi.")
        return

    name, description, genre = anime
    text = f"🎌 **{name}**\n\n🎭 Janr: {genre or 'Noma’lum'}\n📝 {description or 'Tavsif yo‘q.'}\n\n📺 Qismni tanlang:"

    keyboard = []
    row = []
    for (ep_num,) in episodes:
        row.append(InlineKeyboardButton(f"{ep_num}-qism", callback_data=f"watch_{anime_id}_{ep_num}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    fav_text = "❌ Saralanganlardan o'chirish" if is_fav else "⭐️ Saralanganlarga qo'shish"
    fav_action = f"fav_remove_{anime_id}" if is_fav else f"fav_add_{anime_id}"
    
    keyboard.append([InlineKeyboardButton(fav_text, callback_data=fav_action)])
    keyboard.append([InlineKeyboardButton("⬅️ Katalog", callback_data="user_anime_list_page_0")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def toggle_favorite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    anime_id = int(data.split("_")[-1])
    connection = db_connect()
    cursor = connection.cursor()

    if data.startswith("fav_add_"):
        cursor.execute("INSERT OR IGNORE INTO favorites (user_id, anime_id) VALUES (?, ?)", (user_id, anime_id))
        await query.answer("⭐️ Saralanganlarga qo'shildi!", show_alert=True)
    else:
        cursor.execute("DELETE FROM favorites WHERE user_id = ? AND anime_id = ?", (user_id, anime_id))
        await query.answer("❌ Saralanganlardan olib tashlandi!", show_alert=True)

    connection.commit()
    connection.close()
    await user_anime_details(update, context)

async def show_favorites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    connection = db_connect()
    cursor = connection.cursor()
    cursor.execute("""
        SELECT anime.id, anime.name 
        FROM favorites 
        JOIN anime ON anime.id = favorites.anime_id 
        WHERE favorites.user_id = ?
    """, (user_id,))
    favs = cursor.fetchall()
    connection.close()

    if not favs:
        await query.edit_message_text("⭐️ Sizda hali saralangan animelar yo'q.")
        return

    keyboard = [
        [InlineKeyboardButton(name, callback_data=f"user_anime_{anime_id}")]
        for anime_id, name in favs
    ]
    await query.edit_message_text("⭐️ **SARALANGAN ANIMELARINGIZ:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def watch_episode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_")
    anime_id, ep_num = int(parts[1]), int(parts[2])

    connection = db_connect()
    cursor = connection.cursor()
    cursor.execute("""
        SELECT anime.name, episodes.file_id
        FROM episodes JOIN anime ON anime.id = episodes.anime_id
        WHERE episodes.anime_id = ? AND episodes.episode_number = ?
    """, (anime_id, ep_num))
    result = cursor.fetchone()
    connection.close()

    if result:
        anime_name, file_id = result
        keyboard = [[InlineKeyboardButton("⚠️ Video ishlamayaptimi?", callback_data=f"report_{anime_id}_{ep_num}")]]
        await query.message.reply_video(
            video=file_id, 
            caption=f"🎌 **{anime_name}**\n📺 {ep_num}-qism",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

async def report_issue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("⚠️ Adminga xabar berildi!", show_alert=True)

    parts = query.data.split("_")
    anime_id, ep_num = int(parts[1]), int(parts[2])
    user = query.from_user

    report_text = (
        f"⚠️ **XATOLIK XABARI!**\n\n"
        f"👤 Foydalanuvchi: @{user.username or 'yoq'} (ID: `{user.id}`)\n"
        f"🎌 Anime ID: `{anime_id}`\n"
        f"📺 Qism: {ep_num}-qism"
    )
    await context.bot.send_message(chat_id=ADMIN_ID, text=report_text, parse_mode="Markdown")

# =========================================================
# ROUTER & MAIN
# =========================================================

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data == "check_subscription":
        await query.answer()
        if await check_sub(query.from_user.id, context):
            await query.edit_message_text("✅ O'bona tasdiqlandi! Botdan foydalanishingiz mumkin.\n\n /start tugmasini bosing.")
        else:
            await query.answer("❌ Kanalga hali a'zo bo'lmadingiz!", show_alert=True)
    elif data.startswith("user_anime_list_page_"):
        page = int(data.split("_")[-1])
        await user_anime_list_paged(update, context, page)
        
    # Majburiy obona tekshiruvi BARCHA tugmalar uchun
    if not await check_sub(query.from_user.id, context):
        await send_sub_request(update)
        return

    if data == "user_favorites":
        await show_favorites(update, context)
    elif data.startswith("fav_add_") or data.startswith("fav_remove_"):
        await toggle_favorite(update, context)
    elif data.startswith("user_anime_"):
        await user_anime_details(update, context)
    elif data.startswith("watch_"):
        await watch_episode(update, context)
    elif data.startswith("report_"):
        await report_issue(update, context)
    elif data == "delete_anime":
        await delete_anime_list(update, context)
    elif data.startswith("confirm_delete_"):
        await process_delete_anime(update, context)
    elif data == "admin_anime_list":
        await admin_anime_list(update, context)
    elif data == "statistics":
        await admin_stats(update, context)
    elif data == "back_admin":
        await send_admin_panel(update)

def main():
    init_database()
    app = Application.builder().token(TOKEN).build()

    # Conversation Handlers
    add_anime_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_anime_start, pattern="^add_anime$")],
        states={
            ADD_ANIME_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_anime_name)],
            ADD_ANIME_GENRE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_anime_genre)],
            ADD_ANIME_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_anime_description)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    add_episode_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_episode_start, pattern="^add_episode$")],
        states={
            ADD_EPISODE_NUMBER: [
                CallbackQueryHandler(choose_episode_anime, pattern=r"^episode_anime_\d+$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, episode_number),
            ],
            ADD_EPISODE_VIDEO: [MessageHandler(filters.VIDEO, episode_video)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(broadcast_start, pattern="^broadcast$")],
        states={
            BROADCAST_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_send)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(add_anime_conv)
    app.add_handler(add_episode_conv)
    app.add_handler(broadcast_conv)
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_anime))
    app.add_handler(CallbackQueryHandler(callback_router))

    print("Bot muvaffaqiyatli ishga tushdi...")
    app.run_polling()

if __name__ == "__main__":
    main()