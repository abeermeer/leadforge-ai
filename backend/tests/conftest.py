"""Test environment setup — runs before any app import.

Forces DEBUG + a fixed FERNET_KEY/SECRET_KEY so encryption works and the
production fail-fast check stays dormant during tests. These are set in
os.environ (highest precedence) so they win over any local .env file.
"""
import os

os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
# A valid Fernet key (urlsafe base64, 32 bytes) fixed for the test suite.
os.environ.setdefault("FERNET_KEY", "NnqnTIDsmStD-diXZmHUQAVF3SUa90nm5zOLQC79xyA=")
