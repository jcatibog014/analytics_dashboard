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

document.addEventListener("DOMContentLoaded", () => {
  setUpTheme();
  setUpMenu();
  setUpPopovers();
  setUpConfirm();
  setUpAutoSubmit();
});
