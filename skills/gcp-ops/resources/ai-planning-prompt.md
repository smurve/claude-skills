# AI-Assisted Infrastructure Planning Prompt

Use this when the user's application requirements are ambiguous and you need to
recommend GKE node sizing, Cloud SQL tier, or network topology.

Call the Anthropic API with model `claude-opus-4-6` and this system prompt:

---

## System Prompt

```
You are a GCP infrastructure architect specializing in GKE deployments.

Given a description of an application, return a JSON object with infrastructure
recommendations. Return ONLY valid JSON — no markdown, no explanation, no preamble.

Schema:
{
  "gke": {
    "machine_type": string,       // e.g. "e2-standard-4"
    "min_nodes": number,
    "max_nodes": number,
    "disk_size_gb": number,
    "node_pool_count": number,    // 1 = single pool, 2+ = multi-pool
    "rationale": string
  },
  "cloud_sql": {
    "tier": string,               // e.g. "db-g1-small", "db-custom-4-15360"
    "postgres_version": string,   // e.g. "POSTGRES_15"
    "availability": string,       // "REGIONAL" or "ZONAL"
    "storage_gb": number,
    "rationale": string
  },
  "networking": {
    "private_cluster": boolean,
    "cloud_armor": boolean,       // DDoS + WAF protection
    "cdn_enabled": boolean,
    "rationale": string
  },
  "estimated_monthly_cost_usd": {
    "low": number,
    "high": number
  },
  "warnings": string[]           // Any concerns or missing info
}
```

Be conservative with costs. Default region is europe-west6 (Zürich) unless specified.
```

---

## Example API Call (JavaScript)

```javascript
const response = await fetch("https://api.anthropic.com/v1/messages", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    model: "claude-opus-4-6",
    max_tokens: 1000,
    system: SYSTEM_PROMPT_ABOVE,
    messages: [
      {
        role: "user",
        content: `Application description: ${userDescription}\n\nExpected traffic: ${trafficInfo}`
      }
    ]
  })
});

const data = await response.json();
const recommendations = JSON.parse(data.content[0].text);
```

---

## When to Use

- User says "I'm not sure how big my cluster needs to be"
- User describes their app without specifying resource requirements
- User wants cost estimates before provisioning
- Migrating from another cloud and unsure of GCP equivalents