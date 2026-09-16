/**
 * GainARK OntoLeap — Standalone & Hybrid Browser Extension (Manifest V3)
 * Accurate Wikidata Grounding + In-Memory Tab Persistence + Existing Schema Inspector
 */

const DEFAULT_LOCAL_BACKEND = "http://localhost:8080";
const DEFAULT_CLOUD_BACKEND = "https://gainark-ontoleap-35509275124.asia-south1.run.app";

let currentTab = null;
let currentResult = null;
let currentPageData = null;

// App Configuration
let config = {
  engineMode: "gemini_standalone", // "gemini_standalone" | "heuristic_standalone" | "ontoleap_backend"
  geminiApiKey: "",
  geminiModel: "gemini-3.6-flash",
  backendUrl: DEFAULT_LOCAL_BACKEND,
  backendApiKey: ""
};

// DOM Elements
const elModeBadge = document.getElementById("mode-badge");
const elKeyHintBanner = document.getElementById("key-hint-banner");
const btnBannerSettings = document.getElementById("btn-banner-settings");
const elPageTitle = document.getElementById("page-title");
const elPageUrl = document.getElementById("page-url");
const elCachedIndicator = document.getElementById("cached-indicator");
const elSchemaTypeHint = document.getElementById("schema-type-hint");
const btnGenerate = document.getElementById("btn-generate");
const btnGenerateText = document.getElementById("btn-generate-text");

const loadingContainer = document.getElementById("loading-container");
const loadingStatus = document.getElementById("loading-status");
const loadingSub = document.getElementById("loading-sub");
const errorContainer = document.getElementById("error-container");
const errorMessage = document.getElementById("error-message");
const btnRetry = document.getElementById("btn-retry");
const resultsContainer = document.getElementById("results-container");

const badgeType = document.getElementById("badge-type");
const badgeEntities = document.getElementById("badge-entities");
const badgeTriples = document.getElementById("badge-triples");
const badgeExistingCount = document.getElementById("badge-existing-count");

const codeJsonLd = document.getElementById("code-jsonld");
const codeExisting = document.getElementById("code-existing");
const existingSchemaSummary = document.getElementById("existing-schema-summary");
const codeTurtle = document.getElementById("code-turtle");
const entitiesList = document.getElementById("entities-list");
const triplesList = document.getElementById("triples-list");

const btnCopyJsonLd = document.getElementById("btn-copy-jsonld");
const btnCopyExisting = document.getElementById("btn-copy-existing");
const btnCopyTurtle = document.getElementById("btn-copy-turtle");
const btnGoogleTest = document.getElementById("btn-google-test");
const btnDownloadJsonLd = document.getElementById("btn-download-jsonld");
const btnDownloadTurtle = document.getElementById("btn-download-turtle");

// Settings Modal Elements
const btnSettings = document.getElementById("btn-settings");
const settingsModal = document.getElementById("settings-modal");
const btnCloseSettings = document.getElementById("btn-close-settings");
const settingEngineMode = document.getElementById("setting-engine-mode");
const sectionGeminiSettings = document.getElementById("section-gemini-settings");
const settingGeminiKey = document.getElementById("setting-gemini-key");
const settingGeminiModel = document.getElementById("setting-gemini-model");
const settingCustomModel = document.getElementById("setting-custom-model");
const sectionBackendSettings = document.getElementById("section-backend-settings");
const settingBackendUrl = document.getElementById("setting-backend-url");
const settingApiKey = document.getElementById("setting-api-key");
const btnPresetLocal = document.getElementById("btn-preset-local");
const btnPresetCloud = document.getElementById("btn-preset-cloud");
const btnSaveSettings = document.getElementById("btn-save-settings");
const toast = document.getElementById("toast");

// Initialize on Load
document.addEventListener("DOMContentLoaded", async () => {
  await loadSettings();
  updateModeDisplay();
  await inspectActiveTab();
  setupEventListeners();
});

// An API key stays on this machine. chrome.storage.sync is uploaded to Google and copied
// to every Chrome signed in to the same account, which is not what "stored locally" means
// and not somewhere a key the user pasted in should travel to. Preferences still sync;
// the two keys are held in chrome.storage.local, which never leaves the device.
const SECRET_SETTINGS = ["geminiApiKey", "backendApiKey"];
const SYNCED_SETTINGS = ["engineMode", "geminiModel", "backendUrl"];

// Load Settings from Chrome Storage
async function loadSettings() {
  try {
    if (chrome.storage && chrome.storage.sync) {
      const data = await chrome.storage.sync.get(SYNCED_SETTINGS);
      const secrets = await chrome.storage.local.get(SECRET_SETTINGS);

      // A build before 1.2.1 wrote the keys to sync, so they are already in the account.
      // Move them down to this device and take them out of sync as they are first read.
      const stranded = await chrome.storage.sync.get(SECRET_SETTINGS);
      if (SECRET_SETTINGS.some(k => stranded[k])) {
        for (const k of SECRET_SETTINGS) {
          if (!secrets[k] && stranded[k]) secrets[k] = stranded[k];
        }
        await chrome.storage.local.set(secrets);
        await chrome.storage.sync.remove(SECRET_SETTINGS);
      }

      if (data.engineMode) config.engineMode = data.engineMode;
      if (secrets.geminiApiKey) config.geminiApiKey = secrets.geminiApiKey;
      if (data.geminiModel) config.geminiModel = data.geminiModel;
      if (data.backendUrl) config.backendUrl = data.backendUrl;
      if (secrets.backendApiKey) config.backendApiKey = secrets.backendApiKey;
    } else {
      config.engineMode = localStorage.getItem("ontoleap_engine_mode") || config.engineMode;
      config.geminiApiKey = localStorage.getItem("ontoleap_gemini_key") || config.geminiApiKey;
      config.geminiModel = localStorage.getItem("ontoleap_gemini_model") || config.geminiModel;
      config.backendUrl = localStorage.getItem("ontoleap_backend_url") || config.backendUrl;
      config.backendApiKey = localStorage.getItem("ontoleap_backend_key") || config.backendApiKey;
    }
  } catch (err) {
    console.warn("Could not load storage, using defaults:", err);
  }

  // Auto-migrate deprecated 2.0-flash to 3.6-flash
  if (!config.geminiModel || config.geminiModel === "gemini-2.0-flash") {
    config.geminiModel = "gemini-3.6-flash";
  }

  // Populate Modal Fields
  settingEngineMode.value = config.engineMode;
  settingGeminiKey.value = config.geminiApiKey;

  const knownModels = ["gemini-3.6-flash", "gemini-2.5-flash", "gemini-1.5-flash"];
  if (knownModels.includes(config.geminiModel)) {
    settingGeminiModel.value = config.geminiModel;
    settingCustomModel.classList.add("hidden");
  } else {
    settingGeminiModel.value = "custom";
    settingCustomModel.value = config.geminiModel;
    settingCustomModel.classList.remove("hidden");
  }

  settingBackendUrl.value = config.backendUrl;
  settingApiKey.value = config.backendApiKey;
}

// The extension ships with access to the localhost and Cloud Run backends only, so a
// server the user points it at anywhere else has to be granted by the user. Chrome shows
// that prompt only during a user gesture, so it is asked for here, on the click that
// saves the setting, and `request` returns true without prompting when it is already held.
async function ensureBackendAccess(backendUrl) {
  if (!chrome.permissions) return true;
  let origin;
  try {
    origin = new URL(backendUrl).origin + "/*";
  } catch (err) {
    return false;
  }
  try {
    return await chrome.permissions.request({ origins: [origin] });
  } catch (err) {
    console.warn("Could not request access to " + origin, err);
    return false;
  }
}

// Save Settings
async function saveSettings() {
  config.engineMode = settingEngineMode.value;
  config.geminiApiKey = (settingGeminiKey.value || "").trim();

  if (settingGeminiModel.value === "custom") {
    config.geminiModel = (settingCustomModel.value || "gemini-3.6-flash").trim();
  } else {
    config.geminiModel = settingGeminiModel.value || "gemini-3.6-flash";
  }

  config.backendUrl = (settingBackendUrl.value || DEFAULT_LOCAL_BACKEND).trim().replace(/\/+$/, "");
  config.backendApiKey = (settingApiKey.value || "").trim();

  // Asked before the settings are stored, so nothing the user typed is lost when they
  // decline: the URL is saved either way and the modal stays open to say what happened.
  const backendGranted = config.engineMode !== "ontoleap_backend"
    || await ensureBackendAccess(config.backendUrl);

  try {
    if (chrome.storage && chrome.storage.sync) {
      const synced = {}, secrets = {};
      for (const k of SYNCED_SETTINGS) synced[k] = config[k];
      for (const k of SECRET_SETTINGS) secrets[k] = config[k];
      await chrome.storage.sync.set(synced);
      await chrome.storage.local.set(secrets);
    } else {
      localStorage.setItem("ontoleap_engine_mode", config.engineMode);
      localStorage.setItem("ontoleap_gemini_key", config.geminiApiKey);
      localStorage.setItem("ontoleap_gemini_model", config.geminiModel);
      localStorage.setItem("ontoleap_backend_url", config.backendUrl);
      localStorage.setItem("ontoleap_backend_key", config.backendApiKey);
    }
  } catch (err) {
    console.warn("Storage sync failed:", err);
  }

  updateModeDisplay();
  if (!backendGranted) {
    showToast("Saved, but this browser has not been allowed to reach " + config.backendUrl);
    return;
  }
  showToast("Configuration saved!");
  settingsModal.classList.add("hidden");
}

function updateModeDisplay() {
  if (config.engineMode === "gemini_standalone") {
    elModeBadge.textContent = "⚡ Standalone (AI)";
    elModeBadge.className = "mode-badge standalone";
    if (!config.geminiApiKey) {
      elKeyHintBanner.classList.remove("hidden");
    } else {
      elKeyHintBanner.classList.add("hidden");
    }
    sectionGeminiSettings.classList.remove("hidden");
    sectionBackendSettings.classList.add("hidden");
  } else if (config.engineMode === "heuristic_standalone") {
    elModeBadge.textContent = "🧩 Standalone (Fast)";
    elModeBadge.className = "mode-badge standalone";
    elKeyHintBanner.classList.add("hidden");
    sectionGeminiSettings.classList.add("hidden");
    sectionBackendSettings.classList.add("hidden");
  } else {
    elModeBadge.textContent = "🖥️ OntoLeap Engine";
    elModeBadge.className = "mode-badge backend";
    elKeyHintBanner.classList.add("hidden");
    sectionGeminiSettings.classList.add("hidden");
    sectionBackendSettings.classList.remove("hidden");
  }
}

// Inspect Active Tab, Scan Existing Schema & Restore In-Memory Session
async function inspectActiveTab() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.url) {
      elPageTitle.textContent = "Unable to access active tab";
      elPageUrl.textContent = "--";
      btnGenerate.disabled = true;
      return;
    }

    currentTab = tab;
    elPageTitle.textContent = tab.title || "Active Web Page";
    elPageUrl.textContent = tab.url;

    if (tab.url.startsWith("chrome://") || tab.url.startsWith("edge://") || tab.url.startsWith("about:")) {
      elPageTitle.textContent = "Browser Internal Page";
      elPageUrl.textContent = "Extensions cannot analyze internal browser tabs";
      btnGenerate.disabled = true;
      return;
    }

    // 1. Scan Page DOM for Existing Schema.org structured data
    await scanExistingPageSchema(tab.id);

    // 2. Check Tab In-Memory Session Cache (persists until tab is closed)
    await checkTabSessionMemory(tab.id, tab.url);

  } catch (err) {
    console.error("Tab inspection failed:", err);
    elPageTitle.textContent = "Tab Inspection Error";
    elPageUrl.textContent = String(err);
  }
}

// Extract DOM & Existing Schema from Tab
async function scanExistingPageSchema(tabId) {
  try {
    const injectionResults = await chrome.scripting.executeScript({
      target: { tabId: tabId },
      func: () => {
        const getMeta = (name) => {
          const el = document.querySelector(`meta[name="${name}"], meta[property="${name}"]`);
          return el ? el.getAttribute("content") : "";
        };

        const headings = Array.from(document.querySelectorAll("h1, h2, h3"))
          .map(h => h.innerText.trim())
          .filter(h => h.length > 2)
          .slice(0, 15);

        // Extract all existing Schema.org JSON-LD scripts
        const jsonLdScripts = Array.from(document.querySelectorAll('script[type="application/ld+json"]'));
        const existingJsonLd = [];
        jsonLdScripts.forEach(s => {
          try {
            const parsed = JSON.parse(s.innerText);
            existingJsonLd.push(parsed);
          } catch (_) {}
        });

        // Clean readable text
        const bodyClone = document.body.cloneNode(true);
        const toRemove = bodyClone.querySelectorAll("script, style, noscript, nav, footer, svg");
        toRemove.forEach(r => r.remove());
        const cleanText = (bodyClone.innerText || "").replace(/\s+/g, " ").trim().slice(0, 9500);

        return {
          url: window.location.href,
          title: document.title,
          metaDescription: getMeta("description") || getMeta("og:description"),
          ogType: getMeta("og:type"),
          ogSiteName: getMeta("og:site_name"),
          headings: headings,
          existingJsonLd: existingJsonLd,
          textSample: cleanText,
          html: document.documentElement.outerHTML
        };
      }
    });

    if (injectionResults && injectionResults[0] && injectionResults[0].result) {
      currentPageData = injectionResults[0].result;
      renderExistingSchema(currentPageData.existingJsonLd);
    }
  } catch (err) {
    console.warn("DOM extraction script failed (tab may be restricted):", err);
  }
}

// Display Existing Page Schema in UI
function renderExistingSchema(existingJsonLd) {
  if (existingJsonLd && existingJsonLd.length > 0) {
    badgeExistingCount.textContent = existingJsonLd.length;
    badgeExistingCount.classList.remove("hidden");
    existingSchemaSummary.textContent = `Found ${existingJsonLd.length} JSON-LD block(s) on page`;

    // Unwrap if single block for cleaner display
    const displayObj = existingJsonLd.length === 1 ? existingJsonLd[0] : existingJsonLd;
    codeExisting.textContent = JSON.stringify(displayObj, null, 2);
  } else {
    badgeExistingCount.classList.add("hidden");
    existingSchemaSummary.textContent = "No JSON-LD markup found on page";
    codeExisting.textContent = "// No <script type=\"application/ld+json\"> structured data detected on this page.\n// OntoLeap will generate complete Schema.org markup when you click Generate.";
  }
}

// In-Memory Tab Persistence Check
async function checkTabSessionMemory(tabId, url) {
  const sessionKey = `tab_${tabId}`;
  let cached = null;

  try {
    if (chrome.storage && chrome.storage.session) {
      const data = await chrome.storage.session.get([sessionKey]);
      cached = data[sessionKey];
    } else {
      const raw = sessionStorage.getItem(sessionKey);
      if (raw) cached = JSON.parse(raw);
    }
  } catch (err) {
    console.warn("Could not check session storage:", err);
  }

  if (cached && cached.url === url && cached.result) {
    currentResult = cached.result;
    renderResults(currentResult);
    elCachedIndicator.classList.remove("hidden");
    btnGenerateText.textContent = "🔄 Regenerate Schema & Graph";
  } else {
    elCachedIndicator.classList.add("hidden");
    btnGenerateText.textContent = "Generate Schema & Graph";
  }
}

// Save Result to In-Memory Tab Session
async function saveTabSessionMemory(tabId, url, result) {
  const sessionKey = `tab_${tabId}`;
  const payload = {
    url: url,
    result: result,
    timestamp: Date.now()
  };

  try {
    if (chrome.storage && chrome.storage.session) {
      await chrome.storage.session.set({ [sessionKey]: payload });
    } else {
      sessionStorage.setItem(sessionKey, JSON.stringify(payload));
    }
    elCachedIndicator.classList.remove("hidden");
    btnGenerateText.textContent = "🔄 Regenerate Schema & Graph";
  } catch (err) {
    console.warn("Could not save to session storage:", err);
  }
}

// Setup Event Listeners
function setupEventListeners() {
  btnGenerate.addEventListener("click", () => generateSchema());
  btnRetry.addEventListener("click", () => generateSchema());

  // Tabs Navigation
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".tab-pane").forEach(p => p.classList.remove("active"));

      btn.classList.add("active");
      const targetPane = document.getElementById(btn.dataset.tab);
      if (targetPane) targetPane.classList.add("active");
    });
  });

  // Action Buttons
  btnCopyJsonLd.addEventListener("click", () => {
    if (!currentResult || !currentResult.export_jsonld) return;
    const scriptTag = `<script type="application/ld+json">\n${JSON.stringify(currentResult.export_jsonld, null, 2)}\n<\/script>`;
    copyToClipboard(scriptTag, "Generated Schema.org <script> copied!");
  });

  btnCopyExisting.addEventListener("click", () => {
    if (!currentPageData || !currentPageData.existingJsonLd || currentPageData.existingJsonLd.length === 0) {
      showToast("No existing schema to copy");
      return;
    }
    const displayObj = currentPageData.existingJsonLd.length === 1 ? currentPageData.existingJsonLd[0] : currentPageData.existingJsonLd;
    const scriptTag = `<script type="application/ld+json">\n${JSON.stringify(displayObj, null, 2)}\n<\/script>`;
    copyToClipboard(scriptTag, "Existing Schema.org <script> copied!");
  });

  btnCopyTurtle.addEventListener("click", () => {
    if (!currentResult || !currentResult.export_turtle) return;
    copyToClipboard(currentResult.export_turtle, "RDF Turtle copied!");
  });

  btnGoogleTest.addEventListener("click", () => {
    if (!currentTab || !currentTab.url) return;
    const googleUrl = `https://search.google.com/test/rich-results?url=${encodeURIComponent(currentTab.url)}`;
    chrome.tabs.create({ url: googleUrl });
  });

  btnDownloadJsonLd.addEventListener("click", () => {
    if (!currentResult || !currentResult.export_jsonld) return;
    const blob = new Blob([JSON.stringify(currentResult.export_jsonld, null, 2)], { type: "application/ld+json" });
    downloadBlob(blob, "schema.jsonld");
  });

  btnDownloadTurtle.addEventListener("click", () => {
    if (!currentResult || !currentResult.export_turtle) return;
    const blob = new Blob([currentResult.export_turtle], { type: "text/turtle" });
    downloadBlob(blob, "ontology.ttl");
  });

  // Settings Modal Controls
  btnSettings.addEventListener("click", () => settingsModal.classList.remove("hidden"));
  btnBannerSettings.addEventListener("click", () => settingsModal.classList.remove("hidden"));
  btnCloseSettings.addEventListener("click", () => settingsModal.classList.add("hidden"));
  btnSaveSettings.addEventListener("click", saveSettings);

  settingEngineMode.addEventListener("change", (e) => {
    const val = e.target.value;
    if (val === "gemini_standalone") {
      sectionGeminiSettings.classList.remove("hidden");
      sectionBackendSettings.classList.add("hidden");
    } else if (val === "ontoleap_backend") {
      sectionGeminiSettings.classList.add("hidden");
      sectionBackendSettings.classList.remove("hidden");
    } else {
      sectionGeminiSettings.classList.add("hidden");
      sectionBackendSettings.classList.add("hidden");
    }
  });

  settingGeminiModel.addEventListener("change", (e) => {
    if (e.target.value === "custom") {
      settingCustomModel.classList.remove("hidden");
      settingCustomModel.focus();
    } else {
      settingCustomModel.classList.add("hidden");
    }
  });

  btnPresetLocal.addEventListener("click", () => {
    settingBackendUrl.value = DEFAULT_LOCAL_BACKEND;
  });
  btnPresetCloud.addEventListener("click", () => {
    settingBackendUrl.value = DEFAULT_CLOUD_BACKEND;
  });
}

// Main Dispatcher: Generate Schema & Knowledge Graph
async function generateSchema() {
  if (!currentTab || !currentTab.url) return;

  hideError();
  resultsContainer.classList.add("hidden");
  loadingContainer.classList.remove("hidden");
  btnGenerate.disabled = true;

  updateLoadingStatus("Extracting rendered DOM & visible text...", "Reading title, meta tags, and existing schemas");

  // Ensure Page Data is loaded
  if (!currentPageData || currentPageData.url !== currentTab.url) {
    await scanExistingPageSchema(currentTab.id);
  }

  const pageData = currentPageData || {
    url: currentTab.url,
    title: currentTab.title || "",
    metaDescription: "",
    ogType: "",
    ogSiteName: "",
    headings: [],
    existingJsonLd: [],
    textSample: "",
    html: ""
  };

  const typeHint = elSchemaTypeHint.value;

  try {
    let result = null;

    if (config.engineMode === "gemini_standalone") {
      if (config.geminiApiKey) {
        updateLoadingStatus("Running Gemini AI reasoning...", "Synthesizing Schema.org JSON-LD & grounding entities");
        result = await generateWithGemini(pageData, typeHint);
      } else {
        updateLoadingStatus("Running client-side heuristic parser...", "Add a free Gemini key in Settings for deep AI reasoning");
        result = await generateWithHeuristics(pageData, typeHint);
        showToast("Generated with heuristics! Add Gemini Key for full AI extraction.");
      }
    } else if (config.engineMode === "heuristic_standalone") {
      updateLoadingStatus("Running client-side heuristic parser...", "Analyzing meta tags and headings");
      result = await generateWithHeuristics(pageData, typeHint);
    } else {
      updateLoadingStatus("Connecting to OntoLeap Engine...", "Running GLiNER NER & Wikidata SPARQL");
      result = await generateWithBackend(pageData, typeHint);
    }

    currentResult = result;
    renderResults(result);

    // Save to tab session memory
    await saveTabSessionMemory(currentTab.id, currentTab.url, result);

  } catch (err) {
    console.error("Schema generation failed:", err);
    showError(err.message || "Schema generation failed.");
  } finally {
    loadingContainer.classList.add("hidden");
    btnGenerate.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// 1. Direct Gemini Flash Standalone Engine with Verified Wikidata Grounding
// ---------------------------------------------------------------------------
async function generateWithGemini(pageData, typeHint) {
  const apiKey = config.geminiApiKey;
  let model = config.geminiModel || "gemini-3.6-flash";
  if (model === "gemini-2.0-flash") model = "gemini-3.6-flash";
  const endpoint = `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${apiKey}`;

  const systemInstruction = `You are the world's foremost Knowledge Graph & Schema.org Semantic SEO Architect.
Given webpage details, your goal is to extract a comprehensive, valid, and rich Schema.org JSON-LD graph and mine high-confidence relational capabilities.

Requirements:
1. Schema.org JSON-LD must use @context: "https://schema.org".
2. If type hint is specified and not 'auto', prioritize that @type (e.g. SoftwareApplication, Product, FAQPage, Article, Organization).
3. If type hint is 'auto', choose the most specific Schema.org type based on content.
4. If the page already has existing JSON-LD markup, ENRICH and MERGE it, fixing missing properties and adding rich features/capabilities.
5. Populate all relevant Schema.org properties: name, description, url, featureList, availableOnDevice, offers/priceSpecification (if pricing visible), audience, knowsAbout.
6. Extract named entities (platforms, companies, standards, integration partners). Set wikidata_id to null (our deterministic resolver will bind verified Wikidata Q-IDs).
7. Extract relational triples (Subject, Predicate, Object) such as automates, integratesWith, compliesWith, hasFeature, supportsPricingModel with verbatim quote evidence.
8. Provide a clean W3C RDF Turtle snippet.

You must respond ONLY with a single JSON object matching this schema:
{
  "schema_type": "string",
  "json_ld": { ...valid schema.org JSON-LD object... },
  "entities": [
    { "canonical_name": "string", "entity_type": "string", "wikidata_id": null, "mentions_count": 1 }
  ],
  "triples": [
    { "source": "string", "predicate": "string", "target": "string", "confidence": 0.95, "provenance_sentence": "string" }
  ],
  "rdf_turtle": "string"
}`;

  const existingSchemaStr = pageData.existingJsonLd && pageData.existingJsonLd.length > 0
    ? JSON.stringify(pageData.existingJsonLd).slice(0, 2000)
    : "None";

  const promptContent = `Webpage Details:
- URL: ${pageData.url}
- Title: ${pageData.title}
- Meta Description: ${pageData.metaDescription || "N/A"}
- OpenGraph Type: ${pageData.ogType || "N/A"}
- OpenGraph Site Name: ${pageData.ogSiteName || "N/A"}
- Headings: ${JSON.stringify(pageData.headings)}
- Existing Page Schema: ${existingSchemaStr}
- Requested Schema Type: ${typeHint}

Webpage Body Text Sample:
${pageData.textSample}

Return the complete JSON object now.`;

  const requestBody = {
    contents: [
      {
        role: "user",
        parts: [{ text: `${systemInstruction}\n\n${promptContent}` }]
      }
    ],
    generationConfig: {
      response_mime_type: "application/json",
      temperature: 0.1
    }
  };

  const response = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(requestBody)
  });

  if (!response.ok) {
    const errText = await response.text();
    let detail = errText;
    try {
      const errJson = JSON.parse(errText);
      if (errJson.error && errJson.error.message) {
        detail = errJson.error.message;
      }
    } catch (_) {}

    if (response.status === 404 && detail.includes("no longer available")) {
      detail += "\n\n💡 Tip: Update model in ⚙️ Settings to 'gemini-3.6-flash' or select Custom Model.";
    }
    throw new Error(`Gemini API Error (${response.status}):\n${detail}`);
  }

  const jsonResponse = await response.json();
  const textOutput = jsonResponse.candidates?.[0]?.content?.parts?.[0]?.text;
  if (!textOutput) {
    throw new Error("Gemini returned an empty response. Please verify page text and try again.");
  }

  const parsed = JSON.parse(textOutput);

  // Accurate Wikidata Entity Resolution (Curated KB + Live Search API, Never Hallucinated)
  updateLoadingStatus("Resolving verified Wikidata identifiers...", "Checking canonical B2B knowledge base and Wikidata API");
  const verifiedEntities = await resolveEntitiesWikidata(parsed.entities || []);

  // Cleanly inject verified sameAs into matching Schema.org entity nodes
  // (Strictly avoids placing sameAs on root @graph containers)
  const jsonLd = injectVerifiedSameAs(parsed.json_ld || {}, verifiedEntities);

  return {
    schema_type: parsed.schema_type || "WebPage",
    export_jsonld: jsonLd,
    nodes: verifiedEntities,
    edges: (parsed.triples || []).map(t => ({
      source: t.source,
      predicate: t.predicate,
      target: t.target,
      confidence: t.confidence || 0.9,
      provenance_sentence: t.provenance_sentence || ""
    })),
    export_turtle: parsed.rdf_turtle || generateDefaultTurtle(pageData, parsed)
  };
}

// ---------------------------------------------------------------------------
// 2. Client-Side Heuristic Generator with Verified Wikidata Grounding
// ---------------------------------------------------------------------------
async function generateWithHeuristics(pageData, typeHint) {
  const urlObj = new URL(pageData.url);
  const domain = urlObj.hostname.replace("www.", "");
  const brand = pageData.ogSiteName || domain.split(".")[0].toUpperCase();

  let targetType = typeHint !== "auto" ? typeHint : "WebPage";
  if (typeHint === "auto") {
    const textLower = (pageData.textSample + " " + pageData.title).toLowerCase();
    if (textLower.includes("pricing") || textLower.includes("per month") || textLower.includes("/mo")) {
      targetType = "Product";
    } else if (textLower.includes("faq") || textLower.includes("frequently asked questions")) {
      targetType = "FAQPage";
    } else if (textLower.includes("software") || textLower.includes("platform") || textLower.includes("api") || textLower.includes("integration")) {
      targetType = "SoftwareApplication";
    } else if (urlObj.pathname.includes("/blog/") || urlObj.pathname.includes("/news/")) {
      targetType = "Article";
    }
  }

  // If page already has JSON-LD of this type, start from it
  let jsonld = null;
  if (pageData.existingJsonLd && pageData.existingJsonLd.length > 0) {
    const existingMatch = pageData.existingJsonLd.find(b => b["@type"] === targetType);
    if (existingMatch) {
      jsonld = Object.assign({}, existingMatch);
    }
  }

  if (!jsonld) {
    jsonld = {
      "@context": "https://schema.org",
      "@type": targetType,
      "name": pageData.title,
      "url": pageData.url,
      "description": pageData.metaDescription || `${brand} official web page.`
    };
  }

  if (targetType === "SoftwareApplication" || targetType === "Product") {
    jsonld["applicationCategory"] = jsonld["applicationCategory"] || "BusinessApplication";
    if (pageData.headings && pageData.headings.length > 0 && !jsonld["featureList"]) {
      jsonld["featureList"] = pageData.headings.slice(0, 8);
    }
  }

  const candidateNodes = [
    { canonical_name: brand, entity_type: "Organization" },
    ...(pageData.headings || []).slice(0, 6).map(h => ({ canonical_name: h, entity_type: "Feature" }))
  ];

  // Resolve verified Wikidata Q-IDs
  const verifiedNodes = await resolveEntitiesWikidata(candidateNodes);

  const brandEntity = verifiedNodes.find(n => n.canonical_name.toLowerCase() === brand.toLowerCase());
  if (brandEntity && brandEntity.wikidata_url && !jsonld["sameAs"]) {
    jsonld["sameAs"] = brandEntity.wikidata_url;
  }

  const edges = (pageData.headings || []).slice(0, 5).map(h => ({
    source: brand,
    predicate: "hasFeature",
    target: h,
    confidence: 0.85,
    provenance_sentence: `Identified heading: ${h}`
  }));

  return {
    schema_type: targetType,
    export_jsonld: injectVerifiedSameAs(jsonld, verifiedNodes),
    nodes: verifiedNodes,
    edges: edges,
    export_turtle: generateDefaultTurtle(pageData, { schema_type: targetType, entities: verifiedNodes, triples: edges })
  };
}

function generateDefaultTurtle(pageData, parsed) {
  const domain = new URL(pageData.url).hostname.replace("www.", "");
  return `@prefix schema: <http://schema.org/> .
@prefix ex: <https://${domain}/ontology/> .

<${pageData.url}> a schema:${parsed.schema_type || "WebPage"} ;
    schema:name "${(pageData.title || "").replace(/"/g, '\\"')}" ;
    schema:url <${pageData.url}> .
`;
}

/**
 * Inject verified sameAs links into appropriate Schema.org entity nodes.
 * Strictly avoids placing sameAs on root @graph containers.
 */
function injectVerifiedSameAs(jsonLd, verifiedEntities) {
  if (!jsonLd || typeof jsonLd !== "object" || !verifiedEntities || verifiedEntities.length === 0) {
    return jsonLd;
  }

  // Map canonical name -> verified wikidata_url
  const entityMap = new Map();
  verifiedEntities.forEach(e => {
    if (e.wikidata_url && e.canonical_name) {
      entityMap.set(e.canonical_name.toLowerCase().trim(), e.wikidata_url);
    }
  });

  if (entityMap.size === 0) {
    if (Array.isArray(jsonLd["@graph"])) {
      delete jsonLd["sameAs"];
    }
    return jsonLd;
  }

  // Recursive helper to attach sameAs directly to matching entity nodes
  const attachToNode = (node) => {
    if (!node || typeof node !== "object") return;

    if (node.name && typeof node.name === "string") {
      const key = node.name.toLowerCase().trim();
      if (entityMap.has(key)) {
        node.sameAs = entityMap.get(key);
      }
    }

    // Traverse common Schema.org relational fields
    const relationFields = ["about", "mentions", "publisher", "author", "creator", "brand", "itemReviewed", "provider", "offers"];
    for (const field of relationFields) {
      if (node[field]) {
        if (Array.isArray(node[field])) {
          node[field].forEach(item => attachToNode(item));
        } else if (typeof node[field] === "object") {
          attachToNode(node[field]);
        }
      }
    }
  };

  // If jsonLd has an @graph array, attach sameAs ONLY to nodes inside the graph
  if (Array.isArray(jsonLd["@graph"])) {
    jsonLd["@graph"].forEach(item => attachToNode(item));
    // Clean up any misplaced root-level sameAs
    delete jsonLd["sameAs"];
  } else {
    // Single entity (e.g. SoftwareApplication, Product, Organization)
    attachToNode(jsonLd);
  }

  return jsonLd;
}

// ---------------------------------------------------------------------------
// 3. OntoLeap Backend Engine (Localhost / Cloud Run)
// ---------------------------------------------------------------------------
async function generateWithBackend(pageData, typeHint) {
  const endpoint = `${config.backendUrl}/api/kg/page`;
  const headers = { "Content-Type": "application/json" };
  if (config.backendApiKey) {
    headers["X-Ontoleap-Key"] = config.backendApiKey;
    headers["Authorization"] = `Bearer ${config.backendApiKey}`;
  }

  const payload = {
    url: pageData.url,
    html_content: pageData.html || null,
    vertical_id: null
  };

  const response = await fetch(endpoint, {
    method: "POST",
    headers: headers,
    body: JSON.stringify(payload)
  });

  if (!response.ok) {
    const errText = await response.text();
    throw new Error(`OntoLeap Server Error (${response.status}): ${errText}`);
  }

  const data = await response.json();
  const nodes = await resolveEntitiesWikidata(data.nodes || []);

  return {
    schema_type: data.export_jsonld?.["@graph"]?.[0]?.["@type"] || "SoftwareApplication",
    export_jsonld: injectVerifiedSameAs(data.export_jsonld, nodes),
    nodes: nodes,
    edges: data.edges || [],
    export_turtle: data.export_turtle
  };
}

// Render Results to UI
function renderResults(data) {
  badgeType.textContent = data.schema_type || "WebPage";
  badgeEntities.textContent = (data.nodes || []).length;
  badgeTriples.textContent = (data.edges || []).length;

  // JSON-LD
  if (data.export_jsonld) {
    codeJsonLd.textContent = JSON.stringify(data.export_jsonld, null, 2);
  } else {
    codeJsonLd.textContent = "// No JSON-LD schema generated.";
  }

  // Turtle
  if (data.export_turtle) {
    codeTurtle.textContent = data.export_turtle;
  } else {
    codeTurtle.textContent = "# No RDF Turtle generated.";
  }

  // Entities
  entitiesList.innerHTML = "";
  const nodes = data.nodes || [];
  if (nodes.length === 0) {
    entitiesList.innerHTML = `<div class="item-card"><span class="item-evidence">No distinct entities identified.</span></div>`;
  } else {
    nodes.forEach(node => {
      const card = document.createElement("div");
      card.className = "item-card";

      const titleRow = document.createElement("div");
      titleRow.className = "item-title-row";

      const nameSpan = document.createElement("span");
      nameSpan.className = "item-name";
      nameSpan.textContent = node.canonical_name;

      const typeBadge = document.createElement("span");
      typeBadge.className = "item-badge";
      typeBadge.textContent = node.entity_type;

      titleRow.appendChild(nameSpan);
      titleRow.appendChild(typeBadge);
      card.appendChild(titleRow);

      if (node.wikidata_id) {
        const wikiLink = document.createElement("a");
        wikiLink.className = "item-badge wiki";
        wikiLink.href = node.wikidata_url || `https://www.wikidata.org/wiki/${node.wikidata_id}`;
        wikiLink.target = "_blank";
        wikiLink.textContent = `✓ Wikidata: ${node.wikidata_id} ↗`;
        card.appendChild(wikiLink);
      }

      entitiesList.appendChild(card);
    });
  }

  // Triples
  triplesList.innerHTML = "";
  const edges = data.edges || [];
  if (edges.length === 0) {
    triplesList.innerHTML = `<div class="item-card"><span class="item-evidence">No relational capabilities detected.</span></div>`;
  } else {
    edges.forEach(edge => {
      const card = document.createElement("div");
      card.className = "item-card";

      const titleRow = document.createElement("div");
      titleRow.className = "item-title-row";

      const relationSpan = document.createElement("span");
      relationSpan.className = "item-name";
      relationSpan.textContent = `${edge.source} ➔ ${edge.predicate} ➔ ${edge.target}`;

      const confBadge = document.createElement("span");
      confBadge.className = "item-badge";
      confBadge.textContent = `${Math.round((edge.confidence || 0.9) * 100)}% conf`;

      titleRow.appendChild(relationSpan);
      titleRow.appendChild(confBadge);
      card.appendChild(titleRow);

      if (edge.provenance_sentence) {
        const prov = document.createElement("div");
        prov.className = "item-evidence";
        prov.textContent = `"${edge.provenance_sentence}"`;
        card.appendChild(prov);
      }

      triplesList.appendChild(card);
    });
  }

  resultsContainer.classList.remove("hidden");
}

function updateLoadingStatus(msg, sub) {
  loadingStatus.textContent = msg;
  if (sub) loadingSub.textContent = sub;
}

function showError(msg) {
  errorMessage.textContent = msg;
  errorContainer.classList.remove("hidden");
}

function hideError() {
  errorContainer.classList.add("hidden");
}

function showToast(msg) {
  toast.textContent = msg;
  toast.classList.remove("hidden");
  setTimeout(() => toast.classList.add("hidden"), 2500);
}

function copyToClipboard(text, successMsg) {
  navigator.clipboard.writeText(text).then(() => {
    showToast(successMsg);
  }).catch(err => {
    console.error("Clipboard copy failed:", err);
  });
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
