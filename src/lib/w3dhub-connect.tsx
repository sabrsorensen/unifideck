/**
 * W3D Hub sign-in — the one modal it takes, and the promise it settles.
 *
 * W3D Hub is the second store whose sign-in is a form rather than a
 * browser OAuth or a Steam auth shortcut (GameVault was the first —
 * see gamevault-connect.tsx, which this mirrors). Simpler than
 * GameVault's: no local/remote chooser, one modal, one RPC.
 *
 * `useStoreAuth.connect` must settle exactly once, or the Sign In button
 * spins forever — same `settle` guard, same reason, copied from
 * gamevault-connect.tsx (itself copied from `pickStorageForInstall`):
 * Steam's modal manager overwrites the injected `closeModal` prop, so a
 * flow that routes through it loses its own callback.
 */
import { call } from "@decky/api";
import { showModal } from "@decky/ui";

import { rpcRoutes } from "../api/rpc-routes";
import { unwrapRpcEnvelope } from "../api/useRPC";
import { W3DHubCredentialsModal } from "../components/modals/W3DHubCredentialsModal";
import type { AuthResult } from "../types/api";

const STORE = "w3dhub" as const;

export interface W3DHubConnectHandlers {
  /** Toggle the Sign In button's busy state while an RPC is in flight. */
  setBusy: (busy: boolean) => void;
}

/**
 * Drive the W3D Hub sign-in flow.
 *
 * Resolves with the `AuthResult` once the form succeeds, or `null` if
 * the user dismissed it. Never rejects: a failure comes back as
 * `{ success: false, error }` so the caller has one shape to report.
 */
export function connectW3DHub({
  setBusy,
}: W3DHubConnectHandlers): Promise<AuthResult | null> {
  return new Promise<AuthResult | null>((resolve) => {
    let settled = false;
    const settle = (result: AuthResult | null) => {
      if (settled) return;
      settled = true;
      resolve(result);
    };

    const handle = showModal(
      <W3DHubCredentialsModal
        onCancel={() => {
          handle?.Close();
          settle(null);
        }}
        onSubmit={async (username, password) => {
          setBusy(true);
          try {
            const raw = await call<unknown[], unknown>(
              rpcRoutes.connectW3dhub,
              username,
              password,
            );
            const result = unwrapRpcEnvelope<AuthResult>(raw, {
              route: rpcRoutes.connectW3dhub,
              throwing: false,
            });
            if (!result?.success) {
              // Thrown, not settled — the modal's onSubmit catch shows
              // this inline and keeps the form open, exactly like a
              // wrong-password GameVault attempt.
              throw new Error(result?.error ?? "login_failed");
            }
            handle?.Close();
            settle({ ...result, success: true, store: STORE } as AuthResult);
          } finally {
            setBusy(false);
          }
        }}
      />,
    );
  });
}
