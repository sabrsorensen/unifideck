/**
 * W3DHubCredentialsModal — sign-in form for a W3D Hub forum account.
 *
 * Shaped after GameVaultCredentialsModal.tsx (the only other store whose
 * sign-in is a form rather than a browser OAuth or a Steam auth shortcut —
 * see docs/w3d-hub-store-spec.md §4/§11), simplified to the two fields
 * W3D Hub's login actually needs: no server URL, no TLS toggle, no
 * download-dir picker.
 *
 * Copy references the reference Linux launcher's own login page
 * (cyberarm/w3d_hub_linux_launcher, lib/pages/login.rb): "Login using
 * your W3D Hub forum account" — accounts are the site's phpBB forum
 * accounts, not a separate game-specific identity, which is worth saying
 * so a user doesn't go looking for a different signup flow.
 */
import { FC, useState } from "react";
import { ConfirmModal, TextField } from "@decky/ui";
import { useTranslation } from "react-i18next";

interface Props {
  /** Rejecting keeps the modal open with the message shown inline, so a
   *  wrong password can be corrected without retyping the username. */
  onSubmit: (username: string, password: string) => Promise<void>;
  onCancel: () => void;
  initialUsername?: string;
}

export const W3DHubCredentialsModal: FC<Props> = ({
  onSubmit,
  onCancel,
  initialUsername = "",
}) => {
  const { t } = useTranslation();

  const [username, setUsername] = useState(initialUsername);
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleLogin = async () => {
    if (!username) {
      setError(t("w3dhub.errorUsernameRequired"));
      return;
    }
    if (!password) {
      setError(t("w3dhub.errorPasswordRequired"));
      return;
    }

    setError(null);
    setLoading(true);
    try {
      await onSubmit(username, password);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg || t("w3dhub.errorLogin"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <ConfirmModal
      strTitle={t("w3dhub.loginTitle")}
      strDescription={t("w3dhub.loginDescription")}
      strOKButtonText={loading ? t("w3dhub.loggingIn") : t("w3dhub.logIn")}
      strCancelButtonText={t("w3dhub.cancel")}
      bOKDisabled={loading}
      onOK={handleLogin}
      onCancel={onCancel}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
        <TextField
          label={t("w3dhub.username")}
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />

        <TextField
          label={t("w3dhub.password")}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          bIsPassword
        />

        {error && (
          <div
            style={{
              color: "#ef4444",
              fontSize: "12px",
              padding: "4px 0",
            }}
          >
            {error}
          </div>
        )}
      </div>
    </ConfirmModal>
  );
};

export default W3DHubCredentialsModal;
