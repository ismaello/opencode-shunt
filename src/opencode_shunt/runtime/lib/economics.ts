/**
 * When delegation actually pays, computed from prices instead of guessed.
 *
 * The first floor was 400 lines, picked because eight of twenty-eight real calls
 * fell below it and looked wasteful. That was evidence, not arithmetic, and the
 * arithmetic turns out to matter: measured across sixty real Opus sessions, the
 * bill breaks down as 46.6% cache writes, 34.1% output, 19.3% cache reads and
 * essentially no fresh input. Everything the orchestrator ingests is written to
 * cache once at a premium, then re-read every following turn at a tenth.
 *
 * That gives both sides of the trade a price:
 *
 *   saved  = content that never enters the context: the cache write it avoids,
 *            plus the cache read it avoids on every following turn, because
 *            anything left in the context is re-sent for the rest of the session
 *   paid   = the extra round trips delegation adds, each re-sending the whole
 *            conversation at the cache-read rate
 *
 * Both terms are needed. Leaving out the per-turn re-reads put the break-even at
 * 32 KB, which would have refused twenty-three of our twenty-eight real calls and
 * contradicted the 87% per-operation saving we measure. Including them, with the
 * six-message median session actually observed, lands it near 13 KB - close to
 * the 400-line floor originally chosen from evidence alone, which is reassuring
 * for both. The paid term is still why a small file loses money.
 *
 * Every input is overridable per repository, because the two that move the
 * answer most - conversation length and how many extra turns a delegation costs
 * - are properties of how somebody works, not constants.
 */

export type Economics = {
  /** Cost of a token entering the context for the first time, per million. */
  cacheWritePerMillion: number
  /** Cost of re-sending an already-cached token on a later turn, per million. */
  cacheReadPerMillion: number
  /**
   * Typical conversation size when a delegation happens. The median across our
   * own sessions; a repository with longer sessions should raise it, which
   * raises the floor, because extra turns get dearer as context grows.
   */
  assumedConversationTokens: number
  /**
   * Round trips delegation adds over reading directly. Reading is itself a tool
   * call, so this is not the whole cost of delegating: it is the follow-up
   * targeted reads that a summary leads to, which the prompt asks for by design.
   */
  extraTurnsPerDelegation: number
  /**
   * Turns the content would have stayed in context for, being re-sent each time.
   * The single most sensitive input here, and the one most worth setting per
   * repository: our own median is 3, but that is measured over mostly one-shot
   * `opencode run` sessions. Real interactive work runs far longer, which makes
   * delegation pay off sooner, not later.
   */
  remainingTurns: number
  /** Summary size as a fraction of the content. Measured median: 0.13. */
  expectedCompression: number
  /** Characters per token for source code. Measured against worker counts. */
  charsPerToken: number
  /**
   * How far past break-even a call must be before delegating. Above 1 it trades
   * some saving for confidence, since being wrong about a marginal call costs
   * real money and latency while the saving on it was near zero anyway.
   */
  safetyMargin: number
  /**
   * What the worker charges, per million tokens. Zero for a model on your own
   * hardware, which is the only case where zero is the truth.
   *
   * Left out of this calculation entirely at first, which made delegation look
   * free on the cheap side and biased every marginal decision towards
   * delegating. It is not large - around a tenth of the saving at break-even
   * with Flash - but it is the term whose omission always flatters the system,
   * and those are the ones to be careful with.
   */
  workerInPerMillion: number
  workerOutPerMillion: number
}

export const DEFAULT_ECONOMICS: Economics = {
  // Anthropic list prices for Opus: cache write is 1.25x input, read is 0.1x.
  cacheWritePerMillion: 6.25,
  cacheReadPerMillion: 0.5,
  // Measured: 4.37M cache reads over 60 sessions of about three assistant turns.
  assumedConversationTokens: 25_000,
  extraTurnsPerDelegation: 2,
  remainingTurns: 3,
  expectedCompression: 0.13,
  charsPerToken: 2.9,
  safetyMargin: 1.2,
  // Zero only because a config that predates these keys cannot say what its
  // worker costs, and inventing a rate is worse than the status quo. `shunt
  // config` writes the real ones, and `doctor` complains when a remote worker
  // is running without them.
  workerInPerMillion: 0,
  workerOutPerMillion: 0,
}

export type Verdict = {
  worthwhile: boolean
  /** Characters of content below which delegating loses money. */
  breakEvenChars: number
  /** Net dollars saved by delegating this much, negative when it costs more. */
  net: number
  /** Human-readable arithmetic, so a refusal can explain itself. */
  explain: string
}

/**
 * Whether delegating `contentChars` of source is worth it.
 *
 * `conversationTokens` should be the live conversation size when it is known;
 * the assumed value is used otherwise, and being wrong about it is the largest
 * source of error here.
 */
export function assess(
  contentChars: number,
  economics: Economics,
  conversationTokens?: number,
): Verdict {
  const {
    cacheWritePerMillion,
    cacheReadPerMillion,
    assumedConversationTokens,
    extraTurnsPerDelegation,
    remainingTurns,
    expectedCompression,
    charsPerToken,
    safetyMargin,
    workerInPerMillion,
    workerOutPerMillion,
  } = economics

  const conversation = conversationTokens ?? assumedConversationTokens
  const perMillion = (tokens: number, price: number) => (tokens / 1_000_000) * price

  const contentTokens = contentChars / charsPerToken
  const keptOut = contentTokens * (1 - expectedCompression)

  // Written to cache once, then re-sent on every turn that follows.
  const ratePerKeptToken = cacheWritePerMillion + cacheReadPerMillion * remainingTurns
  const saved = perMillion(keptOut, ratePerKeptToken)

  // The worker reads all of it and writes the summary back, and both are
  // billed. Proportional to the content, exactly like the saving, so it does
  // not merely shift the break-even - it can remove it.
  const workerRatePerToken =
    (workerInPerMillion ?? 0) + (workerOutPerMillion ?? 0) * expectedCompression
  const workerCost = perMillion(contentTokens, workerRatePerToken)

  const paid = perMillion(conversation * extraTurnsPerDelegation, cacheReadPerMillion) + workerCost

  // saved(chars) - workerCost(chars) = coordination, solved for chars.
  const netRatePerToken = (1 - expectedCompression) * ratePerKeptToken - workerRatePerToken
  const breakEvenTokens =
    netRatePerToken <= 0
      ? Infinity
      : (conversation * extraTurnsPerDelegation * cacheReadPerMillion) / netRatePerToken
  const breakEvenChars =
    breakEvenTokens === Infinity
      ? Infinity
      : Math.round(breakEvenTokens * charsPerToken * safetyMargin)

  return {
    worthwhile: contentChars >= breakEvenChars,
    breakEvenChars,
    net: saved - paid,
    explain:
      netRatePerToken <= 0
        ? `the worker costs more per token than keeping the content out of context saves, ` +
          `so delegating loses money at every size. Check the worker model in shunt.json.`
        : `keeping ~${Math.round(keptOut).toLocaleString()} tokens out of context saves ` +
          `$${saved.toFixed(3)} (written to cache once, then re-sent for ~${remainingTurns} turns), ` +
          `while the ${extraTurnsPerDelegation} extra round trips over a ` +
          `~${(conversation / 1000).toFixed(0)}k conversation plus the worker's own ` +
          `$${workerCost.toFixed(4)} cost $${paid.toFixed(3)}`,
  }
}

/**
 * Whether refusing a full re-read of an already-summarised file pays for itself.
 *
 * A refusal is not free advice: it costs the turn the model spends being told
 * no and trying again, and turns are the dominant cost in this whole system. So
 * the same arithmetic that decides whether to delegate has to decide whether to
 * block, or the guard costs more than the waste it prevents.
 *
 * Measured before adding this. Blocking every re-read regardless of size turned
 * a $0.25 review into $0.47: five refusals bought five extra turns to keep a few
 * kilobytes out, and the files were far too small for that trade to work.
 *
 * The saving is partial, because the model comes back and reads part of the file
 * anyway with offset and limit — that is the behaviour we want, not a failure.
 * `targetedReadFraction` is how much of it a focused read pulls in; 0.5 is a
 * guess with the sign right, and it makes the threshold conservative.
 */
export function worthBlocking(
  contentChars: number,
  economics: Economics,
  conversationTokens?: number,
  targetedReadFraction = 0.5,
): { worthwhile: boolean; breakEvenChars: number } {
  const {
    cacheWritePerMillion,
    cacheReadPerMillion,
    assumedConversationTokens,
    remainingTurns,
    charsPerToken,
    safetyMargin,
  } = economics

  const conversation = conversationTokens ?? assumedConversationTokens
  const ratePerKeptToken = cacheWritePerMillion + cacheReadPerMillion * remainingTurns
  const kept = 1 - targetedReadFraction

  // One extra turn, not two: nothing is delegated here, the model simply reads
  // differently. saved(chars) = paid, solved for chars.
  const breakEvenTokens = (conversation * cacheReadPerMillion) / (kept * ratePerKeptToken)
  const breakEvenChars = Math.round(breakEvenTokens * charsPerToken * safetyMargin)

  return { worthwhile: contentChars >= breakEvenChars, breakEvenChars }
}

export function loadEconomics(overrides?: Partial<Economics>): Economics {
  return { ...DEFAULT_ECONOMICS, ...(overrides ?? {}) }
}
