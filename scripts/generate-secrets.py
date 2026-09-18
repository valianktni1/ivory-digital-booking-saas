#!/usr/bin/env python3
import base64
import secrets


print("SESSION_PEPPER=" + secrets.token_hex(48))
print("FIELD_ENCRYPTION_KEY=" + base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
print("POSTGRES_PASSWORD=" + secrets.token_urlsafe(36))
