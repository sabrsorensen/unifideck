# W3D Hub Store Integration — Spec (v1 design)

> **Status:** design, not yet implemented. Written 2026-09-14 from a source-only
> investigation of `cyberarm/w3d_hub_linux_launcher` (the open-source, unofficial Linux
> launcher this repo's sibling nix-dendrites config already packages and runs) plus a live
> read of an existing EmeraldEcho Steam library. See `docs/feasibility/w3d_hub.md` for the
> research trail and verdict this spec builds on. Follows the shape of
> `docs/ubisoft-store-spec.md` deliberately — read that first if this is your first store
> spec in this repo.

---

## 1. Overview

W3D Hub games (Renegade X, A Path Beyond, Tiberian Sun: Reborn, Red Alert 2: Apocalypse
Rising, Interim Apex, Battle for Dune: War of Assassins, and others — classic C&C/Renegade-
engine titles) have **no vendor client at all**, unlike Ubisoft/Battle.net. The backend is
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

All paths under `py_modules/unifideck/stores/w3d_hub/` (proposed).

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
| `launcher/proton/handlers/w3d_hub.py` | Launch handler: resolve exe from the install marker, resolve the (constant) shared prefix path, build `-launcher +connect ... +netplayername ...`, run via `run_umu_with_retry` |
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

## 6. Install Flow (v1 — deliberately narrowed scope)

**WWMix (the proprietary archive/patch format, `lib/ww_mix.rb`) is used by the reference
launcher *only* for applying incremental patches to an already-installed copy** —
`apply_patch` is its only call site in `application_manager/tasks/task.rb`. A fresh
install (`Manifest#full?`) never touches it: packages are downloaded as plain `.zip` and
extracted directly (`unpack_package`). **v1 scope: full installs and full reinstalls on
update, never incremental patching.** This defers the entire WWMix port — genuinely the
single biggest complexity reduction available, and a reasonable trade given these are
modest-sized (single-digit-GB) titles where a full re-download on update is a real but
acceptable cost, not a WWMix reimplementation's worth of risk.

Steps, mirroring `application_manager/tasks/installer.rb`'s sequence minus everything
that sequence spends on patch/repair paths:

1. **Fetch the manifest** — `get-package-details` for the target app/channel/version's
   manifest "package" (the manifest itself is fetched the same way as game data, just a
   differently-named package entry), parse the returned XML for `<File name= package=>`
   entries (skip any carrying `<Patch>` — full-install-only, per above) and `<Dependency>`
   entries (informational only in v1 — see §12, the reference never actually wires these
   to anything either).
2. **Build the unique package set** from the `<File package="...">` attributes.
3. **Download** each package `.zip` (parallel, `aiohttp`, bounded by a `parallel_downloads`
   setting mirroring the reference's own default of 4) with a whole-file checksum verify
   against the `get-package-details` response's `checksum` (the reference's chunked
   `checksum_chunks` resumable-partial-download scheme is a later optimization, not v1 —
   see §12).
4. **Extract** each `.zip` into the title's install directory inside the shared prefix.
   Exact on-disk convention needs live confirmation against `write_paths_ini`'s
   `RegClient`/`FileClient` naming (`#{category}\#{id}-#{channel}`) — don't fabricate the
   path shape before checking a real install.
5. **Write `data/paths.ini`** into the install directory — format is fully known, port
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
6. **Write the local install marker** (`w3dhub_installed.json`, §8).

No per-install winetricks/dependency step — that's §3's one-time shared-prefix bootstrap,
already done before the first install ever runs.

---

## 7. Launch Flow

`launcher/proton/handlers/w3d_hub.py :: w3d_hub_launch(plan)`:

1. **Resolve the exe path** from the local install marker (`w3dhub_installed.json`) for
   the `app_id`/`channel` — never reconstructed from the id, same discipline
   `paths.py`-style modules in every other store already follow.
2. **Resolve the prefix path** — the one constant shared path (§3), not derived per-game.
3. **Build launch args**: `-launcher +connect <ip>:<port> +netplayername <nickname>`,
   matching `ApplicationManager#run`/`#join_server` exactly (confirmed live against
   EmeraldEcho's own working non-Steam shortcuts, see `docs/feasibility/w3d_hub.md`).
   `<nickname>` is a free-text local setting (`server_list_username` in the reference,
   **not** bound to the W3D Hub account) — prompt once, persist, same shape as the
   reference's own first-use prompt.
4. **Server selection, v1**: omit `+connect` entirely and let the game's own menu/server
   browser handle it, unless/until the GSH server-list API (`gsh.w3d.cyberarm.dev` —
   `/listings/getAll/v2`, `/listings/getStatus/v2/:id`, both unauthenticated JSON) is
   wired up as a real in-Unifideck picker. Hardcoding one server address (what the
   EmeraldEcho manual shortcuts do) isn't something to ship generally.
5. **Run** via `run_umu_with_retry`/`ProtonLaunchPlan`, same as every other store — the
   only difference from Ubisoft/Battle.net's handlers is that `prefix_path` here is a
   constant, not a per-`game_id` lookup.

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
- **WWMix/patch support is out of scope for v1** (§6) — a fast-follow, not a blocker.
  Revisit if full-reinstall-on-update proves too expensive in practice (unlikely at these
  install sizes).
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
- **Auth requirement for package download is unverified** (§4) — confirm with a live
  account before writing `installer.py`, don't assume either way.
- **`+netplayername` is cosmetic**, not account-bound — fine to default from the account
  username once logged in, but must remain user-editable since it's meaningful in-game
  identity, not an auth artifact.

---

_See `docs/feasibility/w3d_hub.md` for the underlying research (source reading, live
EmeraldEcho shortcut verification) this spec is built on._
