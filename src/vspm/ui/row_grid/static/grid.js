/* VS Production Manager - editable table of the production rows (Streamlit custom component).
 *
 * Typing, ⬇ fill, adding and deleting rows happen here in the browser only; the table is sent to the
 * server once, when the save button is pressed. Rows already printed are read-only.
 */
(function () {
  "use strict";

  const Streamlit = {
    send(type, data) { window.parent.postMessage(Object.assign({ isStreamlitMessage: true, type: type }, data), "*"); },
    ready() { this.send("streamlit:componentReady", { apiVersion: 1 }); },
    // The content's own height: the page's scrollHeight is never smaller than the frame itself, so using
    // it made the frame a little taller on every resize (an endless empty scroll area).
    setHeight() {
      const root = document.getElementById("root");
      const h = Math.ceil(root.getBoundingClientRect().height) + 6;
      if (h !== this._h) { this._h = h; this.send("streamlit:setFrameHeight", { height: h }); }
    },
    setValue(value) { this.send("streamlit:setComponentValue", { value: value, dataType: "json" }); },
  };

  let args = { rows: [], columns: [], texts: {}, version: "" };
  let version = null;
  let rows = [];      // working copy
  let deleted = [];   // ids of saved rows deleted here
  let dirty = false;
  let clicks = 0;
  let newKey = 0;

  function el(tag, attrs, text) {
    const e = document.createElement(tag);
    Object.keys(attrs || {}).forEach((k) => {
      if (k === "class") e.className = attrs[k];
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), attrs[k]);
      else e.setAttribute(k, attrs[k]);
    });
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }

  const tx = (k) => args.texts[k] || k;

  function markDirty() {
    if (!dirty) {
      dirty = true;
      const s = document.getElementById("state");
      if (s) { s.textContent = tx("unsaved"); s.className = "changed"; }
    }
  }

  function editable(r) { return !r.printed; }

  function setCell(r, key, value, input) {
    r[key] = value;
    if (input) input.value = value;
    markDirty();
  }

  function fill(colKey) {
    const first = rows[0];
    if (!first) return;
    const value = first[colKey] == null ? "" : String(first[colKey]);
    rows.forEach((r, i) => {
      if (i === 0 || !editable(r)) return;
      if ((r[colKey] == null ? "" : String(r[colKey])) !== value) {
        const input = document.querySelector(`[data-row="${i}"][data-col="${colKey}"]`);
        setCell(r, colKey, value, input);
      }
    });
  }

  function focusBelow(i, colKey) {
    const next = document.querySelector(`[data-row="${i + 1}"][data-col="${colKey}"]`);
    if (next) { next.focus(); if (next.select) next.select(); }
  }

  function inputFor(r, i, c) {
    const value = r[c.key] == null ? "" : String(r[c.key]);
    let input;
    if (c.options) {
      input = el("select", {});
      [""].concat(c.options).forEach((o) => {
        const opt = el("option", { value: o }, o);
        if (o === value) opt.selected = true;
        input.appendChild(opt);
      });
      input.addEventListener("change", () => setCell(r, c.key, input.value, null));
    } else {
      input = el("input", { type: "text" });
      input.value = value;
      input.addEventListener("input", () => setCell(r, c.key, input.value, null));
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); focusBelow(i, c.key); } });
    }
    input.setAttribute("data-row", String(i));
    input.setAttribute("data-col", c.key);
    if (c.width) input.style.minWidth = c.width + "px";
    if (!editable(r)) input.disabled = true;
    return input;
  }

  function render() {
    const root = document.getElementById("root");
    root.innerHTML = "";
    const wrap = el("div", { class: "wrap" });
    const table = el("table", {});
    const head = el("tr", {});
    head.appendChild(el("th", {}, "#"));
    head.appendChild(el("th", {}, "⚠"));
    args.columns.forEach((c) => head.appendChild(el("th", {}, c.label)));
    head.appendChild(el("th", {}, tx("barcode")));
    head.appendChild(el("th", {}, ""));
    table.appendChild(head);

    rows.forEach((r, i) => {
      const tr = el("tr", { class: r.printed ? "printed" : "" });
      tr.appendChild(el("td", { class: "num" }, r["#"] == null ? "+" : String(r["#"])));
      tr.appendChild(el("td", { class: "flags", title: r.flags || "" }, r.flags || ""));
      args.columns.forEach((c) => {
        const td = el("td", {});
        const cell = el("div", { class: "cell" });
        cell.appendChild(inputFor(r, i, c));
        if (i === 0 && c.fill && rows.length > 1) {
          cell.appendChild(el("button", { class: "fill", title: tx("fill_tip"), "data-fill": c.key,
                                          onclick: () => fill(c.key) }, "⬇"));
        }
        td.appendChild(cell);
        tr.appendChild(td);
      });
      tr.appendChild(el("td", {}, r.barcode || ""));
      const del = el("td", {});
      if (editable(r)) {
        del.appendChild(el("button", { class: "del", title: tx("delete"), onclick: () => {
          if (r.id != null) deleted.push(r.id);
          rows.splice(i, 1);
          markDirty();
          render();
        } }, "✕"));
      }
      tr.appendChild(del);
      table.appendChild(tr);
    });
    wrap.appendChild(table);
    root.appendChild(wrap);

    const bar = el("div", { class: "bar" });
    bar.appendChild(el("button", { onclick: () => {
      const r = { id: null, "#": null, printed: false, flags: "", barcode: "", _new: ++newKey };
      args.columns.forEach((c) => { r[c.key] = ""; });
      rows.push(r);
      markDirty();
      render();
      const inp = document.querySelector(`[data-row="${rows.length - 1}"]`);
      if (inp) inp.focus();
    } }, "+ " + tx("add")));
    bar.appendChild(el("span", { id: "state", class: dirty ? "changed" : "" }, dirty ? tx("unsaved") : tx("saved_hint")));
    root.appendChild(bar);
    root.appendChild(el("button", { class: "primary", "data-testid": "grid-save", onclick: () => {
      clicks += 1;
      Streamlit.setValue({ action: "save", click: clicks, version: version, deleted: deleted.slice(),
                           rows: rows.map((r) => { const o = Object.assign({}, r); delete o._new; return o; }) });
    } }, tx("save")));
    Streamlit.setHeight();
  }

  // Colours of the app's own Streamlit theme (light or dark), so the table looks like the rest of the page.
  function applyTheme(theme) {
    const dark = !!(theme && theme.base === "dark");
    document.body.classList.toggle("dark", dark);
    if (!theme) return;
    const st = document.documentElement.style;
    const set = (name, v) => { if (v) st.setProperty(name, v); };
    set("--bg", theme.backgroundColor);
    set("--fg", theme.textColor);
    set("--head", theme.secondaryBackgroundColor);
    set("--input", theme.backgroundColor);
    set("--accent", theme.primaryColor);
    if (theme.font) document.body.style.fontFamily = theme.font;
  }

  function onRender(event) {
    const data = event.data;
    if (!data || data.type !== "streamlit:render") return;
    args = data.args || args;
    applyTheme(data.theme);
    if (args.version !== version) {  // new document, or saved on the server: start from its rows
      version = args.version;
      rows = (args.rows || []).map((r) => Object.assign({}, r));
      deleted = [];
      dirty = false;
      render();
    } else {
      Streamlit.setHeight();
    }
  }

  window.addEventListener("message", onRender);
  window.addEventListener("resize", () => Streamlit.setHeight());
  Streamlit.ready();
})();
