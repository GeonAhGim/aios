#!/usr/bin/env node
// Lockfile/workspace sync guard (task-3073, DEEPEN of task-1509/CH-1a). CH-1a
// (87da8d1) added frontend/packages/chart-engine as an npm workspace but never
// regenerated frontend/package-lock.json, so `npm ci --workspaces
// --include-workspace-root` (local_ci.py) failed CI red with "Missing:
// @aios/chart-engine@0.0.0 from lock file" (escalations/esc-ci-de7c682bb469.json,
// sha de7c682) until task-1509 (e095abc) patched the lockfile by hand. Nothing
// caught the drift automatically before CI did, and task-1509 itself shipped with
// no test proving the fix or reproducing the break. This ratchet makes that class
// of drift fail locally: every directory matched by root package.json's
// `workspaces` globs must have a `packages/<dir>` entry (name+version) and a
// `node_modules/<name>` link entry in package-lock.json, or npm ci's failure mode
// is reproduced here instead of in CI.
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/** Expands `workspaces` globs of the form "<segment>/*" into existing package directories (posix-relative, sorted). */
export function discoverWorkspaceDirs(rootDir, workspaceGlobs) {
  const dirs = [];
  for (const glob of workspaceGlobs) {
    if (!glob.endsWith("/*")) {
      throw new Error(`unsupported workspace glob (only "<segment>/*" is supported): ${glob}`);
    }
    const segment = glob.slice(0, -2);
    const segmentPath = join(rootDir, segment);
    let entries;
    try {
      entries = readdirSync(segmentPath, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      const rel = `${segment}/${entry.name}`;
      if (existsSync(join(rootDir, rel, "package.json"))) dirs.push(rel);
    }
  }
  return dirs.sort();
}

/** Reads {name, version} out of a workspace directory's own package.json. */
export function readWorkspacePackage(rootDir, dir) {
  const pkg = JSON.parse(readFileSync(join(rootDir, dir, "package.json"), "utf-8"));
  return { dir, name: pkg.name, version: pkg.version ?? "0.0.0" };
}

/**
 * Checks that every discovered workspace package is fully represented in a
 * (parsed) package-lock.json: the `packages/<dir>` entry with matching name and
 * version, plus the `node_modules/<name>` link entry pointing back at `dir`.
 * Violation messages deliberately echo npm ci's own wording ("Missing: X@Y from
 * lock file") so a failure here reads the same as the CI break it reproduces.
 */
export function checkLockfileWorkspaces(workspacePackages, lockfile) {
  const violations = [];
  const packages = lockfile.packages ?? {};
  for (const { dir, name, version } of workspacePackages) {
    const entry = packages[dir];
    if (!entry) {
      violations.push(`Missing: ${name}@${version} from lock file (no packages["${dir}"] entry)`);
      continue;
    }
    // npm omits the redundant "name" field in lockfileVersion 3 when the entry
    // key's last path segment already matches the (unscoped) package name — e.g.
    // "apps/web" for package "web" — so a missing field is only a violation when
    // that shortcut doesn't apply.
    if (entry.name !== undefined) {
      if (entry.name !== name) {
        violations.push(`packages["${dir}"].name is "${entry.name}", expected "${name}"`);
      }
    } else {
      const unscopedName = name.includes("/") ? name.slice(name.indexOf("/") + 1) : name;
      const dirBasename = dir.slice(dir.lastIndexOf("/") + 1);
      if (dirBasename !== unscopedName) {
        violations.push(`packages["${dir}"] has no "name" field and directory basename "${dirBasename}" does not match package name "${name}"`);
      }
    }
    if (entry.version !== version) {
      violations.push(`Invalid: lock file's ${name}@${entry.version} does not satisfy package.json's ${name}@${version}`);
    }
    const linkKey = `node_modules/${name}`;
    const link = packages[linkKey];
    if (!link) {
      violations.push(`Missing: ${name}@${version} from lock file (no packages["${linkKey}"] link entry)`);
      continue;
    }
    if (link.resolved !== dir) {
      violations.push(`packages["${linkKey}"].resolved is "${link.resolved}", expected "${dir}"`);
    }
    if (link.link !== true) {
      violations.push(`packages["${linkKey}"] is missing "link": true (workspace symlink)`);
    }
  }
  return violations;
}

export function main(argv) {
  const scriptDir = fileURLToPath(new URL(".", import.meta.url));
  const args = {};
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--root") args.root = argv[++i];
  }
  const rootDir = args.root ? resolve(args.root) : resolve(scriptDir, "..");

  const rootPkg = JSON.parse(readFileSync(join(rootDir, "package.json"), "utf-8"));
  const lockfile = JSON.parse(readFileSync(join(rootDir, "package-lock.json"), "utf-8"));
  const workspaceDirs = discoverWorkspaceDirs(rootDir, rootPkg.workspaces ?? []);
  const workspacePackages = workspaceDirs.map((dir) => readWorkspacePackage(rootDir, dir));
  const violations = checkLockfileWorkspaces(workspacePackages, lockfile);

  for (const violation of violations) {
    console.log(`FAIL: ${violation}`);
  }
  if (violations.length > 0) {
    console.log(`lockfile-workspaces: ${violations.length} violation(s)`);
    return 1;
  }
  console.log(`lockfile-workspaces: OK (${workspacePackages.length} workspace package(s) in sync)`);
  return 0;
}

const isMain = process.argv[1] && resolve(fileURLToPath(import.meta.url)) === resolve(process.argv[1]);
if (isMain) {
  process.exit(main(process.argv.slice(2)));
}
