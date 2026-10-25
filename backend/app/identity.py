"""Password, email verification, and opaque database-backed session handling."""
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from uuid import uuid4

from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models import EmailVerificationToken, PasswordResetToken, User, UserSession

PASSWORDS = PasswordHash.recommended()
SESSION_TTL = timedelta(hours=12)
VERIFICATION_TTL = timedelta(hours=24)
PASSWORD_RESET_TTL = timedelta(minutes=30)


class EmailAlreadyRegistered(Exception):
    pass


class InvalidCredentials(Exception):
    pass


class VerificationError(Exception):
    pass


class PasswordResetError(Exception):
    pass


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _user_json(user: User) -> dict:
    return {"id": user.id, "email": user.email, "is_active": user.is_active,
            "email_verified": user.email_verified}


class IdentityStore:
    def __init__(self, sessions: sessionmaker):
        self.sessions = sessions

    def register(self, email: str, password: str) -> tuple[dict, str]:
        normalized = email.strip().lower()
        now = datetime.now(timezone.utc)
        verification_token = secrets.token_urlsafe(32)
        user = User(id=str(uuid4()), email=normalized, password_hash=PASSWORDS.hash(password), is_active=True, email_verified=False)
        try:
            with self.sessions() as session, session.begin():
                session.add(user)
                session.flush()
                session.add(EmailVerificationToken(
                    user_id=user.id, token_hash=_token_hash(verification_token),
                    expires_at=now + VERIFICATION_TTL,
                ))
                return _user_json(user), verification_token
        except IntegrityError as exc:
            raise EmailAlreadyRegistered(normalized) from exc

    def verify_email(self, token: str) -> dict:
        now = datetime.now(timezone.utc)
        with self.sessions() as session, session.begin():
            record = session.scalar(select(EmailVerificationToken).where(
                EmailVerificationToken.token_hash == _token_hash(token),
                EmailVerificationToken.consumed_at.is_(None),
                EmailVerificationToken.expires_at > now,
            ).with_for_update())
            if record is None:
                raise VerificationError("Invalid or expired verification token")
            user = session.get(User, record.user_id)
            if user is None or not user.is_active:
                raise VerificationError("User is unavailable")
            record.consumed_at = now
            user.email_verified = True
            return _user_json(user)

    def issue_verification(self, email: str) -> str | None:
        now = datetime.now(timezone.utc)
        with self.sessions() as session, session.begin():
            user = session.scalar(select(User).where(User.email == email.strip().lower()).with_for_update())
            if user is None or not user.is_active or user.email_verified:
                return None
            outstanding = session.scalars(select(EmailVerificationToken).where(
                EmailVerificationToken.user_id == user.id,
                EmailVerificationToken.consumed_at.is_(None),
            ).with_for_update()).all()
            for record in outstanding:
                record.consumed_at = now
            token = secrets.token_urlsafe(32)
            session.add(EmailVerificationToken(
                user_id=user.id, token_hash=_token_hash(token), expires_at=now + VERIFICATION_TTL,
            ))
            return token

    def request_password_reset(self, email: str) -> str | None:
        now = datetime.now(timezone.utc)
        with self.sessions() as session, session.begin():
            user = session.scalar(select(User).where(User.email == email.strip().lower()).with_for_update())
            if user is None or not user.is_active or not user.email_verified:
                return None
            outstanding = session.scalars(select(PasswordResetToken).where(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.consumed_at.is_(None),
            ).with_for_update()).all()
            for record in outstanding:
                record.consumed_at = now
            token = secrets.token_urlsafe(32)
            session.add(PasswordResetToken(
                user_id=user.id, token_hash=_token_hash(token), expires_at=now + PASSWORD_RESET_TTL,
            ))
            return token

    def complete_password_reset(self, token: str, new_password: str) -> None:
        now = datetime.now(timezone.utc)
        with self.sessions() as session, session.begin():
            record = session.scalar(select(PasswordResetToken).where(
                PasswordResetToken.token_hash == _token_hash(token),
                PasswordResetToken.consumed_at.is_(None),
                PasswordResetToken.expires_at > now,
            ).with_for_update())
            if record is None:
                raise PasswordResetError("Invalid or expired password reset token")
            user = session.get(User, record.user_id)
            if user is None or not user.is_active:
                raise PasswordResetError("User is unavailable")
            user.password_hash = PASSWORDS.hash(new_password)
            record.consumed_at = now
            sessions = session.scalars(select(UserSession).where(
                UserSession.user_id == user.id, UserSession.revoked_at.is_(None),
            ).with_for_update()).all()
            for active_session in sessions:
                active_session.revoked_at = now

    def login(self, email: str, password: str) -> tuple[dict, str]:
        with self.sessions() as session:
            user = session.scalar(select(User).where(User.email == email.strip().lower()))
            if user is None or not user.is_active:
                raise InvalidCredentials("Invalid email or password")
            if not PASSWORDS.verify(password, user.password_hash):
                raise InvalidCredentials("Invalid email or password")
            if not user.email_verified:
                raise VerificationError("Verify your email before signing in")
            token = secrets.token_urlsafe(32)
            session.add(UserSession(
                user_id=user.id, token_hash=_token_hash(token),
                expires_at=datetime.now(timezone.utc) + SESSION_TTL,
            ))
            session.commit()
            return _user_json(user), token

    def current_user(self, token: str | None) -> dict | None:
        if not token:
            return None
        now = datetime.now(timezone.utc)
        with self.sessions() as session:
            query = select(User).join(UserSession, UserSession.user_id == User.id).where(
                UserSession.token_hash == _token_hash(token),
                UserSession.revoked_at.is_(None), UserSession.expires_at > now,
                User.is_active.is_(True), User.email_verified.is_(True),
            )
            user = session.scalar(query)
            return _user_json(user) if user else None

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self.sessions() as session, session.begin():
            record = session.scalar(select(UserSession).where(UserSession.token_hash == _token_hash(token)).with_for_update())
            if record is not None and record.revoked_at is None:
                record.revoked_at = datetime.now(timezone.utc)
