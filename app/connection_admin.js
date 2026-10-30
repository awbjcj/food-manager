"use strict";
const $ = selector => document.querySelector(selector);
let csrf = sessionStorage.getItem("connectionCsrf") || "";
const status = text => { $("#status").textContent = text; };
async function api(path, {method = "GET", body, revision, key} = {}) {
  const headers = {"Content-Type": "application/json"};
  if (method !== "GET") headers["X-CSRF-Token"] = csrf;
  if (revision !== undefined) headers["If-Match"] = String(revision);
  if (key) headers["Idempotency-Key"] = key;
  const response = await fetch(path, {method, headers, body: body === undefined ? undefined : JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error?.message || "Request failed");
  return data;
}
const schemas = {
  telegram: [["bootstrapUserId", "Your Telegram user ID"], ["botToken", "Bot token from BotFather", true]],
  slack: [["bootstrapUserId", "Your Slack member ID"], ["clientId", "App client ID", true], ["clientSecret", "App client secret", true], ["signingSecret", "App signing secret", true]],
  whatsapp: [["bootstrapUserId", "Your personal WhatsApp number (digits, country code included)"], ["businessAccountId", "WhatsApp business account ID"], ["phoneNumberId", "Business phone number ID"], ["apiVersion", "Supported Graph API version (for example vNN.0)"], ["accessToken", "System user access token", true], ["appSecret", "Meta app secret", true], ["verifyToken", "Webhook verification token you choose", true], ["templateName", "Approved reminder template name (optional)", false, true], ["templateLanguage", "Template language code, such as en_US (optional)", false, true]]
};
function fields() {
  const provider = $("#create-form").elements.provider.value;
  $("#fields").replaceChildren();
  for (const [name, label, secret, optional] of schemas[provider]) {
    const wrapper = document.createElement("label"), input = document.createElement("input");
    wrapper.textContent = label; input.name = name; input.required = !optional;
    input.type = secret ? "password" : "text"; input.autocomplete = "off";
    wrapper.append(input); $("#fields").append(wrapper);
  }
}
function button(label, action) {
  const node = document.createElement("button"); node.textContent = label; node.className = "secondary";
  node.onclick = async () => { node.disabled = true; try { await action(); } catch (error) { status(error.message); } finally { node.disabled = false; } };
  return node;
}
async function refresh() {
  const items = []; let cursor = "";
  do { const page = await api("/admin/api/connections?cursor=" + encodeURIComponent(cursor)); items.push(...page.items); cursor = page.nextCursor; } while (cursor);
  $("#login").hidden = true; $("#console").hidden = false; $("#connections").replaceChildren();
  for (const row of items) {
    const card = document.createElement("article"), title = document.createElement("h2"), state = document.createElement("p");
    title.textContent = row.label; state.textContent = `${row.provider} · ${row.enabled ? "Enabled" : "Disabled"} · ${row.state.replaceAll("_", " ")} · ${row.externalAccount || "Account not verified"}`;
    card.append(title, state);
    const path = `/admin/api/connections/${encodeURIComponent(row.id)}`;
    card.append(button("Setup instructions", async () => { const data = await api(path + "/instructions"); let pre = card.querySelector("pre"); if (!pre) { pre = document.createElement("pre"); card.append(pre); } pre.textContent = JSON.stringify(data, null, 2); }));
    if (row.provider === "slack") card.append(button("Install in Slack", async () => { const data = await api(path + "/oauth", {method: "POST", revision: row.revision}); location.assign(data.authorizationUrl); }));
    card.append(button("Validate credentials", async () => { await api(path + "/check", {method: "POST", revision: row.revision}); await refresh(); status("Credentials verified. Send a direct message from your bootstrap account, then refresh status."); }));
    card.append(button(row.enabled ? "Disable" : "Enable", async () => { await api(path, {method: "PATCH", revision: row.revision, body: {enabled: !row.enabled}}); await refresh(); status("Connection updated."); }));
    card.append(button("Send test to bootstrap account", async () => {
      const sent = await api(path + "/test", {method: "POST", revision: row.revision, key: crypto.randomUUID()});
      status(`Test queued (${sent.deliveryId}). Checking provider acceptance…`);
      for (let i = 0; i < 10; i++) { await new Promise(resolve => setTimeout(resolve, 1000)); const delivery = await api(path + "/deliveries/" + sent.deliveryId); if (!["pending", "sending", "retry"].includes(delivery.status)) { status(`Test: ${delivery.status}${delivery.errorCode ? " — " + delivery.errorCode : ""}. Accepted means the provider accepted the request; it does not confirm receipt on the device.`); return; } }
      status("Test remains queued. Refresh and check deployment logs if it does not arrive.");
    }));
    if (row.credentialSource === "managed") {
      const details = document.createElement("details"), summary = document.createElement("summary"), form = document.createElement("form");
      summary.textContent = "Rotate credentials"; details.append(summary);
      const description = document.createElement("p"); description.textContent = "Leave unchanged fields blank. Saving disables the connection until it is validated again."; form.append(description);
      for (const [name, label, secret] of schemas[row.provider]) if (secret) { const wrap = document.createElement("label"), input = document.createElement("input"); wrap.textContent = label; input.name = name; input.type = "password"; input.autocomplete = "off"; wrap.append(input); form.append(wrap); }
      const save = document.createElement("button"); save.textContent = "Save credentials"; form.append(save);
      form.onsubmit = async event => { event.preventDefault(); const credentials = Object.fromEntries([...new FormData(form)].filter(([, value]) => value)); try { await api(path, {method:"PATCH", revision:row.revision, body:{credentials}}); form.reset(); await refresh(); status("Credentials updated. Validate the connection again."); } catch (error) { status(error.message); } };
      details.append(form); card.append(details);
    }
    $("#connections").append(card);
  }
  if (!items.length) $("#connections").textContent = "No connections yet. Add your first provider above.";
}
$("#login-form").onsubmit = async event => { event.preventDefault(); try { const result = await api("/admin/api/session", {method:"POST", body:{token:event.target.elements.token.value}}); event.target.reset(); csrf = result.csrfToken; sessionStorage.setItem("connectionCsrf", csrf); await refresh(); status(""); } catch(error) { status(error.message); } };
$("#create-form").elements.provider.onchange = fields;
$("#create-form").onsubmit = async event => {
  event.preventDefault(); const form = event.target, data = new FormData(form), provider = data.get("provider"), config = {}, credentials = {};
  for (const [name,,secret] of schemas[provider]) if (!name.startsWith("template")) (secret ? credentials : config)[name] = data.get(name);
  if (provider === "whatsapp") config.digestTemplates = data.get("templateName") ? [{name:data.get("templateName"),language:data.get("templateLanguage")}] : [];
  try { await api("/admin/api/connections", {method:"POST",body:{provider,label:data.get("label"),settings:config,credentials}}); form.reset(); fields(); await refresh(); status("Connection created. Open its setup instructions to continue."); } catch(error) { status(error.message); }
};
$("#refresh").onclick = () => refresh().catch(error => status(error.message));
$("#logout").onclick = async () => { try { await api("/admin/api/session", {method:"DELETE"}); } finally { sessionStorage.removeItem("connectionCsrf"); csrf = ""; $("#console").hidden = true; $("#login").hidden = false; status(""); } };
fields(); if (csrf) refresh().catch(() => { sessionStorage.removeItem("connectionCsrf"); csrf = ""; });
