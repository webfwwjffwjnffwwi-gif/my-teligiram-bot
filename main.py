import os
import html
import logging
from threading import Thread

from dotenv import load_dotenv
from flask import Flask

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    BigInteger, # <-- Telegram ID sig'ishi uchun qo'shildi
    String,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

# ============================================================
# 1. ENVIRONMENT
# ============================================================

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError(
        "BOT_TOKEN topilmadi! .env yoki hosting Environment Variables "
        "ichiga BOT_TOKEN ni kiriting."
    )

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///anime.db").strip()

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()
if not ADMIN_ID_RAW:
    raise RuntimeError(
        "ADMIN_ID topilmadi! .env yoki hosting Environment Variables "
        "ichiga ADMIN_ID ni kiriting."
    )

try:
    ADMIN_ID = int(ADMIN_ID_RAW)
except ValueError:
    raise RuntimeError("ADMIN_ID faqat raqam bo'lishi kerak.")

REQUIRED_CHANNELS = [
    channel.strip()
    for channel in os.getenv(
        "REQUIRED_CHANNELS", "@Animelar_olami_uz_01"
    ).split(",")
    if channel.strip()
]

PAGE_SIZE = 5
EPISODES_PER_PAGE = 5

# ============================================================
# 2. LOGGING
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

# ============================================================
# 3. FLASK WEB SERVER
# ============================================================

web_app = Flask(__name__)


@web_app.route("/")
def home():
    return "🤖 Bot muvaffaqiyatli ishlayapti!"


@web_app.route("/health")
def health():
    return "OK"


def run_web_server():
    port = int(os.environ.get("PORT", "8080"))
    web_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


# ============================================================
# 4. DATABASE
# ============================================================

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )
else:
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_recycle=300,
    )

SessionFactory = sessionmaker(
    bind=engine,
    expire_on_commit=False,
)

Session = scoped_session(SessionFactory)
Base = declarative_base()


class Anime(Base):
    __tablename__ = "anime"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    genre = Column(String, nullable=True)


class Episode(Base):
    __tablename__ = "episodes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    anime_id = Column(Integer, ForeignKey("anime.id"), nullable=False)
    episode_number = Column(Integer, nullable=False)
    file_id = Column(String, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "anime_id",
            "episode_number",
            name="uq_anime_episode",
        ),
    )


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False)  # <-- BigInteger qilindi
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    last_anime_id = Column(Integer, nullable=True)
    last_episode_num = Column(Integer, nullable=True)


class Favorite(Base):
    __tablename__ = "favorites"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)  # <-- Xavfsizlik uchun bu ham BigInteger qilindi
    anime_id = Column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "anime_id",
            name="uq_user_anime_favorite",
        ),
    )


def init_database():
    Base.metadata.create_all(engine)
    logger.info("Database tayyor.")


def save_user(user_data):
    if not user_data:
        return

    session = Session()

    try:
        db_user = (
            session.query(User)
            .filter_by(telegram_id=user_data.id)
            .first()
        )

        username = user_data.username
        first_name = user_data.first_name

        if db_user is None:
            db_user = User(
                telegram_id=user_data.id,
                username=username,
                first_name=first_name,
            )
            session.add(db_user)
        else:
            db_user.username = username
            db_user.first_name = first_name

        session.commit()

    except Exception as e:
        session.rollback()
        logger.exception("Foydalanuvchini saqlashda xatolik: %s", e)

    finally:
        Session.remove()


# ============================================================
# 5. CONVERSATION STATES
# ============================================================

(
    ADD_ANIME_NAME,
    ADD_ANIME_GENRE,
    ADD_ANIME_DESCRIPTION,
) = range(3)

ADD_EPISODE_NUMBER, ADD_EPISODE_VIDEO = range(3, 5)

BROADCAST_MESSAGE = 5


# ============================================================
# 6. COMMON HELPERS
# ============================================================

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def channel_url(channel: str) -> str:
    channel = channel.strip()

    if channel.startswith("https://t.me/"):
        return channel

    if channel.startswith("@"):
        return f"https://t.me/{channel[1:]}"

    return f"https://t.me/{channel}"


def channel_chat_id(channel: str):
    channel = channel.strip()

    if channel.startswith("https://t.me/"):
        username = channel.rstrip("/").split("/")[-1]
        return f"@{username}"

    if channel.startswith("@"):
        return channel

    return f"@{channel}"


async def check_sub(
    user_id: int,
    context: ContextTypes.DEFAULT_TYPE,
) -> bool:
    if is_admin(user_id):
        return True

    if not REQUIRED_CHANNELS:
        return True

    for channel in REQUIRED_CHANNELS:
        try:
            member = await context.bot.get_chat_member(
                chat_id=channel_chat_id(channel),
                user_id=user_id,
            )

            if member.status not in (
                "member",
                "administrator",
                "creator",
            ):
                return False

        except Exception as e:
            logger.error(
                "Kanal obunasini tekshirishda xatolik (%s). "
                "Bot kanalda ADMIN ekanini va username to'g'riligini tekshiring: %s",
                channel,
                e,
            )
            return False

    return True


async def send_sub_request(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    keyboard = []

    for channel in REQUIRED_CHANNELS:
        display_channel = (
            channel
            if channel.startswith("@")
            else f"@{channel}"
        )

        keyboard.append(
            [
                InlineKeyboardButton(
                    f"📢 {display_channel} kanaliga a'zo bo'lish",
                    url=channel_url(channel),
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "✅ Obunani tekshirish",
                callback_data="check_subscription",
            )
        ]
    )

    text = (
        "🚨 <b>DIQQAT!</b>\n\n"
        "Botdan foydalanish uchun quyidagi barcha "
        "kanallarga a'zo bo'ling."
    )

    markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        query = update.callback_query

        try:
            await query.edit_message_text(
                text,
                reply_markup=markup,
                parse_mode="HTML",
            )
        except Exception:
            if query.message:
                await query.message.reply_text(
                    text,
                    reply_markup=markup,
                    parse_mode="HTML",
                )
    elif update.message:
        await update.message.reply_text(
            text,
            reply_markup=markup,
            parse_mode="HTML",
        )


def main_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🎌 Anime katalogi 📚",
                    callback_data="user_anime_list_page_0",
                )
            ],
            [
                InlineKeyboardButton(
                    "👤 Shaxsiy profil",
                    callback_data="user_profile",
                )
            ],
        ]
    )


def admin_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "➕ Yangi anime qo'shish 🎌",
                    callback_data="add_anime",
                )
            ],
            [
                InlineKeyboardButton(
                    "📺 Yangi qism qo'shish 🎬",
                    callback_data="add_episode",
                )
            ],
            [
                InlineKeyboardButton(
                    "📢 Xabar tarqatish 🚀",
                    callback_data="broadcast",
                )
            ],
            [
                InlineKeyboardButton(
                    "📋 Barcha animelar ro'yxati",
                    callback_data="admin_anime_list",
                )
            ],
            [
                InlineKeyboardButton(
                    "🗑 Animeni o'chirish",
                    callback_data="delete_anime",
                )
            ],
            [
                InlineKeyboardButton(
                    "📊 Bot statistikasi",
                    callback_data="statistics",
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Bosh menyu 🏠",
                    callback_data="back_to_main",
                )
            ],
        ]
    )


# ============================================================
# 7. USER START / SEARCH
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    try:
        user = update.effective_user

        if not user:
            return

        save_user(user)

        if not await check_sub(user.id, context):
            await send_sub_request(update, context)
            return

        first_name_clean = html.escape(
            user.first_name or "Foydalanuvchi"
        )

        text = (
            f"✨ 🌟 <b>Salom, {first_name_clean}!</b> 🌟 ✨\n\n"
            "🎌 <b>O'zbekistondagi anime botga xush kelibsiz!</b> "
            "🎬🍿\n\n"
            "👇 <b>Quyidagi imkoniyatlardan foydalaning:</b>\n\n"
            "🔍 <i>Anime nomini yoki ID raqamini yozib izlang</i>\n"
            "📚 <i>Katalog orqali anime toping</i>\n"
            "⭐️ <i>Sevimli animelaringizni saqlang</i>"
        )

        if update.callback_query:
            query = update.callback_query
            await query.answer()

            try:
                await query.edit_message_text(
                    text,
                    reply_markup=main_keyboard(),
                    parse_mode="HTML",
                )
            except Exception:
                if query.message:
                    await query.message.reply_text(
                        text,
                        reply_markup=main_keyboard(),
                        parse_mode="HTML",
                    )
        elif update.message:
            await update.message.reply_text(
                text,
                reply_markup=main_keyboard(),
                parse_mode="HTML",
            )

    except Exception as e:
        logger.exception("START xatosi: %s", e)


async def search_anime(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    try:
        user = update.effective_user

        if not user or not update.message:
            return

        save_user(user)

        if not await check_sub(user.id, context):
            await send_sub_request(update, context)
            return

        query_text = update.message.text.strip()

        if not query_text:
            return

        session = Session()

        try:
            if query_text.isdigit():
                anime_id = int(query_text)

                results = (
                    session.query(Anime.id, Anime.name)
                    .filter(Anime.id == anime_id)
                    .all()
                )
            else:
                results = (
                    session.query(Anime.id, Anime.name)
                    .filter(
                        Anime.name.ilike(
                            f"%{query_text}%"
                        )
                    )
                    .order_by(Anime.name)
                    .limit(10)
                    .all()
                )

        finally:
            Session.remove()

        if not results:
            await update.message.reply_text(
                "😔 <b>Kechirasiz, anime topilmadi.</b>\n\n"
                "Nomni yoki ID raqamini tekshirib ko'ring.",
                parse_mode="HTML",
            )
            return

        keyboard = []

        for anime_id, name in results:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"🆔 {anime_id} | 🎌 {name}",
                        callback_data=f"user_anime_{anime_id}_0",
                    )
                ]
            )

        keyboard.append(
            [
                InlineKeyboardButton(
                    "⬅️ Bosh menyuga qaytish 🏠",
                    callback_data="back_to_main",
                )
            ]
        )

        await update.message.reply_text(
            "🔎 <b>Qidiruv natijalari:</b> 🎬",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )

    except Exception as e:
        logger.exception("SEARCH xatosi: %s", e)


# ============================================================
# 8. ADMIN PANEL
# ============================================================

async def admin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user:
        return

    if not is_admin(update.effective_user.id):
        if update.message:
            await update.message.reply_text(
                "🚫 Kechirasiz, siz admin emassiz!"
            )
        return

    await send_admin_panel(update)


async def send_admin_panel(update: Update):
    text = (
        "⚙️ <b>ADMINISTRATOR PANELI</b> 🛠\n\n"
        "👇 Kerakli bo'limni tanlang:"
    )

    if update.callback_query:
        query = update.callback_query
        await query.edit_message_text(
            text,
            reply_markup=admin_keyboard(),
            parse_mode="HTML",
        )
    elif update.message:
        await update.message.reply_text(
            text,
            reply_markup=admin_keyboard(),
            parse_mode="HTML",
        )


# ============================================================
# 9. ADMIN DELETE
# ============================================================

async def delete_anime_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    session = Session()

    try:
        anime_list = (
            session.query(Anime.id, Anime.name)
            .order_by(Anime.id.desc())
            .all()
        )
    finally:
        Session.remove()

    if not anime_list:
        await query.edit_message_text(
            "❌ O'chirish uchun anime yo'q.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "⬅️ Orqaga",
                            callback_data="back_admin",
                        )
                    ]
                ]
            ),
        )
        return

    keyboard = []

    for anime_id, name in anime_list:
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"❌ {name}",
                    callback_data=f"confirm_delete_{anime_id}",
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "⬅️ Orqaga ⚙️",
                callback_data="back_admin",
            )
        ]
    )

    await query.edit_message_text(
        "🗑 <b>O'chirmoqchi bo'lgan animeni tanlang:</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )


async def process_delete_anime(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    try:
        anime_id = int(query.data.split("_")[-1])
    except (ValueError, IndexError):
        await query.edit_message_text("❌ Noto'g'ri anime ID.")
        return

    session = Session()

    try:
        anime = (
            session.query(Anime)
            .filter_by(id=anime_id)
            .first()
        )

        if not anime:
            text = "❌ Anime topilmadi."
        else:
            anime_name = anime.name

            session.query(Episode).filter_by(
                anime_id=anime_id
            ).delete(synchronize_session=False)

            session.query(Favorite).filter_by(
                anime_id=anime_id
            ).delete(synchronize_session=False)

            session.delete(anime)
            session.commit()

            text = (
                f"✅ <b>{html.escape(anime_name)}</b>\n\n"
                "Anime va uning barcha qismlari o'chirildi."
            )

    except Exception as e:
        session.rollback()
        logger.exception(
            "Anime o'chirishda xatolik: %s",
            e,
        )
        text = "❌ Anime o'chirishda xatolik yuz berdi."

    finally:
        Session.remove()

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "⬅️ Admin panel",
                        callback_data="back_admin",
                    )
                ]
            ]
        ),
        parse_mode="HTML",
    )


# ============================================================
# 10. ADMIN ANIME LIST / STATS
# ============================================================

async def admin_anime_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    session = Session()

    try:
        anime_list = (
            session.query(Anime.id, Anime.name)
            .order_by(Anime.id.desc())
            .all()
        )
    finally:
        Session.remove()

    if not anime_list:
        text = "📋 Hozircha botda animelar yo'q."
    else:
        lines = [
            "📋 <b>BOTDAGI ANIMELAR:</b> 🎌",
            "",
        ]

        for anime_id, name in anime_list:
            lines.append(
                f"🆔 <code>{anime_id}</code> | "
                f"🎌 <b>{html.escape(name)}</b>"
            )

        text = "\n".join(lines)

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "⬅️ Orqaga ⚙️",
                        callback_data="back_admin",
                    )
                ]
            ]
        ),
        parse_mode="HTML",
    )


async def admin_stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    session = Session()

    try:
        users_count = session.query(User).count()
        anime_count = session.query(Anime).count()
        episodes_count = session.query(Episode).count()
    finally:
        Session.remove()

    text = (
        "📊 <b>BOT STATISTIKASI</b> 📈\n\n"
        f"👥 Foydalanuvchilar: <b>{users_count}</b> ta\n"
        f"🎌 Animelar: <b>{anime_count}</b> ta\n"
        f"🍿 Qismlar: <b>{episodes_count}</b> ta"
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "⬅️ Orqaga ⚙️",
                        callback_data="back_admin",
                    )
                ]
            ]
        ),
        parse_mode="HTML",
    )


# ============================================================
# 11. BROADCAST
# ============================================================

async def broadcast_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return ConversationHandler.END

    await query.edit_message_text(
        "📢 <b>XABAR TARQATISH</b> 🚀\n\n"
        "Barcha foydalanuvchilarga yubormoqchi "
        "bo'lgan matningizni yozing.\n\n"
        "Bekor qilish: /cancel",
        parse_mode="HTML",
    )

    return BROADCAST_MESSAGE


async def broadcast_send(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user or not is_admin(update.effective_user.id):
        return ConversationHandler.END

    if not update.message:
        return BROADCAST_MESSAGE

    message_text = update.message.text.strip()

    if not message_text:
        await update.message.reply_text(
            "❌ Xabar bo'sh bo'lishi mumkin emas."
        )
        return BROADCAST_MESSAGE

    session = Session()

    try:
        users = session.query(User.telegram_id).all()
    finally:
        Session.remove()

    success_count = 0
    failed_count = 0

    for (user_id,) in users:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=message_text,
            )
            success_count += 1
        except Exception as e:
            failed_count += 1
            logger.warning(
                "Broadcast yuborilmadi (%s): %s",
                user_id,
                e,
            )

    await update.message.reply_text(
        "🚀 <b>Broadcast tugadi!</b>\n\n"
        f"✅ Yuborildi: <b>{success_count}</b>\n"
        f"❌ Yuborilmadi: <b>{failed_count}</b>",
        parse_mode="HTML",
    )

    return ConversationHandler.END


# ============================================================
# 12. ADD ANIME
# ============================================================

async def add_anime_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return ConversationHandler.END

    context.user_data.clear()

    await query.edit_message_text(
        "➕ <b>YANGI ANIME QO'SHISH</b> 🎌\n\n"
        "Anime nomini yozing:\n\n"
        "Bekor qilish: /cancel",
        parse_mode="HTML",
    )

    return ADD_ANIME_NAME


async def add_anime_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message:
        return ADD_ANIME_NAME

    name = update.message.text.strip()

    if not name:
        await update.message.reply_text(
            "❌ Anime nomi bo'sh bo'lmasin."
        )
        return ADD_ANIME_NAME

    context.user_data["anime_name"] = name

    await update.message.reply_text(
        "🎭 <b>Anime janrini kiriting:</b>",
        parse_mode="HTML",
    )

    return ADD_ANIME_GENRE


async def add_anime_genre(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message:
        return ADD_ANIME_GENRE

    genre = update.message.text.strip()

    if not genre:
        await update.message.reply_text(
            "❌ Janr bo'sh bo'lmasin."
        )
        return ADD_ANIME_GENRE

    context.user_data["anime_genre"] = genre

    await update.message.reply_text(
        "📝 <b>Anime haqida qisqacha tavsif kiriting:</b>",
        parse_mode="HTML",
    )

    return ADD_ANIME_DESCRIPTION


async def add_anime_description(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message:
        return ADD_ANIME_DESCRIPTION

    name = context.user_data.get("anime_name")
    genre = context.user_data.get("anime_genre")
    description = update.message.text.strip()

    if not name or not genre:
        await update.message.reply_text(
            "❌ Seans ma'lumotlari topilmadi. /admin orqali qayta boshlang."
        )
        context.user_data.clear()
        return ConversationHandler.END

    if not description:
        await update.message.reply_text(
            "❌ Tavsif bo'sh bo'lmasin."
        )
        return ADD_ANIME_DESCRIPTION

    session = Session()
    success = False

    try:
        new_anime = Anime(
            name=name,
            genre=genre,
            description=description,
        )

        session.add(new_anime)
        session.commit()
        success = True

    except Exception as e:
        session.rollback()
        logger.exception(
            "Anime qo'shishda xatolik: %s",
            e,
        )

    finally:
        Session.remove()

    context.user_data.clear()

    if success:
        await update.message.reply_text(
            "🎉 <b>ANIME MUVAFFAQIYATLI QO'SHILDI!</b> 🎌\n\n"
            f"📌 Nomi: <b>{html.escape(name)}</b>\n"
            f"🎭 Janri: <b>{html.escape(genre)}</b>",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            "❌ Anime bazaga saqlanmadi. Loglarni tekshiring."
        )

    return ConversationHandler.END


# ============================================================
# 13. ADD EPISODE
# ============================================================

async def add_episode_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return ConversationHandler.END

    session = Session()

    try:
        anime_list = (
            session.query(Anime.id, Anime.name)
            .order_by(Anime.id.desc())
            .all()
        )
    finally:
        Session.remove()

    if not anime_list:
        await query.edit_message_text(
            "❌ Qism qo'shish uchun avval anime yarating.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "⬅️ Admin panel",
                            callback_data="back_admin",
                        )
                    ]
                ]
            ),
        )
        return ConversationHandler.END

    keyboard = []

    for anime_id, name in anime_list:
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"🆔 {anime_id} | 🎌 {name}",
                    callback_data=f"episode_anime_{anime_id}",
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "⬅️ Bekor qilish",
                callback_data="back_admin",
            )
        ]
    )

    await query.edit_message_text(
        "📺 <b>QISM QO'SHISH</b> 🎬\n\n"
        "Qaysi animega qism qo'shmoqchisiz?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )

    return ADD_EPISODE_NUMBER


async def choose_episode_anime(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return ConversationHandler.END

    try:
        anime_id = int(query.data.split("_")[-1])
    except (ValueError, IndexError):
        await query.edit_message_text("❌ Noto'g'ri anime ID.")
        return ConversationHandler.END

    session = Session()

    try:
        anime = (
            session.query(Anime)
            .filter_by(id=anime_id)
            .first()
        )

        if anime is None:
            await query.edit_message_text(
                "❌ Anime topilmadi."
            )
            return ConversationHandler.END

        anime_name = anime.name

        max_ep = (
            session.query(Episode.episode_number)
            .filter_by(anime_id=anime_id)
            .order_by(Episode.episode_number.desc())
            .first()
        )

        max_ep_num = max_ep[0] if max_ep else 0
        next_suggested = max_ep_num + 1

    finally:
        Session.remove()

    context.user_data["episode_anime_id"] = anime_id
    context.user_data["episode_anime_name"] = anime_name

    await query.edit_message_text(
        f"🎬 <b>Tanlangan anime:</b> "
        f"{html.escape(anime_name)}\n\n"
        f"🔢 <b>Qism raqamini kiriting</b>\n"
        f"Masalan: <code>{next_suggested}</code>\n\n"
        "Bekor qilish: /cancel",
        parse_mode="HTML",
    )

    return ADD_EPISODE_NUMBER


async def episode_number(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message:
        return ADD_EPISODE_NUMBER

    text = update.message.text.strip()

    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text(
            "❌ Iltimos, musbat raqam kiriting!"
        )
        return ADD_EPISODE_NUMBER

    ep_num = int(text)
    anime_id = context.user_data.get("episode_anime_id")

    if not anime_id:
        await update.message.reply_text(
            "❌ Anime tanlanmagan. /admin orqali qayta urinib ko'ring."
        )
        return ConversationHandler.END

    context.user_data["episode_number"] = ep_num

    await update.message.reply_text(
        "📹 <b>Endi ushbu qismning videosini yuboring:</b> 📲\n\n"
        "Bekor qilish: /cancel",
        parse_mode="HTML",
    )

    return ADD_EPISODE_VIDEO


async def episode_video(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message:
        return ADD_EPISODE_VIDEO

    msg = update.message

    video = (
        msg.video
        or msg.document
        or msg.animation
        or msg.video_note
    )

    if not video:
        await msg.reply_text(
            "❌ Iltimos, video fayl yuboring!"
        )
        return ADD_EPISODE_VIDEO

    anime_id = context.user_data.get("episode_anime_id")
    ep_num = context.user_data.get("episode_number")
    anime_name = context.user_data.get(
        "episode_anime_name",
        "Anime",
    )

    if not anime_id or not ep_num:
        await msg.reply_text(
            "❌ Seans xatosi. /admin menyusidan qayta urinib ko'ring."
        )
        context.user_data.clear()
        return ConversationHandler.END

    video_file_id = video.file_id

    session = Session()
    success = False

    try:
        existing_ep = (
            session.query(Episode)
            .filter_by(
                anime_id=anime_id,
                episode_number=ep_num,
            )
            .first()
        )

        if existing_ep:
            existing_ep.file_id = video_file_id
        else:
            session.add(
                Episode(
                    anime_id=anime_id,
                    episode_number=ep_num,
                    file_id=video_file_id,
                )
            )

        session.commit()
        success = True

    except Exception as e:
        session.rollback()
        logger.exception(
            "Epizod saqlashda xatolik: %s",
            e,
        )

    finally:
        Session.remove()

    if not success:
        await msg.reply_text(
            "❌ Video bazaga saqlanmadi."
        )
        return ConversationHandler.END

    keyboard = [
        [
            InlineKeyboardButton(
                f"➕ Keyingi ({ep_num + 1}-qism)",
                callback_data=(
                    f"quick_add_next_{anime_id}_{ep_num + 1}"
                ),
            )
        ],
        [
            InlineKeyboardButton(
                "📋 Qismlar ro'yxati",
                callback_data=f"user_anime_{anime_id}_0",
            )
        ],
        [
            InlineKeyboardButton(
                "⚙️ Admin panel",
                callback_data="back_admin",
            )
        ],
    ]

    await msg.reply_text(
        f"🎉 <b>{ep_num}-QISM MUVAFFAQIYATLI SAQLANDI!</b> 🎬\n\n"
        f"🎌 Anime: <b>{html.escape(anime_name)}</b>\n"
        f"📺 Epizod: <b>{ep_num}-qism</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )

    context.user_data.clear()
    return ConversationHandler.END


async def quick_add_next_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return ConversationHandler.END

    parts = query.data.split("_")

    if len(parts) != 5:
        await query.edit_message_text(
            "❌ Noto'g'ri qism ma'lumoti."
        )
        return ConversationHandler.END

    try:
        anime_id = int(parts[3])
        next_ep = int(parts[4])
    except ValueError:
        await query.edit_message_text(
            "❌ Noto'g'ri qism ma'lumoti."
        )
        return ConversationHandler.END

    session = Session()

    try:
        anime = (
            session.query(Anime)
            .filter_by(id=anime_id)
            .first()
        )

        if anime is None:
            await query.edit_message_text(
                "❌ Anime topilmadi."
            )
            return ConversationHandler.END

        anime_name = anime.name

    finally:
        Session.remove()

    context.user_data["episode_anime_id"] = anime_id
    context.user_data["episode_number"] = next_ep
    context.user_data["episode_anime_name"] = anime_name

    await query.edit_message_text(
        f"🎬 <b>Anime:</b> {html.escape(anime_name)}\n\n"
        f"📹 <b>{next_ep}-qism uchun video yuboring:</b> 📲\n\n"
        "Bekor qilish: /cancel",
        parse_mode="HTML",
    )

    return ADD_EPISODE_VIDEO


# ============================================================
# 14. USER ANIME CATALOG
# ============================================================

async def user_anime_list_paged(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    page: int = 0,
):
    query = update.callback_query

    if query:
        await query.answer()

    page = max(0, page)

    session = Session()

    try:
        anime_list = (
            session.query(Anime.id, Anime.name)
            .order_by(Anime.name.asc())
            .all()
        )
    finally:
        Session.remove()

    if not anime_list:
        if query:
            await query.edit_message_text(
                "🎌 Hozircha botda animelar yo'q.",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "⬅️ Bosh menyu",
                                callback_data="back_to_main",
                            )
                        ]
                    ]
                ),
            )
        return

    total_items = len(anime_list)

    max_page = max(
        0,
        (total_items - 1) // PAGE_SIZE,
    )

    if page > max_page:
        page = max_page

    start_offset = page * PAGE_SIZE
    end_offset = start_offset + PAGE_SIZE

    current_items = anime_list[
        start_offset:end_offset
    ]

    keyboard = []

    for anime_id, name in current_items:
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"🆔 {anime_id} | 🎌 {name}",
                    callback_data=f"user_anime_{anime_id}_0",
                )
            ]
        )

    nav_row = []

    if page > 0:
        nav_row.append(
            InlineKeyboardButton(
                "⬅️ Oldingi",
                callback_data=(
                    f"user_anime_list_page_{page - 1}"
                ),
            )
        )

    if end_offset < total_items:
        nav_row.append(
            InlineKeyboardButton(
                "Keyingi ➡️",
                callback_data=(
                    f"user_anime_list_page_{page + 1}"
                ),
            )
        )

    if nav_row:
        keyboard.append(nav_row)

    keyboard.append(
        [
            InlineKeyboardButton(
                "⬅️ Bosh menyu 🏠",
                callback_data="back_to_main",
            )
        ]
    )

    await query.edit_message_text(
        f"📚 <b>ANIME KATALOGI</b>\n"
        f"Sahifa: <b>{page + 1}/{max_page + 1}</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )


# ============================================================
# 15. ANIME DETAILS
# ============================================================

async def user_anime_details(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    ep_page: int = 0,
):
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_")

    if len(parts) < 3:
        await query.edit_message_text(
            "❌ Noto'g'ri anime ma'lumoti."
        )
        return

    try:
        anime_id = int(parts[2])

        if len(parts) > 3:
            ep_page = int(parts[3])

    except ValueError:
        await query.edit_message_text(
            "❌ Noto'g'ri anime ma'lumoti."
        )
        return

    ep_page = max(0, ep_page)

    user_id = query.from_user.id

    session = Session()

    try:
        anime = (
            session.query(Anime)
            .filter_by(id=anime_id)
            .first()
        )

        if anime is None:
            await query.edit_message_text(
                "❌ Anime topilmadi.",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "⬅️ Katalog",
                                callback_data="user_anime_list_page_0",
                            )
                        ]
                    ]
                ),
            )
            return

        anime_name = anime.name
        anime_genre = anime.genre or "Noma'lum"
        anime_description = (
            anime.description
            or "Tavsif berilmagan."
        )

        episodes = [
            ep_num
            for (ep_num,) in (
                session.query(Episode.episode_number)
                .filter_by(anime_id=anime_id)
                .order_by(Episode.episode_number.asc())
                .all()
            )
        ]

        is_fav = (
            session.query(Favorite)
            .filter_by(
                user_id=user_id,
                anime_id=anime_id,
            )
            .first()
            is not None
        )

    finally:
        Session.remove()

    text = (
        f"🎌 <b>{html.escape(anime_name)}</b>\n"
        f"🆔 ID: <code>{anime_id}</code>\n\n"
        f"🎭 <b>Janri:</b> "
        f"{html.escape(anime_genre)}\n"
        f"📝 <b>Tavsif:</b> "
        f"<i>{html.escape(anime_description)}</i>\n\n"
        "🍿 <b>Qismni tanlang:</b>"
    )

    keyboard = []

    total_episodes = len(episodes)

    start_idx = ep_page * EPISODES_PER_PAGE
    end_idx = start_idx + EPISODES_PER_PAGE

    current_episodes = episodes[
        start_idx:end_idx
    ]

    row = []

    for ep_num in current_episodes:
        row.append(
            InlineKeyboardButton(
                f"🎬 {ep_num}-qism",
                callback_data=f"watch_{anime_id}_{ep_num}",
            )
        )

        if len(row) == 3:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    ep_nav = []

    if ep_page > 0:
        ep_nav.append(
            InlineKeyboardButton(
                "⬅️ Oldingi 5 ta",
                callback_data=(
                    f"user_anime_{anime_id}_{ep_page - 1}"
                ),
            )
        )

    if end_idx < total_episodes:
        ep_nav.append(
            InlineKeyboardButton(
                "Keyingi 5 ta ➡️",
                callback_data=(
                    f"user_anime_{anime_id}_{ep_page + 1}"
                ),
            )
        )

    if ep_nav:
        keyboard.append(ep_nav)

    if is_fav:
        fav_text = "❌ Saralanganlardan chiqarish"
        fav_action = f"fav_remove_{anime_id}"
    else:
        fav_text = "⭐️ Saralanganlarga qo'shish"
        fav_action = f"fav_add_{anime_id}"

    keyboard.append(
        [
            InlineKeyboardButton(
                fav_text,
                callback_data=fav_action,
            )
        ]
    )

    keyboard.append(
        [
            InlineKeyboardButton(
                "⬅️ Katalog 📚",
                callback_data="user_anime_list_page_0",
            )
        ]
    )

    keyboard.append(
        [
            InlineKeyboardButton(
                "🏠 Bosh sahifa",
                callback_data="back_to_main",
            )
        ]
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )


# ============================================================
# 16. FAVORITES
# ============================================================

async def toggle_favorite(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    data = query.data
    user_id = query.from_user.id

    try:
        anime_id = int(data.split("_")[-1])
    except (ValueError, IndexError):
        await query.answer(
            "❌ Noto'g'ri anime.",
            show_alert=True,
        )
        return

    session = Session()

    try:
        if data.startswith("fav_add_"):
            existing = (
                session.query(Favorite)
                .filter_by(
                    user_id=user_id,
                    anime_id=anime_id,
                )
                .first()
            )

            if not existing:
                session.add(
                    Favorite(
                        user_id=user_id,
                        anime_id=anime_id,
                    )
                )
                session.commit()

            await query.answer(
                "⭐️ Saralanganlarga qo'shildi!",
                show_alert=True,
            )

        else:
            (
                session.query(Favorite)
                .filter_by(
                    user_id=user_id,
                    anime_id=anime_id,
                )
                .delete(synchronize_session=False)
            )

            session.commit()

            await query.answer(
                "❌ Saralanganlardan olib tashlandi!",
                show_alert=True,
            )

    except Exception as e:
        session.rollback()

        logger.exception(
            "Favorite xatosi: %s",
            e,
        )

        await query.answer(
            "❌ Xatolik yuz berdi.",
            show_alert=True,
        )

    finally:
        Session.remove()

    query.data = f"user_anime_{anime_id}_0"

    await user_anime_details(
        update,
        context,
        ep_page=0,
    )


async def show_favorites(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    session = Session()

    try:
        favs = (
            session.query(Anime.id, Anime.name)
            .join(
                Favorite,
                Favorite.anime_id == Anime.id,
            )
            .filter(
                Favorite.user_id == user_id
            )
            .order_by(Anime.name.asc())
            .all()
        )

    finally:
        Session.remove()

    if not favs:
        await query.edit_message_text(
            "⭐️ Sizda hali saralangan animelar yo'q.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "⬅️ Profilga qaytish",
                            callback_data="user_profile",
                        )
                    ]
                ]
            ),
        )
        return

    keyboard = []

    for anime_id, name in favs:
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"🆔 {anime_id} | 🎌 {name}",
                    callback_data=f"user_anime_{anime_id}_0",
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "⬅️ Profil",
                callback_data="user_profile",
            )
        ]
    )

    await query.edit_message_text(
        "⭐️ <b>SARALANGAN ANIMELARINGIZ</b> 🌟",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )


# ============================================================
# 17. USER PROFILE
# ============================================================

async def user_profile(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    save_user(user)

    session = Session()

    try:
        fav_count = (
            session.query(Favorite)
            .filter_by(user_id=user.id)
            .count()
        )

        db_user = (
            session.query(User)
            .filter_by(telegram_id=user.id)
            .first()
        )

        last_watch_str = (
            "Hali hech qanday anime ko'rilmagan 📺"
        )

        if db_user and db_user.last_anime_id:
            anime = (
                session.query(Anime)
                .filter_by(id=db_user.last_anime_id)
                .first()
            )

            if anime:
                ep_num = db_user.last_episode_num or 0

                last_watch_str = (
                    f"🎬 <b>{html.escape(anime.name)}</b> "
                    f"({ep_num}-qism)"
                )

    finally:
        Session.remove()

    text = (
        "👤 <b>SHAXSIY PROFIL</b> 🆔\n\n"
        f"🆔 <b>Telegram ID:</b> "
        f"<code>{user.id}</code>\n"
        f"👤 <b>Ism:</b> "
        f"{html.escape(user.first_name or '')}\n"
        f"⭐️ <b>Saralangan:</b> "
        f"{fav_count} ta\n"
        f"📺 <b>Oxirgi ko'rilgan:</b> "
        f"{last_watch_str}"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "⭐️ Saralangan animelarim",
                callback_data="user_favorites",
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Bosh menyu 🏠",
                callback_data="back_to_main",
            )
        ],
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )


# ============================================================
# 18. WATCH EPISODE
# ============================================================

async def watch_episode(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_")

    if len(parts) != 3:
        if query.message:
            await query.message.reply_text("❌ Noto'g'ri qism ma'lumoti.")
        return

    try:
        anime_id = int(parts[1])
        ep_num = int(parts[2])
    except ValueError:
        if query.message:
            await query.message.reply_text("❌ Noto'g'ri qism ma'lumoti.")
        return

    user_id = query.from_user.id

    session = Session()

    try:
        db_user = (
            session.query(User)
            .filter_by(telegram_id=user_id)
            .first()
        )

        if db_user is None:
            db_user = User(
                telegram_id=user_id,
                username=query.from_user.username,
                first_name=query.from_user.first_name,
            )
            session.add(db_user)

        db_user.last_anime_id = anime_id
        db_user.last_episode_num = ep_num

        ep_data = (
            session.query(
                Anime.name,
                Episode.file_id,
            )
            .join(
                Episode,
                Episode.anime_id == Anime.id,
            )
            .filter(
                Episode.anime_id == anime_id,
                Episode.episode_number == ep_num,
            )
            .first()
        )

        if ep_data:
            anime_name = ep_data[0]
            file_id = ep_data[1]

            has_next = (
                session.query(Episode)
                .filter_by(
                    anime_id=anime_id,
                    episode_number=ep_num + 1,
                )
                .first()
                is not None
            )

            has_prev = (
                session.query(Episode)
                .filter_by(
                    anime_id=anime_id,
                    episode_number=ep_num - 1,
                )
                .first()
                is not None
            )
        else:
            anime_name = None
            file_id = None
            has_next = False
            has_prev = False

        session.commit()

    except Exception as e:
        session.rollback()
        logger.exception(
            "Watch episode xatosi: %s",
            e,
        )

        if query.message:
            await query.message.reply_text(
                "❌ Qismni ochishda xatolik yuz berdi."
            )
        return

    finally:
        Session.remove()

    if not file_id:
        if query.message:
            await query.message.reply_text(
                "❌ Ushbu qism topilmadi!"
            )
        return

    nav_buttons = []

    if has_prev:
        nav_buttons.append(
            InlineKeyboardButton(
                "⬅️ Oldingi qism",
                callback_data=(
                    f"watch_{anime_id}_{ep_num - 1}"
                ),
            )
        )

    if has_next:
        nav_buttons.append(
            InlineKeyboardButton(
                "Keyingi qism ➡️",
                callback_data=(
                    f"watch_{anime_id}_{ep_num + 1}"
                ),
            )
        )
    else:
        nav_buttons.append(
            InlineKeyboardButton(
                "Keyingi qism ➡️",
                callback_data=f"no_next_{ep_num + 1}",
            )
        )

    keyboard = []

    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append(
        [
            InlineKeyboardButton(
                "⚠️ Videoda muammo bormi?",
                callback_data=f"report_{anime_id}_{ep_num}",
            )
        ]
    )

    keyboard.append(
        [
            InlineKeyboardButton(
                "📋 Qismlar ro'yxati",
                callback_data=f"user_anime_{anime_id}_0",
            )
        ]
    )

    keyboard.append(
        [
            InlineKeyboardButton(
                "🏠 Bosh sahifa",
                callback_data="back_to_main",
            )
        ]
    )

    try:
        await context.bot.send_video(
            chat_id=query.message.chat_id,
            video=file_id,
            caption=(
                f"🎌 <b>{html.escape(anime_name)}</b>\n"
                f"📺 <b>{ep_num}-qism</b> 🍿"
            ),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )

    except Exception as e:
        logger.exception(
            "Video yuborishda xatolik: %s",
            e,
        )

        if query.message:
            await query.message.reply_text(
                "❌ Videoni yuborishda xatolik yuz berdi."
            )


async def handle_no_next_episode(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    try:
        next_ep = query.data.split("_")[2]
    except IndexError:
        next_ep = "Keyingi"

    await query.answer(
        text=(
            f"📌 {next_ep}-qism hali chiqarilmagan. "
            "Tez orada yuklanadi! ⏳"
        ),
        show_alert=True,
    )


# ============================================================
# 19. REPORT
# ============================================================

async def report_issue(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    parts = query.data.split("_")

    try:
        anime_id = int(parts[1])
        ep_num = int(parts[2])
    except (ValueError, IndexError):
        await query.answer(
            "❌ Noto'g'ri ma'lumot.",
            show_alert=True,
        )
        return

    await query.answer(
        "⚠️ Adminga xabar yuborildi!",
        show_alert=True,
    )

    user = query.from_user

    username = (
        f"@{user.username}"
        if user.username
        else "username yo'q"
    )

    report_text = (
        "🚨 <b>XATOLIK XABARI!</b> ⚠️\n\n"
        f"👤 Foydalanuvchi: {html.escape(username)}\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"🎌 Anime ID: <code>{anime_id}</code>\n"
        f"📺 Qism: <b>{ep_num}-qism</b>"
    )

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=report_text,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.exception(
            "Admin report yuborishda xatolik: %s",
            e,
        )


# ============================================================
# 20. CALLBACK ROUTER
# ============================================================

async def callback_router(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    data = query.data or ""

    if data == "check_subscription":
        if await check_sub(
            query.from_user.id,
            context,
        ):
            await query.answer(
                "✅ Obuna tasdiqlandi!",
                show_alert=True,
            )

            await start(update, context)

        else:
            await query.answer(
                "❌ Siz hali barcha kanallarga a'zo bo'lmagansiz!",
                show_alert=True,
            )

        return

    if not await check_sub(
        query.from_user.id,
        context,
    ):
        await send_sub_request(update, context)
        return

    if data == "back_to_main":
        await start(update, context)
        return

    if data == "back_admin":
        if is_admin(query.from_user.id):
            await query.answer()
            await send_admin_panel(update)
        return

    if data == "delete_anime":
        await delete_anime_list(update, context)
        return

    if data.startswith("confirm_delete_"):
        await process_delete_anime(update, context)
        return

    if data == "admin_anime_list":
        await admin_anime_list(update, context)
        return

    if data == "statistics":
        await admin_stats(update, context)
        return

    if data.startswith("user_anime_list_page_"):
        try:
            page = int(data.split("_")[-1])
        except ValueError:
            page = 0

        await user_anime_list_paged(
            update,
            context,
            page,
        )
        return

    if data == "user_profile":
        await user_profile(update, context)
        return

    if data == "user_favorites":
        await show_favorites(update, context)
        return

    if data.startswith("fav_add_") or data.startswith(
        "fav_remove_"
    ):
        await toggle_favorite(update, context)
        return

    if data.startswith("user_anime_"):
        await user_anime_details(update, context)
        return

    if data.startswith("watch_"):
        await watch_episode(update, context)
        return

    if data.startswith("no_next_"):
        await handle_no_next_episode(update, context)
        return

    if data.startswith("report_"):
        await report_issue(update, context)
        return

    await query.answer()


# ============================================================
# 21. CANCEL
# ============================================================

async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    context.user_data.clear()

    if update.callback_query:
        await update.callback_query.answer()
        await send_admin_panel(update)
    elif update.message:
        await update.message.reply_text(
            "❌ Amal bekor qilindi.\n\n"
            "/admin orqali panelni qayta ochishingiz mumkin."
        )

    return ConversationHandler.END


# ============================================================
# 22. ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.exception(
        "Telegram update xatosi: %s",
        context.error,
    )


# ============================================================
# 23. MAIN
# ============================================================

def main():
    init_database()

    web_thread = Thread(
        target=run_web_server,
        daemon=True,
    )
    web_thread.start()

    application = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    # -------------------------
    # ADD ANIME CONVERSATION
    # -------------------------

    add_anime_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                add_anime_start,
                pattern=r"^add_anime$",
            )
        ],
        states={
            ADD_ANIME_NAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    add_anime_name,
                )
            ],
            ADD_ANIME_GENRE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    add_anime_genre,
                )
            ],
            ADD_ANIME_DESCRIPTION: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    add_anime_description,
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(cancel, pattern=r"^back_admin$"),
        ],
    )

    # -------------------------
    # ADD EPISODE CONVERSATION
    # -------------------------

    add_episode_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                add_episode_start,
                pattern=r"^add_episode$",
            ),
            CallbackQueryHandler(
                quick_add_next_start,
                pattern=r"^quick_add_next_\d+_\d+$",
            ),
        ],
        states={
            ADD_EPISODE_NUMBER: [
                CallbackQueryHandler(
                    choose_episode_anime,
                    pattern=r"^episode_anime_\d+$",
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    episode_number,
                ),
            ],
            ADD_EPISODE_VIDEO: [
                MessageHandler(
                    filters.VIDEO
                    | filters.Document.VIDEO
                    | filters.ANIMATION
                    | filters.VIDEO_NOTE,
                    episode_video,
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(cancel, pattern=r"^back_admin$"),
        ],
    )

    # -------------------------
     # BROADCAST CONVERSATION
    # -------------------------

    broadcast_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                broadcast_start,
                pattern=r"^broadcast$",
            )
        ],
        states={
            BROADCAST_MESSAGE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    broadcast_send,
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(cancel, pattern=r"^back_admin$"),
        ],
    )

    # -------------------------
    # HANDLERS
    # -------------------------

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        CommandHandler(
            "admin",
            admin,
        )
    )

    application.add_handler(add_anime_conv)
    application.add_handler(add_episode_conv)
    application.add_handler(broadcast_conv)

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            search_anime,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            callback_router
        )
    )

    application.add_error_handler(error_handler)

    logger.info("🤖 Bot ishga tushmoqda...")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()