const API = "/api/v1";
const SESSION_TOKEN_KEY = "arrenda-demo-token";
const OPERATION_HISTORY_KEY = "arrenda-operation-history";

const state = {
  token: sessionStorage.getItem(SESSION_TOKEN_KEY) || "demo-operator-token",
  parties: [],
  rates: [],
  policies: [],
  operations: new Map(),
  selectedPolicyId: null,
  selectedDirectory: "parties",
  policyFilter: "",
  policySearch: "",
  addRiskPolicyId: null,
  renewPolicyId: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const pause = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}

function displayId(value) {
  const text = String(value || "");
  return text ? `${text.slice(0, 8)}…${text.slice(-4)}` : "—";
}

function idempotencyKey(kind) {
  const suffix = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `web-${kind}-${suffix}`;
}

function statusLabel(status) {
  return ({
    ACTIVE: "ACTIVA",
    CANCELLED: "CANCELADA",
    CURRENT: "VIGENTE",
    SCHEDULED: "PROGRAMADO",
    EXPIRED: "FINALIZADO",
    PENDING: "PENDIENTE",
    WAITING_INPUT: "FALTA IPC",
    PROCESSING: "PROCESANDO",
    RECONCILING: "CONSULTANDO CORE",
    SUCCEEDED: "CONFIRMADA",
    REJECTED: "RECHAZADA",
    NEEDS_REVIEW: "REVISIÓN",
  })[status] || status || "DESCONOCIDO";
}

function badgeClass(status) {
  if (["SUCCEEDED", "ACTIVE", "CURRENT"].includes(status)) return "";
  if (["PENDING", "WAITING_INPUT"].includes(status)) return "is-pending";
  if (["PROCESSING", "RECONCILING"].includes(status)) return "is-processing";
  if (["NEEDS_REVIEW", "REJECTED", "CANCELLED"].includes(status)) return "is-error";
  if (status === "SCHEDULED") return "is-scheduled";
  return "is-neutral";
}

class ApiError extends Error {
  constructor(status, body) {
    const detail = Array.isArray(body?.detail)
      ? body.detail.map((item) => item.msg).join("; ")
      : body?.detail || `HTTP ${status}`;
    super(detail);
    this.status = status;
    this.body = body;
  }
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  headers.set("Authorization", `Bearer ${state.token}`);
  let body = options.body;
  if (body && typeof body !== "string") {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(body);
  }
  const response = await fetch(`${API}${path}`, { ...options, headers, body });
  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!response.ok) throw new ApiError(response.status, data);
  return { data, response };
}

function toast(title, message = "", kind = "success") {
  const node = document.createElement("div");
  node.className = `toast ${kind === "error" ? "is-error" : ""}`;
  node.innerHTML = `<strong>${escapeHtml(title)}</strong><span>${escapeHtml(message)}</span>`;
  $("#toast-region").append(node);
  window.setTimeout(() => node.remove(), 5200);
}

function showError(error) {
  const status = error instanceof ApiError ? `${error.status} · ` : "";
  toast("No se pudo completar", `${status}${error.message || "Error de conexión"}`, "error");
}

function setApiStatus(online, message) {
  $("#api-status").textContent = message;
  $("#api-dot").classList.toggle("is-online", online);
  $("#api-dot").classList.toggle("is-offline", !online);
}

function renderSummary() {
  const individual = state.policies.filter((policy) => policy.policy_type === "INDIVIDUAL").length;
  const collective = state.policies.filter((policy) => policy.policy_type === "COLLECTIVE").length;
  $("#policy-count").textContent = state.policies.length;
  $("#individual-count").textContent = individual;
  $("#collective-count").textContent = collective;
  $("#operation-count").textContent = state.operations.size;
  $("#operations-pill").textContent = state.operations.size;
}

function filteredPolicies() {
  const search = state.policySearch.trim().toLowerCase();
  return state.policies.filter((policy) => {
    const byType = !state.policyFilter || policy.policy_type === state.policyFilter;
    const byText = !search || `${policy.policy_number} ${policy.policy_type} ${policy.status}`.toLowerCase().includes(search);
    return byType && byText;
  });
}

function renderPolicies() {
  const list = $("#policy-list");
  const policies = filteredPolicies();
  if (!policies.length) {
    list.innerHTML = '<div class="empty-list">No hay pólizas para este filtro.</div>';
    return;
  }
  list.innerHTML = policies.map((policy) => `
    <button class="policy-card ${state.selectedPolicyId === policy.id ? "is-selected" : ""}" type="button" data-policy-id="${escapeHtml(policy.id)}">
      <span class="policy-card-top">
        <span class="policy-number">${escapeHtml(policy.policy_number)}</span>
        <span class="badge ${badgeClass(policy.status)}">${escapeHtml(statusLabel(policy.status))}</span>
      </span>
      <span class="policy-card-meta"><span class="policy-type">${escapeHtml(policy.policy_type)}</span><span>${escapeHtml(displayId(policy.id))}</span></span>
    </button>
  `).join("");
  $$("[data-policy-id]", list).forEach((button) => {
    button.addEventListener("click", () => selectPolicy(button.dataset.policyId));
  });
}

function renderPartyOptions() {
  const options = ['<option value="">Selecciona una Party</option>'];
  for (const party of state.parties) {
    options.push(`<option value="${escapeHtml(party.id)}">${escapeHtml(party.display_name)} · ${escapeHtml(party.party_type)}</option>`);
  }
  $$(".party-select").forEach((select) => {
    const previous = select.value;
    select.innerHTML = options.join("");
    if (state.parties.some((party) => party.id === previous)) select.value = previous;
  });
}

function renderDirectory() {
  const content = $("#directory-content");
  if (state.selectedDirectory === "parties") {
    content.innerHTML = `
      <div class="directory-actions"><button class="button button-quiet button-small" type="button" id="directory-new-party">＋ Crear Party</button></div>
      <div class="directory-list">
        ${state.parties.length ? state.parties.map((party) => `
          <div class="directory-item"><div><strong>${escapeHtml(party.display_name)}</strong><small>${escapeHtml(party.document_reference)} · ${escapeHtml(party.party_type)}</small></div><button class="text-action" type="button" data-copy-party="${escapeHtml(party.id)}" title="Copiar ID">Copiar ID</button></div>
        `).join("") : '<div class="empty-list">Aún no hay Parties.</div>'}
      </div>
    `;
    $("#directory-new-party")?.addEventListener("click", () => openModal("party-modal"));
    $$('[data-copy-party]', content).forEach((button) => button.addEventListener("click", async () => {
      await navigator.clipboard?.writeText(button.dataset.copyParty);
      toast("ID copiado", displayId(button.dataset.copyParty));
    }));
    return;
  }
  content.innerHTML = `
    <div class="directory-actions"><button class="button button-quiet button-small" type="button" id="directory-new-rate">＋ Registrar IPC</button></div>
    <div class="directory-list">
      ${state.rates.length ? state.rates.map((rate) => `
        <div class="directory-item"><div><strong>${escapeHtml(rate.reference_period)} · ${(Number(rate.rate) * 100).toFixed(2)}%</strong><small>${escapeHtml(rate.source)} · ${escapeHtml(rate.effective_on)}</small></div><span class="badge ${rate.is_fictional ? "is-neutral" : ""}">${rate.is_fictional ? "FICTICIO" : "IPC"}</span></div>
      `).join("") : '<div class="empty-list">No hay tasas IPC registradas.</div>'}
    </div>
  `;
  $("#directory-new-rate")?.addEventListener("click", () => openModal("rate-modal"));
}

function renderDetail(detail, periods) {
  const panel = $("#policy-detail");
  const risks = detail.risks || [];
  panel.innerHTML = `
    <div class="detail-heading">
      <div><p class="eyebrow">${escapeHtml(detail.policy_type)} <span>/</span> ${escapeHtml(detail.policy_number)}</p><h2>${escapeHtml(detail.policy_number)}</h2><span class="detail-id">${escapeHtml(detail.id)}</span></div>
      <div class="detail-actions">
        <button class="button button-quiet button-small" type="button" id="detail-add-risk">＋ Riesgo</button>
        <button class="button button-rust button-small" type="button" id="detail-renew">Solicitar renovación</button>
      </div>
    </div>
    <div class="detail-meta">
      <div class="meta-cell"><span class="meta-label">ESTADO</span><span class="meta-value"><span class="badge ${badgeClass(detail.status)}">${escapeHtml(statusLabel(detail.status))}</span></span></div>
      <div class="meta-cell"><span class="meta-label">TIPO</span><span class="meta-value">${escapeHtml(detail.policy_type)}</span></div>
      <div class="meta-cell"><span class="meta-label">TOMADOR</span><span class="meta-value">${escapeHtml(displayId(detail.holder_party_id))}</span></div>
    </div>
    <form class="detail-notes" id="notes-form">
      <label><span class="field-label">Nota operativa</span><input class="text-input" name="notes" maxlength="500" value="${escapeHtml(detail.notes || "")}" placeholder="Añade una nota no contractual" /></label>
      <button class="button button-quiet button-small" type="submit">Guardar</button>
    </form>
    <div class="section-divider"><h3>Riesgos</h3><span class="section-count">${risks.length} registros</span></div>
    <div class="risk-list">
      ${risks.length ? risks.map((risk) => `
        <article class="risk-card"><div><p class="risk-address">${escapeHtml(risk.property_address)}</p><p class="risk-parties">Tenant ${escapeHtml(displayId(risk.tenant_party_id))} · Landlord ${escapeHtml(displayId(risk.landlord_party_id))}</p></div><div class="risk-tools"><span class="badge ${badgeClass(risk.status)}">${escapeHtml(statusLabel(risk.status))}</span>${risk.status === "ACTIVE" ? `<button class="text-action" type="button" data-cancel-risk="${escapeHtml(risk.id)}">Cancelar</button>` : ""}</div></article>
      `).join("") : '<div class="empty-list">No hay riesgos registrados.</div>'}
    </div>
    <div class="section-divider period-heading"><h3>Periodos</h3><span class="section-count">Historial contractual</span></div>
    ${periods.length ? `<div class="table-wrap"><table class="periods-table"><thead><tr><th>PERIODO</th><th>DESDE</th><th>HASTA</th><th>IPC</th><th>ESTADO</th></tr></thead><tbody>${periods.map((period) => `<tr><td>${period.sequence}</td><td>${escapeHtml(period.starts_on)}</td><td>${escapeHtml(period.ends_on)}</td><td>${period.ipc_rate_snapshot == null ? "—" : `${(Number(period.ipc_rate_snapshot) * 100).toFixed(2)}%`}</td><td><span class="badge ${badgeClass(period.status)}">${escapeHtml(statusLabel(period.status))}</span></td></tr>`).join("")}</tbody></table></div>` : '<div class="empty-list">Sin periodos confirmados todavía.</div>'}
  `;
  $("#detail-add-risk").addEventListener("click", () => openRiskModal(detail));
  $("#detail-renew").addEventListener("click", () => openRenewalModal(detail));
  $("#notes-form").addEventListener("submit", (event) => saveNotes(event, detail.id));
  $$('[data-cancel-risk]').forEach((button) => button.addEventListener("click", () => cancelRisk(detail.id, button.dataset.cancelRisk)));
}

async function loadParties() {
  const { data } = await api("/parties?limit=100&offset=0");
  state.parties = data.items || [];
  renderPartyOptions();
  renderDirectory();
}

async function loadRates() {
  const { data } = await api("/ipc-rates");
  state.rates = data || [];
  renderDirectory();
}

async function loadPolicies() {
  const query = new URLSearchParams({ limit: "100", offset: "0" });
  if (state.policyFilter) query.set("policy_type", state.policyFilter);
  const { data } = await api(`/policies?${query}`);
  state.policies = data.items || [];
  renderPolicies();
  renderSummary();
  if (state.selectedPolicyId && !state.policies.some((policy) => policy.id === state.selectedPolicyId)) {
    state.selectedPolicyId = null;
  }
}

async function loadAll() {
  setApiStatus(false, "Consultando servicios…");
  try {
    const [health] = await Promise.all([
      fetch("/health/ready").then((response) => {
        if (!response.ok) throw new Error(`health ${response.status}`);
        return response.json();
      }),
      loadParties(),
      loadRates(),
      loadPolicies(),
    ]);
    setApiStatus(true, `API y PostgreSQL listos · ${health.status}`);
  } catch (error) {
    setApiStatus(false, "No disponible · revisa Docker o el token");
    showError(error);
  }
}

async function selectPolicy(policyId) {
  state.selectedPolicyId = policyId;
  renderPolicies();
  $("#policy-detail").innerHTML = '<div class="loading-state">Cargando detalle…</div>';
  try {
    const [policyResponse, periodResponse] = await Promise.all([
      api(`/policies/${policyId}`),
      api(`/policies/${policyId}/periods`),
    ]);
    renderDetail(policyResponse.data, periodResponse.data || []);
  } catch (error) {
    $("#policy-detail").innerHTML = '<div class="empty-state"><p class="eyebrow">ERROR</p><h2>No se pudo abrir el expediente</h2><p class="api-error">' + escapeHtml(error.message) + "</p></div>";
    showError(error);
  }
}

function openModal(id) {
  const dialog = document.getElementById(id);
  if (dialog && !dialog.open) dialog.showModal();
}

function closeModal(id) {
  const dialog = document.getElementById(id);
  if (dialog?.open) dialog.close();
}

function fillPartySelects() {
  const optionHtml = [
    '<option value="">Selecciona una Party</option>',
    ...state.parties.map((party) => `<option value="${escapeHtml(party.id)}">${escapeHtml(party.display_name)} · ${escapeHtml(party.party_type)}</option>`),
  ].join("");
  $$(".party-select").forEach((select) => {
    const old = select.value;
    select.innerHTML = optionHtml;
    if (state.parties.some((party) => party.id === old)) select.value = old;
  });
}

function makeRiskRow(defaults = {}) {
  const row = document.createElement("div");
  row.className = "risk-editor-row";
  const options = [
    '<option value="">Selecciona Party</option>',
    ...state.parties.map((party) => `<option value="${escapeHtml(party.id)}">${escapeHtml(party.display_name)}</option>`),
  ].join("");
  row.innerHTML = `
    <label class="address-field"><span class="field-label">Inmueble</span><input class="text-input" data-risk-field="property_address" maxlength="240" required value="${escapeHtml(defaults.property_address || "")}" placeholder="Calle y número" /></label>
    <label><span class="field-label">Tenant</span><select class="text-input" data-risk-field="tenant_party_id" required>${options}</select></label>
    <label><span class="field-label">Landlord</span><select class="text-input" data-risk-field="landlord_party_id" required>${options}</select></label>
    <label><span class="field-label">Canon COP</span><input class="text-input" data-risk-field="monthly_rent" type="number" min="0.01" step="0.01" required value="${escapeHtml(defaults.monthly_rent || "")}" placeholder="1500000.00" /></label>
    <button class="remove-risk-row" type="button" aria-label="Quitar riesgo">×</button>
  `;
  const tenant = row.querySelector('[data-risk-field="tenant_party_id"]');
  const landlord = row.querySelector('[data-risk-field="landlord_party_id"]');
  if (defaults.tenant_party_id) tenant.value = defaults.tenant_party_id;
  if (defaults.landlord_party_id) landlord.value = defaults.landlord_party_id;
  row.querySelector(".remove-risk-row").addEventListener("click", () => {
    if ($$(".risk-editor-row", $("#risk-editor-list")).length > 1) row.remove();
  });
  $("#risk-editor-list").append(row);
}

function openPolicyModal() {
  if (!state.parties.length) {
    toast("Crea primero una Party", "El tomador, tenant y landlord se seleccionan del directorio.", "error");
    openModal("party-modal");
    return;
  }
  $("#policy-form").reset();
  $("#policy-start").value = new Date().toISOString().slice(0, 10);
  $("#risk-editor-list").innerHTML = "";
  $("#add-risk-row").disabled = $("#policy-type").value === "INDIVIDUAL";
  renderPartyOptions();
  makeRiskRow();
  openModal("policy-modal");
}

function collectRiskRows(root) {
  return $$(".risk-editor-row", root).map((row) => {
    const item = {};
    $$('[data-risk-field]', row).forEach((field) => {
      item[field.dataset.riskField] = field.value.trim();
    });
    return item;
  });
}

async function submitParty(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const fields = Object.fromEntries(new FormData(form));
  try {
    const { data } = await api("/parties", { method: "POST", body: fields });
    closeModal("party-modal");
    form.reset();
    await loadParties();
    toast("Party creada", `${data.display_name} quedó disponible en el directorio.`);
  } catch (error) {
    showError(error);
  }
}

function saveOperation(operation, label) {
  if (!operation?.operation_id) return;
  state.operations.set(operation.operation_id, { ...operation, label });
  const history = [...new Set([
    operation.operation_id,
    ...JSON.parse(localStorage.getItem(OPERATION_HISTORY_KEY) || "[]"),
  ])].slice(0, 12);
  localStorage.setItem(OPERATION_HISTORY_KEY, JSON.stringify(history));
  renderOperations();
}

async function readOperation(operationId) {
  const { data } = await api(`/operations/${operationId}`);
  state.operations.set(data.operation_id, data);
  renderOperations();
  return data;
}

async function pollOperation(operationId, label) {
  const terminal = new Set(["WAITING_INPUT", "SUCCEEDED", "REJECTED", "NEEDS_REVIEW"]);
  for (let attempt = 0; attempt < 24; attempt += 1) {
    try {
      const operation = await readOperation(operationId);
      if (terminal.has(operation.status)) {
        if (operation.status === "SUCCEEDED") {
          toast("Cambio confirmado", `${label}: CORE confirmó la operación.`);
          if (operation.policy_id) {
            await loadPolicies();
            await selectPolicy(operation.policy_id);
          }
        } else if (operation.status === "WAITING_INPUT") {
          toast("Operación pendiente", `${label}: aporta una tasa IPC para continuar.`);
        } else if (operation.status === "NEEDS_REVIEW") {
          toast("Requiere revisión", `${label}: consulta el resultado CORE con rol reviewer.`, "error");
        } else {
          toast("CORE rechazó la solicitud", operation.error_message || label, "error");
        }
        return;
      }
    } catch (error) {
      showError(error);
      return;
    }
    await pause(1500);
  }
  toast("Sigue en proceso", "La operación permanece en la lista; puedes actualizar su estado más tarde.");
}

async function acceptOperation(response, label) {
  const operation = response.data;
  saveOperation(operation, label);
  toast("Solicitud aceptada · 202", `${label} quedó persistida. No es una confirmación contractual.`);
  void pollOperation(operation.operation_id, label);
}

async function submitPolicy(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  const policyType = data.get("policy_type");
  const risks = collectRiskRows($("#risk-editor-list"));
  if (policyType === "INDIVIDUAL" && risks.length !== 1) {
    toast("Revisa los riesgos", "La póliza individual requiere exactamente un riesgo.", "error");
    return;
  }
  const payload = {
    policy_type: policyType,
    holder_party_id: data.get("holder_party_id"),
    starts_on: data.get("starts_on"),
    duration_months: Number(data.get("duration_months")),
    risks: risks.map((risk) => ({ ...risk, monthly_rent: String(risk.monthly_rent) })),
  };
  try {
    const response = await api("/policies", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey("policy-create") },
      body: payload,
    });
    closeModal("policy-modal");
    await acceptOperation(response, "Creación de póliza");
  } catch (error) {
    showError(error);
  }
}

async function saveNotes(event, policyId) {
  event.preventDefault();
  const notes = new FormData(event.currentTarget).get("notes");
  try {
    await api(`/policies/${policyId}`, { method: "PATCH", body: { notes } });
    toast("Notas guardadas", "El cambio es operativo y no modifica el contrato.");
    await selectPolicy(policyId);
  } catch (error) {
    showError(error);
  }
}

function openRiskModal(policy) {
  if (!state.parties.length) {
    toast("Crea primero una Party", "El nuevo riesgo necesita tenant y landlord.", "error");
    openModal("party-modal");
    return;
  }
  state.addRiskPolicyId = policy.id;
  $("#risk-policy-caption").textContent = `${policy.policy_number} · ${policy.policy_type}`;
  $("#risk-form").reset();
  renderPartyOptions();
  openModal("risk-modal");
}

async function submitRisk(event) {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  const body = {
    property_address: data.get("property_address"),
    tenant_party_id: data.get("tenant_party_id"),
    landlord_party_id: data.get("landlord_party_id"),
    monthly_rent: String(data.get("monthly_rent")),
  };
  try {
    const response = await api(`/policies/${state.addRiskPolicyId}/risks`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey("risk-add") },
      body,
    });
    closeModal("risk-modal");
    await acceptOperation(response, "Alta de riesgo");
  } catch (error) {
    showError(error);
  }
}

async function cancelRisk(policyId, riskId) {
  try {
    const response = await api(`/policies/${policyId}/risks/${riskId}/cancellations`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey("risk-cancel") },
    });
    await acceptOperation(response, "Cancelación de riesgo");
  } catch (error) {
    showError(error);
  }
}

function openRenewalModal(policy) {
  state.renewPolicyId = policy.id;
  $("#renewal-policy-caption").textContent = `${policy.policy_number} · IPC ausente deja la operación en WAITING_INPUT.`;
  $("#renewal-rate").innerHTML = [
    '<option value="">Esperar IPC (WAITING_INPUT)</option>',
    ...state.rates.map((rate) => `<option value="${escapeHtml(rate.id)}">${escapeHtml(rate.reference_period)} · ${(Number(rate.rate) * 100).toFixed(2)}%${rate.is_fictional ? " · ficticio" : ""}</option>`),
  ].join("");
  openModal("renewal-modal");
}

async function submitRenewal(event) {
  event.preventDefault();
  const ipcRateId = new FormData(event.currentTarget).get("ipc_rate_id") || null;
  try {
    const response = await api(`/policies/${state.renewPolicyId}/renewals`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey("renewal") },
      body: { ipc_rate_id: ipcRateId },
    });
    closeModal("renewal-modal");
    await acceptOperation(response, "Renovación");
  } catch (error) {
    showError(error);
  }
}

async function submitRate(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  const body = {
    reference_period: data.get("reference_period"),
    effective_on: data.get("effective_on"),
    rate: data.get("rate"),
    source: data.get("source"),
    is_fictional: data.get("is_fictional") === "on",
  };
  try {
    await api("/ipc-rates", { method: "POST", body });
    closeModal("rate-modal");
    form.reset();
    await loadRates();
    toast("IPC registrado", "La tasa quedó disponible para nuevas renovaciones.");
  } catch (error) {
    showError(error);
  }
}

async function loadOperationById(operationId) {
  const id = operationId.trim();
  if (!id) return;
  try {
    const operation = await readOperation(id);
    saveOperation(operation, operation.operation_type);
  } catch (error) {
    showError(error);
  }
}

async function resumeOperation(operationId, select) {
  const ipcRateId = select.value;
  if (!ipcRateId) {
    toast("Selecciona una tasa", "La operación no se puede reanudar sin IPC.", "error");
    return;
  }
  try {
    const { data } = await api(`/operations/${operationId}/resume`, {
      method: "POST",
      body: { ipc_rate_id: ipcRateId },
    });
    saveOperation(data, data.operation_type);
    toast("IPC recibido", "La operación vuelve a la cola para procesarse.");
    void pollOperation(operationId, "Renovación");
  } catch (error) {
    showError(error);
  }
}

async function reconcileOperation(operationId) {
  try {
    const { data } = await api(`/operations/${operationId}/reconcile`, { method: "POST" });
    const operation = await readOperation(operationId);
    toast("Reconciliación consultada", `${statusLabel(data.status || operation.status)} · no se reenvió la mutación.`);
    if (operation.policy_id) await selectPolicy(operation.policy_id);
  } catch (error) {
    showError(error);
  }
}

function renderOperations() {
  const list = $("#operation-list");
  const operations = [...state.operations.values()];
  $("#operation-count").textContent = operations.length;
  $("#operations-pill").textContent = operations.length;
  if (!operations.length) {
    list.innerHTML = '<div class="quiet-state">Las solicitudes nuevas aparecerán aquí.</div>';
    return;
  }
  list.innerHTML = operations.map((operation) => `
    <article class="operation-card">
      <div class="operation-card-top"><span class="operation-kind">${escapeHtml(operation.label || operation.operation_type)}</span><span class="badge ${badgeClass(operation.status)}">${escapeHtml(statusLabel(operation.status))}</span></div>
      <code class="operation-id">${escapeHtml(operation.operation_id)}</code>
      ${operation.error_message ? `<p class="operation-error">${escapeHtml(operation.error_message)}</p>` : ""}
      <div class="operation-actions">
        ${operation.status === "WAITING_INPUT" ? `<select class="text-input" data-resume-rate="${escapeHtml(operation.operation_id)}"><option value="">Selecciona IPC</option>${state.rates.map((rate) => `<option value="${escapeHtml(rate.id)}">${escapeHtml(rate.reference_period)} · ${(Number(rate.rate) * 100).toFixed(2)}%</option>`).join("")}</select><button class="button button-quiet button-small" data-resume-operation="${escapeHtml(operation.operation_id)}" type="button">Reanudar</button>` : ""}
        ${operation.status === "NEEDS_REVIEW" ? `<button class="button button-quiet button-small" data-reconcile-operation="${escapeHtml(operation.operation_id)}" type="button">Reconciliar</button>` : ""}
        <button class="text-action" data-refresh-operation="${escapeHtml(operation.operation_id)}" type="button">Actualizar</button>
      </div>
    </article>
  `).join("");
  $$('[data-refresh-operation]', list).forEach((button) => button.addEventListener("click", () => loadOperationById(button.dataset.refreshOperation)));
  $$('[data-reconcile-operation]', list).forEach((button) => button.addEventListener("click", () => reconcileOperation(button.dataset.reconcileOperation)));
  $$('[data-resume-operation]', list).forEach((button) => button.addEventListener("click", () => {
    const select = list.querySelector(`[data-resume-rate="${CSS.escape(button.dataset.resumeOperation)}"]`);
    resumeOperation(button.dataset.resumeOperation, select);
  }));
}

async function restoreOperationHistory() {
  const history = JSON.parse(localStorage.getItem(OPERATION_HISTORY_KEY) || "[]");
  await Promise.all(history.slice(0, 8).map(async (id) => {
    try {
      const { data } = await api(`/operations/${id}`);
      state.operations.set(data.operation_id, data);
    } catch {
      // El historial local puede apuntar a otra base de datos o a datos ya depurados.
    }
  }));
  renderOperations();
}

function bindEvents() {
  $("#refresh-button").addEventListener("click", () => loadAll());
  $("#policy-reload").addEventListener("click", () => loadPolicies());
  $("#new-policy-button").addEventListener("click", openPolicyModal);
  $("#new-party-button").addEventListener("click", () => openModal("party-modal"));
  $("#token-button").addEventListener("click", () => {
    $("#token-value").value = state.token;
    openModal("token-modal");
  });
  $("#token-form").addEventListener("submit", (event) => {
    event.preventDefault();
    state.token = $("#token-value").value.trim();
    sessionStorage.setItem(SESSION_TOKEN_KEY, state.token);
    closeModal("token-modal");
    void loadAll();
    void restoreOperationHistory();
    toast("Token actualizado", "La siguiente llamada usará el nuevo Bearer token.");
  });
  $("#party-form").addEventListener("submit", submitParty);
  $("#policy-form").addEventListener("submit", submitPolicy);
  $("#risk-form").addEventListener("submit", submitRisk);
  $("#renewal-form").addEventListener("submit", submitRenewal);
  $("#rate-form").addEventListener("submit", submitRate);
  $("#operation-lookup-form").addEventListener("submit", (event) => {
    event.preventDefault();
    void loadOperationById($("#operation-id-input").value);
  });
  $("#add-risk-row").addEventListener("click", () => makeRiskRow());
  $("#policy-type").addEventListener("change", () => {
    const add = $("#add-risk-row");
    add.disabled = $("#policy-type").value === "INDIVIDUAL";
    if ($("#policy-type").value === "INDIVIDUAL") {
      const rows = $$(".risk-editor-row", $("#risk-editor-list"));
      rows.slice(1).forEach((row) => row.remove());
    }
  });
  $("#policy-search").addEventListener("input", (event) => {
    state.policySearch = event.currentTarget.value;
    renderPolicies();
  });
  $("#policy-filter").addEventListener("change", (event) => {
    state.policyFilter = event.currentTarget.value;
    void loadPolicies();
  });
  $$('[data-close]').forEach((button) => button.addEventListener("click", () => closeModal(button.dataset.close)));
  $$("[data-directory]").forEach((button) => button.addEventListener("click", () => {
    state.selectedDirectory = button.dataset.directory;
    $$("[data-directory]").forEach((tab) => {
      const active = tab === button;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", String(active));
    });
    renderDirectory();
  }));
}

async function start() {
  bindEvents();
  renderPartyOptions();
  await loadAll();
  await restoreOperationHistory();
  $("#add-risk-row").disabled = $("#policy-type").value === "INDIVIDUAL";
}

void start();
