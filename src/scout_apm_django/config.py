from django.conf import settings
from scout_apm.config.config import ScoutConfigPython


import logging


logger = logging.getLogger(__name__)


class ConfigAdapter:
    @classmethod
    def install(cls):
        configs = {}
        for name in filter(lambda x: x.startswith('SCOUT_'), dir(settings)):
            value = getattr(settings, name)
            clean_name = name.replace('SCOUT_', '').lower()
            configs[clean_name] = value
        ScoutConfigPython.set(**configs)
