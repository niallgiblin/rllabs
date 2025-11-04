from datetime import datetime, timedelta, timezone
import jwt
SECRET_KEY = "your-secret-key"
ALGORITHM = "HS256"
def _make_jwt(sub: str = "test-user", scopes=("api:read", "api:write")) -> str:
    """Create a signed JWT token"""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub,
        "scopes": list(scopes),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=30)).timestamp()),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
print(_make_jwt())