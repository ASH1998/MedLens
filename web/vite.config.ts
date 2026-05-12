import { defineConfig } from "vite";
import type { ViteDevServer } from "vite";
import react from "@vitejs/plugin-react";
import crypto from "node:crypto";
import fs from "node:fs";
import type { IncomingMessage, ServerResponse } from "node:http";
import path from "node:path";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react(), bedrockDevProxy()],
  server: { port: 5173 },
});

function bedrockDevProxy() {
  return {
    name: "medlens-bedrock-dev-proxy",
    configureServer(server: ViteDevServer) {
      server.middlewares.use("/api/bedrock/claude", async (req: IncomingMessage, res: ServerResponse) => {
        if (req.method !== "POST") {
          res.statusCode = 405;
          res.end("Method not allowed");
          return;
        }
        try {
          const env = loadRootEnv();
          const model = requiredEnv(env, "CLAUDE_MODEL");
          const region = env.AWS_REGION || "us-east-1";
          const payload = JSON.parse(await readRequestBody(req));
          const upstream = await invokeBedrock({
            accessKeyId: requiredEnv(env, "AWS_ACCESS_KEY_ID"),
            secretAccessKey: requiredEnv(env, "AWS_SECRET_ACCESS_KEY"),
            sessionToken: env.AWS_SESSION_TOKEN,
            region,
            model,
            payload,
          });
          res.statusCode = upstream.status;
          res.setHeader("Content-Type", upstream.contentType || "application/json");
          res.end(upstream.body);
        } catch (err) {
          res.statusCode = 500;
          res.setHeader("Content-Type", "application/json");
          res.end(JSON.stringify({ error: err instanceof Error ? err.message : String(err) }));
        }
      });
    },
  };
}

interface BedrockInvokeArgs {
  accessKeyId: string;
  secretAccessKey: string;
  sessionToken?: string;
  region: string;
  model: string;
  payload: unknown;
}

async function invokeBedrock(args: BedrockInvokeArgs): Promise<{ status: number; body: string; contentType: string | null }> {
  const service = "bedrock";
  const host = `bedrock-runtime.${args.region}.amazonaws.com`;
  const { requestUri, canonicalUri } = bedrockModelInvokeUris(args.model);
  const body = JSON.stringify(args.payload);
  const now = new Date();
  const amzDate = toAmzDate(now);
  const dateStamp = amzDate.slice(0, 8);
  const bodyHash = sha256Hex(body);
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Host: host,
    "X-Amz-Date": amzDate,
  };
  if (args.sessionToken) headers["X-Amz-Security-Token"] = args.sessionToken;

  const sortedHeaderKeys = Object.keys(headers).sort();
  const signedHeaders = sortedHeaderKeys.map((key) => key.toLowerCase()).join(";");
  const canonicalHeaders = sortedHeaderKeys.map((key) => `${key.toLowerCase()}:${headers[key].trim()}\n`).join("");
  const canonicalRequest = ["POST", canonicalUri, "", canonicalHeaders, signedHeaders, bodyHash].join("\n");
  const credentialScope = `${dateStamp}/${args.region}/${service}/aws4_request`;
  const stringToSign = [
    "AWS4-HMAC-SHA256",
    amzDate,
    credentialScope,
    sha256Hex(canonicalRequest),
  ].join("\n");
  const signature = hmacHex(signingKey(args.secretAccessKey, dateStamp, args.region, service), stringToSign);
  headers.Authorization =
    `AWS4-HMAC-SHA256 Credential=${args.accessKeyId}/${credentialScope}, ` +
    `SignedHeaders=${signedHeaders}, Signature=${signature}`;

  const response = await fetch(`https://${host}${requestUri}`, {
    method: "POST",
    headers,
    body,
  });
  return {
    status: response.status,
    body: await response.text(),
    contentType: response.headers.get("content-type"),
  };
}

function loadRootEnv(): Record<string, string> {
  const env: Record<string, string> = { ...process.env } as Record<string, string>;
  const envPath = path.resolve(process.cwd(), "..", ".env");
  if (!fs.existsSync(envPath)) return env;
  for (const rawLine of fs.readFileSync(envPath, "utf8").split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const index = line.indexOf("=");
    const key = line.slice(0, index).trim();
    const value = line.slice(index + 1).trim().replace(/^['"]|['"]$/g, "");
    if (!(key in env)) env[key] = value;
  }
  return env;
}

function requiredEnv(env: Record<string, string>, key: string): string {
  const value = env[key];
  if (!value) throw new Error(`Missing ${key} in ../.env`);
  return value;
}

function readRequestBody(req: NodeJS.ReadableStream): Promise<string> {
  return new Promise((resolve, reject) => {
    let body = "";
    req.setEncoding("utf8");
    req.on("data", (chunk) => {
      body += chunk;
    });
    req.on("end", () => resolve(body));
    req.on("error", reject);
  });
}

function bedrockModelInvokeUris(model: string): { requestUri: string; canonicalUri: string } {
  const requestModel = encodeURIComponent(model);
  const canonicalModel = encodeURIComponent(requestModel);
  return {
    requestUri: `/model/${requestModel}/invoke`,
    canonicalUri: `/model/${canonicalModel}/invoke`,
  };
}

function toAmzDate(date: Date): string {
  return date.toISOString().replace(/[:-]|\.\d{3}/g, "");
}

function sha256Hex(value: string): string {
  return crypto.createHash("sha256").update(value, "utf8").digest("hex");
}

function hmac(key: crypto.BinaryLike, value: string): Buffer {
  return crypto.createHmac("sha256", key).update(value, "utf8").digest();
}

function hmacHex(key: crypto.BinaryLike, value: string): string {
  return crypto.createHmac("sha256", key).update(value, "utf8").digest("hex");
}

function signingKey(secret: string, dateStamp: string, region: string, service: string): Buffer {
  const dateKey = hmac(`AWS4${secret}`, dateStamp);
  const regionKey = hmac(dateKey, region);
  const serviceKey = hmac(regionKey, service);
  return hmac(serviceKey, "aws4_request");
}
