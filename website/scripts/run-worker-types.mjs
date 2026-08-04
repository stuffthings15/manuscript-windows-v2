import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const wrangler = fileURLToPath(
  new URL("../node_modules/wrangler/bin/wrangler.js", import.meta.url),
);
const check = process.argv.includes("--check");
const args = [
  wrangler,
  "types",
  "worker-configuration.d.ts",
  "-c",
  "wrangler.types.jsonc",
  "--env-file",
  ".dev.vars.example",
  "--env-interface",
  "CloudflareEnv",
  "--include-runtime",
  "true",
  "--include-env",
  "true",
];
if (check) args.push("--check");

const result = spawnSync(process.execPath, args, {
  cwd: root,
  env: {
    ...process.env,
    WRANGLER_LOG_PATH: ".wrangler/logs",
    WRANGLER_WRITE_LOGS: "false",
  },
  stdio: "inherit",
});
process.exit(result.status ?? 1);
