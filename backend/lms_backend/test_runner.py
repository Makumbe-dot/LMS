"""The test runner: Django's, with a cheap password hasher and no cache.

PBKDF2 at Django's default work factor costs about a second a hash, and nearly
every test signs a user in, so before this most of the suite's time was spent in
`pbkdf2_hmac`. Under test the first hasher is MD5, which is what Django's own
documentation recommends for test runs. Nothing the suite asserts depends on the
algorithm: the password validators, lockout, change and reset all behave the
same. PBKDF2 stays in the list so a hash made under the production setting still
verifies. Production settings are untouched; this only runs under `manage.py test`.

The cache is a no-op under test because the rate limits count in it: the suite
signs in hundreds of times a minute from one address. The throttle tests switch a
real cache on for themselves.
"""
from django.conf import settings
from django.test.runner import DiscoverRunner, ParallelTestSuite
from django.test.utils import override_settings


def _use_test_settings(*_args):
    _use_fast_hashers()
    # Through override_settings, so the cache handler hears of the change.
    override_settings(CACHES={"default": {
        "BACKEND": "django.core.cache.backends.dummy.DummyCache"}}).enable()


def _use_fast_hashers():
    hashers = list(settings.PASSWORD_HASHERS)
    md5 = "django.contrib.auth.hashers.MD5PasswordHasher"
    settings.PASSWORD_HASHERS = [md5] + [h for h in hashers if h != md5]

    from django.contrib.auth.hashers import get_hashers, get_hashers_by_algorithm
    get_hashers.cache_clear()
    get_hashers_by_algorithm.cache_clear()


class _ParallelTestSuite(ParallelTestSuite):
    # Run in each worker before django.setup() when workers are spawned rather
    # than forked (Windows, macOS), since a spawned worker re-imports settings.
    process_setup = _use_test_settings


class TestRunner(DiscoverRunner):
    parallel_test_suite = _ParallelTestSuite

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        _use_test_settings()
