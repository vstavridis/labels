/*
 * VS Production Manager — DYMO browser bridge (Streamlit custom component).
 *
 * Runs in the browser of the employee's Windows PC. Uses DYMO's official
 * dymo.connect.framework.js to talk to the DYMO Web Service installed on THIS
 * computer (https://127.0.0.1:41951…41960) and prints on the LabelWriter
 * attached to THIS computer. The cloud-hosted Python app never touches a printer.
 *
 * Safety rules enforced here, independently of the server:
 *  - a label is printed only if its SHA-256 equals the hash the server prepared,
 *    every barcode object carries exactly the reserved barcode, nothing is linked
 *    to a DYMO COUNTER, and any counter object would print the same text;
 *  - a job key is recorded in localStorage BEFORE the print is submitted, so a
 *    reload, a second render or a second tab never submits it again;
 *  - results are kept in an outbox until the server acknowledges them.
 */

declare const dymo: any;

// ---------------------------------------------------------------------------
// Types shared with Python (src/vspm/ui/dymo_bridge/__init__.py)
// ---------------------------------------------------------------------------
interface Job {
  job_key: string;
  kind: string; // initial | retry | reprint | test
  label_xml: string;
  expected_barcode: string;
  expected_sha256: string;
  template_key: string;
  title: string;
}

interface Args {
  mode: "diagnose" | "print";
  lang: "el" | "en";
  job: Job | null;
  acked: string[];
  rescan_token?: string;
}

interface JobResult {
  job_key: string;
  status: "submitted" | "failed" | "not_submitted" | "rejected" | "uncertain";
  barcode: string;
  printer: string;
  station_id: string;
  station_name: string;
  xml_sha256: string;
  problems: string[];
  error: string;
  at: string;
}

interface Printer {
  name: string;
  modelName: string;
  isConnected: boolean;
  isLocal: boolean;
  printerType: string;
}

interface Diagnostics {
  framework_loaded: boolean;
  service_present: boolean;
  browser_supported: boolean;
  error_details: string;
  printers: Printer[];
  selected_printer: string;
  lna_permission: string;
  secure_context: boolean;
  top_same_origin: boolean | null;
  user_agent: string;
  origin: string;
  checked_at: string;
  errors: string[];
}

// ---------------------------------------------------------------------------
// Texts (Greek default)
// ---------------------------------------------------------------------------
const TEXT: Record<string, [string, string]> = {
  checking: ["Έλεγχος υπηρεσίας DYMO σε αυτόν τον υπολογιστή…", "Checking the DYMO service on this computer…"],
  service_ok: ["Υπηρεσία DYMO: βρέθηκε", "DYMO service: found"],
  service_missing: ["Υπηρεσία DYMO: ΔΕΝ βρέθηκε σε αυτόν τον υπολογιστή", "DYMO service: NOT found on this computer"],
  framework_missing: ["Δεν φορτώθηκε το DYMO framework", "DYMO framework did not load"],
  printers: ["Εκτυπωτής DYMO αυτού του υπολογιστή", "DYMO printer of this computer"],
  no_printers: ["Δεν βρέθηκε συνδεδεμένος εκτυπωτής DYMO", "No connected DYMO printer found"],
  station: ["Όνομα θέσης (π.χ. ΓΡΑΦΕΙΟ)", "Station name (e.g. OFFICE)"],
  recheck: ["Επανέλεγχος", "Check again"],
  print_now: ["Εκτύπωση τώρα", "Print now"],
  retry: ["Δοκιμή ξανά", "Try again"],
  lna_denied: [
    "Ο browser δεν επιτρέπει σε αυτή τη σελίδα να επικοινωνήσει με την υπηρεσία DYMO του υπολογιστή. " +
      "Πατήστε το εικονίδιο αριστερά της διεύθυνσης → Ρυθμίσεις ιστότοπου → «Πρόσβαση τοπικού δικτύου» / " +
      "«Εφαρμογές σε αυτή τη συσκευή» → Να επιτρέπεται, και μετά «Επανέλεγχος».",
    "The browser blocks this page from contacting the DYMO service of this computer. Click the icon left of the " +
      "address → Site settings → 'Local network access' / 'Apps on this device' → Allow, then 'Check again'.",
  ],
  lna_prompt: [
    "Αν ο browser ρωτήσει αν θα επιτρέψει πρόσβαση σε εφαρμογές/συσκευές αυτού του υπολογιστή, επιλέξτε «Να επιτρέπεται».",
    "If the browser asks whether to allow access to apps/devices on this computer, choose 'Allow'.",
  ],
  service_hint: [
    "Ελέγξτε ότι είναι εγκατεστημένο το DYMO Label v8 και ότι τρέχει η υπηρεσία DYMO (εικονίδιο DYMO Web Service " +
      "στη γραμμή εργασιών). Δοκιμή: άνοιγμα https://127.0.0.1:41951/DYMO/DLS/Printing/StatusConnected σε νέα " +
      "καρτέλα — πρέπει να εμφανίζει true.",
    "Check that DYMO Label v8 is installed and the DYMO Web Service is running (DYMO icon in the task bar). " +
      "Test: open https://127.0.0.1:41951/DYMO/DLS/Printing/StatusConnected in a new tab — it must show true.",
  ],
  job: ["Ετικέτα", "Label"],
  printing: ["Αποστολή στον εκτυπωτή…", "Sending to the printer…"],
  submitted: ["Στάλθηκε στον εκτυπωτή — ελέγξτε ότι βγήκε σωστά", "Sent to the printer — check that it came out correctly"],
  failed: ["Η αποστολή απέτυχε", "Sending failed"],
  not_submitted: ["Δεν στάλθηκε", "Not sent"],
  rejected: ["ΑΠΟΡΡΙΦΘΗΚΕ — η ετικέτα δεν πέρασε τον έλεγχο ασφαλείας και ΔΕΝ τυπώθηκε",
    "REJECTED — the label failed the safety check and was NOT printed"],
  uncertain: ["Άγνωστο αν τυπώθηκε (η σελίδα ανανεώθηκε κατά την αποστολή) — ελέγξτε τον εκτυπωτή",
    "Unknown whether it printed (the page reloaded while sending) — check the printer"],
  already: ["Αυτή η εργασία έχει ήδη σταλεί από αυτόν τον browser — δεν ξαναστέλνεται",
    "This job was already sent from this browser — it is not sent again"],
  choose_printer: ["Επιλέξτε εκτυπωτή", "Choose a printer"],
  errors: ["Σφάλματα browser", "Browser errors"],
};
let LANG: "el" | "en" = "el";
const t = (k: string): string => (TEXT[k] ? TEXT[k][LANG === "el" ? 0 : 1] : k);

// ---------------------------------------------------------------------------
// Streamlit component protocol (same messages as streamlit-component-lib)
// ---------------------------------------------------------------------------
const Streamlit = {
  send(type: string, data: Record<string, unknown>): void {
    window.parent.postMessage({ isStreamlitMessage: true, type, ...data }, "*");
  },
  ready(): void {
    this.send("streamlit:componentReady", { apiVersion: 1 });
  },
  setHeight(): void {
    const h = Math.ceil(document.body.getBoundingClientRect().height) + 8;
    this.send("streamlit:setFrameHeight", { height: h });
  },
  setValue(value: unknown): void {
    this.send("streamlit:setComponentValue", { value, dataType: "json" });
  },
};

// ---------------------------------------------------------------------------
// Storage (per browser profile, survives reloads; failures tolerated)
// ---------------------------------------------------------------------------
const store = {
  get<T>(key: string, fallback: T): T {
    try {
      const v = window.localStorage.getItem("vspm." + key);
      return v === null ? fallback : (JSON.parse(v) as T);
    } catch (e) {
      return fallback;
    }
  },
  set(key: string, value: unknown): boolean {
    try {
      window.localStorage.setItem("vspm." + key, JSON.stringify(value));
      return true;
    } catch (e) {
      return false;
    }
  },
};

function uuid(): string {
  try {
    if (window.crypto && "randomUUID" in window.crypto) return (window.crypto as any).randomUUID();
  } catch (e) { /* fall through */ }
  return "x" + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
}

function station(): { id: string; name: string } {
  let s = store.get<{ id: string; name: string } | null>("station", null);
  if (!s || !s.id) {
    s = { id: uuid(), name: "" };
    store.set("station", s);
  }
  return s;
}

const errors: string[] = store.get<string[]>("errors", []);
function logError(msg: string): void {
  const line = new Date().toISOString().slice(0, 19).replace("T", " ") + "  " + msg;
  errors.unshift(line);
  errors.length = Math.min(errors.length, 30);
  store.set("errors", errors);
}

// ---------------------------------------------------------------------------
// SHA-256 (WebCrypto when available, small fallback otherwise)
// ---------------------------------------------------------------------------
function sha256Fallback(bytes: Uint8Array): string {
  const K = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98,
    0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
    0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8,
    0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819,
    0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
    0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
    0xc67178f2]);
  const H = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
  const l = bytes.length;
  const withPad = ((l + 9 + 63) >> 6) << 6;
  const m = new Uint8Array(withPad);
  m.set(bytes);
  m[l] = 0x80;
  const bitLen = l * 8;
  const dv = new DataView(m.buffer);
  dv.setUint32(withPad - 4, bitLen >>> 0);
  dv.setUint32(withPad - 8, Math.floor(bitLen / 0x100000000));
  const w = new Uint32Array(64);
  const rotr = (x: number, n: number) => (x >>> n) | (x << (32 - n));
  for (let off = 0; off < withPad; off += 64) {
    for (let i = 0; i < 16; i++) w[i] = dv.getUint32(off + i * 4);
    for (let i = 16; i < 64; i++) {
      const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
      const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
    }
    let [a, b, c, d, e, f, g, h] = Array.from(H);
    for (let i = 0; i < 64; i++) {
      const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const t1 = (h + S1 + ch + K[i] + w[i]) >>> 0;
      const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const mj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (S0 + mj) >>> 0;
      h = g; g = f; f = e; e = (d + t1) >>> 0; d = c; c = b; b = a; a = (t1 + t2) >>> 0;
    }
    H[0] = (H[0] + a) >>> 0; H[1] = (H[1] + b) >>> 0; H[2] = (H[2] + c) >>> 0; H[3] = (H[3] + d) >>> 0;
    H[4] = (H[4] + e) >>> 0; H[5] = (H[5] + f) >>> 0; H[6] = (H[6] + g) >>> 0; H[7] = (H[7] + h) >>> 0;
  }
  return Array.from(H).map((x) => x.toString(16).padStart(8, "0")).join("");
}

async function sha256Hex(text: string): Promise<string> {
  const bytes = new TextEncoder().encode(text);
  try {
    if (window.crypto && window.crypto.subtle) {
      const d = await window.crypto.subtle.digest("SHA-256", bytes);
      return Array.from(new Uint8Array(d)).map((x) => x.toString(16).padStart(2, "0")).join("");
    }
  } catch (e) { /* fall back */ }
  return sha256Fallback(bytes);
}

// ---------------------------------------------------------------------------
// Label safety check (mirrors vspm.dymo.template.check_prepared_label)
// ---------------------------------------------------------------------------
const CODE39 = /^[0-9A-Z\-. $/+%]+$/;

function childText(el: Element, name: string): string {
  for (const c of Array.from(el.children)) if (c.tagName === name) return (c.textContent || "");
  return "";
}

function counterText(c: Element): string {
  const pre = childText(c, "PreText");
  const post = childText(c, "PostText");
  const cur = childText(c, "Current").trim();
  const width = parseInt(childText(c, "FormatWidth") || "0", 10) || 0;
  const zeros = childText(c, "UseLeadingZeros").trim().toLowerCase() === "true";
  const digits = zeros && width > cur.length ? cur.padStart(width, "0") : cur;
  return pre + digits + post;
}

async function checkLabel(job: Job): Promise<{ problems: string[]; sha: string }> {
  const problems: string[] = [];
  const sha = await sha256Hex(job.label_xml);
  if (sha !== job.expected_sha256) problems.push("label content differs from what the server prepared (SHA-256)");
  if (!CODE39.test(job.expected_barcode)) problems.push("barcode is not valid Code 39 text");
  const doc = new DOMParser().parseFromString(job.label_xml, "text/xml");
  if (doc.getElementsByTagName("parsererror").length) {
    problems.push("label XML cannot be read");
    return { problems, sha };
  }
  if (doc.documentElement.tagName !== "DieCutLabel") problems.push("not a DYMO Label v8 (DieCutLabel) file");
  const counters = Array.from(doc.getElementsByTagName("CounterObject"));
  const counterNames = counters.map((c) => childText(c, "Name"));
  const barcodes = Array.from(doc.getElementsByTagName("BarcodeObject"));
  if (barcodes.length === 0) problems.push("label has no barcode object");
  for (const b of barcodes) {
    const text = childText(b, "Text");
    if (text !== job.expected_barcode) problems.push(`barcode object prints '${text}' instead of '${job.expected_barcode}'`);
    if (childText(b, "LinkedObjectName").trim()) problems.push("barcode is linked to another object (DYMO counter)");
  }
  for (const el of Array.from(doc.getElementsByTagName("LinkedObjectName"))) {
    const target = (el.textContent || "").trim();
    if (target && counterNames.indexOf(target) >= 0) problems.push(`an object is linked to the DYMO counter '${target}'`);
  }
  for (const c of counters) {
    const txt = counterText(c);
    if (txt !== job.expected_barcode) problems.push(`counter object would print '${txt}' instead of '${job.expected_barcode}'`);
  }
  return { problems, sha };
}

// ---------------------------------------------------------------------------
// DYMO service, printers
// ---------------------------------------------------------------------------
let frameworkLoaded = false;
let initDone = false;
let diag: Diagnostics = emptyDiagnostics();

function emptyDiagnostics(): Diagnostics {
  let topSame: boolean | null = null;
  try {
    topSame = !!(window.top && window.top.location && window.top.location.href);
  } catch (e) {
    topSame = false;
  }
  return {
    framework_loaded: false, service_present: false, browser_supported: false, error_details: "",
    printers: [], selected_printer: store.get<string>("printer", ""), lna_permission: "unknown",
    secure_context: !!window.isSecureContext, top_same_origin: topSame, user_agent: navigator.userAgent,
    origin: window.location.origin, checked_at: "", errors: errors.slice(0, 10),
  };
}

async function lnaPermission(): Promise<string> {
  const perms: any = (navigator as any).permissions;
  if (!perms || !perms.query) return "unsupported";
  for (const name of ["loopback-network", "local-network-access", "local-network"]) {
    try {
      const s = await perms.query({ name });
      return `${name}:${s.state}`;
    } catch (e) { /* try next name */ }
  }
  return "not-applicable";
}

function initFramework(): Promise<void> {
  return new Promise((resolve) => {
    try {
      frameworkLoaded = typeof dymo !== "undefined" && !!dymo.label && !!dymo.label.framework;
      if (!frameworkLoaded) {
        logError("dymo.connect.framework.js did not load");
        return resolve();
      }
      let done = false;
      const finish = () => { if (!done) { done = true; resolve(); } };
      dymo.label.framework.init(finish);
      setTimeout(finish, 15000);
    } catch (e) {
      logError("framework init: " + e);
      resolve();
    }
  });
}

async function detect(): Promise<Diagnostics> {
  diag = emptyDiagnostics();
  render();
  await initFramework();
  diag.framework_loaded = frameworkLoaded;
  diag.lna_permission = await lnaPermission();
  if (frameworkLoaded) {
    try {
      const env = dymo.label.framework.checkEnvironment();
      diag.browser_supported = !!env.isBrowserSupported;
      diag.service_present = !!env.isWebServicePresent;
      diag.error_details = env.isWebServicePresent ? "" : String(env.errorDetails || "");
    } catch (e) {
      diag.error_details = String(e);
    }
    if (diag.service_present) {
      try {
        const list: any[] = await dymo.label.framework.getPrintersAsync();
        diag.printers = Array.from(list || []).map((p: any) => ({
          name: String(p.name), modelName: String(p.modelName), isConnected: !!p.isConnected,
          isLocal: !!p.isLocal, printerType: String(p.printerType),
        }));
      } catch (e) {
        logError("getPrinters: " + e);
      }
    } else {
      logError("DYMO service not found" + (diag.error_details ? " (" + diag.error_details + ")" : ""));
    }
  }
  chooseDefaultPrinter();
  diag.checked_at = new Date().toISOString();
  diag.errors = errors.slice(0, 10);
  initDone = true;
  return diag;
}

function labelWriters(): Printer[] {
  return diag.printers.filter((p) => p.printerType === "LabelWriterPrinter");
}

function chooseDefaultPrinter(): void {
  const saved = store.get<string>("printer", "");
  const usable = labelWriters().filter((p) => p.isConnected);
  if (saved && usable.some((p) => p.name === saved)) {
    diag.selected_printer = saved;
  } else if (usable.length === 1) {
    diag.selected_printer = usable[0].name; // exactly one connected LabelWriter: use it
    store.set("printer", diag.selected_printer);
  } else {
    diag.selected_printer = saved && labelWriters().some((p) => p.name === saved) ? saved : "";
  }
}

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------
interface JobState { status: string; printer: string; at: string; result?: JobResult; }

let args: Args = { mode: "diagnose", lang: "el", job: null, acked: [] };
let currentJobView: { job: Job; state: string; message: string; problems: string[] } | null = null;
const inFlight: Record<string, boolean> = {};

function outbox(): JobResult[] {
  return store.get<JobResult[]>("outbox", []);
}

function pushResult(r: JobResult): void {
  const box = outbox().filter((x) => x.job_key !== r.job_key);
  box.push(r);
  store.set("outbox", box.slice(-50));
  // Also keep a per-job record so the job is never submitted again from this browser.
  store.set("job." + r.job_key, { status: r.status, printer: r.printer, at: r.at, result: r } as JobState);
}

function ackResults(acked: string[]): void {
  if (!acked || !acked.length) return;
  const box = outbox().filter((x) => acked.indexOf(x.job_key) < 0);
  store.set("outbox", box);
}

function makeResult(job: Job, status: JobResult["status"], extra: Partial<JobResult>): JobResult {
  const st = station();
  return {
    job_key: job.job_key, status, barcode: job.expected_barcode, printer: diag.selected_printer,
    station_id: st.id, station_name: st.name, xml_sha256: "", problems: [], error: "",
    at: new Date().toISOString(), ...extra,
  };
}

async function runJob(job: Job, userInitiated: boolean): Promise<void> {
  const key = job.job_key;
  if (inFlight[key]) return;
  const prev = store.get<JobState | null>("job." + key, null);
  if (prev && ["submitted", "failed", "rejected", "uncertain"].indexOf(prev.status) >= 0) {
    currentJobView = { job, state: prev.status, message: t("already"), problems: [] };
    if (prev.result && !outbox().some((r) => r.job_key === key) && args.acked.indexOf(key) < 0) pushResult(prev.result);
    return flush();
  }
  if (prev && prev.status === "printing") {
    // The page was reloaded while this job was being submitted: never resubmit automatically.
    const r = makeResult(job, "uncertain", { error: "page reloaded while the print request was being sent" });
    pushResult(r);
    currentJobView = { job, state: "uncertain", message: t("uncertain"), problems: [] };
    return flush();
  }
  if (!initDone) await detect();
  if (!diag.service_present || !diag.selected_printer) {
    const reason = !diag.service_present ? t("service_missing") : t("choose_printer");
    currentJobView = { job, state: "not_submitted", message: reason, problems: [] };
    pushResult(makeResult(job, "not_submitted", { error: reason }));
    return flush();
  }
  inFlight[key] = true;
  try {
    const chk = await checkLabel(job);
    if (chk.problems.length) {
      currentJobView = { job, state: "rejected", message: t("rejected"), problems: chk.problems };
      chk.problems.forEach((p) => logError(`${job.expected_barcode}: ${p}`));
      pushResult(makeResult(job, "rejected", { problems: chk.problems, xml_sha256: chk.sha }));
      return flush();
    }
    const doPrint = async () => {
      // Recorded BEFORE submitting: a reload during submission can never print twice.
      if (!store.set("job." + key, { status: "printing", printer: diag.selected_printer, at: new Date().toISOString() })) {
        throw new Error("browser storage unavailable: refusing to print without duplicate protection");
      }
      currentJobView = { job, state: "printing", message: t("printing"), problems: [] };
      render();
      const params = dymo.label.framework.createLabelWriterPrintParamsXml({ copies: 1, jobTitle: "VSPM " + job.expected_barcode });
      try {
        await dymo.label.framework.printLabelAsync(diag.selected_printer, params, job.label_xml, "");
        pushResult(makeResult(job, "submitted", { xml_sha256: chk.sha }));
        currentJobView = { job, state: "submitted", message: t("submitted"), problems: [] };
      } catch (e) {
        logError(`${job.expected_barcode}: print failed: ${e}`);
        pushResult(makeResult(job, "failed", { xml_sha256: chk.sha, error: String(e) }));
        currentJobView = { job, state: "failed", message: t("failed") + ": " + e, problems: [] };
      }
    };
    const locks: any = (navigator as any).locks;
    if (locks && locks.request) {
      await locks.request("vspm-job-" + key, { ifAvailable: true }, async (lock: any) => {
        if (lock) await doPrint();
      });
    } else {
      await doPrint();
    }
  } catch (e) {
    logError("job " + key + ": " + e);
    currentJobView = { job, state: "not_submitted", message: String(e), problems: [] };
    pushResult(makeResult(job, "not_submitted", { error: String(e) }));
  } finally {
    delete inFlight[key];
    void userInitiated;
  }
  return flush();
}

// ---------------------------------------------------------------------------
// Report to Python (only when something changed, to avoid rerun loops)
// ---------------------------------------------------------------------------
let lastSent = "";
function flush(): void {
  render();
  const box = outbox().filter((r) => args.acked.indexOf(r.job_key) < 0);
  const st = station();
  const payload = {
    station_id: st.id, station_name: st.name,
    diagnostics: initDone ? { ...diag, errors: errors.slice(0, 10) } : null,
    results: box,
  };
  const key = JSON.stringify({ ...payload, diagnostics: payload.diagnostics ? { ...payload.diagnostics, checked_at: "" } : null });
  if (key === lastSent) return;
  lastSent = key;
  Streamlit.setValue({ ...payload, report_id: uuid() });
}

// ---------------------------------------------------------------------------
// UI
// ---------------------------------------------------------------------------
function el(tag: string, attrs: Record<string, string> = {}, text = ""): HTMLElement {
  const e = document.createElement(tag);
  for (const k of Object.keys(attrs)) e.setAttribute(k, attrs[k]);
  if (text) e.textContent = text;
  return e;
}

function render(): void {
  const root = document.getElementById("root")!;
  root.innerHTML = "";
  const status = el("div", { class: "status" });
  if (!initDone) {
    status.appendChild(el("div", { class: "line wait" }, t("checking")));
  } else if (!diag.framework_loaded) {
    status.appendChild(el("div", { class: "line bad", "data-testid": "service" }, "✖ " + t("framework_missing")));
  } else {
    const ok = diag.service_present;
    status.appendChild(el("div", { class: "line " + (ok ? "ok" : "bad"), "data-testid": "service" },
      (ok ? "✔ " : "✖ ") + (ok ? t("service_ok") : t("service_missing"))));
    if (!ok) {
      const denied = diag.lna_permission.indexOf("denied") >= 0;
      const prompt = diag.lna_permission.indexOf("prompt") >= 0;
      status.appendChild(el("div", { class: "hint" }, denied ? t("lna_denied") : t("service_hint")));
      if (prompt) status.appendChild(el("div", { class: "hint" }, t("lna_prompt")));
    }
  }
  root.appendChild(status);

  if (initDone && diag.service_present) {
    const row = el("div", { class: "row" });
    row.appendChild(el("label", { for: "printer" }, t("printers") + ":"));
    const sel = el("select", { id: "printer", "data-testid": "printer-select" }) as HTMLSelectElement;
    const lws = labelWriters();
    if (!lws.length) {
      sel.appendChild(el("option", { value: "" }, t("no_printers")));
    } else {
      sel.appendChild(el("option", { value: "" }, "— " + t("choose_printer") + " —"));
      for (const p of lws) {
        const o = el("option", { value: p.name }, `${p.name}${p.isConnected ? "" : " (δεν είναι συνδεδεμένος)"}`) as HTMLOptionElement;
        if (p.name === diag.selected_printer) o.selected = true;
        sel.appendChild(o);
      }
    }
    sel.onchange = () => {
      diag.selected_printer = sel.value;
      store.set("printer", sel.value);
      flush();
    };
    row.appendChild(sel);
    root.appendChild(row);
  }

  if (args.mode === "diagnose") {
    const row = el("div", { class: "row" });
    row.appendChild(el("label", { for: "station" }, t("station") + ":"));
    const inp = el("input", { id: "station", "data-testid": "station-name", maxlength: "40" }) as HTMLInputElement;
    inp.value = station().name;
    inp.onchange = () => {
      const s = station();
      s.name = inp.value.trim();
      store.set("station", s);
      flush();
    };
    row.appendChild(inp);
    root.appendChild(row);
  }

  if (currentJobView) {
    const j = currentJobView;
    const box = el("div", { class: "job " + j.state, "data-testid": "job-status", "data-state": j.state });
    box.appendChild(el("div", { class: "jobtitle" }, `${t("job")}: ${j.job.title} — ${j.job.expected_barcode}`));
    box.appendChild(el("div", {}, j.message));
    for (const p of j.problems) box.appendChild(el("div", { class: "problem" }, "• " + p));
    root.appendChild(box);
  }

  const buttons = el("div", { class: "row" });
  const recheck = el("button", { "data-testid": "recheck" }, t("recheck")) as HTMLButtonElement;
  recheck.onclick = async () => { initDone = false; await detect(); flush(); };
  buttons.appendChild(recheck);
  if (args.job && currentJobView && currentJobView.state === "not_submitted") {
    const retry = el("button", { "data-testid": "retry", class: "primary" }, t("retry")) as HTMLButtonElement;
    retry.onclick = async () => {
      // not_submitted means nothing reached the printer, so trying again is safe.
      try { window.localStorage.removeItem("vspm.job." + args.job!.job_key); } catch (e) { /* ignore */ }
      currentJobView = null;
      initDone = false;
      await detect();
      await runJob(args.job!, true);
    };
    buttons.appendChild(retry);
  }
  root.appendChild(buttons);

  if (args.mode === "diagnose" && errors.length) {
    const det = el("details", {});
    det.appendChild(el("summary", {}, `${t("errors")} (${errors.length})`));
    const pre = el("pre", { "data-testid": "errors" }, errors.join("\n"));
    det.appendChild(pre);
    root.appendChild(det);
  }
  Streamlit.setHeight();
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------
let started = false;
let lastJobKey = "";
let lastRescan = "";

async function onRender(event: MessageEvent): Promise<void> {
  const data = event.data;
  if (!data || data.type !== "streamlit:render") return;
  const a = (data.args || {}) as Partial<Args>;
  args = {
    mode: a.mode === "print" ? "print" : "diagnose",
    lang: a.lang === "en" ? "en" : "el",
    job: a.job || null,
    acked: Array.isArray(a.acked) ? a.acked : [],
    rescan_token: a.rescan_token || "",
  };
  LANG = args.lang;
  ackResults(args.acked);
  if (!started || (args.rescan_token && args.rescan_token !== lastRescan)) {
    started = true;
    lastRescan = args.rescan_token || "";
    initDone = false;
    await detect();
  }
  if (args.job && args.job.job_key !== lastJobKey) {
    lastJobKey = args.job.job_key;
    currentJobView = null;
    await runJob(args.job, false);
    return;
  }
  if (!args.job) currentJobView = null;
  flush();
}

window.addEventListener("message", (e) => { void onRender(e); });
window.addEventListener("resize", () => Streamlit.setHeight());
(window as any).__vspmBridge = { checkLabel, sha256Hex, counterText, sha256Fallback };
Streamlit.ready();
