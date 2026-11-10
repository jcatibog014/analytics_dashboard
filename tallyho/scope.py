"""What a page is looking at: the dates (ranges.py) and the filters (region,
channel, category), read from the address and checked against this
workspace's own lists. Every analytics page starts with scope.current().
"""
from dataclasses import dataclass

from flask import g, request, url_for

from . import analytics, db, ranges


@dataclass
class Scope:
    choice: ranges.Choice
    filters: analytics.Filters
    before: analytics.Filters | None   # the same filters over the compare period
    options: dict                      # regions / channels / categories for the menus
    bounds: tuple                      # first and last day with a sale
    chosen: dict                       # {"region": (id, name), ...} for the filter chips

    @property
    def compare_label(self):
        return ranges.COMPARE_NAMES.get(self.choice.compare_key, "")

    def args(self, **changes):
        """Address parts for a link to the same view, with some things changed."""
        out = ranges.args_for(self.choice)
        if request.args.get("grain"):
            out["grain"] = request.args["grain"]
        for name in ("region", "channel", "category"):
            if name in self.chosen:
                out[name] = self.chosen[name][0]
        out.update(changes)
        return {k: v for k, v in out.items() if v is not None}

    def url(self, endpoint=None, keep=None, **changes):
        """A link to a page (this one by default) with the same dates and filters."""
        values = dict(request.view_args or {}) if endpoint is None else {}
        values.update({k: v for k, v in (keep or {}).items() if v})
        values.update(self.args(**changes))
        return url_for(endpoint or request.endpoint, **{k: v for k, v in values.items() if v not in (None, "")})


def _pick(options, value):
    try:
        wanted = int(value)
    except (TypeError, ValueError):
        return None
    return next(((id_, name) for id_, name in options if id_ == wanted), None)


def current(**extra):
    """The Scope for this request. extra adds a product_id or customer_id."""
    s = db.session()
    workspace = g.workspace
    version = workspace.data_version or 0
    bounds = analytics.bounds(s, workspace.id, version)
    options = analytics.choices(s, workspace.id, version)
    choice = ranges.choose(request.args, bounds)
    chosen = {}
    for name, plural in (("region", "regions"), ("channel", "channels"), ("category", "categories")):
        picked = _pick(options[plural], request.args.get(name))
        if picked:
            chosen[name] = picked
    filters = analytics.filters_for(
        workspace, choice.period,
        region_id=chosen.get("region", (None,))[0], channel_id=chosen.get("channel", (None,))[0],
        category_id=chosen.get("category", (None,))[0], **extra)
    before = filters.during(choice.compare) if choice.compare else None
    return Scope(choice, filters, before, options, bounds, chosen)
