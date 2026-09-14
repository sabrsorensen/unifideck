# W3D Hub Store Integration — Spec (v1)

> **Status:** implemented 2026-09-14 (commits `2d3c52c5`, `dd4a5b3d`) — backend complete
> (`stores/w3dhub/`, plus the small shared-file additions in §2/§7), including WWMix patch
> application, the one open question this spec originally deferred and then had to walk
> back (§13). **Not yet built: the frontend login form** — everything else described below
> is real, tested code, not a plan. Written from a source-only investigation of
> `cyberarm/w3d_hub_linux_launcher` (the open-source, unofficial Linux launcher this repo's
> sibling nix-dendrites config already packages and runs) plus a live read of an existing
> EmeraldEcho Steam library. See `docs/feasibility/w3d_hub.md` for the research trail and
> verdict this spec builds on. Follows the shape of `docs/ubisoft-store-spec.md`
> deliberately — read that first if this is your first store spec in this repo.

---

## 1. Overview

W3D Hub games (C&C Renegade, A Path Beyond, Tiberian Sun: Reborn, Red Alert 2: Apocalypse
Rising, Interim Apex, Expansive Civilian Warfare, Battle for Dune: War of Assassins — the live
catalog, confirmed 2026-09-14, lists exactly these 7 — classic C&C/Renegade-engine titles) have
**no vendor client at all**, unlike Ubisoft/Battle.net. The backend is
a plain JSON/HTTPS API (`secure.w3dhub.com`, plus a community-run alt backend) with a
username/password login, a catalog endpoint, and package download endpoints — fully
specified by the reference launcher's own source, which functions as a de facto protocol
spec (see `docs/feasibility/w3d_hub.md` §"Source investigation").

Consequently this store is **not** a wrapper (Ubisoft/Battle.net) and **not** a
CLI-delegate (Epic/GOG/Amazon's `legendary`/`gogdl`/`nile`) — Unifideck *is* the client:
own HTTP calls for auth/catalog/packages, own package extraction, own launch. Closest
existing shape in this codebase is Battle.net's `ownership/game_accounts.py` (own aiohttp
client against an unofficial JSON endpoint) crossed with GOG's download pipeline (real
file fetch + extract + local install-state tracking) — but with no OAuth/browser step
anywhere, since login is a plain credentials POST.

**Deliberate architecture decision, and the one place this spec most diverges from every
other store:** every W3D Hub title launches from **one shared Proton prefix**, not a
per-game prefix. See §3.

---

## 2. Package Layout

All paths under `py_modules/unifideck/stores/w3dhub/` (proposed).

| Module | Responsibility |
| ------ | -------------- |
| `store.py` | `W3DHubStore(StoreBase)` — facade; implements the store contract |
| `config.py` | `W3DHubConfig` — endpoints, shared prefix path, install base, feature flags (from `stores.w3dhub.*`) |
| `api.py` | Thin aiohttp client: login/refresh, `get-applications`, `get-package-details`, `get-package` (download), GSH server-list (stretch). Mirrors the request/response shapes `lib/api.rb` documents inline. |
| `catalog.py` | Fetch + `CacheManager`-cache `get-applications`; mirrors Battle.net's PUB-catalog caching pattern (`ownership/pub_catalog.py`) |
| `manifest.py` | Parse the per-app-channel manifest — **XML**, not JSON (`xml.etree.ElementTree`, stdlib, no new dependency) — into files/dependencies, matching `application_manager/manifest.rb` |
| `installer.py` | Download (parallel, aiohttp) + checksum-verify + extract packages; write `paths.ini`; write the local install marker |
| `prefix.py` | The one shared prefix: path constant, winetricks bootstrap (ported from nix-dendrites' `_package.nix`), created once |
| `library.py` | Join cached catalog metadata against the local install-marker file into `Game`s |
| `installed_state.py` | `w3dhub_installed.json` read/write — see §8 |

W3D Hub-specific code **outside** the package:

| Path | Responsibility |
| ---- | -------------- |
| `launcher/proton/infrastructure/core.py` | Add one `_w3d_hub_prefix_path(ctx)` branch to `_resolve_prefix` — the constant shared path (§3). |
| `launcher/proton/handlers/generic.py` | Add `_w3d_hub_launch(plan)` (small, same size class as `_amazon_launch`) + one `generic_launch` dispatch branch — builds the `-launcher [+connect ...] +netplayername <nickname>` args from local state, see §7 (corrected 2026-09-14 — not baked into the shortcut's `LaunchOptions`, which is always the fixed `"<store>:<game_id>"` dispatch token). |
| Frontend login form | **New UI surface** — every existing store signs in via OAuth-in-Edge or a vendor-GUI shortcut; this is the first plain username/password form. No existing component to reuse as-is. |

---

## 3. The Shared Prefix Model

One Proton prefix, used by every W3D Hub title — not Ubisoft/Battle.net's auth/template/
per-game three-tier model.

**Why this is safe here specifically, and wouldn't be for Ubisoft/Battle.net:** the
per-game isolation those stores use exists because of a *vendor client* that self-updates
aggressively and can corrupt shared state for everything sharing its prefix (Battle.net's
Agent churn, UPC's own update cycle). W3D Hub has no such client — each title is a
standalone .exe with no background service living in the prefix. The failure mode
isolation defends against structurally doesn't exist here.

**Why it's also the cheaper choice:** all W3D Hub titles share one engine's dependency
profile. nix-dendrites' `_package.nix` already proves this empirically — it bootstraps
*one* prefix with *one* winetricks set for every game the existing desktop launcher runs:

```
corefonts vcrun2008 vcrun2010 xact xact_x64 d3dx9 d3dx9_43 msxml3 dotnet452 win7
d3dcompiler_47 dxvk
```

(`win7` is a DLL-override, not a winetricks verb, for `dotnet452`; `dxvk` is required
specifically because A Path Beyond's `-launcher` UI is a DX11/CEF overlay in front of the
DX9-era engine — confirmed by reproducing a crash down to the missing `dxgi.dll`/`d3d11.dll`
Wine ships no native implementation of.)

A per-game model would re-run that identical bootstrap once per title for zero benefit.
Under the shared model it runs exactly once, at prefix-creation time (§6 has no
per-install dependency step at all).

**The two real caveats, both bounded:**

- **Pin one Proton/GE-Proton build for this prefix.** A shared prefix means a Proton
  version change is a blast-radius-of-every-title event instead of one. `config.py` should
  carry an explicit pinned version rather than "whatever's currently the user's default
  compat tool", same spirit as Battle.net's Proton-coupling note in its own audit.
- **Cross-title registry/config collision is possible, not structurally prevented.**
  Different titles write to their own install-path-scoped keys and separate install
  subdirectories, so collision isn't the norm — but per-game isolation makes it
  impossible, and shared doesn't. Worth a real check once titles are actually installed
  side-by-side; not a blocker for v1.

---

## 4. Authentication Flow

Plain `POST /apis/launcher/1/user-login`:

```json
{"username": "...", "password": "..."}
```
or, for an already-signed-in session:
```json
{"refreshToken": "<token>"}
```

Response: `{session_token, userid, accessToken, refreshToken, accessTokenExpiry}` (or
`{"error": "login-failed"}`). No OAuth, no browser, no CDP. `accessToken` goes on
subsequent calls as `Authorization: Bearer <token>`.

**Unverified, first thing to confirm against a live account before writing `installer.py`:**
whether `get-package-details`/`get-package` (the actual download endpoints) require a
Bearer token for every channel, or only for access-gated ones (beta/restricted). The
reference source's own comment marks the Bearer header as *optional* on
`get-applications` — package download's requirement isn't documented either way in the
observed source and needs a real request to confirm.

**Frontend:** genuinely new surface. Every existing store signs in via an OAuth popup in
the shared Edge browser or a vendor-GUI Steam shortcut — none has a credentials form.
`StoreBase.start_auth(self, **kwargs)` already accepts arbitrary kwargs at the interface
level, so `username`/`password` can flow through the existing `store_auth(store, action)`
RPC without a new backend entry point; what's missing is the frontend form itself and
its plumbing into that RPC call.

`refreshToken` persisted locally (same "sign in once, read local state after that" shape
Ubisoft/Battle.net use for their vendor-client sessions) lets subsequent syncs skip the
interactive login.

---

## 5. Library Flow

Unlike every other store, there is no local vendor-client catalog to parse — the catalog
lives entirely on the server, and install state lives entirely in whatever Unifideck
itself records (§8, no UPC-style on-disk database to read).

1. **Fetch + cache the catalog** — `get-applications` (alt backend works unauthenticated
   per the reference's own merge logic in `Api._applications`; the primary backend adds
   entries gated by the account's access level when signed in). Cache via `CacheManager`,
   refreshed on sync — same pattern as Battle.net's PUB catalog cache, much simpler since
   there's no local-file freshness question to reason about.
2. **Read the local install marker** (`w3dhub_installed.json`) for what's actually
   installed: `{app_id, channel, version, install_path, exe_name, installed_at}` per
   title, written by `installer.py` on a successful install.
3. **Join** catalog metadata (name, icon, channel list, access level) against install
   state into `Game`s. A title with no local marker is "not installed", not "not owned" —
   channel access-level gating (if any) determines true ownership for restricted
   channels; the public `release` channel is presumed generally installable.

---

## 6. Install Flow

**Correction (2026-09-14, live-verified against the real API — see §13): WWMix patch
application is required for v1, not deferrable.** The original design here assumed a
fresh install could always fetch a `type="Full"` manifest and skip WWMix entirely, since
`apply_patch` is only reached for `Manifest#patch?`. That's true, but it doesn't mean
patching is avoidable: **most titles' current `release` manifest is itself a `Patch`**,
walking a `baseVersion` chain back to the nearest `Full` manifest — measured live, chain
depth ranges from 1 (`woa`, Full directly) to 30 (`tsr`). A fresh install of `tsr` today
means fetching the `Full` baseline manifest and applying 29 patches in sequence, in order,
every single time — there is no "give me the current full snapshot" shortcut
(`version: ""` returns `{"error": "not-found"}`, confirmed live).

The good news: **patching a WWMix container is not a binary-diff algorithm.** Traced
`apply_patch` fully (`application_manager/tasks/task.rb`) — a "patch" package is a
downloaded `.zip` containing one file, `<target-file-name>.patch`, itself a WWMix archive
whose entries are: one special `.w3dhub.patch`/`.bhppatch` entry (a JSON blob,
`{removedFiles: [...], updatedFiles: [...]}`), plus one **whole fresh copy** of every
updated file. Applying it is: open the target `.mix`, delete the named `removedFiles`
entries, copy in the fresh `updatedFiles` entries by name (`add_entry(replace: true)`),
write the merged result to a temp path, then swap it over the original. No delta/diff of
file *contents* anywhere — WWMix's whole-container `save()` (mechanical: header + sorted
CRC32-keyed entry table + names, confirmed by reading it fully, ~140 LOC) already does the
"write the result" half. **The WWMix port (read + write + this merge routine) stays small
— the earlier ~287 LOC estimate for the reader alone was accurate; the writer is
comparably sized.** What changed is *when* it's needed, not how big it is.

`ren` (C&C Renegade) has no downloadable manifest at all
(`auto_import_win32_registry`/`auto_import` in the reference only run
`unless W3DHub.windows?` returns — Windows-only registry import from an existing
Steam/GOG install of the original game). **Out of scope for the install pipeline
entirely** — 6 of the 7 catalog titles (`apb`, `ar`, `ecw`, `ia`, `tsr`, `woa`) go through
the package system; `ren` needs a separate, later "import an existing install" flow if
ever pursued.

**Recommended staging, to de-risk the WWMix work rather than absorb it all at once:**
build the full pipeline (auth → catalog → download → extract → launch) against **`woa`
first** — its current release manifest is `Full` directly, so it proves every other piece
end-to-end with zero WWMix code. Add the patch-merge routine as the very next slice, which
then unlocks all five remaining downloadable titles at once (it's one routine, not
per-title logic).

Steps, mirroring `application_manager/tasks/installer.rb`'s sequence minus its
partial-download-resume and repair paths (see §12):

1. **Fetch the manifest chain** — `get-package-details` for
   `{category: "games", subcategory: <app_id>, name: "manifest.xml", version: <target>}`,
   parse the XML, and if `type == "Patch"`, repeat for `baseVersion` until `type == "Full"`
   is reached. Collect the full chain in base-to-target order.
2. **Full manifest's files** — download+extract each unique `<File package="...">` `.zip`
   directly into the install directory (no WWMix involved for these).
3. **Each subsequent patch manifest, in order** — download the patch `.zip`(s) it
   references, and for each target `.mix` file the patch touches: load it, apply the
   remove/update merge described above, save.
4. **Write `data/paths.ini`** into the install directory — format is fully known, port
   directly:
   ```ini
   [paths]
   RegBase=W3D Hub
   RegClient=<category>\<id>-<channel>
   RegFDS=<category>\<id>-<channel>-server
   FileBase=W3D Hub
   FileClient=<category>\<id>-<channel>
   FileFDS=<category>\<id>-<channel>-server
   UseRenFolder=<bool, from the catalog's usesRenFolder extended-data flag>
   ```
5. **Write the local install marker** (`w3dhub_installed.json`, §8).

Checksum verification: whole-file checksum against `get-package-details`' `checksum` field
for v1; the reference's chunked `checksum_chunks` resumable-partial-download scheme is a
later optimization (see §12).

No per-install winetricks/dependency step — that's §3's one-time shared-prefix bootstrap,
already done before the first install ever runs.

---

## 7. Launch Flow

**Correction (2026-09-14): `LaunchOptions` is not where the connect args go.** The
original version of this section assumed Steam's per-shortcut `LaunchOptions` field could
carry `-launcher +connect ...` directly, the way the EmeraldEcho manual shortcuts do.
Checked `ShortcutService` directly: every Unifideck-generated shortcut's `Exe` is always
`unifideck-launcher`, and `LaunchOptions` is always the fixed dispatch token
`"<store>:<game_id>"` (confirmed against real shortcuts —
`battlenet:w3`/`battlenet:s1`/etc. in EmeraldEcho's own `shortcuts.vdf`) — it's what the
launcher's own argv parsing uses to pick *which* store/game to run, not a place for
game-specific arguments. `launcher/types/options.py`'s `parse_launch_options` (which feeds
`plan.state.game_args`) exists to capture anything a user *additionally* appends via
Steam's own "Edit Launch Options" dialog on top of that token — it's user customization
layered on, not the primary source of a store's own launch arguments. Amazon's
`_read_amazon_fuel_args` is the actual precedent: store-specific args are constructed
*inside the launch handler*, from local state, and `plan.state.game_args` is appended
after.

So this **does** need one small addition to `generic.py`'s dispatch (not a bespoke new
file — the function is a handful of lines, same size class as `_amazon_launch`):

1. **`_w3d_hub_launch(plan)`** in `generic.py`, alongside `_gog_launch`/`_amazon_launch`,
   wired into `generic_launch`'s `store ==` branches. Builds
   `["-launcher"] + (["+connect", f"{ip}:{port}"] if a server is chosen else []) +
   ["+netplayername", nickname]`, reading the nickname from a small local settings file
   (`w3dhub_settings.json` in the store's data dir — same local-file pattern as Amazon's
   `fuel.json` read, not a `ConfigManager` round trip), then appends
   `plan.state.game_args` (any user customization) and runs via `run_umu_with_retry`,
   identical to `_raw_exe_launch` otherwise.
2. **One `_resolve_prefix` branch** in `launcher/proton/infrastructure/core.py` — a
   `_w3d_hub_prefix_path()` returning the constant shared path (§3), alongside the
   existing `_ubisoft_prefix_path`/`_battlenet_prefix_path` functions (which derive a
   path per `game_id`; this one doesn't need `ctx` at all).
3. **`<nickname>` is a free-text local setting** (`server_list_username` in the
   reference, **not** bound to the W3D Hub account) — prompt once, persist, same shape as
   the reference's own first-use prompt.
4. **Server selection, v1**: omit `+connect` entirely and let the game's own menu/server
   browser handle it, unless/until the GSH server-list API (`gsh.w3d.cyberarm.dev` —
   `/listings/getAll/v2`, `/listings/getStatus/v2/:id`, both unauthenticated JSON,
   confirmed live) is wired up as a real in-Unifideck picker. Hardcoding one server
   address (what the EmeraldEcho manual shortcuts do) isn't something to ship generally.

This also settles a question the original draft of this spec hadn't asked yet: W3D Hub
should **not** join `launcher.wrapper_stores.WRAPPER_STORES` (`{"ubisoft", "battlenet"}`)
— that set (and its `client_runs_in_prefix` flag) exists for stores with an actual vendor
client resident in the prefix, which W3D Hub doesn't have; it's shaped like a GOG/Amazon
Windows title that happens to share one prefix across games, not like a wrapper store.

5. **Exe path resolution** — from the local install marker (`w3dhub_installed.json`) for
   the `app_id`/`channel`, never reconstructed from the id, same discipline `paths.py`-
   style modules in every other store already follow. This is what `LaunchContext`
   construction needs to resolve before `generic_launch` ever runs.

---

## 8. Local State (`w3dhub_installed.json`)

There is no vendor-client catalog to read for install/ownership state (unlike Ubisoft's
`ubisoft_id_map.json`, which bridges *IDs* across systems that all separately know about
the game — here, Unifideck is the only thing that knows a title is installed at all).

Location: `~/.local/share/unifideck/w3dhub_installed.json`. Per-`<app_id>-<channel>` entry:

```json
{
  "<app_id>-<channel>": {
    "version": "1.2.3",
    "install_path": "/abs/path/inside/the/shared/prefix/.../<category>/<id>-<channel>",
    "exe_name": "game.exe",
    "installed_at": "2026-09-14T00:00:00Z"
  }
}
```

`ecw` is the one app with a non-standard exe name (`game500.exe` per the reference) —
worth a small lookup table or a field on the catalog entry rather than a hardcoded
special case buried in launch code, unlike the reference's own inline `app_id == "ecw"`
check.

---

## 9. File & Path Conventions

```
~/.local/share/unifideck/
├── w3dhub_installed.json               # install-state marker, §8
└── prefixes/w3dhub/
    └── shared/                          # the one prefix, every title (§3)
        └── drive_c/.../W3D Hub/<category>/<id>-<channel>/   # per-title install dir — verify exact
                                                                # shape against a live install, don't
                                                                # assume from paths.ini's naming alone
```

---

## 10. Configuration (`stores.w3dhub.*`)

Proposed keys, `config.py` (`W3DHubConfig`):

| Key | Default | Meaning |
| --- | ------- | ------- |
| `prefix_path` | `~/.local/share/unifideck/prefixes/w3dhub/shared` | The one shared Proton prefix |
| `pinned_proton_version` | *(explicit, not "current default")* | See §3 — a shared prefix should not silently follow the user's default compat-tool choice |
| `install_marker_file` | `~/.local/share/unifideck/w3dhub_installed.json` | §8 |
| `parallel_downloads` | `4` | Matches the reference's own default |
| `netplayer_name` | *(prompted once, persisted)* | §7 |
| `server_list_source` | `:gsh` | Stretch — GSH endpoint selection once the browser lands |

---

## 11. Frontend Integration

| Concern | Where |
| ------- | ----- |
| Login form | **New component** — username/password, not an OAuth popup or a shortcut launch. No existing store's sign-in UI is a template for this. |
| Install/launch | Normal download-queue + play-button flow — **no shortcut-launch dance at all**, since there's no vendor GUI to `RunGame`. Simpler than every wrapper store, and simpler than the OAuth stores too (no browser popup). |
| Nickname setting | A settings-panel field, prompted on first launch if unset (mirrors the reference's own first-use prompt) |
| Server browser (stretch) | New page/panel backed by the GSH listings API, feeding `+connect` — not v1 |

---

## 12. Notable Constraints & Deferred Scope

- **No upstream LICENSE.** `cyberarm/w3d_hub_linux_launcher` ships no license file (also
  why nix-dendrites' own package treats the prebuilt binary as unfree). Implement from
  the observed protocol/format shape (the JSON request/response bodies, the manifest XML
  schema, the `paths.ini` format) rather than translating the Ruby source line-by-line —
  formats and protocols aren't copyrightable, specific code expression is.
- **WWMix patch-merge is required for v1** (§6, corrected 2026-09-14 — the original
  version of this doc assumed it was deferrable; live testing showed otherwise). Stage the
  build against `woa` (no patching needed) first to prove the rest of the pipeline, then
  add the patch-merge routine to unlock the other five downloadable titles at once.
- **`<Dependency>` manifest entries are unwired even upstream.** `create_wine_prefix` and
  `install_dependencies` in the reference's own `task.rb` are literal `# TODO:` stubs —
  the winetricks set this spec uses (§3) was derived independently (community knowledge,
  proven by nix-dendrites' `_package.nix`), not extracted from a working reference
  implementation. There is no "more correct" per-manifest dependency list to port; the
  fixed set is the best available source of truth.
- **Chunked/resumable download verification exists in the reference** (`checksum_chunks`,
  partial-file resume) and is deliberately not in v1's scope (§6) — whole-file checksum
  only, full re-download on any failure. Fine for modest install sizes; worth adding if
  larger titles or flaky connections make it worth the complexity.
- **No ToS/support statement found** for W3D Hub/Cyberarm, unlike Battle.net's public 2025
  Linux/Wine statement (see the Battle.net feasibility doc for that precedent). The
  existing Linux launcher operating openly against the same API for years is a reasonable
  signal, not a substitute for checking directly before shipping broadly.
- **`+netplayername` is cosmetic**, not account-bound — fine to default from the account
  username once logged in, but must remain user-editable since it's meaningful in-game
  identity, not an auth artifact.
- **`ren` (C&C Renegade) is out of the install pipeline entirely** (§6) — no downloadable
  manifest; the reference imports it from an existing Windows registry entry left by a
  separate Steam/GOG install. A later, separate "import" flow if ever pursued — v1 covers
  the other 6 catalog titles.

---

## 13. Live API verification (2026-09-14)

Unauthenticated `GET`/`POST` requests against the alt backend
(`w3dhub-api.w3d.cyberarm.dev`), no account involved — resolves several items §4/§12
previously flagged as unverified.

**§4's auth-requirement question is resolved for the public catalog: no Bearer token
needed.** `get-applications`, `get-package-details`, and the resulting `download_url`
fetches all returned real data with zero `Authorization` header. Whether *restricted*
channels (a `user-level` above `public`) need one is still unconfirmed — none of the 7
games' channels in the current catalog carry anything but `"user-level": "public"`, so
there was nothing gated to test against.

**Live catalog** (`get-applications`, alt backend): 7 games, matching §1's corrected list
exactly — `apb`, `ar`, `ecw`, `ia`, `ren`, `tsr`, `woa`. Confirmed field shape matches
§1/§5 exactly, e.g. `apb`: `channels: [{id, name, "current-version", "user-level"}, ...]`,
`extended-data: [{name: "colour"|"usesEngineCfg"|"usesRenFolder", value}]`.

**Manifest chain depth per title**, walking `baseVersion` from each `release` channel's
`current-version` to the nearest `Full` manifest (drives §6's corrected install flow):

| `app_id` | release version | chain depth | notes |
|---|---|---|---|
| `woa` | 1.0.1.3 | 1 (Full) | no patching needed — build the pipeline against this first |
| `ia` | 1.0.4.1 | 2 | 1 patch |
| `ecw` | 1.0.1.5 | 3 | 2 patches |
| `apb` | 3.8.1.0 | 4 | 3 patches |
| `ar` | 0.9.0.13 | 8 | 7 patches |
| `tsr` | 2.1.0.2 | 30 | 29 patches — the real stress case for the patch-merge routine |
| `ren` | 1.0.0.0 | — | `manifest.xml` request returns `{"error": "not-found"}` — confirms §6's registry-import special case, not a download-pipeline title |

`version: ""` (hypothesized "give me the current full snapshot" shortcut) was tested
directly and returns `{"error": "not-found"}` — there is no way around walking the chain.

---

_See `docs/feasibility/w3d_hub.md` for the underlying research (source reading, live
EmeraldEcho shortcut verification) this spec is built on._
