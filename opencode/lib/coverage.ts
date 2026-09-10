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
    const line = block.find((candidate) => candidate.includes(path))
    if (line) {
      ;(IRRELEVANT.test(line) ? notRelevant : analysed).push(path)
      continue
    }
    // Some models drop the section but still discuss the files plainly. Treating
    // that as a miss would spend a second call re-reading what was already read.
    if (start === -1 && text.includes(path)) {
      analysed.push(path)
      continue
    }
    missing.push(path)
  }

  return { analysed, notRelevant, missing, declared: start !== -1 }
}
