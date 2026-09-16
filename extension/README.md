# GainARK OntoLeap — Standalone Browser Extension (Manifest V3)

> **Standalone Schema.org JSON-LD Structured Data & Knowledge Graph Generator for Any Web Page**  
> **Zero Server Required** • Direct Gemini Flash AI • Client-Side Heuristics • Optional OntoLeap Engine Toggle

---

## ⚡ What Makes It Standalone?

Unlike traditional tools that require a running Python/Node backend:
1. **Direct-to-Browser Gemini AI (Zero Server)**: Calls Google's Gemini Flash API directly from the browser using your free Gemini key. It extracts rich Schema.org structured data, grounds entities to Wikidata Q-IDs, and mines relational capabilities.
2. **Instant Heuristic Fallback (Zero Key, Zero Server)**: Works right out of the box even before adding an API key by extracting in-tab metadata, OpenGraph tags, and headings to generate valid Schema.org.
3. **Bypasses Bot Blockers & Cloudflare**: Because it runs inside your active browser tab, it extracts the live rendered DOM (`outerHTML`) regardless of paywalls, captchas, or login sessions.
4. **Full SPA & Dynamic JS Support**: Works seamlessly on React, Vue, Next.js, and client-hydrated single-page apps.

---

## 📥 Quick Install (Chrome / Brave / Edge)

1. Open your browser and go to your extensions manager:
   * **Chrome**: `chrome://extensions`
   * **Brave**: `brave://extensions`
   * **Edge**: `edge://extensions`
2. Turn on **Developer mode** (toggle in the top-right corner).
3. Click **Load unpacked** (top-left button).
4. Select the `extension` folder inside this repository:
   ```
   d:\gainARK\drive-download-20260904T061910Z-1-001\Ontology\extension
   ```
5. Pin **OntoLeap** to your browser toolbar!

---

## 🚀 How to Use (Step-by-Step)

### 1. (Optional but Recommended) Add Your Free Gemini Key
1. Get a free API key at [Google AI Studio (aistudio.google.com/app/apikey)](https://aistudio.google.com/app/apikey).
2. Click the **⚙️** icon in the OntoLeap extension popup.
3. Paste your Gemini API key and click **Save Configuration**.  
   *(Your key is held in `chrome.storage.local`, so it stays on this machine — it is not synced to your Google account, and it is sent to nobody but Google's own Gemini endpoint when you generate. Add it again on each device you use.)*

### 2. Generate Schema on Any Page
1. Navigate to any website (e.g. `https://stripe.com/pricing`, `https://linear.app`, or your own web app).
2. Click the **OntoLeap** extension icon.
3. Select your desired schema type (or leave on **Auto-Detect**).
4. Click **Generate Schema & Graph**.
5. You'll instantly receive:
   * **Schema.org JSON-LD**: Ready to copy and paste into your CMS or site header.
   * **Google Rich Results Test**: 1-click button to test your schema live in Google's official validator.
   * **Grounded Entities**: Badges linking to verified Wikidata Q-IDs.
   * **Relational Capabilities**: Evidenced statements (`integratesWith`, `compliesWith`, `automates`, `hasFeature`).
   * **RDF Turtle**: W3C-compliant graph snippet.

---

## 🔄 Engine Modes in Settings (⚙️)

You can toggle between three modes at any time:
1. **⚡ Standalone Gemini AI (Recommended)**: 100% serverless; direct Gemini 2.0 / 1.5 Flash extraction.
2. **🧩 Standalone Heuristics**: 100% serverless, zero key needed; fast DOM & meta tag parser.
3. **🖥️ OntoLeap Engine**: Connects to your local or Cloud Run Python server for enterprise W3C RDFLib SKOS taxonomy alignment and OWL 2 DL ontologies.

---

## 🔒 Permissions

The extension reads a page only when you click its icon (`activeTab`), and asks the browser
for nothing beyond the hosts it actually calls: the Gemini API, Wikidata, `localhost:8080`
and the deployed OntoLeap server. It holds no standing access to the sites you browse.

Point **OntoLeap Engine** mode at a server of your own and Chrome will ask you to allow
that one origin the first time you save it. Decline and the setting is still stored, but
the extension cannot reach it until you allow it.

### Letting your server answer the extension

A server refuses a browser extension by default, because allowing the
`chrome-extension://` scheme by pattern would allow *every* extension the user has
installed. List this one instead — its id is shown under it on `chrome://extensions`, and
an unpacked build gets a different id on every machine it is loaded on:

```bash
ALLOWED_EXTENSION_IDS=your32letterextensionidgoeshere
```

Comma-separate several. Until it is set, the server's log says so at startup and backend
mode fails its preflight.
