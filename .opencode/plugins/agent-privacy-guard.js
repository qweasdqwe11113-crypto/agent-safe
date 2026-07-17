export const AgentPrivacyGuard = async ({ client }) => {
  await client.app.log({
    body: {
      service: "agent-privacy-guard",
      level: "info",
      message: "Review-first privacy gateway enabled at http://127.0.0.1:8000/debug",
    },
  })

  return {
    "chat.headers": async (input, output) => {
      output.headers ??= {}
      output.headers["x-opencode-session-id"] = input.sessionID
      output.headers["x-apg-client"] = "opencode-plugin"
    },
  }
}
