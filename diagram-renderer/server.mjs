import { execFile } from "node:child_process";
import crypto from "node:crypto";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { promisify } from "node:util";

import express from "express";
import sharp from "sharp";

const execFileAsync = promisify(execFile);
const app = express();
app.use(express.json({ limit: "32kb" }));

const forbidden = /%%\{|click\s|https?:\/\//i;
const allowedStart = /^(flowchart|graph|sequenceDiagram|stateDiagram)\b/i;

app.get("/health", (_request, response) => response.json({ status: "ok" }));

app.post("/render", async (request, response) => {
  const source = String(request.body?.source ?? "").trim();
  const width = Number(request.body?.width ?? 1600);
  const height = Number(request.body?.height ?? 900);
  if (!source || source.length > 12000 || forbidden.test(source) || !allowedStart.test(source)) {
    return response.status(400).json({ detail: "Invalid Mermaid source" });
  }
  if (width !== 1600 || height !== 900) {
    return response.status(400).json({ detail: "Only 1600x900 output is supported" });
  }

  const directory = await mkdtemp(path.join(tmpdir(), "courseflow-mermaid-"));
  const input = path.join(directory, "diagram.mmd");
  const rawOutput = path.join(directory, "raw.png");
  const output = path.join(directory, "diagram.png");
  const config = path.join(directory, "config.json");
  const puppeteerConfig = path.join(directory, "puppeteer.json");
  try {
    await writeFile(input, source, "utf8");
    await writeFile(
      config,
      JSON.stringify({
        securityLevel: "strict",
        htmlLabels: false,
        deterministicIds: true,
        deterministicIDSeed: "courseflow",
        theme: "neutral"
      }),
      "utf8",
    );
    await writeFile(
      puppeteerConfig,
      JSON.stringify({
        executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || "/usr/bin/chromium",
        args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
      }),
      "utf8",
    );
    await execFileAsync(
      "/app/node_modules/.bin/mmdc",
      [
        "-i", input,
        "-o", rawOutput,
        "-c", config,
        "-p", puppeteerConfig,
        "-b", "white",
        "-w", "1500",
        "-H", "800",
      ],
      { timeout: 60000, maxBuffer: 1024 * 1024 },
    );
    await sharp(rawOutput)
      .resize(width, height, { fit: "contain", background: "white" })
      .png({ compressionLevel: 9 })
      .toFile(output);
    const content = await readFile(output);
    response
      .set("Content-Type", "image/png")
      .set("X-Request-ID", crypto.randomUUID())
      .send(content);
  } catch (error) {
    response.status(422).json({ detail: String(error?.stderr || error?.message || error).slice(0, 1000) });
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

app.listen(3010, "0.0.0.0");
