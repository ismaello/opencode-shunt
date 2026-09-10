/**
 * Tests for the coverage parser, which decides whether a worker actually looked
 * at every file it was sent.
 *
 * Worth testing deterministically because the interesting case is hard to
 * provoke on purpose: requiring a COVERAGE section stopped the workers skipping
 * files at all, so the retry path it feeds cannot be triggered from a real call
 * on demand. These fake answers exercise it directly.
 *
 * Run: node --experimental-strip-types --no-warnings tests/coverage.test.mjs
 */

import { readCoverage } from "../opencode/lib/coverage.ts"

const files = ["src/a.py", "src/b.py", "src/c.py"]
let failed = 0

const check = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want)
  if (!ok) failed++
  console.log(`${ok ? "ok  " : "FAIL"}  ${name}`)
  if (!ok) console.log(`        got ${JSON.stringify(got)}\n       want ${JSON.stringify(want)}`)
}

// All three accounted for, one of them irrelevant. Used to be a false alarm.
let r = readCoverage(
  `COVERAGE:
- src/a.py: analysed - holds the matcher
- src/b.py: not relevant - only logging
- src/c.py: analysed - calls the matcher

### Findings
whatever`,
  files,
)
check("mixed coverage: analysed", r.analysed.sort(), ["src/a.py", "src/c.py"])
check("mixed coverage: irrelevant", r.notRelevant, ["src/b.py"])
check("mixed coverage: nothing missing", r.missing, [])

// The failure this whole change exists to catch, and what triggers the retry.
r = readCoverage(
  `COVERAGE:
- src/a.py: analysed - holds the matcher
- src/b.py: analysed - calls it

### Findings
src/a.py 10-20 does the thing`,
  files,
)
check("silently skipped file is reported missing", r.missing, ["src/c.py"])

// No section at all: fall back to mentions rather than burn a retry re-reading.
r = readCoverage(
  `src/a.py 10-20 defines the matcher
src/b.py 5-9 calls it`,
  files,
)
check("no section: declared is false", r.declared, false)
check("no section: mentioned files counted", r.analysed.sort(), ["src/a.py", "src/b.py"])
check("no section: unmentioned file missing", r.missing, ["src/c.py"])

// Section present but the model chose a heading, asterisks and other wording.
r = readCoverage(
  `## COVERAGE:
* src/a.py — irrelevant, unrelated to the question
* src/b.py — analysed
* src/c.py — no bearing on this
UNCERTAIN: none`,
  files,
)
check("loose formatting: irrelevant detected", r.notRelevant.sort(), ["src/a.py", "src/c.py"])
check("loose formatting: nothing missing", r.missing, [])

// A findings heading must not be swallowed into the coverage block.
r = readCoverage(
  `COVERAGE:
- src/a.py: analysed
### Findings
- src/b.py: mentioned down here, not in coverage
- src/c.py: also down here`,
  files,
)
check("findings section is not read as coverage", r.missing.sort(), ["src/b.py", "src/c.py"])

console.log(failed ? `\n${failed} check(s) failed` : "\nall checks passed")
process.exit(failed ? 1 : 0)
