import os
from pathlib import Path


TEST_ROOT = Path(__file__).parent / ".runtime"
TEST_ROOT.mkdir(parents=True, exist_ok=True)
os.environ.update({
    "APP_ENV": "testing",
    "DATABASE_URL": f"sqlite:///{TEST_ROOT / 'test.db'}",
    "SESSION_PEPPER": "test-pepper-with-more-than-enough-random-looking-characters-123456789",
    "FIELD_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    "PLATFORM_ADMIN_EMAIL": "manager@example.com",
    "PLATFORM_ADMIN_NAME": "Mark Powell",
    "PLATFORM_ADMIN_PASSWORD": "TestPlatformPassword!2026",
    "MANAGER_URL": "http://manager.test",
    "STUDIO_URL": "http://studio.test",
    "CLIENT_URL": "http://client.test",
    "COOKIE_SECURE": "false",
    "PLATFORM_STORAGE_ROOT": str(TEST_ROOT / "platform"),
    "TENANT_STORAGE_ROOT": str(TEST_ROOT / "tenants"),
})

database_file = TEST_ROOT / "test.db"
if database_file.exists():
    database_file.unlink()
