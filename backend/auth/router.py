"""Auth endpoints — register and login."""

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from backend.auth.jwt import create_access_token
from backend.auth.schemas import LoginResponse, RegisterRequest, UserOut
from backend.database import get_db
from backend.models.user import User
from backend.models.puzzle import UserRating

router = APIRouter(prefix="/auth", tags=["auth"])


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == req.username).first():
        raise HTTPException(status_code=400, detail="Username already taken")
    if db.query(User).filter(User.email == req.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        username=req.username,
        email=req.email,
        password_hash=_hash(req.password),
    )
    db.add(user)
    db.flush()  # get user.id before committing

    # Initialise puzzle rating for new user
    db.add(UserRating(user_id=user.id))
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=LoginResponse)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == form.username).first()
    if not user or not _verify(form.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = create_access_token({"sub": str(user.id)})
    return LoginResponse(access_token=token, username=user.username)


@router.get("/me", response_model=UserOut)
def me(user_id: int = Depends(lambda token=None: None), db: Session = Depends(get_db)):
    """Returns the currently logged-in user's profile."""
    from backend.auth.jwt import get_current_user_id
    raise HTTPException(status_code=501, detail="Use /auth/me with Authorization header")
