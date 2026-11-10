import { addScopedKey, loadScopedKeys, removeScopedKey } from './auth.js';

// `rowboat-server scoped-key <add|list|remove>` — manage narrow-rights keys
// without a running server. `add` prints ONLY the token on stdout so it can be
// piped straight into a file; nothing else ever prints it.

export interface CliIo {
  out(line: string): void;
  err(line: string): void;
}

const USAGE = [
  'usage: scoped-key add <name> <channel,channel,...>   prints the new token (only now)',
  '       scoped-key list                               names and channels, never tokens',
  '       scoped-key remove <name>                      revokes at once, no restart',
].join('\n');

export async function runScopedKeyCli(argv: string[], workDir: string, io: CliIo): Promise<number> {
  const [cmd, ...rest] = argv;
  try {
    if (cmd === 'add' && rest.length === 2) {
      const channels = rest[1].split(',').map((c) => c.trim()).filter(Boolean);
      io.out(await addScopedKey(workDir, rest[0], channels));
      return 0;
    }
    if (cmd === 'list' && rest.length === 0) {
      const keys = await loadScopedKeys(workDir);
      if (keys.length === 0) io.out('(no scoped keys)');
      for (const k of keys) io.out(`${k.name}\t${k.channels.join(',')}`);
      return 0;
    }
    if (cmd === 'remove' && rest.length === 1) {
      if (await removeScopedKey(workDir, rest[0])) return 0;
      io.err(`no scoped key named '${rest[0]}'`);
      return 1;
    }
  } catch (err) {
    io.err(err instanceof Error ? err.message : String(err));
    return 1;
  }
  io.err(USAGE);
  return 2;
}
