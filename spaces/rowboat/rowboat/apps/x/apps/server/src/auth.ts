import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import { isRpcChannel } from './channels.js';

// Server key: a single random bearer token minted on first boot
// (~/.rowboat/server-key, mode 0600). Every client — the Electron forwarder,
// paired phones, third-party UIs — presents it. Rotation = delete the file
// and restart; that revokes every paired client at once.

export const SERVER_KEY_FILE = 'server-key';

export async function loadOrCreateServerKey(workDir: string): Promise<string> {
  const keyPath = path.join(workDir, SERVER_KEY_FILE);
  try {
    const key = (await fs.readFile(keyPath, 'utf8')).trim();
    if (key) return key;
  } catch {
    // fall through to mint
  }
  const key = crypto.randomBytes(32).toString('base64url');
  await fs.mkdir(workDir, { recursive: true });
  await fs.writeFile(keyPath, key + '\n', { mode: 0o600 });
  return key;
}

export async function rotateServerKey(workDir: string): Promise<string> {
  await fs.rm(path.join(workDir, SERVER_KEY_FILE), { force: true });
  return loadOrCreateServerKey(workDir);
}

// Hash both sides so timingSafeEqual gets equal-length buffers regardless of
// what the client sent.
export function tokenMatches(presented: string, expected: string): boolean {
  const a = crypto.createHash('sha256').update(presented).digest();
  const b = crypto.createHash('sha256').update(expected).digest();
  return crypto.timingSafeEqual(a, b);
}

// Scoped keys: extra bearer tokens that may only POST /rpc/<channel> for the
// channels listed with them — no other channel, no /workspace, no WebSocket.
// For services that need a narrow slice (e.g. the approval bridge only touches
// the todo tray), so leaking their key does not hand out the whole server.
// ~/.rowboat/scoped-keys.json (0600) holds only sha256 hashes:
//   { "keys": [ { "name": "freigabe-bote", "sha256": "<hex>", "channels": ["todo:get"] } ] }
// Read per request (cached by mtime+size), so removing an entry revokes at once.

export const SCOPED_KEYS_FILE = 'scoped-keys.json';

export interface ScopedKey {
  name: string;
  sha256: string;
  channels: string[];
}

const NAME_RE = /^[a-z0-9][a-z0-9-]{0,39}$/;
const HEX_RE = /^[0-9a-f]{64}$/;
const scopedCache = new Map<string, { mtimeMs: number; size: number; keys: ScopedKey[] }>();

function sha256(text: string): Buffer {
  return crypto.createHash('sha256').update(text).digest();
}

function parseScopedKeys(raw: string): ScopedKey[] {
  const data = JSON.parse(raw) as { keys?: unknown };
  if (!Array.isArray(data.keys)) throw new Error('keys is not a list');
  return data.keys.map((k: unknown) => {
    const { name, sha256: hash, channels } = (k ?? {}) as Record<string, unknown>;
    if (typeof name !== 'string' || !NAME_RE.test(name)) throw new Error('bad name');
    if (typeof hash !== 'string' || !HEX_RE.test(hash)) throw new Error('bad hash');
    if (!Array.isArray(channels) || channels.length === 0 || !channels.every((c) => typeof c === 'string')) {
      throw new Error('bad channels');
    }
    return { name, sha256: hash, channels: [...(channels as string[])] };
  });
}

/** Scoped keys of a workdir; missing or corrupt file means none (fail closed). */
export async function loadScopedKeys(workDir: string): Promise<ScopedKey[]> {
  const file = path.join(workDir, SCOPED_KEYS_FILE);
  let st;
  try {
    st = await fs.stat(file);
  } catch {
    scopedCache.delete(file);
    return [];
  }
  const hit = scopedCache.get(file);
  if (hit && hit.mtimeMs === st.mtimeMs && hit.size === st.size) return hit.keys;
  let keys: ScopedKey[] = [];
  try {
    keys = parseScopedKeys(await fs.readFile(file, 'utf8'));
  } catch (err) {
    console.error(`[server] ${SCOPED_KEYS_FILE} unreadable, no scoped keys: ${err instanceof Error ? err.message : err}`);
  }
  scopedCache.set(file, { mtimeMs: st.mtimeMs, size: st.size, keys });
  return keys;
}

/** The scoped key a presented token belongs to, compared in constant time. */
export function findScopedKey(presented: string, keys: ScopedKey[]): ScopedKey | null {
  if (!presented) return null;
  const digest = sha256(presented);
  let found: ScopedKey | null = null;
  for (const k of keys) {
    if (crypto.timingSafeEqual(digest, Buffer.from(k.sha256, 'hex')) && !found) found = k;
  }
  return found;
}

async function writeScopedKeys(workDir: string, keys: ScopedKey[]): Promise<void> {
  const file = path.join(workDir, SCOPED_KEYS_FILE);
  await fs.mkdir(workDir, { recursive: true });
  const tmp = `${file}.${process.pid}.tmp`;
  await fs.writeFile(tmp, JSON.stringify({ keys }, null, 2) + '\n', { mode: 0o600 });
  await fs.chmod(tmp, 0o600);
  await fs.rename(tmp, file);
  scopedCache.delete(file);
}

async function readScopedKeysStrict(workDir: string): Promise<ScopedKey[]> {
  try {
    return parseScopedKeys(await fs.readFile(path.join(workDir, SCOPED_KEYS_FILE), 'utf8'));
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === 'ENOENT') return [];
    throw new Error(`${SCOPED_KEYS_FILE} is unreadable — fix or remove it first`);
  }
}

/** Mint a scoped key (replacing one of the same name). Returns the token — the only time it exists in clear. */
export async function addScopedKey(workDir: string, name: string, channels: string[]): Promise<string> {
  if (!NAME_RE.test(name)) throw new Error(`bad name '${name}' (a-z, 0-9, -; max 40)`);
  if (channels.length === 0) throw new Error('at least one channel is required');
  for (const ch of channels) {
    if (!isRpcChannel(ch)) throw new Error(`unknown channel: ${ch}`);
  }
  const keys = (await readScopedKeysStrict(workDir)).filter((k) => k.name !== name);
  const token = crypto.randomBytes(32).toString('base64url');
  keys.push({ name, sha256: sha256(token).toString('hex'), channels: [...new Set(channels)] });
  await writeScopedKeys(workDir, keys);
  return token;
}

/** Revoke a scoped key by name. True when one was removed. */
export async function removeScopedKey(workDir: string, name: string): Promise<boolean> {
  const keys = await readScopedKeysStrict(workDir);
  const rest = keys.filter((k) => k.name !== name);
  if (rest.length === keys.length) return false;
  await writeScopedKeys(workDir, rest);
  return true;
}

// Extracts the bearer token from an Authorization header value or a ?token=
// query fallback (browser WebSocket clients cannot set headers).
export function extractBearer(
  authorizationHeader: string | undefined,
  queryToken?: string | null,
): string | null {
  if (authorizationHeader) {
    const match = /^Bearer\s+(.+)$/i.exec(authorizationHeader.trim());
    if (match) return match[1];
  }
  return queryToken || null;
}
