const REVIEW_URL = "http://127.0.0.1:8000"
const reviewBySession = new Map()
const responsePartsByReview = new Map()
const tokenMapBySession = new Map()

const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds))

async function createAndWaitForReview(text, sessionID, route) {
  const response = await fetch(`${REVIEW_URL}/plugin/reviews`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text, session_id: sessionID, route }),
  })
  if (!response.ok) throw new Error("Agent Privacy Guard review service is unavailable")
  const created = await response.json()

  for (;;) {
    await sleep(300)
    const reviewResponse = await fetch(`${REVIEW_URL}/reviews/${created.review_id}`, { cache: "no-store" })
    if (!reviewResponse.ok) throw new Error("Agent Privacy Guard review disappeared")
    const review = await reviewResponse.json()
    if (review.status === "approved") return review
    if (["expired", "interrupted", "error"].includes(review.status)) {
      return { ...review, final_action: "block" }
    }
  }
}

async function recordAssistantText(reviewID, partID, text, tokenMap) {
  if (!reviewID || !text) return

  const parts = responsePartsByReview.get(reviewID) ?? new Map()
  parts.set(partID, text)
  responsePartsByReview.set(reviewID, parts)

  // A reply can contain multiple completed text parts (for example after a
  // tool call). Keep all completed parts so the review page receives the full
  // cloud response, not just the final fragment.
  const response = await fetch(`${REVIEW_URL}/plugin/reviews/${encodeURIComponent(reviewID)}/output`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text: [...parts.values()].join("\n"), token_map: tokenMap }),
  })
  if (!response.ok) throw new Error("Agent Privacy Guard could not record the assistant response")
}

function restoreText(text, tokenMap) {
  let restored = text
  for (const [placeholder, original] of Object.entries(tokenMap ?? {})) {
    restored = restored.split(placeholder).join(original)
  }
  return restored
}

function reviewedText(review, originalText, blockedMessage) {
  if (review.final_action === "allow") return originalText
  if (review.final_action === "mask") return review.redacted_text
  return blockedMessage
}

function addReviewTokenMap(sessionID, review) {
  if (review.final_action !== "mask") return
  const tokenMap = tokenMapBySession.get(sessionID) ?? {}
  Object.assign(tokenMap, review.token_map ?? {})
  tokenMapBySession.set(sessionID, tokenMap)
}

export const AgentPrivacyGuard = async ({ client }) => ({
  "experimental.chat.messages.transform": async (_input, output) => {
    const message = [...output.messages].reverse().find((item) => item.info.role === "user")
    if (!message) return

    const textParts = message.parts.filter((part) => part.type === "text")
    const originalText = textParts.map((part) => part.text).join("\n")
    if (!originalText) return

    const review = await createAndWaitForReview(originalText, message.info.sessionID, "plugin/messages.transform")
    reviewBySession.set(message.info.sessionID, review.review_id)
    responsePartsByReview.delete(review.review_id)
    tokenMapBySession.set(message.info.sessionID, {})
    addReviewTokenMap(message.info.sessionID, review)

    const replacement = reviewedText(review, originalText, "[This user message was blocked by the local privacy policy.]")
    if (review.final_action === "allow") return
    textParts[0].text = replacement
    for (const part of textParts.slice(1)) part.text = ""

    await client.app.log({
      body: {
        service: "agent-privacy-guard",
        level: "info",
        message: `Review ${review.review_id} applied action: ${review.final_action}`,
      },
    })
  },
  "tool.execute.after": async (input, output) => {
    const originalText = output.output
    if (typeof originalText !== "string" || !originalText.trim()) return

    const review = await createAndWaitForReview(originalText, input.sessionID, "plugin/tool.execute.after")
    addReviewTokenMap(input.sessionID, review)
    output.output = reviewedText(
      review,
      originalText,
      `[Tool output from ${input.tool} was blocked by the local privacy policy.]`,
    )

    await client.app.log({
      body: {
        service: "agent-privacy-guard",
        level: "info",
        message: `Review ${review.review_id} applied action: ${review.final_action} to tool ${input.tool}`,
      },
    })
  },
  "experimental.text.complete": async (input, output) => {
    const reviewID = reviewBySession.get(input.sessionID)
    if (!reviewID) return

    const cloudText = output.text
    // This mutation changes only OpenCode's local display. The cloud has
    // already received the masked text; restored values are never resent.
    const tokenMap = tokenMapBySession.get(input.sessionID) ?? {}
    output.text = restoreText(cloudText, tokenMap)
    try {
      // Record the unmodified cloud text for the audit page. This is kept
      // independent from restoring the OpenCode display above.
      await recordAssistantText(reviewID, input.partID, cloudText, tokenMap)
    } catch (error) {
      // Auditing must not make an already-completed model reply fail in OpenCode.
      await client.app.log({
        body: {
          service: "agent-privacy-guard",
          level: "warn",
          message: `Could not save restored response for review ${reviewID}: ${error.message}`,
        },
      })
    }
  },
})
