/**
 * Tests for the checks that stand between a worker model and the repository.
 *
 * These matter more than most tests here because every one of them guards a
 * failure that is silent. An allowlist that accidentally matches src/ lets
 * generated code into business logic. A missed abbreviation marker deletes the
 * part of a file the model did not bother to repeat, and the result still
 * parses, so the syntax check passes and the tests that exist still run.
 *
 * Run: node --experimental-strip-types --no-warnings tests/guards.test.mjs
 */

import {
  DEFAULT_EDIT_PATHS,
  DEFAULT_WRITE_PATHS,
  extractSymbols,
  globToRegExp,
  isAllowed,
  isForbidden,
  leaksSecret,
  looksAbbreviated,
  looksSecret,
  resolveInside,
  searchReplaceCost,
  stripFences,
} from "../lib/guards.ts"

let failed = 0
const ok = (name, condition, detail = "") => {
  if (!condition) failed++
  console.log(`${condition ? "ok  " : "FAIL"}  ${name}${detail ? `  (${detail})` : ""}`)
}

console.log("-- glob matching --")
ok("** spans directories", globToRegExp("**/tests/**").test("a/b/tests/c/d.py"))
ok("**/ also matches at the root", globToRegExp("**/test_*.py").test("test_x.py"))
ok("* stays inside one segment", !globToRegExp("tests/*.py").test("tests/sub/x.py"))
ok("a dot is literal, not any character", !globToRegExp("**/*.test.ts").test("axtestxts"))
ok("no partial matches", !globToRegExp("tests/**").test("other/tests/x.py"))

console.log("\n-- the allowlist keeps generation out of production code --")
for (const path of [
  "src/cofers_engine/api/routes.py",
  "src/main.py",
  "opencode/lib/worker.ts",
  "package.json",
  "pyproject.toml",
  ".github/workflows/ci.yml",
  "migrations/001_init.sql",
  "src/tests_helper.py",
  "Dockerfile",
]) {
  ok(`refuses ${path}`, !isAllowed(path, DEFAULT_WRITE_PATHS))
}
for (const path of [
  "tests/test_schemas.py",
  "tests/unit/test_matching.py",
  "src/cofers_engine/tests/test_x.py",
  "tests/conftest.py",
  "tests/fixtures/sample.json",
  "internal/matching_test.go",
  "src/lib/parser.test.ts",
]) {
  ok(`allows ${path}`, isAllowed(path, DEFAULT_WRITE_PATHS))
}

console.log("\n-- secrets --")
for (const path of [
  ".env",
  ".env.production",
  "config/credentials.yml",
  "secrets.json",
  "deploy/service-account.json",
  "certs/server.pem",
  "keys/id_rsa",
]) {
  ok(`treats ${path} as secret`, looksSecret(path))
}
ok("a normal file is not a secret", !looksSecret("src/environment.py"))
ok("catches a private key in output", leaksSecret("x = '''-----BEGIN RSA PRIVATE KEY-----\nabc'''"))
ok("catches an AWS key", leaksSecret('KEY = "AKIAIOSFODNN7EXAMPLE"'))
ok("catches a Google key", leaksSecret('K="AIzaSyD-9tSrke72PouQMnMX-a7eZSW0jkFMBWY"'))
ok("allows an obvious placeholder", !leaksSecret('KEY = "fake-api-key-for-tests"'))

console.log("\n-- abbreviation, the check that stops code being deleted --")
for (const text of [
  "def a():\n    pass\n\n# ... rest of file unchanged\n",
  "def a():\n    pass\n\n// rest of the code is unchanged\n",
  "def a():\n    pass\n\n    # ...\n",
  "def a():\n    pass\n\n# [unchanged]\n",
  "def a():\n    pass\n\n# snip\n",
  "<div/>\n<!-- ... -->\n",
  "def a():\n    pass\n\n...remainder of the module as before\n",
]) {
  ok(`spots: ${text.trim().split("\n").pop()}`, looksAbbreviated(text))
}
// Ellipsis is real syntax in Python and TypeScript, so the check must not fire
// on legitimate code or it would refuse valid edits.
for (const text of [
  "def stub() -> None:\n    ...\n",
  "class P(Protocol):\n    def f(self) -> int: ...\n",
  "x = [1, 2, 3]\ny = {**a, **b}\n",
  "def f(*args: Any) -> None:\n    ...\n",
  "# This tests the rest of the pipeline\n",
]) {
  ok(`leaves alone: ${text.trim().split("\n")[0]}`, !looksAbbreviated(text))
}

console.log("\n-- fences --")
ok("strips a fence with a language tag", stripFences("```python\nx = 1\n```") === "x = 1\n")
ok("strips a bare fence", stripFences("```\nx = 1\n```") === "x = 1\n")
ok("leaves unfenced content alone", stripFences("x = 1") === "x = 1\n")
ok(
  "does not strip a fence that is part of the content",
  stripFences("x = 1\n\n```\nnot the whole thing\n```\n\ny = 2").includes("y = 2"),
)

console.log("\n-- what a delegated edit actually saves --")
const diff = `diff --git a/x.py b/x.py
index 111..222 100644
--- a/x.py
+++ b/x.py
@@ -87,4 +87,5 @@ def helper() -> dict:
 
 def test_one() -> None:
+    """Checks one thing."""
     data = {"name": "x"}
     assert data
`
const cost = searchReplaceCost(diff)
ok("counts the change, not the file", cost > 0 && cost < 1_000, `${cost} chars`)
ok("charges context twice, since it appears in both old and new text", cost > diff.length / 2)
ok("ignores diff headers", !searchReplaceCost("diff --git a/x b/x\nindex 1..2\n--- a/x\n+++ b/x\n"))
// The load-bearing property: many scattered small edits cost far more than one
// big edit of the same number of lines, because context is paid at every site.
const scattered = Array.from(
  { length: 10 },
  (_, i) => `@@ -${i * 20},2 +${i * 20},3 @@\n a = 1\n+    added line here\n b = 2\n`,
).join("")
const clustered = `@@ -1,12 +1,22 @@\n a = 1\n${"+    added line here\n".repeat(10)} b = 2\n`
ok(
  "ten scattered edits cost more than the same ten lines together",
  searchReplaceCost(scattered) > searchReplaceCost(clustered),
  `${searchReplaceCost(scattered)} vs ${searchReplaceCost(clustered)} chars`,
)

console.log("\n-- symbols in a receipt --")
const symbols = extractSymbols(
  "class Foo:\n    def bar(self): pass\n\nasync def baz(): pass\n\nfunc Qux() {}\n",
)
ok("finds classes, methods and functions", ["Foo", "bar", "baz", "Qux"].every((s) => symbols.includes(s)))
ok("does not repeat itself", new Set(symbols).size === symbols.length)

// The two allowlists must not converge. Creating a file nobody reads and
// changing one that comes back as a reviewable diff are different risks, and
// holding edits to the write list was measured costing 39% on a real task.
ok("writes stay away from source", !isAllowed("app/services.py", DEFAULT_WRITE_PATHS))
ok("edits reach source", isAllowed("app/services.py", DEFAULT_EDIT_PATHS))
ok("edits still cover tests", isAllowed("tests/test_services.py", DEFAULT_EDIT_PATHS))
ok("edits leave data alone", !isAllowed("app/fixtures/users.json", DEFAULT_EDIT_PATHS))

for (const forbidden of [
  ".github/workflows/deploy.yml",
  "db/migrations/001_add_users.py",
  "alembic/versions/abc.py",
  "package-lock.json",
  "poetry.lock",
  ".opencode/shunt.json",
]) {
  ok(`off limits: ${forbidden}`, isForbidden(forbidden))
}
ok("ordinary source is not forbidden", !isForbidden("app/services.py"))
ok("a workflows directory outside .github is fine", !isForbidden("app/workflows/order.py"))
// The deny list has to outrank a widened allowlist, or it is only advice.
ok(
  "forbidden paths can match a wide allowlist",
  isAllowed(".github/workflows/deploy.yml", ["**"]) && isForbidden(".github/workflows/deploy.yml"),
)

console.log("-- containment --")
{
  const fs = await import("node:fs/promises")
  const os = await import("node:os")
  const nodePath = await import("node:path")

  const root = await fs.mkdtemp(nodePath.join(os.tmpdir(), "shunt-contain-"))
  const repo = nodePath.join(root, "repo")
  const outside = nodePath.join(root, "outside")
  await fs.mkdir(nodePath.join(repo, "src"), { recursive: true })
  await fs.mkdir(outside, { recursive: true })
  await fs.writeFile(nodePath.join(outside, "secret.py"), "# not ours\n")

  const rejects = async (name, relative) => {
    try {
      await resolveInside(repo, relative)
      ok(name, false, "was allowed")
    } catch {
      ok(name, true)
    }
  }

  ok("an ordinary path inside the repository is allowed", !!(await resolveInside(repo, "src/new.py")))
  await rejects("an absolute path is refused", "/etc/passwd")
  await rejects("traversal out of the repository is refused", "../outside/secret.py")

  // The ancestor walk starts at the parent because the target may not exist,
  // which left the last component unchecked. A file sitting inside the
  // repository can itself be a link out of it: every directory passes, the
  // write follows the link, the bytes land outside.
  await fs.symlink(nodePath.join(outside, "secret.py"), nodePath.join(repo, "src", "linked.py"))
  await rejects("a file that is itself a symlink out is refused", "src/linked.py")

  await fs.symlink(outside, nodePath.join(repo, "escape"))
  await rejects("a directory symlink out is refused", "escape/secret.py")

  await fs.rm(root, { recursive: true, force: true })
}

console.log(failed ? `\n${failed} check(s) failed` : "\nall checks passed")
process.exit(failed ? 1 : 0)
