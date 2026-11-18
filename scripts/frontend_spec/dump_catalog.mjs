// Print one i18n catalog as JSON: node --import ./register_hook.mjs dump_catalog.mjs <catalog.ts> <exportName>
import { pathToFileURL } from "node:url";

const [, , file, exportName] = process.argv;
const mod = await import(pathToFileURL(file).href);
console.log(JSON.stringify(mod[exportName]));
