####
## NetBox base configuration for the Eve-NG Lab dedicated instance.
####
import os

ALLOWED_HOSTS = ['*']
DATABASE = {
    'NAME': os.environ.get('DB_NAME', 'netbox'),
    'USER': os.environ.get('DB_USER', 'netbox'),
    'PASSWORD': os.environ.get('DB_PASSWORD', ''),
    'HOST': os.environ.get('DB_HOST', 'postgres'),
    'PORT': os.environ.get('DB_PORT', ''),
    'CONN_MAX_AGE': int(os.environ.get('DB_CONN_MAX_AGE', '300')),
}

REDIS = {
    'tasks': {
        'HOST': os.environ.get('REDIS_HOST', 'redis'),
        'PORT': int(os.environ.get('REDIS_PORT', 6379)),
        'PASSWORD': os.environ.get('REDIS_PASSWORD', ''),
        'DATABASE': int(os.environ.get('REDIS_DATABASE', 0)),
        'SSL': os.environ.get('REDIS_SSL', 'False').lower() == 'true',
    },
    'caching': {
        'HOST': os.environ.get('REDIS_CACHE_HOST', 'redis-cache'),
        'PORT': int(os.environ.get('REDIS_CACHE_PORT', 6379)),
        'PASSWORD': os.environ.get('REDIS_CACHE_PASSWORD', ''),
        'DATABASE': int(os.environ.get('REDIS_CACHE_DATABASE', 1)),
        'SSL': os.environ.get('REDIS_CACHE_SSL', 'False').lower() == 'true',
    },
}

SECRET_KEY = os.environ.get('SECRET_KEY', '')

# NetBox 4.5+ requires peppers to encrypt API tokens at rest.
# Lab values — replace with strong random bytes for production.
API_TOKEN_PEPPERS = {
    1: 'eve-ng-lab-api-token-pepper-01-replace-with-secure-random-bytes-XYZ123',
}
API_TOKEN_DEFAULT_PEPPER_ID = 1

# Discover and load plugin configuration from configuration/plugins.py
try:
    from .plugins import PLUGINS, PLUGINS_CONFIG  # noqa: F401
except ImportError:
    PLUGINS = []
    PLUGINS_CONFIG = {}
