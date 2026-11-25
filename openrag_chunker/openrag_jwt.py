# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""OpenRAG user JWTs for DLS-mode requests.

OpenRAG sends user-scoped OpenSearch requests with an RS256 JWT that the
security plugin's openid domain verifies against the backend's JWKS. The
claims (session_manager._create_signed_jwt_token) feed DLS: ``sub`` becomes
``${user.name}``, ``email`` becomes ``${attr.jwt.email}`` and ``roles`` maps
the user onto OpenRAG's DLS roles. Basic-auth users cannot stand in, because
OpenSearch Security rejects DLS queries that reference undefined attributes.
"""

import base64
import json
import os
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

KEY_ID = "openrag-key-1"
DEFAULT_ISSUER = "http://openrag-backend:8000"


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def load_private_key(path):
    with open(path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def generate_private_key(path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    # Owner-only: this key signs tokens the security plugin trusts.
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))
    return key


def jwks(private_key):
    """The key set OpenRAG serves at /auth/jwks (api/oidc.py)."""
    numbers = private_key.public_key().public_numbers()

    def as_b64url(value):
        return _b64url(value.to_bytes((value.bit_length() + 7) // 8, "big"))

    return {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": KEY_ID,
                      "n": as_b64url(numbers.n), "e": as_b64url(numbers.e)}]}


def sign(claims, private_key):
    header = {"alg": "RS256", "typ": "JWT", "kid": KEY_ID}
    signing_input = f"{_b64url(json.dumps(header, separators=(',', ':')).encode())}." \
                    f"{_b64url(json.dumps(claims, separators=(',', ':')).encode())}"
    signature = private_key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{_b64url(signature)}"


def openrag_user_token(user_id, email, name, private_key, *, issuer=DEFAULT_ISSUER, ttl_seconds=86400):
    """A bearer token with the claims OpenRAG puts in a user's OpenSearch JWT."""
    now = int(time.time())
    roles = ["openrag_user"]
    claims = {
        "iss": issuer,
        "sub": user_id,
        "aud": ["opensearch", "openrag"],
        "exp": now + ttl_seconds,
        "iat": now,
        "auth_time": now,
        "user_id": user_id,
        "email": email,
        "name": name,
        "preferred_username": email,
        "email_verified": True,
        "roles": roles,
        "user_roles": roles,
    }
    return f"Bearer {sign(claims, private_key)}"
