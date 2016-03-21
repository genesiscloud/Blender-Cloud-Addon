"""External dependencies loader."""

import glob
import os.path
import sys
import logging

my_dir = os.path.join(os.path.dirname(__file__))
log = logging.getLogger(__name__)


def load_wheel(module_name, fname_prefix):
    """Loads a wheel from 'fname_prefix*.whl', unless the named module can be imported.

    This allows us to use system-installed packages before falling back to the shipped wheels.
    This is useful for development, less so for deployment.
    """

    try:
        module = __import__(module_name)
    except ImportError:
        pass
    else:
        log.debug('Was able to load %s from %s, no need to load wheel %s',
                  module_name, module.__file__, fname_prefix)
        return

    path_pattern = os.path.join(my_dir, '%s*.whl' % fname_prefix)
    wheels = glob.glob(path_pattern)
    if not wheels:
        raise RuntimeError('Unable to find wheel at %r' % path_pattern)

    sys.path.append(wheels[0])
    module = __import__(module_name)
    log.debug('Loaded %s from %s', module_name, module.__file__)


def load_wheels():
    load_wheel('lockfile', 'lockfile')
    load_wheel('cachecontrol', 'CacheControl')
    load_wheel('pillarsdk', 'pillar_sdk')