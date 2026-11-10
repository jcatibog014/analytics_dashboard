"""Remembered answers, so the same dashboard isn't worked out twice.

The key holds the workspace's data_version (in analytics.Filters). An import
or undo adds 1 to it, so every old answer simply stops being asked for: there
is no "clear the cache" step to forget. Old answers fall out when the cache
is full (least recently used first).

Each app (and each test) has its own cache in app.extensions. On Render there
is one gunicorn worker, so one cache serves everyone.
"""
import functools
import threading
from collections import OrderedDict

from flask import current_app, has_app_context

MAX_ITEMS = 1000


class Cache:
    def __init__(self, max_items=MAX_ITEMS):
        self.items = OrderedDict()
        self.max_items = max_items
        self.lock = threading.Lock()
        self.hits = self.misses = 0
        self.enabled = True

    def get(self, key):
        with self.lock:
            if key in self.items:
                self.items.move_to_end(key)
                self.hits += 1
                return True, self.items[key]
            self.misses += 1
            return False, None

    def put(self, key, value):
        with self.lock:
            self.items[key] = value
            self.items.move_to_end(key)
            while len(self.items) > self.max_items:
                self.items.popitem(last=False)

    def clear(self):
        with self.lock:
            self.items.clear()
            self.hits = self.misses = 0

    @property
    def hit_rate(self):
        asked = self.hits + self.misses
        return self.hits / asked if asked else None


def current():
    return current_app.extensions.setdefault("tallyho_cache", Cache()) if has_app_context() else None


def memo(function):
    """Remember function(s, filters, ...) by everything but the session.
    Pass source="raw" or "rollup" and the answer isn't remembered (the Lab times real queries)."""
    @functools.wraps(function)
    def wrapped(s, *args, **kwargs):
        cache = current()
        if cache is None or not cache.enabled or kwargs.get("source", "auto") != "auto":
            return function(s, *args, **kwargs)
        key = (function.__name__, args, tuple(sorted(kwargs.items())))
        found, value = cache.get(key)
        if found:
            return value
        value = function(s, *args, **kwargs)
        cache.put(key, value)
        return value
    return wrapped
