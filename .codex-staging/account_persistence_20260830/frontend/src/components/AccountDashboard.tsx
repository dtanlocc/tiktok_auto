import { useCallback, useEffect, useRef, useState, type ChangeEvent } from "react";
import {
  FileUp,
  FolderOpen,
  Image as ImageIcon,
  KeyRound,
  ShieldCheck,
  UserPlus,
} from "lucide-react";
import type { ManagedAccountRecord, SignupDraft } from "../features/sessions/types";
import { importAccounts, listAccounts } from "../features/sessions/api";
import type { StoredAccount } from "../features/sessions/types";

const DEFAULT_ACCOUNT_PASSWORD = "Virgo_09";
const USERNAME_WORDS = [
  "daily", "nova", "pixel", "river", "urban", "astro", "mint", "bright",
];

interface ImportedMailbox {
  id: string;
  email: string;
  emailPassword: string;
  refreshToken: string;
  clientId: string;
  username: string;
  source: string;
  persisted: boolean;
}

interface Props {
  records: ManagedAccountRecord[];
  onUseMailbox: (draft: SignupDraft) => void;
}

function stableHash(value: string): number {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

export function usernameForEmail(email: string): string {
  const local = email.split("@", 1)[0]?.toLowerCase() ?? "user";
  const letters = local.replace(/[^a-z]/g, "");
  const prefix = (letters.slice(0, 6) || "user").padEnd(3, "x");
  const hash = stableHash(email.toLowerCase());
  const word = USERNAME_WORDS[hash % USERNAME_WORDS.length];
  const firstDigit = String((hash % 9) + 1);
  const lastDigits = String((Math.floor(hash / 10) % 90) + 10);
  const candidate = `${prefix}${firstDigit}_${word}${lastDigits}`;
  return candidate.slice(0, 18);
}

function validEmail(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
}

function statusLabel(status: ManagedAccountRecord["status"]): string {
  if (status === "completed") return "Created";
  if (status === "email_rejected") return "Already exists";
  if (status === "captcha_required") return "CAPTCHA stopped";
  return status.replaceAll("_", " ");
}

export function AccountDashboard({ records, onUseMailbox }: Props) {
  const [mailboxes, setMailboxes] = useState<ImportedMailbox[]>([]);
  const [invalidLines, setInvalidLines] = useState(0);
  const [duplicateLines, setDuplicateLines] = useState(0);
  const [importMessage, setImportMessage] = useState<string | null>(null);
  const [importBusy, setImportBusy] = useState(false);
  const [images, setImages] = useState<File[]>([]);
  const [folderName, setFolderName] = useState("");
  const mailboxInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);

  const syncPersistedAccounts = useCallback(async (
    inMemory: Map<string, ImportedMailbox> = new Map(),
  ) => {
    const stored = await listAccounts();
    setMailboxes(stored.map((account: StoredAccount) => {
      const cached = inMemory.get(account.email);
      return cached ?? {
        id: account.id,
        email: account.email,
        emailPassword: "",
        refreshToken: "",
        clientId: "",
        username: usernameForEmail(account.email),
        source: account.source_name,
        persisted: true,
      };
    }));
  }, []);

  useEffect(() => {
    void syncPersistedAccounts().catch(() => {
      setImportMessage("Could not load saved accounts from the database.");
    });
  }, [syncPersistedAccounts]);

  const importMailboxes = async (event: ChangeEvent<HTMLInputElement>) => {
    setImportBusy(true);
    setImportMessage(null);
    const files = Array.from(event.target.files ?? []);
    const existing = new Set<string>();
    const imported: ImportedMailbox[] = [];
    let rejected = 0;

    for (const file of files) {
      const lines = (await file.text()).split(/\r?\n/);
      for (const rawLine of lines) {
        const line = rawLine.trim();
        if (!line || line.startsWith("#")) continue;
        const parts = line.split("|");
        if (parts.length < 4) {
          rejected += 1;
          continue;
        }
        const email = parts[0].trim().toLowerCase();
        const emailPassword = parts[1].trim();
        const refreshToken = parts[2].trim();
        const clientId = parts[3].trim();
        if (!validEmail(email) || !emailPassword || refreshToken.length < 8 || clientId.length < 8 || existing.has(email)) {
          rejected += 1;
          continue;
        }
        existing.add(email);
        imported.push({
          id: crypto.randomUUID(),
          email,
          emailPassword,
          refreshToken,
          clientId,
          username: usernameForEmail(email),
          source: file.name,
          persisted: false,
        });
      }
    }
    try {
      if (imported.length) {
        const result = await importAccounts(imported.map((mailbox) => ({
          email: mailbox.email,
          email_password: mailbox.emailPassword,
          refresh_token: mailbox.refreshToken,
          client_id: mailbox.clientId,
          source_name: mailbox.source,
        })));
        const currentSecrets = new Map(mailboxes.map((item) => [item.email, item]));
        for (const mailbox of imported) currentSecrets.set(mailbox.email, mailbox);
        await syncPersistedAccounts(currentSecrets);
        setDuplicateLines(result.duplicates);
        setImportMessage(
          `Saved ${result.imported} account${result.imported === 1 ? "" : "s"}; skipped ${result.duplicates} duplicate${result.duplicates === 1 ? "" : "s"}.`,
        );
      } else {
        setImportMessage("No new valid account rows were found.");
      }
      setInvalidLines(rejected);
    } catch (reason) {
      setImportMessage(reason instanceof Error ? reason.message : "Account import failed.");
    } finally {
      setImportBusy(false);
      event.target.value = "";
    }
  };

  const selectImageFolder = (event: ChangeEvent<HTMLInputElement>) => {
    const selected = Array.from(event.target.files ?? []).filter((file) => (
      file.type.startsWith("image/") || /\.(?:avif|gif|jpe?g|png|webp)$/i.test(file.name)
    ));
    setImages(selected);
    const relative = selected[0]?.webkitRelativePath ?? "";
    setFolderName(relative.split("/")[0] || (selected.length ? "Selected folder" : ""));
  };

  return (
    <section className="panel account-dashboard" id="accounts" aria-labelledby="accounts-title">
      <div className="account-dashboard__heading">
        <div>
          <span className="eyebrow">Single-account preparation</span>
          <h2 id="accounts-title">Account dashboard</h2>
          <p>Import owned Outlook/Hotmail OAuth rows, prepare one signup at a time and keep only masked result metadata.</p>
        </div>
        <span className="signup-safety-chip"><ShieldCheck aria-hidden="true" />Secrets stay in memory</span>
      </div>

      <div className="account-dashboard__actions">
        <button className="button button--secondary" type="button" disabled={importBusy} onClick={() => mailboxInput.current?.click()}>
          <FileUp aria-hidden="true" />{importBusy ? "Saving accounts…" : "Import Microsoft mailbox file"}
        </button>
        <input ref={mailboxInput} className="visually-hidden" type="file" accept=".txt,text/plain" multiple onChange={(event) => void importMailboxes(event)} />
        <button className="button button--secondary" type="button" onClick={() => folderInput.current?.click()}>
          <FolderOpen aria-hidden="true" />Choose image folder
        </button>
        <input
          ref={folderInput}
          className="visually-hidden"
          type="file"
          accept="image/*"
          multiple
          {...{ webkitdirectory: "", directory: "" }}
          onChange={selectImageFolder}
        />
        <span><KeyRound aria-hidden="true" />Default TikTok password: <strong>{DEFAULT_ACCOUNT_PASSWORD}</strong></span>
      </div>

      <div className="account-dashboard__metrics">
        <div><span>Ready mailboxes</span><strong>{mailboxes.length}</strong></div>
        <div><span>Invalid / duplicate</span><strong>{invalidLines + duplicateLines}</strong></div>
        <div><span>Created records</span><strong>{records.filter((item) => item.status === "completed").length}</strong></div>
        <div><span>Folder images</span><strong>{images.length}</strong><small>{folderName || "No folder selected"}</small></div>
      </div>

      {importMessage && <div className="account-import-message" role="status">{importMessage}</div>}

      <div className="account-dashboard__grid">
        <div className="account-card">
          <div className="account-card__title"><UserPlus aria-hidden="true" /><strong>Imported mailboxes</strong><span>email | password | refresh token | client ID</span></div>
          {mailboxes.length ? (
            <div className="account-table-wrap">
              <table className="account-table">
                <thead><tr><th>Email</th><th>Generated username</th><th>Source</th><th /></tr></thead>
                <tbody>{mailboxes.map((mailbox) => (
                  <tr key={mailbox.id}>
                    <td>{mailbox.email}</td>
                    <td><code>{mailbox.username}</code></td>
                    <td>{mailbox.source}</td>
                    <td><button className="button button--primary" type="button" disabled={!mailbox.refreshToken || !mailbox.clientId} title={!mailbox.refreshToken ? "Re-import this source file to use its encrypted credentials in the current signup form." : undefined} onClick={() => onUseMailbox({
                      email: mailbox.email,
                      refresh_token: mailbox.refreshToken,
                      client_id: mailbox.clientId,
                      username: mailbox.username,
                      account_password: DEFAULT_ACCOUNT_PASSWORD,
                    })}>Use for signup</button></td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          ) : <div className="account-empty">Import a UTF-8 text file. All four columns are saved to the encrypted local database.</div>}
        </div>

        <div className="account-card">
          <div className="account-card__title"><ImageIcon aria-hidden="true" /><strong>Signup results & media</strong><span>Publishing requires an authorized posting integration.</span></div>
          {records.length ? (
            <ul className="account-results">{records.map((record) => (
              <li key={record.id}><span><strong>@{record.username}</strong><small>{record.email_masked}</small></span><em>{statusLabel(record.status)}</em></li>
            ))}</ul>
          ) : <div className="account-empty">Completed signup results will appear here without tokens or passwords.</div>}
          {images.length > 0 && <div className="media-preview"><strong>{folderName}</strong><span>{images.length} image files ready for an authorized posting flow.</span><small>{images.slice(0, 3).map((file) => file.name).join(" · ")}</small></div>}
        </div>
      </div>
    </section>
  );
}
