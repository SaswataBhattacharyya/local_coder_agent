import { build } from "esbuild";
import path from "path";
import fs from "fs";

const root = process.cwd();
const srcDir = path.join(root, "webview-ui", "src");
const outDir = path.join(root, "dist-webview");

if (!fs.existsSync(srcDir)) {
  console.error("webview-ui/src not found. Skipping webview build.");
  process.exit(1);
}

if (!fs.existsSync(outDir)) {
  fs.mkdirSync(outDir, { recursive: true });
}

await build({
  entryPoints: [path.join(srcDir, "main.tsx")],
  bundle: true,
  format: "iife",
  platform: "browser",
  sourcemap: false,
  outfile: path.join(outDir, "index.js"),
  loader: { ".tsx": "tsx", ".ts": "ts", ".css": "css" },
  define: {
    "process.env.NODE_ENV": "\"production\"",
  },
});

// Copy CSS (if emitted by esbuild, or fallback to source file)
const cssPath = path.join(outDir, "index.css");
if (!fs.existsSync(cssPath)) {
  const srcCss = path.join(srcDir, "styles.css");
  if (fs.existsSync(srcCss)) {
    fs.copyFileSync(srcCss, cssPath);
  }
}
