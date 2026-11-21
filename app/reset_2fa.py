"""Last resort: turn off two-step login for a user who lost the phone and the recovery codes.

Run where the panel runs:
  Docker:                docker exec -it wgpanel python -m app.reset_2fa admin
  Container in MikroTik: /container shell wgpanel   then   cd /app && python -m app.reset_2fa admin
"""
import sys

from .db import SessionLocal, User, audit


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python -m app.reset_2fa <username>")
        return 2
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == sys.argv[1]).first()
        if not user:
            print(f"User '{sys.argv[1]}' not found. Users: {', '.join(u.username for u in db.query(User).all())}")
            return 1
        user.totp_enabled = False
        user.totp_secret_enc = ""
        user.recovery_codes_json = "[]"
        db.commit()
        audit(db, "terminal", "verificação em 2 etapas desativada para outro usuário", user.username)
        print(f"Two-step login turned off for '{user.username}'. Log in with the password and set it up again.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
