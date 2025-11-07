#!/usr/bin/env python3
"""
JWT Token Generator
===================

Generate JWT tokens for testing and development.
Supports regular users and admin users.

Usage:
    python generate_token.py                    # Regular user token
    python generate_token.py --admin            # Admin token
    python generate_token.py --user user-123    # Custom user ID
    python generate_token.py --admin --user admin-1  # Admin with custom ID
"""

import jwt
import argparse
from datetime import datetime, timedelta, timezone
import sys

# Must match api_gateway/jwt_auth.py
SECRET_KEY = "your-secret-key"
ALGORITHM = "HS256"
DEFAULT_EXPIRY_MINUTES = 30


def generate_token(user_id: str = "test-user", scopes: list = None, expires_minutes: int = DEFAULT_EXPIRY_MINUTES) -> str:
    """
    Generate a JWT token
    
    Args:
        user_id: User identifier (sub claim)
        scopes: List of scopes (e.g., ["api:read", "api:write", "api:admin"])
        expires_minutes: Token expiration time in minutes
    
    Returns:
        Encoded JWT token string
    """
    if scopes is None:
        scopes = ["api:read", "api:write"]
    
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "scopes": scopes,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expires_minutes)).timestamp()),
    }
    
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    return token


def decode_token(token: str) -> dict:
    """
    Decode and verify a JWT token (for debugging)
    
    Args:
        token: JWT token string
    
    Returns:
        Decoded payload
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        return {"error": "Token has expired"}
    except jwt.InvalidTokenError as e:
        return {"error": f"Invalid token: {str(e)}"}


def main():
    parser = argparse.ArgumentParser(
        description="Generate JWT tokens for API Gateway authentication",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Regular user token
  python generate_token.py
  
  # Admin token
  python generate_token.py --admin
  
  # Custom user ID
  python generate_token.py --user my-user-id
  
  # Admin with custom user ID
  python generate_token.py --admin --user admin-1
  
  # Decode a token
  python generate_token.py --decode <token>
        """
    )
    
    parser.add_argument(
        "--user",
        default="test-user",
        help="User ID (default: test-user)"
    )
    
    parser.add_argument(
        "--admin",
        action="store_true",
        help="Include admin scope (api:admin)"
    )
    
    parser.add_argument(
        "--expires",
        type=int,
        default=DEFAULT_EXPIRY_MINUTES,
        help=f"Token expiration in minutes (default: {DEFAULT_EXPIRY_MINUTES})"
    )
    
    parser.add_argument(
        "--decode",
        metavar="TOKEN",
        help="Decode and display token payload"
    )
    
    parser.add_argument(
        "--scopes",
        nargs="+",
        help="Custom scopes (default: api:read api:write, or adds api:admin if --admin)"
    )
    
    args = parser.parse_args()
    
    # Decode mode
    if args.decode:
        payload = decode_token(args.decode)
        print("Token Payload:")
        print("-" * 50)
        for key, value in payload.items():
            print(f"{key}: {value}")
        return
    
    # Generate token
    scopes = args.scopes if args.scopes else ["api:read", "api:write"]
    
    if args.admin and "api:admin" not in scopes:
        scopes.append("api:admin")
    
    token = generate_token(
        user_id=args.user,
        scopes=scopes,
        expires_minutes=args.expires
    )
    
    # Display token info
    print("=" * 70)
    print("JWT Token Generated")
    print("=" * 70)
    print(f"User ID:     {args.user}")
    print(f"Scopes:      {', '.join(scopes)}")
    print(f"Expires in:  {args.expires} minutes")
    print()
    print("Token:")
    print("-" * 70)
    print(token)
    print("-" * 70)
    print()
    print("Usage:")
    print(f'  curl -H "Authorization: Bearer {token[:50]}..." http://localhost:8080/api/models')
    print()
    
    # Verify token
    payload = decode_token(token)
    if "error" not in payload:
        print("Token Verification:")
        print(f"  Valid: Yes")
        print(f"  User:  {payload.get('sub')}")
        print(f"  Scopes: {', '.join(payload.get('scopes', []))}")
        expires_at = datetime.fromtimestamp(payload.get('exp', 0), tz=timezone.utc)
        print(f"  Expires: {expires_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    else:
        print(f"Token Verification: {payload['error']}")
        sys.exit(1)


if __name__ == "__main__":
    main()