import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config import DATABASE_URL
from app.models.base import Base

os.makedirs("data", exist_ok=True)

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db():
    import app.models  # noqa: F401 ensure all models are registered before create_all

    Base.metadata.create_all(bind=engine)


def get_session():
    return SessionLocal()
