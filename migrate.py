from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from main import Base, Anime, Episode, User

# 1. Eski kompyuterdagi lokal baza (SQLite)
sqlite_engine = create_engine("sqlite:///anime.db")
SqliteSession = sessionmaker(bind=sqlite_engine)
sqlite_db = SqliteSession()

# 2. Yangi bulutli baza (Render PostgreSQL)
postgres_url = "postgresql://anime_db_n2d3_user:0MTajYZoT0ai3AXU7iyTcdsaLnF4oTEl@dpg-da3dvtajnfac73cagpj0-a.oregon-postgres.render.com/anime_db_n2d3"
postgres_engine = create_engine(postgres_url)

# DIQQAT: Eski 'Integer out of range' xatosi yo'qolishi uchun 
# Render'dagi eski jadvallarni o'chirib, BigInteger bilan qaytadan yaratamiz
print("Bulutli bazadagi jadvallar yangilanmoqda...")
Base.metadata.drop_all(postgres_engine)
Base.metadata.create_all(postgres_engine)

PostgresSession = sessionmaker(bind=postgres_engine)
postgres_db = PostgresSession()

try:
    print("Ma'lumotlar ko'chirilmoqda...")

    # 1. Animelarni ko'chirish
    animelar = sqlite_db.query(Anime).all()
    for a in animelar:
        exists = postgres_db.query(Anime).filter_by(id=a.id).first()
        if not exists:
            postgres_db.add(Anime(
                id=a.id, 
                name=a.name
            ))

    # 2. Qismlarni ko'chirish
    qismlar = sqlite_db.query(Episode).all()
    for q in qismlar:
        exists = postgres_db.query(Episode).filter_by(id=q.id).first()
        if not exists:
            postgres_db.add(Episode(
                id=q.id,
                anime_id=q.anime_id,
                file_id=q.file_id,
                episode_number=q.episode_number
            ))

    # 3. Foydalanuvchilarni (User) ko'chirish
    foydalanuvchilar = sqlite_db.query(User).all()
    for u in foydalanuvchilar:
        exists = postgres_db.query(User).filter_by(telegram_id=u.telegram_id).first()
        if not exists:
            postgres_db.add(User(
                id=u.id,
                telegram_id=u.telegram_id,
                username=u.username,
                first_name=u.first_name,
                last_anime_id=u.last_anime_id,
                last_episode_num=u.last_episode_num
            ))

    # O'zgarishlarni saqlaymiz
    postgres_db.commit()
    print("Muvaffaqiyatli ko'chirildi! Barcha ma'lumotlar bulutli bazaga o'tdi.")

except Exception as e:
    postgres_db.rollback()
    print(f"Xatolik yuz berdi: {e}")

finally:
    sqlite_db.close()
    postgres_db.close()