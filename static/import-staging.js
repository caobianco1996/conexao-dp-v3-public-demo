(() => {
  const form = document.getElementById("import-stage-form");
  if (!form) return;
  const status = document.getElementById("import-stage-status");
  const preview = document.getElementById("import-stage-preview");
  const list = document.getElementById("import-stage-list");
  let csrf = "";
  let busy = false;

  const el = (tag, value, className = "") => {
    const node = document.createElement(tag);
    node.textContent = value;
    if (className) node.className = className;
    return node;
  };
  async function request(url, method = "GET", body = null) {
    const response = await fetch(url, {
      method, credentials: "same-origin", cache: "no-store",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify({ ...body, csrf }) : undefined,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Falha ${response.status}`);
    return data;
  }
  function readAsBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(new Error("Não foi possível ler o CSV."));
      reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
      reader.readAsDataURL(file);
    });
  }
  async function showBatch(id) {
    const data = await request(`/api/ui/import-batches/${id}`);
    preview.replaceChildren();
    const heading = el("h3", `Prévia do lote ${data.batch.id} · ${data.batch.status}`);
    const summary = el("p", `${data.batch.company_scope} · ${data.batch.row_count} linha(s) · ${data.batch.error_count} conflito(s)`);
    preview.append(heading, summary);
    const table = document.createElement("table");
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    ["Linha", "Matrícula", "Nome", "Ação", "Revisão"].forEach(label => headRow.append(el("th", label)));
    head.append(headRow);
    const body = document.createElement("tbody");
    data.items.forEach(item => {
      const row = document.createElement("tr");
      [item.line_number, item.normalized.registration, item.normalized.name, item.action,
        item.errors.join(" ") || "—"].forEach(value => row.append(el("td", String(value || ""))));
      body.append(row);
    });
    table.append(head, body);
    const wrap = document.createElement("div");
    wrap.className = "table-wrap";
    wrap.append(table);
    preview.append(wrap);
    if (data.batch.status === "STAGED") {
      const apply = el("button", "Aprovar e aplicar no cadastro", "btn btn-success");
      apply.type = "button";
      apply.disabled = !data.canApply;
      apply.addEventListener("click", async () => {
        if (busy || !window.confirm(`Aplicar ${data.batch.row_count} linha(s) do lote ${id} para ${data.batch.company_scope}?`)) return;
        busy = true; apply.disabled = true; status.textContent = "Aplicando lote…";
        try {
          await request(`/api/ui/import-batches/${id}`, "POST", { action: "apply" });
          status.textContent = "Lote aplicado. Os dados importados já podem ser consultados em Pessoas e Analytics.";
          await refresh(); await showBatch(id);
        } catch (error) { status.textContent = error.message; apply.disabled = false; }
        finally { busy = false; }
      });
      const reject = el("button", "Rejeitar lote", "btn btn-outline-secondary");
      reject.type = "button";
      reject.addEventListener("click", async () => {
        if (busy || !window.confirm(`Rejeitar o lote ${id}?`)) return;
        busy = true; status.textContent = "Rejeitando lote…";
        try { await request(`/api/ui/import-batches/${id}`, "POST", { action: "reject" });
          status.textContent = "Lote rejeitado; nenhum cadastro foi alterado.";
          await refresh(); await showBatch(id);
        } catch (error) { status.textContent = error.message; }
        finally { busy = false; }
      });
      preview.append(apply, reject);
    }
  }
  async function refresh() {
    const data = await request("/api/ui/import-batches");
    csrf = data.csrf;
    list.replaceChildren();
    if (!data.batches.length) { list.append(el("p", "Nenhum lote preparado.")); return; }
    data.batches.forEach(batch => {
      const button = el("button", `#${batch.id} · ${batch.source_name} · ${batch.company_scope} · ${batch.status}`);
      button.type = "button";
      button.addEventListener("click", () => showBatch(batch.id).catch(error => { status.textContent = error.message; }));
      list.append(button);
    });
  }
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (busy) return;
    const fields = new FormData(form);
    const file = fields.get("file");
    if (!(file instanceof File) || !file.size || file.size > 10 * 1024 * 1024) {
      status.textContent = "Escolha um CSV de até 10 MB."; return;
    }
    busy = true; status.textContent = "Lendo e validando o arquivo…";
    try {
      const contentBase64 = await readAsBase64(file);
      const data = await request("/api/ui/import-batches", "POST", {
        sourceName: fields.get("sourceName"), companyScope: String(fields.get("companyScope") || "").trim(), contentBase64,
      });
      status.textContent = data.canApply ? "Prévia pronta. Confira antes de aplicar." : "O lote tem conflitos. Corrija o CSV e prepare outro lote.";
      await refresh(); await showBatch(data.batch.id);
    } catch (error) { status.textContent = error.message; }
    finally { busy = false; }
  });
  refresh().catch(error => { status.textContent = error.message; });
})();
