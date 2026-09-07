// The lockfile the images install from must agree with package.json.
//
// Every build path in this repo installs with pnpm and `--frozen-lockfile`
// (`frontend/Dockerfile`, `docker-compose.override.yml`), which fails the build
// outright when the two disagree — `ERR_PNPM_OUTDATED_LOCKFILE`. The failure is
// nowhere near the change that caused it: adding a dependency and committing
// only package.json leaves every test green and every local run working, and
// the first thing to notice is `./scripts/prod.sh` on somebody else's machine.
//
// So the agreement is checked here, where it costs a millisecond and names the
// missing package.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const at = (name) => fileURLToPath(new URL(`../${name}`, import.meta.url));

const pkg = JSON.parse(readFileSync(at("package.json"), "utf8"));
const lock = readFileSync(at("pnpm-lock.yaml"), "utf8");

/** The `importers:` entry for this package, which is the only part that records
 *  what package.json asked for. The `packages:` section below it lists the whole
 *  resolved tree, so searching the file as a whole would pass on a transitive
 *  dependency that nothing in package.json actually names. */
function importerSpecifiers() {
  const start = lock.indexOf("importers:");
  assert.notEqual(start, -1, "pnpm-lock.yaml has no importers section");
  const end = lock.indexOf("\npackages:", start);
  const block = lock.slice(start, end === -1 ? undefined : end);
  const found = new Map();
  const re = /^ {6}'?([^'\s:]+)'?:\n {8}specifier: (.+)$/gm;
  for (const [, name, spec] of block.matchAll(re)) found.set(name, spec.trim());
  return found;
}

describe("pnpm-lock.yaml", () => {
  const locked = importerSpecifiers();

  for (const field of ["dependencies", "devDependencies"]) {
    for (const [name, specifier] of Object.entries(pkg[field] ?? {})) {
      it(`records ${field.replace("Dependencies", "")} dependency ${name}`, () => {
        assert.equal(
          locked.get(name),
          specifier,
          `package.json asks for ${name}@${specifier}. Run \`pnpm install\` in ` +
            "frontend/ and commit pnpm-lock.yaml, or the image build fails with " +
            "ERR_PNPM_OUTDATED_LOCKFILE.",
        );
      });
    }
  }

  it("records nothing package.json does not ask for", () => {
    const asked = new Set([
      ...Object.keys(pkg.dependencies ?? {}),
      ...Object.keys(pkg.devDependencies ?? {}),
    ]);
    assert.deepEqual([...locked.keys()].filter((n) => !asked.has(n)), []);
  });
});
