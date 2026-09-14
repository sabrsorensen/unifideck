# Feasibility Study: W3D Hub store

**Verdict: FEASIBLE — a new archetype, and a comparatively cheap one.** W3D Hub (C&C Renegade,
A Path Beyond, Tiberian Sun: Reborn, Red Alert 2: Apocalypse Rising, Interim Apex, Expansive
Civilian Warfare, Battle for Dune: War of Assassins — classic C&C/Renegade-engine titles built on
the W3D engine; confirmed 2026-09-14 against the live catalog, 7 games total) has no
official Linux support and no official API docs, but unlike Battle.net there is no vendor client
to wrap: the backend is a plain JSON/HTTPS API, already fully specified by an open-source
Linux-native launcher (`cyberarm/w3d_hub_linux_launcher`, MIT-adjacent-but-unlicensed — see
Risks) that this repo's sibling nix-dendrites config already packages and runs on EmeraldEcho.
The shape is closer to Epic's `legendary-gl` (a thin API client Unifideck drives directly) than to
Ubisoft/Battle.net's launcher-wrapper archetype — except there is no existing CLI to shell out to,
so Unifideck would need to be its own thin client.

## MVP-bar walkthrough

| Step | Status | Evidence |
|------|--------|----------|
| Login | Proven by reference source | `POST /apis/launcher/1/user-login` with `{username, password}` or `{refreshToken}` → `{session_token, userid, accessToken, refreshToken, accessTokenExpiry}`. Plain JSON over HTTPS, no OAuth, no browser, no CDP — simpler than every store Unifideck already has. |
| Enumerate owned/available games | Proven by reference source | `POST /apis/launcher/1/get-applications` (Bearer token optional) → `{applications: [{id, name, type, category, "studio-id", channels: [...], "web-links", "extended-data"}]}`, filtered to `category == "games"`. Each channel carries its own version + the account's access/user level — this *is* the ownership/entitlement signal, no separate endpoint needed. |
| Download/install | Proven feasible, not yet exercised end-to-end | Packages are fetched individually (`get-package-details` → `get-package`) and unpacked from **WWMix**, a small proprietary archive format (287 LOC reference implementation — see below). No compression, no real cryptography despite the "encrypted" flag name (it only selects a MIX1 vs MIX2 header magic). |
| Launch | **Proven live, twice** | (1) `ApplicationManager#run`/`#join_server` in the reference source spawn `wine <install_dir>/game.exe -launcher +connect <ip>:<port> +netplayername <name>` directly — no launcher GUI involved once installed. (2) Independently confirmed on EmeraldEcho's own live Steam library — see below. This is the strongest single piece of evidence in this study. |

Stretch: a server browser / "Play Now" quick-match, backed by a *second*, independent API
(`gsh.w3d.cyberarm.dev`, the "GSH"/game-server-hub listings) — `GET /listings/getAll/v2` and
`/listings/getStatus/v2/:id`, both plain JSON, no auth. Post-MVP, but cheap to add once the launch
path exists, since it only feeds the same `+connect` argument.

## Source investigation (2026-09-14): the reference launcher is the spec

`~/src/w3d_hub_linux_launcher` is a from-scratch, native-Linux reimplementation of the official
(Windows/.NET-only) W3D Hub launcher, written in Ruby using the Gosu game-engine/GUI toolkit
(bundled as a Tebako standalone binary — this is what nix-dendrites' `_package.nix` fetches and
wraps). Because it's open, actively maintained (commits within the last few weeks, including
bugfixes to the exact format this study leans on), and extensively self-documented in code
comments, it functions as a de facto protocol spec:

- **`lib/api.rb`** — the entire HTTP surface in ~300 lines: login/refresh, applications list,
  package details/download, news, events, and the separate GSH server-list API. Every endpoint
  has its request/response shape documented inline by the author (who reverse-engineered it from
  the official launcher).
- **`lib/ww_mix.rb`** (287 LOC) — MIX1/MIX2 archive reader/writer. Header: magic (`MIX1`/`MIX2`),
  file-data offset, file-names offset. Entries: CRC32-of-uppercased-name as the key, content
  offset + length. No compression, no encryption in the cryptographic sense — small and
  self-contained enough to port directly.
- **`lib/settings.rb`** — confirms account/session state persists to a local `data/settings.json`
  (`account: {}`, `games: {}`, `applications: {}` keys) once the user has logged in once, the
  same "sign in once, read local state after that" shape Unifideck's Ubisoft/Battle.net stores
  already use for the *native-client* half of their auth.
- **`lib/application_manager.rb`** — `run(app_id, channel, *args)` builds
  `<dxvk_env><wine_command><exe_path> -launcher <args>`; `join_server` supplies
  `+connect <ip>:<port> +netplayername <username>[+password "..."][+multi]`, where `username` is
  a free-text local nickname (`Store.settings[:server_list_username]`), **not** bound to the W3D
  Hub account login. `play_now` layers a "find the best open server for the installed version"
  matcher on top of the GSH listings before calling the same `join_server`.
- No CLI flags exist for a headless/direct game launch *of the launcher itself*
  (`ARGV` only understands `--bundler`/`--debug`/`--developer`) — the GUI always opens. This does
  **not** block Unifideck, because Unifideck would never launch the launcher; see below.

## Live verification: EmeraldEcho's own Steam library (2026-09-14)

The user had already hand-built five non-Steam Steam shortcuts on EmeraldEcho for W3D Hub titles
installed via the same `w3d-hub-launcher` this repo's sibling nix-dendrites config packages, to
auto-join their preferred community server. Read directly from
`~/.steam/steam/userdata/<id>/config/shortcuts.vdf` (binary VDF, parsed with a throwaway stdlib
script):

```
AppName:       Red Alert: A Path Beyond
Exe:           wine "/home/sam/.local/share/W3DHubAlt/games/apb/release/game.exe"
StartDir:      "/home/sam/.local/share/W3DHubAlt/games/apb/release/"
LaunchOptions: -launcher +connect 192.99.148.53:7000 +netplayername SmootherMars32
```

(Four more, identically shaped, for Tiberian Sun: Reborn, Red Alert 2: Apocalypse Rising, Interim
Apex, and Battle for Dune: War of Assassins — different `<code>`/port per title.)

Three things this confirms, none of which could be verified from source reading alone:

1. **`W3DHubAlt` is the launcher's own `DIR_NAME`** (`lib/version.rb`) — these are titles the
   *existing, already-packaged* Cyberarm launcher installed, not a separate/unofficial install
   method. A Unifideck integration can read this exact directory layout for install detection
   with zero install-flow work as a first increment.
2. **The command is a byte-for-byte match** of `ApplicationManager#run` + `#join_server` above —
   this is the officially-supported launch path, independently arrived at by the user, not a
   fragile reverse-engineered trick.
3. **The launcher GUI is genuinely optional for launch.** The "no headless CLI flag" finding from
   the source read looked like a blocker for a tight Unifideck integration; it isn't one, because
   the thing that needs a CLI hook is the *game*, and the game already has one (standard
   Renegade-engine `+connect`/`+netplayername` arguments), independent of the launcher entirely.

## Integration design (native API-client archetype, staged)

**Phase 1 — detect + direct-launch already-installed games.** No WWMix work, no install flow.
Scan `~/.local/share/W3DHubAlt/games/<code>/<channel>/` for a `game.exe` (`game500.exe` for the
one special-cased app, `ecw`), cross-reference `<code>` against `get-applications` for display
name/icon/channel metadata, and launch via `wine <path>/game.exe -launcher +connect <ip>:<port>
+netplayername <nickname>` — mirroring Battle.net's `unifideck-launcher battlenet:<code>` Steam
shortcut convention with a `w3dhub:<code>` equivalent. Server selection sourced from the GSH
listings API rather than a hardcoded address. This alone gets EmeraldEcho's five titles (and any
others the user installs via the existing desktop launcher) into Unifideck's library with real
Gaming Mode launch, for a small fraction of a full store implementation's cost.

**Phase 2 — native install/update.** Port `ww_mix.rb` to Python (small, self-contained — see
Risks re: licensing), implement the login/applications/package-details/package-download API calls
(aiohttp, same pattern as the rest of Unifideck's stores), and drive install into the *same*
`~/.local/share/W3DHubAlt/games/<code>/<channel>/` layout so Phase 1's detection and launch code
needs no changes. Reuse nix-dendrites' `_package.nix` winetricks bootstrap set verbatim
(corefonts, vcrun2008/2010, xact, d3dx9, msxml3, dotnet452, `win7` DLL-override, `d3dcompiler_47`,
**dxvk** — already proven necessary for A Path Beyond's DX11/CEF launcher overlay) for the
per-prefix setup, the same way Ubisoft/Battle.net's stores reuse a shared winetricks bootstrap.

## Effort estimate

**Phase 1: small — comparable in scope to a single existing store's ownership+launch slice
without any install/auth machinery, roughly 5–8 files.** No WWMix, no auth flow (installed-state
detection alone is unauthenticated — `get-applications` without a Bearer token still returns the
public/alt-backend catalog per `Api._applications`'s merge logic), no prefix lifecycle beyond what
`_package.nix` already bootstraps.

**Phase 2: medium, Ubisoft-adjacent minus the vendor-client compat workarounds.** Login + catalog
+ package-details are trivial JSON (a day or two). WWMix read+write is the real port (the
reference is 287 LOC and self-contained; budget more for edge cases the Ruby bugfix history
suggests exist — see Risks). No prefix-crash-workaround research needed (unlike Battle.net's
Xalia/`WINE_SIMULATE_WRITECOPY` saga) since the games are plain DirectX 9-era titles, not a
CEF-embedding vendor client.

## Risks

- **No upstream license.** `cyberarm/w3d_hub_linux_launcher` ships no LICENSE file — nix-dendrites'
  own package comment already flags this ("treat the prebuilt binary as unfree"). Port the
  *protocol* (the JSON shapes, the WWMix binary layout) from the observed wire format/file
  structure, not literal translated Ruby — formats and protocols aren't copyrightable, specific
  code expression is.
- **WWMix is a live target.** Recent upstream commits include a real correctness bug in this exact
  subsystem ("Fix WWMix using a signed byte instead of unsigned byte as length container") and
  encryption-flag-preservation fixes to its patching path — evidence the format has sharp edges a
  clean-room port could also hit. Budget test coverage against real downloaded packages, not just
  the header spec.
- **Unofficial API, no ToS statement found.** Unlike Battle.net (which has a public 2025
  Linux/Wine support statement), no equivalent W3D Hub/Cyberarm statement was found in this pass —
  worth a direct check before shipping broadly, though the existing Linux launcher operating
  openly against the same API for years is a reasonable signal.
- **Phase 1's value is contingent on the user already running the existing desktop launcher at
  least once per game** — it's a launch-time convenience layer, not a replacement, until Phase 2
  lands. Worth shipping anyway: it's cheap and immediately useful for EmeraldEcho's existing
  installs.
- **`+netplayername` is cosmetic, not account-bound** — fine for MVP, but a nice-to-have is
  letting the user set it from a Unifideck setting rather than requiring them to have already
  configured `server_list_username` in the desktop launcher.

## OSS leverage

| Project | License | Health (2026-09-14) | Reuse |
|---------|---------|---------------------|-------|
| cyberarm/w3d_hub_linux_launcher | **none shipped** — treat as all-rights-reserved | active (commits within weeks) | De facto API + WWMix format spec; do not copy code verbatim (see Risks) — reimplement from the observed protocol/format shape. |
| This repo's sibling `nix-dendrites` `modules/features/w3d-hub-launcher/_package.nix` | repo-internal | current | Already-solved winetricks bootstrap set for W3D-engine games under Wine (corefonts/vcrun/xact/d3dx9/msxml3/dotnet452/win7/d3dcompiler_47/dxvk) — reuse the exact package list for any prefix Unifideck manages for these titles, no re-derivation needed. |

## Sources

`~/src/w3d_hub_linux_launcher` (cyberarm/w3d_hub_linux_launcher, cloned locally):
`lib/api.rb`, `lib/api/applications.rb`, `lib/api/account.rb`, `lib/ww_mix.rb`,
`lib/application_manager.rb`, `lib/settings.rb`, `lib/version.rb`. nix-dendrites:
`modules/features/w3d-hub-launcher/w3d-hub-launcher.nix`,
`modules/features/w3d-hub-launcher/_package.nix`, `docs/lutris-renegade-x-launcher.md` (prior
manual-Lutris attempt, superseded by the native launcher but documents the same winetricks set
independently). Live verification: EmeraldEcho `~/.steam/steam/userdata/<id>/config/shortcuts.vdf`,
read 2026-09-14 over SSH.
