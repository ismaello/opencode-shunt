/**
 * Tests for the delegation break-even.
 *
 * These check the shape of the model rather than exact dollars: that the trade
 * moves in the right direction when each input changes, and that the threshold
 * lands where our real calls say it should. A model that gets the direction
 * wrong would quietly refuse work worth delegating, or delegate work that loses
 * money, and neither shows up as an error.
 *
 * Run: node --experimental-strip-types --no-warnings tests/economics.test.mjs
 */

import { DEFAULT_ECONOMICS, assess, loadEconomics, worthBlocking } from "../lib/economics.ts"

let failed = 0
const ok = (name, condition, detail = "") => {
  if (!condition) failed++
  console.log(`${condition ? "ok  " : "FAIL"}  ${name}${detail ? `  (${detail})` : ""}`)
}

const e = DEFAULT_ECONOMICS
const base = assess(0, e)
const kb = (n) => Math.round(n / 1024)

console.log(`break-even: ${base.breakEvenChars.toLocaleString()} chars (${kb(base.breakEvenChars)} KB)\n`)

// The empirical floor was 400 lines, and Python averages ~35 chars a line. The
// arithmetic agreeing with the evidence is the main thing worth checking.
ok(
  "break-even is in the same range as the 400-line floor chosen from evidence",
  base.breakEvenChars > 8_000 && base.breakEvenChars < 20_000,
  `${kb(base.breakEvenChars)} KB vs ~14 KB`,
)

// Cases taken from real telemetry.
ok("a 5 KB file is not worth delegating", !assess(5_219, e).worthwhile)
ok("a 8 KB file is not worth delegating", !assess(8_192, e).worthwhile)
ok("a 27 KB file is worth delegating", assess(27_648, e).worthwhile)
ok("a 54 KB file is worth delegating", assess(55_296, e).worthwhile)

ok("net is negative below break-even", assess(5_219, e).net < 0)
ok("net is positive above break-even", assess(55_296, e).net > 0)

// Directional checks: each input must push the threshold the way it should.
const longerChat = assess(0, loadEconomics({ assumedConversationTokens: 200_000 }))
ok(
  "a longer conversation raises the floor",
  longerChat.breakEvenChars > base.breakEvenChars,
  `${kb(longerChat.breakEvenChars)} KB`,
)

const longerSession = assess(0, loadEconomics({ remainingTurns: 20 }))
ok(
  "content surviving more turns lowers the floor",
  longerSession.breakEvenChars < base.breakEvenChars,
  `${kb(longerSession.breakEvenChars)} KB`,
)

// Scaling both prices together cannot move the crossover, because the saving and
// the cost are in the same currency and scale with it. A cheaper orchestrator
// saves less money at every size without changing where delegating starts to pay.
// Gemini and Anthropic both price a cache read at about 8% of a cache write, so
// the threshold happens to be the same for either.
const cheap = loadEconomics({ cacheWritePerMillion: 0.375, cacheReadPerMillion: 0.03 })
ok(
  "scaling both prices leaves the floor where it was",
  assess(0, cheap).breakEvenChars === base.breakEvenChars,
  `${kb(assess(0, cheap).breakEvenChars)} KB with Gemini pricing`,
)
ok(
  "but a cheaper model saves proportionally less",
  assess(55_296, cheap).net < assess(55_296, e).net / 10,
)

// What does move it is the ratio between the two rates: the dearer a cache read
// is relative to a write, the more the extra turns cost and the higher the floor.
const dearReads = assess(0, loadEconomics({ cacheReadPerMillion: 3.0 }))
ok(
  "dearer cache reads raise the floor",
  dearReads.breakEvenChars > base.breakEvenChars,
  `${kb(dearReads.breakEvenChars)} KB`,
)

const worseCompression = assess(0, loadEconomics({ expectedCompression: 0.6 }))
ok(
  "a worker that compresses poorly raises the floor",
  worseCompression.breakEvenChars > base.breakEvenChars,
  `${kb(worseCompression.breakEvenChars)} KB`,
)

const noMargin = assess(0, loadEconomics({ safetyMargin: 1 }))
ok("the safety margin only ever raises the floor", noMargin.breakEvenChars < base.breakEvenChars)

// A live conversation size must override the assumption.
const live = assess(20_000, e, 500_000)
ok("a live conversation size is used when given", !live.worthwhile, "20 KB refused in a 500k chat")

// --- refusing a re-read of something already summarised ----------------------
//
// The guard against delegating and then reading the files anyway. It costs a
// turn, so it has to earn that turn: blocking every re-read regardless of size
// turned a measured $0.25 review into $0.47.

const block = worthBlocking(0, e)
ok("a re-read has a floor of its own", block.breakEvenChars > 0, `${kb(block.breakEvenChars)} KB`)
ok("small files are not worth a refusal", !worthBlocking(3_000, e).worthwhile, "3 KB")
ok("large ones are", worthBlocking(30_000, e).worthwhile, "30 KB")

// Blocking buys one turn, delegating buys two plus a summary, so the floor for
// a refusal must sit below the floor for a delegation or the guard never fires
// on anything the delegation floor already let through.
ok(
  "refusing is a cheaper act than delegating",
  block.breakEvenChars < assess(0, e).breakEvenChars,
  `${kb(block.breakEvenChars)} KB vs ${kb(assess(0, e).breakEvenChars)} KB`,
)

ok(
  "a longer conversation makes a refusal dearer, so the floor rises",
  worthBlocking(0, e, 200_000).breakEvenChars > block.breakEvenChars,
)
ok(
  "expecting the follow-up to read more of the file also raises it",
  worthBlocking(0, e, undefined, 0.9).breakEvenChars > block.breakEvenChars,
)

console.log(failed ? `\n${failed} check(s) failed` : "\nall checks passed")
process.exit(failed ? 1 : 0)
