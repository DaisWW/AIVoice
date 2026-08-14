export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;",
  })[character]);
}

export function multiline(value) {
  return escapeHtml(value).replace(/\r?\n/g, "<br>");
}

export function setPanelLoading(element, loading) {
  element.classList.toggle("is-loading", loading);
  element.setAttribute("aria-busy", String(loading));
}

export function setButtonBusy(button, busy) {
  button.disabled = busy;
  button.classList.toggle("is-busy", busy);
  button.setAttribute("aria-busy", String(busy));
}

export function renderSelect(select, items, placeholder, getLabel) {
  const current = select.value;
  const options = items.map((item) => ({ id: item.id, text: getLabel(item) }));
  const signature = JSON.stringify([placeholder, options]);
  if (select.dataset.optionsSignature === signature) return;
  select.innerHTML = [
    placeholder ? `<option value="">${escapeHtml(placeholder)}</option>` : "",
    ...options.map(
      (item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.text)}</option>`,
    ),
  ].join("");
  if (items.some((item) => item.id === current)) select.value = current;
  select.dataset.optionsSignature = signature;
}
