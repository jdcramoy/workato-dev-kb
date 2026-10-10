# Slack bot connections in Workato — the governed path

Standing rules for EVERY new Slack-bot/integration connection, plus
the automation traps for api.slack.com and the triage logic for
silent no-delivery genies. Follow the governed path for every
build; assume every asset lands in production one day.

## What is the governing principle?

Best practices, prod-bound: assume every asset lands in prod one
day. The governed path — a workspace-owned, admin-approved Slack
app; a net-new custom OAuth profile with the right credentials from
scratch; a proper channel; ticket-tracked when project-scale — is
THE path. Shortcuts around it are rejected even when they work
sooner.

## What are the non-negotiable connection rules?

1. NEVER post through a personal user connection. A personal Slack
   account is for personal use; an operational post rendered as an
   individual's name is a defect. Operational/monitoring messages go
   to a dedicated CHANNEL from a BOT identity only.
2. Create a NET-NEW custom OAuth profile with the right credentials
   from scratch. Do NOT reuse another team's existing profile or
   connection.
3. The Slack app behind the profile is workspace-owned and
   admin-approved: built from a manifest with `display_information`,
   a `features.bot_user` display name, `oauth_config.bot.scopes`,
   and (for a plain workspace app) NO `settings.org_install_required`
   (Slack's validator rejects it for non-Grid workspaces). App
   credentials come from api.slack.com (Basic Information →
   Credentials).
4. The manifest must include the scopes the flow needs — a posting
   bot needs `chat:write` (+ `chat:write.public` for uninvited
   public-channel posts, `channels:read` for channel pickers). A
   manifest with only read scopes cannot post. A missing
   `users:read` scope also fails the post-OAuth user-info step.
5. Connector choice: the Workbot-for-Slack connector (`slack_bot`)
   for bot posts — its post action takes channel + blocks; the plain
   "Slack" connector posts via a user token and renders as the user.
6. Custom OAuth profiles are provider-specific: a `slack`-connector
   connection can only use `slack`-provider profiles; `slack_bot`
   connections need `slack_bot`-provider profiles (the profile picker
   filters by provider).

## What mechanics bite during the OAuth handoff?

- Workato's OAuth start opens a POPUP that browser automation
  suppresses — the tab spins forever and never reaches Slack's Allow
  screen. The final Connect → Allow needs the human's hands unless
  the popup opens as a tab. Budget for it in the build plan.
- Channel-name resolution fails for bots not in the channel
  ("Channel not found") — invite the bot's Slack user to the channel,
  or post by channel ID.
- The Workato editor and automated pushes fight: a push makes any
  open editor session obsolete ("can't be saved or tested" — reload
  loses unsaved work). Pick ONE surface per change.
- Before wiring, verify the connection's Slack workspace/team matches
  the target channel's team — a connection from another workspace
  yields `channel_not_found`.
- Check the environment switcher (Development) before saving profile
  secrets — saving from the wrong environment is a real incident
  class.
- Rotation semantics: a custom OAuth profile secret update does NOT
  touch the live bot token — running integrations continue; only
  future OAuth handshakes use the new values.

## How do you automate api.slack.com app setup when the wizard fights back?

- The create-from-manifest wizard is UNAUTOMATABLE via JS injection:
  the React state never syncs from CodeMirror `setValue` (it created
  a duplicate app even with editor content verified correct right
  before Next), and synthetic keyboard/pointer events don't land.
- The WORKING path: create ANY app, then edit the manifest
  post-creation at `api.slack.com/apps/<id>` → App Manifest → YAML.
  THAT editor's `setValue` + full mouse-event dispatch
  (mousedown/mouseup/click with `bubbles:true`) validates and saves
  correctly — it can rename an app and fix scopes in one save.
- Installing a workspace-created app goes through "Request to
  install" → Slack admin review (governance by design). The React
  form message textarea needs the native-value-setter bypass:
  `Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(ta,msg); ta.dispatchEvent(new Event('input',{bubbles:true}))`
  — a synthetic `.value=` assignment alone does NOT sync and Submit
  stays disabled.
- React controlled inputs generally: use the native setter + `input`
  event; Slack's Submit/Save buttons stay disabled until real state
  changes.
- base64-injecting non-ASCII YAML (e.g. em-dashes) through `atob`
  corrupts it (Latin-1 decode) → "manifest with errors". Keep
  injected manifests pure ASCII (comments are the usual culprit —
  strip them).
- Scope/subscription changes trigger an ADMIN APPROVAL cycle for the
  app reinstall before new scopes take effect — factor the lead time
  into any event wiring. Slack may update scopes in place on
  reinstall (same token string carries new scopes); verify before
  swapping stored credentials.

## How do you triage a genie that silently stopped responding to DMs?

Symptom: a customer genie stopped responding to DMs and @mentions
with ZERO job history. Request URL verified, scopes fine — still
dead. Triage logic:

- Scopes ≠ event subscriptions. A verified Request URL only proves
  the endpoint answers Slack's challenge. Slack sends zero message
  events unless "Subscribe to bot events" includes `message.im`
  (DM) and `app_mention` (@mention). A scope screenshot proves
  nothing about events — that is the #1 miss. Editing the app
  manifest to add scopes commonly DROPS the events list in the
  process.
- Reinstall revokes the bot token. If scopes were added and the app
  reinstalled from api.slack.com directly (not through the genie
  flow), the platform holds a dead token. Fix: re-save the genie's
  Slack app details (Client ID / Secret / Signing Secret) and re-add
  the Slack trigger, or refresh the channel-responses section to
  re-sync scopes.
- One-log discriminator: api.slack.com → app → Event Subscriptions →
  delivery log after a DM + a @mention. No deliveries = the events
  list is missing (Slack-side fix). 4xx/5xx deliveries = receiver
  side (re-save app details; persistent errors → platform
  engineering with the app ID + timestamps).
- "(2)" in the app name = a duplicate install; verify the bot being
  DM'd is the app ID actually wired to the genie.
- Access boundary: internal support teams generally cannot enter
  customer workspaces or their api.slack.com apps — replies are
  guided checklists unless access is granted.

## What is the full SOP end to end?

App (from manifest, or create-then-edit-manifest) → admin install
approval → net-new custom OAuth profile (client id + secret + signing
secret) → Workato connection bound to the profile (redirect URL added
to the app manifest) → human Connect → Allow → channel invite (or
`chat:write.public`) → bind the recipe step's connection → on-demand
Test → verify the message landed in-channel FROM THE BOT identity →
start the recipe. The full step-by-step skill covers the mechanics;
this file holds the standing rules and the why.
