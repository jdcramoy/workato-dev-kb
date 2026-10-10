# Jira/Atlassian MCP formatting conventions

Load this file when the AIRO task also involves filing or updating a Jira
ticket (via the Atlassian MCP's `createJiraIssue`/`editJiraIssue`/
`addCommentToJiraIssue`) — a common pairing when documenting a build or bug
fix found while working in AIRO.

- **A leading `#` renders as an H1 heading, not a list item** — this holds
  in both explicit `contentFormat: "markdown"` mode and this Jira instance's
  native wiki-markup rendering. Use real Markdown structure instead: `1.`/
  `2.` for ordered lists, `-` for bullets, `**bold**`, `_italic_`, backticks
  or fenced blocks for code. This Jira instance's workflow also has no
  "Done" status — use UAT / Final Tech Review / Closed as the terminal
  states.
- **`createJiraIssue`'s `description` parameter silently double-escapes
  content**, even with `contentFormat: "markdown"` explicitly set — real
  `\n` line breaks and markdown (`###`, `**`, `*`) land as literal
  backslash-n text and literal punctuation, a single unformatted wall of
  text, with no error surfaced. `editJiraIssue`'s `fields.description` with
  the identical `contentFormat: "markdown"` value and identical content
  handles it correctly (confirmed via a live A/B test on the same issue).

**Standing workflow:** create the issue with `createJiraIssue` using only
`summary`/`issueTypeName`/`projectKey`/`parent`/`assignee` (a one-line plain
string in `description` at most, never anything with structure), then
immediately set the real formatted description via a follow-up
`editJiraIssue` call. Always re-fetch the issue afterward
(`getJiraIssue`, `responseContentFormat: "markdown"`) to visually confirm
the description actually rendered before reporting the ticket as ready —
don't take either call's success response as proof of correct rendering.
