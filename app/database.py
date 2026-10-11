from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def make_session_factory(database_url: str):
    """Create the engine + session factory and make sure tables exist."""
    connect_args = {}
    if database_url.startswith("sqlite"):
        # FastAPI handles requests/background work on different threads.
        connect_args["check_same_thread"] = False
    engine = create_engine(database_url, connect_args=connect_args)

    # Imported here so the models register themselves on Base before create_all.
    from app import models  # noqa: F401

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)
