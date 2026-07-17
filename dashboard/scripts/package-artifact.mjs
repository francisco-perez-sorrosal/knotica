import { copyFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const here = new URL("..", import.meta.url);
const source = new URL("dist/index.html", here);
const destination = new URL("../src/knotica/dashboard/app.html", here);

await copyFile(fileURLToPath(source), fileURLToPath(destination));
