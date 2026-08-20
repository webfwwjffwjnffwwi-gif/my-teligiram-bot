from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from main import Base, Anime, Episode, User  # Agar boshqa jadvallaringiz bo'lsa shu yerga qo'shasiz

# 1. Eski kompyuterdagi lokal baza (SQLite)
sqlite_engine = create_engine("sqlite:///anime.db")
SqliteSession = sessionmaker(bind=sqlite_engine)
sqlite_db = SqliteSession()

# 2. Yangi bulutli baza (Render PostgreSQL - siz bergan havola)
postgres_url = "postgresql://anime_db_n2d3_user:0MTajYZoT0ai3AXU7iyTcdsaLnF4oTEl@dpg-da3dvtajnfac73cagpj0-a.oregon-postgres.render.com/anime_db_n2d3"
postgres_engine = create_engine(postgres_url)

# PostgreSQL'da jadvallar yo'q bo'lsa, avtomatik yaratib olamiz
Base.metadata.create_all(postgres_engine)

PostgresSession = sessionmaker(bind=postgres_engine)
postgres_db = PostgresSession()

try:
    print("Ma'lumotlar ko'chirilmoqda...")

    # Animelarni ko'chirish
    animelar = sqlite_db.query(Anime).all()
    for a in animelar:
        exists = postgres_db.query(Anime).filter_by(id=a.id).first()
        if not exists:
            postgres_db.add(Anime(
                id=a.id, 
                name=a.name
            ))

    # Qismlarni ko'chirish
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

    # O'zgarishlarni saqlaymiz
    postgres_db.commit()
    print("Muvaffaqiyatli ko'chirildi! Barcha ma'lumotlar bulutli bazaga o'tdi.")

except Exception as e:
    postgres_db.rollback()
    print(f"Xatolik yuz berdi: {e}")

finally:
    sqlite_db.close()
    postgres_db.close()