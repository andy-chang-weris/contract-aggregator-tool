/**
 * MCP server for the Contract Aggregator tool.
 *
 * Scope note: this server exposes contract data only. Outlook sending is
 * intentionally NOT implemented here. ChatGPT's existing Outlook connector
 * handles drafting/sending after this MCP server returns contract data.
 *
 * Source-of-truth check performed before writing this file, and
 * re-verified against project knowledge this turn:
 *   - GET  /api/opportunities  confirmed in proxy.py, used by search_contracts
 *   - POST /api/feedback       confirmed in proxy.py, used by mark_feedback
 *   - GET  /api/opportunities/<id>  still NOT found anywhere in project
 *     knowledge. get_contract_details below calls this URL, but the route
 *     does not exist yet in proxy.py and will 404 until you add it.
 *
 * I cannot confirm the /api/opportunities/<id> route exists. Treat that
 * tool as unverified until the backend route is added and tested.
 */

import express from "express";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { z } from "zod";

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:5000";
const PORT = process.env.PORT || 3000;

// Confirmed against agent/interactive-ui/index.html's DEFAULT_CLIENT_ID, and
// verified as a real working row in the production clients table by a
// successful POST /api/feedback test earlier in this project (returned
// feedback.id: 37, status: ok). Overridable via env var if you ever need a
// different single client without editing code.
const DEFAULT_CLIENT_ID =
  process.env.DEFAULT_CLIENT_ID || "9f68f2aa-fc61-496f-81ed-b89ee5a92cdf";

function buildServer() {
  const server = new McpServer({
    name: "contract-aggregator",
    version: "0.1.0",
  });

// ── search_contracts ────────────────────────────────────────────────────
// Verified against GET /api/opportunities in proxy.py.
server.registerTool(
  "search_contracts",
  {
    title: "Search Contracts",
    description:
      "Search Virginia government contract opportunities by agency, NAICS code, contract type, and source. Supports sorting by posted_date (chronological) or relevance (best match to the client's saved preferences, for building digests/recommendations).",
    annotations: {
      readOnlyHint: true,
      openWorldHint: false,
      destructiveHint: false,
    },
    inputSchema: {
      agency: z.string().optional().describe("Agency name filter, e.g. 'DEPARTMENT OF TRANSPORTATION'"),
      naics: z
        .array(z.string())
        .optional()
        .describe("One or more NAICS codes, e.g. ['541511', '541512']"),
      contractType: z.string().optional().describe("Substring match against award_status"),
      source: z.string().optional().describe("Exact source_site value"),
      sortBy: z
        .enum(["posted_date", "relevance"])
        .optional()
        .describe(
          "'relevance' ranks by best match to the configured client's saved preferences (uses a different backend endpoint). 'posted_date' sorts chronologically. Use 'relevance' when building a digest of top matches."
        ),
      sortDir: z.enum(["asc", "desc"]).optional().describe("Only applies to sortBy: 'posted_date'."),
      excludeNegativeFeedback: z
        .boolean()
        .optional()
        .describe("Only applies to sortBy: 'relevance'. Excludes postings the client has disliked."),
      limit: z.number().int().min(1).max(1000).optional().default(20),
      offset: z.number().int().min(0).optional().default(0),
    },
  },
  async ({ agency, naics, contractType, source, sortBy, sortDir, excludeNegativeFeedback, limit, offset }) => {
    const params = new URLSearchParams();
    if (agency) params.set("agency", agency);
    if (naics && naics.length > 0) params.set("naics", naics.join(","));
    if (contractType) params.set("contractType", contractType);
    if (source) params.set("source", source);
    params.set("limit", String(limit));
    params.set("offset", String(offset));

    // Verified against proxy.py: GET /api/opportunities only supports
    // sortBy=posted_date (ALLOWED_SORT_FIELDS = {"posted_date"}). Best-match
    // ranking lives on a separate route, GET /api/clients/<id>/ranked-opportunities,
    // confirmed in proxy.py's ranked_opportunities() function. There is no
    // single endpoint that supports both, so this tool switches endpoints
    // based on sortBy rather than passing sortBy=relevance to /api/opportunities,
    // which would silently be ignored by the backend (sort_by falls back to
    // None if not in ALLOWED_SORT_FIELDS).
    let url: string;
    if (sortBy === "relevance") {
      url = `${BACKEND_URL}/api/clients/${DEFAULT_CLIENT_ID}/ranked-opportunities`;
      params.set("excludeNegativeFeedback", String(excludeNegativeFeedback ?? true));
    } else {
      if (sortBy) params.set("sortBy", sortBy);
      if (sortDir) params.set("sortDir", sortDir);
      url = `${BACKEND_URL}/api/opportunities`;
    }

    const res = await fetch(`${url}?${params.toString()}`);
    const data = await res.json();

    if (!res.ok) {
      return {
        content: [{ type: "text", text: `Search failed (${res.status}): ${JSON.stringify(data)}` }],
        isError: true,
      };
    }

    return {
      content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
    };
  }
);

// ── get_contract_details ────────────────────────────────────────────────
// NOT VERIFIED: GET /api/opportunities/<id> does not exist in proxy.py as
// of the last project knowledge search. This will return a 404 until that
// route is added.
server.registerTool(
  "get_contract_details",
  {
    title: "Get Contract Details",
    description:
      "Retrieve full details for a single contract posting by its numeric id. Requires a backend route (GET /api/opportunities/<id>) that does not exist yet in the current repo.",
    annotations: {
      readOnlyHint: true,
      openWorldHint: false,
      destructiveHint: false,
    },
    inputSchema: {
      posting_id: z.number().int().describe("The postings.id value from search_contracts results"),
    },
  },
  async ({ posting_id }) => {
    const res = await fetch(`${BACKEND_URL}/api/opportunities/${posting_id}`);
    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
      return {
        content: [
          {
            type: "text",
            text: `Could not fetch contract ${posting_id} (${res.status}): ${JSON.stringify(
              data
            )}. Note: this endpoint may not exist yet on the backend.`,
          },
        ],
        isError: true,
      };
    }

    return {
      content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
    };
  }
);

// ── save_contract_summary ───────────────────────────────────────────────
// NOT VERIFIED: PATCH /api/opportunities/<id>/summary does not exist yet in
// proxy.py as of this project knowledge search. This tool calls that URL,
// but the route must be added to proxy.py (and the ai_summary column added
// via db.py's columns_to_ensure) before this will work. It will 404 until
// then.
server.registerTool(
  "save_contract_summary",
  {
    title: "Save Contract Summary",
    description:
      "Save an AI-generated plain-language summary for a contract posting, to be displayed in place of the raw description in the web UI. Write the summary yourself based on the posting's title, description, and any other details you have, then call this tool to persist it. Requires a backend route (PATCH /api/opportunities/<id>/summary) that does not exist yet in the current repo.",
    annotations: {
      readOnlyHint: false,
      openWorldHint: false,
      destructiveHint: false,
    },
    inputSchema: {
      posting_id: z.number().int().describe("The postings.id value"),
      ai_summary: z
        .string()
        .min(1)
        .describe("The plain-language AI-written summary of this contract, to replace the raw description"),
    },
  },
  async ({ posting_id, ai_summary }) => {
    const res = await fetch(`${BACKEND_URL}/api/opportunities/${posting_id}/summary`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ai_summary }),
    });
    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
      return {
        content: [
          {
            type: "text",
            text: `Could not save summary for contract ${posting_id} (${res.status}): ${JSON.stringify(
              data
            )}. Note: this endpoint may not exist yet on the backend.`,
          },
        ],
        isError: true,
      };
    }

    return {
      content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
    };
  }
);

// ── mark_feedback ────────────────────────────────────────────────────────
// Verified against POST /api/feedback in proxy.py. action is restricted to
// 'like' or 'dislike' (VALID_FEEDBACK_ACTIONS in proxy.py); rating 1-5 is
// optional.
server.registerTool(
  "mark_feedback",
  {
    title: "Mark Contract Feedback",
    description:
      "Record like/dislike feedback on a specific contract posting for the configured client. client_id is optional and defaults to the single configured client, so it does not need to be asked from the user.",
    annotations: {
      readOnlyHint: false,
      openWorldHint: false,
      destructiveHint: false,
    },
    inputSchema: {
      client_id: z
        .string()
        .uuid()
        .optional()
        .describe(
          "UUID of the client recording feedback. Defaults to the single configured client if omitted."
        ),
      posting_id: z.number().int().describe("The postings.id value"),
      action: z.enum(["like", "dislike"]),
      rating: z.number().int().min(1).max(5).optional(),
      notes: z.string().optional(),
    },
  },
  async ({ client_id, posting_id, action, rating, notes }) => {
    const resolvedClientId = client_id || DEFAULT_CLIENT_ID;
    const res = await fetch(`${BACKEND_URL}/api/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        client_id: resolvedClientId,
        posting_id,
        action,
        rating,
        notes,
        feedback_source: "chatgpt_mcp",
      }),
    });
    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
      return {
        content: [{ type: "text", text: `Feedback failed (${res.status}): ${JSON.stringify(data)}` }],
        isError: true,
      };
    }

    return {
      content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
    };
  }
);

  return server;
}

// Outlook sending is intentionally not implemented as an MCP tool. Per the
// architecture decision above, the ChatGPT agent calls search_contracts and
// get_contract_details, summarizes results, then uses its own Outlook
// connector to draft/send.

const app = express();
app.use(express.json());

// Stateless mode: a new McpServer + transport per request, per the pattern
// shown in the MCP TypeScript SDK's StreamableHTTPServerTransport docs
// (sessionIdGenerator: undefined = stateless). I could not find this exact
// snippet in project knowledge since your repo has no MCP code; this is
// standard SDK usage, not repo-sourced.
app.post("/mcp", async (req, res) => {
  try {
    const server = buildServer();
    const transport = new StreamableHTTPServerTransport({
      sessionIdGenerator: undefined,
    });
    res.on("close", () => {
      transport.close();
      server.close();
    });
    await server.connect(transport);
    await transport.handleRequest(req, res, req.body);
  } catch (err) {
    console.error("MCP request error:", err);
    if (!res.headersSent) {
      res.status(500).json({
        jsonrpc: "2.0",
        error: { code: -32603, message: "Internal server error" },
        id: null,
      });
    }
  }
});

// GET/DELETE are not used in stateless mode; return 405 per the SDK's own
// stateless example rather than leaving them unhandled.
app.get("/mcp", (_req, res) => {
  res.status(405).json({
    jsonrpc: "2.0",
    error: { code: -32000, message: "Method not allowed. This server runs in stateless mode." },
    id: null,
  });
});
app.delete("/mcp", (_req, res) => {
  res.status(405).json({
    jsonrpc: "2.0",
    error: { code: -32000, message: "Method not allowed. This server runs in stateless mode." },
    id: null,
  });
});

app.get("/health", (_req, res) => {
  res.json({ status: "ok" });
});

app.listen(PORT, () => {
  console.log(`Contract Aggregator MCP server listening on port ${PORT}`);
  console.log(`MCP endpoint: http://localhost:${PORT}/mcp`);
});