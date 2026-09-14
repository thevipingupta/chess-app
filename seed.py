"""Seed script — creates default admin user if not already present.

Run once:
    .venv/Scripts/python seed.py
"""

import bcrypt
from backend.database import SessionLocal, engine, Base
from backend.models.user import User
from backend.models.puzzle import UserRating

Base.metadata.create_all(bind=engine)

db = SessionLocal()
try:
    if db.query(User).filter(User.username == "admin").first():
        print("Admin user already exists — skipping.")
    else:
        user = User(
            username="admin",
            email="admin@chess.local",
            password_hash=bcrypt.hashpw(b"admin", bcrypt.gensalt()).decode(),
        )
        db.add(user)
        db.flush()
        db.add(UserRating(user_id=user.id))
        db.commit()
        print("✅ Default user created — username: admin  password: admin")
finally:
    db.close()
