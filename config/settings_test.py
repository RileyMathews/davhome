"""Test settings optimized for fast local test runs."""

from .settings_dev import *  # noqa: F403


PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]
