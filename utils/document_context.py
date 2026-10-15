"""Bounded, private last-document contexts for Telegram follow-up questions."""

from collections import OrderedDict
from copy import deepcopy
from threading import Lock
from time import monotonic

_contexts, _lock = OrderedDict(), Lock()


def remember_document(key, extraction):
    with _lock:
        _contexts[key] = (monotonic(), deepcopy(extraction))
        _contexts.move_to_end(key)
        while len(_contexts) > 256:
            _contexts.popitem(last=False)


def last_document(key):
    with _lock:
        item = _contexts.get(key)
        if not item or monotonic() - item[0] > 3600:
            _contexts.pop(key, None)
            return None
        return deepcopy(item[1])
