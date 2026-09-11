/**
 * Work out which of the files sent to a worker it actually accounted for.
 *
 * The earlier check asked only whether a path appeared anywhere in the answer,
 * which cannot tell a file the worker skipped from one it read and correctly
 * judged irrelevant. Since the prompt tells it to ignore whatever does not bear
 * on the question, that check cried wolf every time an unrelated file was part
 * of the call: three such false alarms in three multi-file reads.
 *
 * So the worker is now required to emit a COVERAGE section naming every file
 * with a status, and this reads it. A file marked irrelevant is useful
 * information to pass on. A file missing from the section entirely is the real
 * failure, and the only case worth spending a second call on.
 */

export type Coverage = {
  analysed: string[]
  notRelevant: string[]
  /** Neither analysed nor declared irrelevant: genuinely unaccounted for. */
  missing: string[]
  /** False when the worker ignored the instruction to produce the section. */
  declared: boolean
}

const IRRELEVANT =
  /\b(not[ _]relevant|irrelevant|no bearing|not applicable|nothing relevant|unrelated)\b/i

/** Where the coverage list stops and the findings begin. */
const SECTION_END = /^\s*#{1,3}\s|^\s*\**\s*(UNCERTAIN|FINDINGS)\b/i

/** Characters that can sit inside a path, so finding one next to a match means
 * the match is part of a longer name. */
const PATH_CHAR = /[A-Za-z0-9._/\\-]/

/**
 * Whether the text names this exact path, rather than merely containing its
 * letters.
 *
 * A plain substring test reads "tests/test_foo.py: analysed" as coverage of
 * "foo.py", and "src/a.py" as coverage of "a.py". Both mark a file the worker
 * never looked at as accounted for, and the read hook then has grounds to
 * block the orchestrator from reading it. The failure is silent and points the
 * wrong way: it hides evidence rather than surfacing it.
 */
export function mentions(text: string, target: string): boolean {
  let from = 0
  for (;;) {
    const at = text.indexOf(target, from)
    if (at === -1) return false
    const before = at === 0 ? "" : text[at - 1]
    const after = text[at + target.length] ?? ""
    if (!PATH_CHAR.test(before) && !PATH_CHAR.test(after)) return true
    from = at + 1
  }
}

export function readCoverage(text: string, expected: string[]): Coverage {
  const lines = text.split("\n")
  const start = lines.findIndex((line) => /^\s*[#*\s]*COVERAGE\s*:?\s*$|^\s*[#*\s]*COVERAGE\s*:/i.test(line))

  const block: string[] = []
  if (start !== -1) {
    // Bounded by the number of files asked about, plus slack for stray blank
    // lines and preamble, so a malformed answer cannot swallow the findings.
    for (const line of lines.slice(start + 1, start + expected.length + 8)) {
      if (SECTION_END.test(line)) break
      block.push(line)
    }
  }

  const analysed: string[] = []
  const notRelevant: string[] = []
  const missing: string[] = []

  for (const path of expected) {
    const line = block.find((candidate) => mentions(candidate, path))
    if (line) {
      ;(IRRELEVANT.test(line) ? notRelevant : analysed).push(path)
      continue
    }
    // Some models drop the section but still discuss the files plainly. Treating
    // that as a miss would spend a second call re-reading what was already read.
    if (start === -1 && mentions(text, path)) {
      analysed.push(path)
      continue
    }
    missing.push(path)
  }

  return { analysed, notRelevant, missing, declared: start !== -1 }
}
