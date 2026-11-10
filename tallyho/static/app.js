// Extras only: every page works without this file.

// The Appearance choice on the Settings page. theme.js applies it on every page.
function setUpTheme() {
  const section = document.querySelector("[data-theme-settings]");
  if (!section) return;
  const root = document.documentElement;
  section.hidden = false;
  section.querySelectorAll('input[name="theme"]').forEach((choice) => {
    choice.checked = choice.value === (root.dataset.theme || "system");
    choice.addEventListener("change", () => {
      try {
        if (choice.value === "system") {
          delete root.dataset.theme;
          localStorage.removeItem("tallyho-theme");
        } else {
          root.dataset.theme = choice.value;
          localStorage.setItem("tallyho-theme", choice.value);
        }
      } catch (error) {
        // Storage can be blocked. The theme still changes for this page.
      }
    });
  });
}

// The ☰ button on phones.
function setUpMenu() {
  const button = document.querySelector("[data-menu-button]");
  const menu = document.getElementById(button && button.getAttribute("aria-controls"));
  if (!button || !menu) return;
  button.hidden = false;
  const setOpen = (open) => {
    button.setAttribute("aria-expanded", open);
    menu.classList.toggle("is-open", open);
  };
  button.addEventListener("click", () => setOpen(button.getAttribute("aria-expanded") !== "true"));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setOpen(false);
  });
}

// <details> menus close on an outside click or Escape.
function setUpPopovers() {
  const menus = () => document.querySelectorAll("details[data-popover][open]");
  document.addEventListener("click", (event) => {
    menus().forEach((menu) => {
      if (!menu.contains(event.target)) menu.open = false;
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    menus().forEach((menu) => {
      menu.open = false;
      menu.querySelector("summary").focus();
    });
  });
}

function setUpConfirm() {
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!confirm(form.dataset.confirm)) event.preventDefault();
    });
  });
}

// A select that saves itself (a filter, a date range): no Apply button needed.
function setUpAutoSubmit() {
  document.querySelectorAll("[data-auto-submit]").forEach((control) => {
    const button = control.form.querySelector("[data-auto-submit-button]");
    if (button) button.hidden = true;
    control.addEventListener("change", () => control.form.requestSubmit());
  });
}

// Import: drop a file on the box instead of clicking "Choose a file".
function setUpDropZone() {
  document.querySelectorAll(".drop-zone").forEach((zone) => {
    const input = zone.querySelector('input[type="file"]');
    ["dragenter", "dragover"].forEach((name) =>
      zone.addEventListener(name, (event) => {
        event.preventDefault();
        zone.classList.add("is-over");
      }));
    ["dragleave", "drop"].forEach((name) => zone.addEventListener(name, () => zone.classList.remove("is-over")));
    zone.addEventListener("drop", (event) => {
      event.preventDefault();
      if (event.dataTransfer.files.length) input.files = event.dataTransfer.files;
    });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  setUpTheme();
  setUpDropZone();
  setUpMenu();
  setUpPopovers();
  setUpConfirm();
  setUpAutoSubmit();
});

// The range bar: picking "Custom dates" shows the two date boxes instead of
// reloading; changing a date picks "Custom dates" and applies it.
function setUpRangeBar() {
  document.querySelectorAll("[data-range-bar]").forEach((form) => {
    const select = form.querySelector("[data-range-select]");
    const dates = form.querySelector("[data-custom-dates]");
    const [from, to] = dates.querySelectorAll("input");
    select.addEventListener("change", () => {
      if (select.value === "custom") {
        dates.classList.remove("is-folded");
        from.focus();
      } else {
        form.requestSubmit();
      }
    });
    [from, to].forEach((input) =>
      input.addEventListener("change", () => {
        select.value = "custom";
        if (from.value && to.value) form.requestSubmit();
      }));
  });
}
document.addEventListener("DOMContentLoaded", setUpRangeBar);

// Reports: the Print button (without JavaScript, the browser's own Print works the same).
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-print]").forEach((button) => {
    button.hidden = false;
    button.addEventListener("click", () => window.print());
  });
});

