"""
Condor Buildings Generator - SSL context for HTTPS downloads.

Windows keeps its own root certificate store, and on a machine that has not
been updated for a while it can still hold an EXPIRED root of the Let's Encrypt
chain the Overpass servers use.  Python then refuses the connection with
"[SSL: CERTIFICATE_VERIFY_FAILED] certificate has expired" and every download
in the plugin fails, even though the servers are perfectly fine.

To be independent of that, the addon ships its own, up to date certificate
bundle in  certs/cacert.pem  and every download goes through the context built
from it.  Nothing here imports bpy, so the module can also be used by the
standalone helpers that run outside Blender.
"""

import os
import ssl
import socket
import logging
import urllib.request

logger = logging.getLogger(__name__)

# Built once, then reused (False = "not built yet", None = "no context")
_CONTEXT = False

# certs/cacert.pem in the addon root, one level above this package
_BUNDLE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "certs", "cacert.pem",
)


def get_ssl_context():
    """SSL context for the plugin's HTTPS downloads.

    Certificate sources, in order:
      1. certs/cacert.pem bundled with the addon (always up to date, ships with the plugin)
      2. certifi shipped with Blender
      3. Python default (system store) - the last resort

    Never raises: returns None when no context could be built at all, and the
    caller then simply opens the connection the way it did before.
    """
    global _CONTEXT
    if _CONTEXT is not False:
        return _CONTEXT

    _CONTEXT = None

    # 1) the bundle that ships with the addon
    try:
        if os.path.isfile(_BUNDLE) and os.path.getsize(_BUNDLE) > 0:
            _CONTEXT = ssl.create_default_context(cafile=_BUNDLE)
            logger.info("SSL: using the certificates bundled with the addon (%s)", _BUNDLE)
            return _CONTEXT
    except Exception as e:
        logger.warning("SSL: the bundled certificates could not be used: %s", e)

    # 2) certifi, if this Python has one
    try:
        import certifi
        _CONTEXT = ssl.create_default_context(cafile=certifi.where())
        logger.info("SSL: using the certificates from certifi (%s)", certifi.where())
        return _CONTEXT
    except Exception as e:
        logger.warning("SSL: certifi is not available: %s", e)

    # 3) whatever the system store holds
    try:
        _CONTEXT = ssl.create_default_context()
        logger.info("SSL: using the system certificate store")
    except Exception as e:
        logger.warning("SSL: no context could be built (%s), opening without one", e)
        _CONTEXT = None

    return _CONTEXT


def urlopen_ssl(request, timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
    """urllib.request.urlopen() with the plugin's certificates.

    A drop-in replacement for urlopen(); when no context is available it falls
    back to the plain call, i.e. exactly the behaviour before this module.
    """
    ctx = get_ssl_context()
    if ctx is None:
        return urllib.request.urlopen(request, timeout=timeout)
    return urllib.request.urlopen(request, timeout=timeout, context=ctx)
