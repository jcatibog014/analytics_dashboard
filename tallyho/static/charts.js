// The charts' hover layer. Extras only: every value is also in each chart's table view.
// Names and values come from the page's data-* attributes and go in with textContent,
// never innerHTML, so a product name from a file can never become markup.

function tooltipRows(tooltip, label, rows) {
  tooltip.replaceChildren();
  const title = document.createElement("p");
  title.className = "tooltip-title";
  title.textContent = label;
  tooltip.append(title);
  rows.forEach(([key, name, value]) => {
    const row = document.createElement("p");
    row.className = "tooltip-row";
    const mark = document.createElement("span");
    mark.className = `key key-line key-${key}`;
    const strong = document.createElement("strong");
    strong.textContent = value;
    const small = document.createElement("span");
    small.textContent = name;
    row.append(mark, strong, small);
    tooltip.append(row);
  });
}

function placeTooltip(figure, tooltip, clientX, clientY) {
  const box = figure.getBoundingClientRect();
  tooltip.hidden = false;
  const width = tooltip.offsetWidth;
  let left = clientX - box.left + 14;
  if (left + width > box.width) left = clientX - box.left - width - 14;
  tooltip.style.left = `${Math.max(0, left)}px`;
  tooltip.style.top = `${Math.max(0, clientY - box.top - 10)}px`;
}

// Line charts: a crosshair snaps to the nearest day; the tooltip lists every series there.
function setUpLineChart(figure) {
  const svg = figure.querySelector("svg");
  const tooltip = figure.querySelector(".tooltip");
  const crosshair = svg.querySelector(".crosshair");
  const dot = svg.querySelector(".hover-dot");
  const hits = [...svg.querySelectorAll(".hit")];
  if (!hits.length) return;
  let current = -1;

  const show = (index, clientX, clientY) => {
    const hit = hits[index];
    if (!hit) return;
    current = index;
    const x = hit.dataset.x;
    crosshair.setAttribute("x1", x);
    crosshair.setAttribute("x2", x);
    crosshair.hidden = false;
    if (hit.dataset.y) {
      dot.setAttribute("cx", x);
      dot.setAttribute("cy", hit.dataset.y);
      dot.hidden = false;
    } else {
      dot.hidden = true;
    }
    const rows = (hit.dataset.rows || "").split(";").filter(Boolean).map((part) => part.split("|"));
    tooltipRows(tooltip, hit.dataset.label, rows);
    if (clientX === undefined) {
      const rect = hit.getBoundingClientRect();
      clientX = rect.left + rect.width / 2;
      clientY = rect.top + 20;
    }
    placeTooltip(figure, tooltip, clientX, clientY);
  };
  const hide = () => {
    crosshair.hidden = true;
    dot.hidden = true;
    tooltip.hidden = true;
    current = -1;
  };

  hits.forEach((hit, index) => {
    hit.addEventListener("pointermove", (event) => show(index, event.clientX, event.clientY));
  });
  svg.addEventListener("pointerleave", hide);
  // Keyboard: focus the chart, then the arrow keys walk through the days.
  svg.addEventListener("keydown", (event) => {
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault();
      const step = event.key === "ArrowRight" ? 1 : -1;
      show(Math.min(hits.length - 1, Math.max(0, (current < 0 ? hits.length : current) + step)));
    } else if (event.key === "Escape") {
      hide();
    }
  });
  svg.addEventListener("focus", () => show(hits.length - 1));
  svg.addEventListener("blur", hide);
}

// Bars and heatmap cells: each mark is its own hover and focus target.
function setUpMarks() {
  document.querySelectorAll("[data-tip-label]").forEach((mark) => {
    const figure = mark.closest(".chart, .card");
    if (!figure) return;
    let tooltip = figure.querySelector(":scope > .tooltip, :scope .mark-tooltip");
    if (!tooltip) {
      tooltip = document.createElement("div");
      tooltip.className = "tooltip mark-tooltip";
      tooltip.setAttribute("role", "status");
      tooltip.hidden = true;
      figure.classList.add("has-tooltip");
      figure.append(tooltip);
    }
    const show = (clientX, clientY) => {
      const rows = [["mark", mark.dataset.tipNote || "", mark.dataset.tipValue]];
      tooltipRows(tooltip, mark.dataset.tipLabel, rows);
      if (clientX === undefined) {
        const rect = mark.getBoundingClientRect();
        clientX = rect.left + rect.width / 2;
        clientY = rect.top;
      }
      placeTooltip(figure, tooltip, clientX, clientY);
    };
    mark.addEventListener("pointermove", (event) => show(event.clientX, event.clientY));
    mark.addEventListener("pointerleave", () => (tooltip.hidden = true));
    mark.addEventListener("focus", () => show());
    mark.addEventListener("blur", () => (tooltip.hidden = true));
  });
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll('[data-chart="line"]').forEach(setUpLineChart);
  setUpMarks();
});
