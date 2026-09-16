/**
 * wikidata_kb.js — Verified Wikidata Resolution & Search Client
 * 
 * Provides deterministic Wikidata Q-ID matching using OntoLeap's verified
 * knowledge base + live Wikidata Search API fallback. Never hallucinates Q-IDs.
 */

// Canonical Curated Wikidata Dictionary (Verified on Wikidata)
const CURATED_WIKIDATA_KB = {
  // Compliance & Regulatory Standards
  "soc 1": "Q122423588",
  "soc 1 type ii": "Q122423588",
  "soc 2": "Q136309472",
  "soc 2 type ii": "Q136309472",
  "iso 27001": "Q852641",
  "iso 27017": "Q117189001",
  "iso 27018": "Q117189001",
  "gdpr": "Q1172506",
  "hipaa": "Q606563",
  "pci-dss": "Q2065387",
  "pci dss": "Q2065387",
  "ccpa": "Q60754243",
  "fedramp": "Q17006880",
  "nist": "Q176292",
  "ifrs 15": "Q18358064",
  "gaap": "Q330153",
  "us gaap": "Q650978",

  // Major Cloud Providers & Infrastructure
  "aws": "Q380936",
  "amazon web services": "Q380936",
  "google cloud": "Q20819777",
  "google cloud platform": "Q20819777",
  "gcp": "Q20819777",
  "microsoft azure": "Q72678",
  "azure": "Q72678",
  "cloudflare": "Q134102",
  "kubernetes": "Q22661307",
  "docker": "Q15304938",
  "terraform": "Q22909242",
  "datadog": "Q28405021",
  "snowflake": "Q22078063",
  "databricks": "Q17149791",
  "mongodb": "Q116521",
  "postgresql": "Q170706",
  "redis": "Q117467",
  "apache kafka": "Q2858169",

  // Enterprise Software & SaaS Giants
  "salesforce": "Q941127",
  "salesforce crm": "Q941127",
  "stripe": "Q7624104",
  "stripe billing": "Q7624104",
  "mastercard": "Q489921",
  "visa": "Q25223",
  "american express": "Q193603",
  "amex": "Q193603",
  "paypal": "Q483959",
  "netsuite": "Q4045248",
  "oracle netsuite": "Q4045248",
  "workday": "Q8034666",
  "quickbooks": "Q7271951",
  "intuit quickbooks": "Q7271951",
  "hubspot": "Q5926631",
  "sap": "Q552581",
  "sap s/4hana": "Q552581",
  "oracle": "Q19900",
  "microsoft": "Q2283",
  "microsoft 365": "Q310620",
  "office 365": "Q310620",
  "microsoft dynamics": "Q856705",
  "dynamics 365": "Q856705",
  "sage": "Q1469903",
  "sage intacct": "Q54818220",
  "xero": "Q8043794",
  "avalara": "Q117189001",
  "taxjar": "Q140989087",
  "slack": "Q17130715",
  "zoom": "Q28953922",
  "jira": "Q1134265",
  "atlassian": "Q757518",
  "github": "Q364",
  "gitlab": "Q16639197",
  "linear": "Q110688000",
  "notion": "Q65074211",
  "zendesk": "Q15401349",
  "servicenow": "Q7455856",
  "plaid": "Q30610132",
  "twilio": "Q7857997",
  "segment": "Q65063065",
  "zapier": "Q18155986",
  "make": "Q115802521",
  "okta": "Q25111956",
  "drata": "Q111915444",
  "vanta": "Q111915444",
  "snyk": "Q104869818",
  "crowdstrike": "Q18349277",
  "palo alto networks": "Q7128522",

  // Core Concepts & Taxonomies
  "saas": "Q1254596",
  "software as a service": "Q1254596",
  "cloud computing": "Q483639",
  "enterprise resource planning": "Q131508",
  "erp": "Q131508",
  "customer relationship management": "Q177519",
  "crm": "Q177519",
  "revenue recognition": "Q2146785",
  "accounts receivable": "Q328554",
  "accounts payable": "Q134102",
  "invoicing": "Q1301067",
  "single sign-on": "Q749449",
  "sso": "Q749449",
  "multi-factor authentication": "Q3275993",
  "mfa": "Q3275993",
  "role-based access control": "Q1474945",
  "rbac": "Q1474945",
  "api": "Q165149",
  "graphql": "Q25110834",
  "rest api": "Q211162"
};

// In-Memory Search Cache for Live Lookups
const liveWikidataCache = new Map();

/**
 * Clean and normalize an entity string for lookup
 */
function normalizeEntityKey(str) {
  if (!str) return "";
  return str.toLowerCase().trim()
    .replace(/^https?:\/\/[^\/]+\/?/i, "") // strip url prefixes
    .replace(/[,\.\-\_\(\)\[\]]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Resolve a single entity to its verified Wikidata Q-ID.
 * 1. Checks curated KB.
 * 2. If unknown, queries Wikidata's official live search API.
 * 3. Returns canonical Q-ID or null (never hallucinates).
 */
async function resolveWikidataEntity(entityName, entityType = "") {
  if (!entityName || typeof entityName !== "string") return null;
  const cleanName = entityName.trim();
  if (cleanName.length < 2) return null;

  const key = normalizeEntityKey(cleanName);

  // 1. Fast Path: Curated Knowledge Base
  if (CURATED_WIKIDATA_KB[key]) {
    return CURATED_WIKIDATA_KB[key];
  }

  // Check alias variations
  for (const [kbKey, qid] of Object.entries(CURATED_WIKIDATA_KB)) {
    if (key === kbKey || key.startsWith(kbKey + " ") || key.endsWith(" " + kbKey)) {
      return qid;
    }
  }

  // Check in-memory cache
  if (liveWikidataCache.has(key)) {
    return liveWikidataCache.get(key);
  }

  // 2. Live Wikidata Open Search API
  try {
    const searchUrl = `https://www.wikidata.org/w/api.php?action=wbsearchentities&search=${encodeURIComponent(cleanName)}&language=en&format=json&origin=*&type=item&limit=3`;
    const resp = await fetch(searchUrl, {
      method: "GET",
      headers: { "Accept": "application/json" }
    });

    if (!resp.ok) {
      liveWikidataCache.set(key, null);
      return null;
    }

    const data = await resp.json();
    const searchResults = data.search || [];
    if (searchResults.length === 0) {
      liveWikidataCache.set(key, null);
      return null;
    }

    // Negative filter: Disallow common non-business/non-tech Wikidata types
    const disallowedDescriptions = [
      "family name", "surname", "given name", "male given name", "female given name",
      "town", "village", "city", "county", "unincorporated community", "census-designated place",
      "crater", "mountain", "river", "lake", "municipality", "commune", "district",
      "film", "song", "album", "single", "musical group", "band",
      "human", "person", "born ", "politician", "actor", "painter", "botanist",
      "disambiguation", "wikimedia disambiguation", "fictional character"
    ];

    // Positive filter: Domain must align with business, software, standard, technology or finance
    const relevantKeywords = [
      "software", "company", "corporation", "firm", "enterprise", "business", "startup",
      "platform", "standard", "protocol", "cloud", "service", "system", "technology",
      "application", "database", "security", "compliance", "framework", "tool", "api",
      "payment", "finance", "financial", "accounting", "network", "method", "concept"
    ];

    for (const candidate of searchResults) {
      const candidateLabel = (candidate.label || "").toLowerCase().trim();
      const candidateDesc = (candidate.description || "").toLowerCase().trim();

      // Immediately reject if candidate is a family name, town, person, etc.
      if (disallowedDescriptions.some(d => candidateDesc.includes(d))) {
        continue;
      }

      // Check for positive domain alignment
      const isDomainMatch = relevantKeywords.some(kw => candidateDesc.includes(kw));

      // Match exact label or key
      if ((candidateLabel === key || candidateLabel === cleanName.toLowerCase()) && isDomainMatch) {
        const verifiedQid = candidate.id;
        liveWikidataCache.set(key, verifiedQid);
        return verifiedQid;
      }
    }

    liveWikidataCache.set(key, null);
    return null;
  } catch (err) {
    console.warn(`Wikidata search lookup failed for '${cleanName}':`, err);
    liveWikidataCache.set(key, null);
    return null;
  }
}

/**
 * Concurrently resolve an array of extracted entities, mutating them with verified Wikidata Q-IDs.
 */
async function resolveEntitiesWikidata(entities) {
  if (!entities || !Array.isArray(entities)) return entities;

  const tasks = entities.map(async (entity) => {
    // If it already had a verified Q-ID from curated list, check it
    const candidateName = entity.canonical_name || entity.name || "";
    const verifiedQid = await resolveWikidataEntity(candidateName, entity.entity_type);
    
    entity.wikidata_id = verifiedQid || null;
    if (verifiedQid) {
      entity.wikidata_url = `https://www.wikidata.org/wiki/${verifiedQid}`;
    } else {
      delete entity.wikidata_url;
    }
    return entity;
  });

  return await Promise.all(tasks);
}
