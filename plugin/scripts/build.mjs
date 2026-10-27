import { build } from "esbuild";
import { readFile, writeFile, mkdir } from "node:fs/promises";
await mkdir("dist", { recursive: true });
await build({
  entryPoints: ["src/server.ts"],
  bundle: true,
  platform: "node",
  format: "esm",
  target: "node22",
  outfile: "dist/server.mjs",
  banner: {
    js: "import {createRequire} from 'node:module'; const require=createRequire(import.meta.url);",
  },
});
const bundle = await build({
  entryPoints: ["src/panel.ts"],
  bundle: true,
  platform: "browser",
  format: "esm",
  target: "es2022",
  write: false,
  outfile: "dist/panel.js",
  loader: { ".module.css": "local-css" },
  define: { "process.env.NODE_ENV": '"production"' },
  minify: true,
});
const html = (await readFile("src/panel.html", "utf8"))
  .replace(
    "/*TWEET_CSS*/",
    () => bundle.outputFiles.find((f) => f.path.endsWith(".css"))?.text || "",
  )
  .replace("/*BUNDLE*/", () =>
    bundle.outputFiles
      .find((f) => f.path.endsWith(".js"))
      .text.replace(/<\/script/gi, "<\\/script"),
  );
await writeFile("dist/panel.html", html);
