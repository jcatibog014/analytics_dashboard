"""Chart geometry: numbers in, SVG coordinates out.

Python works out where every line, bar and label goes; the Jinja macros in
templates/_charts.html draw them as SVG. Doing it in a template (not by
gluing strings together) means every product or region name is escaped,
so a name from a CSV file can never become markup.

The look follows the dataviz rules (docs/PLAN.md): 2px lines, a 10% area
wash, bars at most 24px thick with a 4px rounded end and a square base,
hairline gridlines, clean round axis numbers, a legend for two or more
series, and a hover layer (static/charts.js) that never holds back a value
the table view doesn't also show.
"""
import math

from . import formatting

FORMATTERS = {
    "money": formatting.money,
    "money_short": formatting.money_short,
    "number": formatting.number,
    "short": formatting.short_number,
    "percent": formatting.percent,
}


def fmt(kind, value, short=False):
    if value is None:
        return "—"
    if kind == "money":
        return formatting.money_short(value) if short else formatting.money(value)
    if kind == "percent":
        return formatting.percent(value)
    return formatting.short_number(value) if short else formatting.number(round(value))


def nice_step(rough):
    """1, 2, 2.5 or 5 times a power of ten: axes count in steps people read easily."""
    if rough <= 0:
        return 1
    power = 10 ** math.floor(math.log10(rough))
    for multiple in (1, 2, 2.5, 5, 10):
        if rough <= multiple * power:
            return multiple * power
    return 10 * power


def nice_scale(top, ticks=4):
    """(axis top, [tick values]) starting at 0, for a largest value of top."""
    if not top or top <= 0:
        return 1, [0, 1]
    step = nice_step(top / ticks)
    count = max(1, math.ceil(top / step))
    return step * count, [step * n for n in range(count + 1)]


def money_scale(top_centavos, ticks=4):
    """Like nice_scale, but in whole pesos (an axis never says P1,234.56)."""
    top, values = nice_scale(top_centavos / 100, ticks)
    return top * 100, [v * 100 for v in values]


def line_chart(points, kind="money", width=1000, height=300, compare=False, average=False, max_labels=8,
               second=False):
    """points: [{"label", "value", "compare"?, "avg"?, "tip"}] in time order.

    Returns everything _charts.html needs: paths, axis ticks, x labels and hover columns.
    """
    left, right, top_pad, bottom = (64 if width > 600 else 52), 16, 14, 30
    plot_w, plot_h = width - left - right, height - top_pad - bottom
    values = [p.get("value") for p in points]
    values += [p.get("compare") for p in points] if compare else []
    values += [p.get("avg") for p in points] if average else []
    values += [p.get("second") for p in points] if second else []
    biggest = max([v for v in values if v is not None] or [0])
    if kind == "money":
        axis_top, ticks = money_scale(biggest)
    elif kind == "percent":
        axis_top, ticks = nice_scale(biggest)
    else:
        axis_top, ticks = nice_scale(biggest)
    count = len(points)

    def x_at(index):
        return left + (plot_w * index / (count - 1) if count > 1 else plot_w / 2)

    def y_at(value):
        return top_pad + plot_h - (plot_h * value / axis_top if axis_top else 0)

    def path(key):
        parts, pen_down = [], False
        for index, point in enumerate(points):
            value = point.get(key)
            if value is None:
                pen_down = False
                continue
            parts.append(f"{'L' if pen_down else 'M'}{x_at(index):.1f},{y_at(value):.1f}")
            pen_down = True
        return " ".join(parts)

    main = path("value")
    area = ""
    if main and count > 1 and all(p.get("value") is not None for p in points):
        area = f"{main} L{x_at(count - 1):.1f},{y_at(0):.1f} L{x_at(0):.1f},{y_at(0):.1f} Z"
    # X labels: at most about max_labels, evenly spread, always the first and last.
    every = max(1, math.ceil(count / max_labels))
    x_labels = [{"x": x_at(i), "text": p["label"], "anchor": "middle"}
                for i, p in enumerate(points) if i % every == 0]
    if x_labels:
        x_labels[0]["anchor"] = "start" if count > 1 else "middle"
        if count > 1 and (count - 1) % every:
            # The last point always gets a label; drop the one before if they would touch.
            if (count - 1) % every < every * 0.6:
                x_labels.pop()
            x_labels.append({"x": x_at(count - 1), "text": points[-1]["label"], "anchor": "end"})
        elif count > 1:
            x_labels[-1]["anchor"] = "end"
    # Hover columns: each point owns the strip halfway to its neighbours.
    columns = []
    for i, point in enumerate(points):
        x = x_at(i)
        x0 = left if i == 0 else (x + x_at(i - 1)) / 2
        x1 = left + plot_w if i == count - 1 else (x + x_at(i + 1)) / 2
        y = y_at(point["value"]) if point.get("value") is not None else None
        columns.append({"x": x, "x0": x0, "w": max(x1 - x0, 1), "y": y, "tip": point.get("tip", {})})
    last = points[-1] if points else None
    end = None
    if last and last.get("value") is not None:
        end = {"x": x_at(count - 1), "y": y_at(last["value"])}
    return {
        "width": width, "height": height, "left": left, "right": width - right, "top": top_pad,
        "bottom": top_pad + plot_h, "main": main, "area": area,
        "compare": path("compare") if compare else "", "avg": path("avg") if average else "",
        "second": path("second") if second else "",
        "y_ticks": [{"y": y_at(t), "text": fmt(kind, t, short=True)} for t in ticks],
        "x_labels": x_labels, "columns": columns, "end": end,
    }


def line_charts(points, kind="money", compare=False, average=False, second=False):
    """The same chart twice: wide for computers, compact for phones (CSS shows one).
    An SVG scales its text with it, so one drawing can't be readable at both sizes."""
    return {"wide": line_chart(points, kind, compare=compare, average=average, second=second),
            "narrow": line_chart(points, kind, width=360, height=240, compare=compare, average=average,
                                 max_labels=4, second=second)}


def sparkline(values, width=100, height=28):
    """A tiny line for a KPI tile: no axes, just the shape (and the last point)."""
    clean = [v for v in values if v is not None]
    if len(clean) < 2:
        return None
    low, high = min(clean), max(clean)
    span = high - low or 1
    count = len(values)
    parts, pen_down, last = [], False, None
    for index, value in enumerate(values):
        if value is None:
            pen_down = False
            continue
        x = width * index / (count - 1)
        y = 3 + (height - 6) * (1 - (value - low) / span)
        parts.append(f"{'L' if pen_down else 'M'}{x:.1f},{y:.1f}")
        pen_down, last = True, (x, y)
    return {"d": " ".join(parts), "width": width, "height": height, "end": last}


def bars(rows, value_key="revenue", kind="money", label_key="name"):
    """Horizontal bars as HTML rows, each with its width in percent of the biggest."""
    biggest = max([r.get(value_key) or 0 for r in rows] or [0])
    out = []
    for row in rows:
        value = row.get(value_key) or 0
        share = value / biggest * 100 if biggest > 0 else 0
        out.append({**row, "label": row.get(label_key), "value": value, "text": fmt(kind, value),
                    "width": round(max(share, 0.6 if value else 0), 2), "half": round(share / 2, 2)})
    return out


def heat_level(value, biggest, levels=5):
    """0 (nothing) to levels: which step of the sequential ramp a heatmap cell takes."""
    if not value or not biggest:
        return 0
    return max(1, min(levels, math.ceil(value / biggest * levels)))
