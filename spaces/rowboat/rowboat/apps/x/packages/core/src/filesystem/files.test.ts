import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let tmpDir: string;
let workspaceDir: string;

beforeEach(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "rowboat-files-test-"));
  workspaceDir = path.join(tmpDir, "workspace");
  process.env.ROWBOAT_WORKDIR = workspaceDir;
  vi.resetModules();
  vi.doMock("../knowledge/version_history.js", () => ({
    commitAll: vi.fn(async () => undefined),
    initRepo: vi.fn(async () => undefined),
  }));
  vi.doMock("../knowledge/deprecate_today_note.js", () => ({
    deprecateTodayNote: vi.fn(async () => undefined),
  }));
});

afterEach(async () => {
  delete process.env.ROWBOAT_WORKDIR;
  vi.doUnmock("../knowledge/version_history.js");
  vi.doUnmock("../knowledge/deprecate_today_note.js");
  vi.resetModules();
  await fs.rm(tmpDir, { recursive: true, force: true });
});

async function loadFiles() {
  return import("./files.js");
}

describe("filesystem files", () => {
  it("resolves relative paths inside ROWBOAT_WORKDIR", async () => {
    const files = await loadFiles();

    const resolved = files.resolveFilePath("notes/example.md");

    expect(resolved.originalPath).toBe("notes/example.md");
    expect(resolved.resolvedPath).toBe(path.join(workspaceDir, "notes", "example.md"));
    expect(resolved.isInsideWorkspace).toBe(true);
    expect(resolved.workspaceRelPath).toBe("notes/example.md");
  });

  it("keeps absolute paths outside the workspace absolute", async () => {
    const files = await loadFiles();
    const absolutePath = path.join(tmpDir, "outside.txt");

    const resolved = files.resolveFilePath(absolutePath);

    expect(resolved.resolvedPath).toBe(absolutePath);
    expect(resolved.isInsideWorkspace).toBe(false);
    expect(resolved.workspaceRelPath).toBeNull();
  });

  it("expands home-relative paths", async () => {
    const files = await loadFiles();

    const resolved = files.resolveFilePath("~/rowboat-test.txt");

    expect(resolved.resolvedPath).toBe(path.join(os.homedir(), "rowboat-test.txt"));
    expect(resolved.isInsideWorkspace).toBe(false);
  });

  it("canonicalizes symlinked paths for permission checks", async () => {
    const files = await loadFiles();
    const externalDir = path.join(tmpDir, "external");
    const linkPath = path.join(workspaceDir, "linked");
    await fs.mkdir(externalDir, { recursive: true });
    await fs.mkdir(workspaceDir, { recursive: true });
    try {
      await fs.symlink(externalDir, linkPath, "dir");
    } catch (error) {
      if (error && typeof error === "object" && "code" in error && error.code === "EPERM") {
        return;
      }
      throw error;
    }

    const canonicalExternalDir = await fs.realpath(externalDir);
    const resolved = await files.resolveFilePathForPermission("linked/new-file.txt");

    expect(resolved.resolvedPath).toBe(path.join(workspaceDir, "linked", "new-file.txt"));
    expect(resolved.canonicalPath).toBe(path.join(canonicalExternalDir, "new-file.txt"));
    expect(resolved.isInsideWorkspace).toBe(false);
    expect(resolved.workspaceRelPath).toBeNull();
  });

  it("reads text with line numbers and pagination metadata", async () => {
    const files = await loadFiles();
    await fs.mkdir(workspaceDir, { recursive: true });
    await fs.writeFile(path.join(workspaceDir, "readme.txt"), "alpha\nbeta\ngamma\n", "utf8");

    const result = await files.readText("readme.txt", 2, 1);

    expect(result.path).toBe("readme.txt");
    expect(result.encoding).toBe("utf8");
    expect(result.offset).toBe(2);
    expect(result.limit).toBe(1);
    expect(result.totalLines).toBe(3);
    expect(result.hasMore).toBe(true);
    expect(result.content).toContain("2: beta");
  });

  it("rejects files containing NUL bytes as binary", async () => {
    const files = await loadFiles();
    await fs.mkdir(workspaceDir, { recursive: true });
    await fs.writeFile(path.join(workspaceDir, "binary.dat"), Buffer.from([0x61, 0x00, 0x62]));

    await expect(files.readText("binary.dat")).rejects.toThrow("binary file");
  });

  it("rejects files with a high non-printable byte ratio", async () => {
    const files = await loadFiles();
    await fs.mkdir(workspaceDir, { recursive: true });
    await fs.writeFile(path.join(workspaceDir, "control.dat"), Buffer.from([0x01, 0x02, 0x03, 0x41]));

    await expect(files.readText("control.dat")).rejects.toThrow("binary file");
  });

  it("rejects files that decode with many UTF-8 replacement characters", async () => {
    const files = await loadFiles();
    await fs.mkdir(workspaceDir, { recursive: true });
    await fs.writeFile(path.join(workspaceDir, "invalid-utf8.txt"), Buffer.alloc(512, 0xff));

    await expect(files.readText("invalid-utf8.txt")).rejects.toThrow("binary file");
  });

  it("accepts normal text control characters", async () => {
    const files = await loadFiles();
    await fs.mkdir(workspaceDir, { recursive: true });
    await fs.writeFile(path.join(workspaceDir, "tabs.txt"), "one\ttwo\nthree\rfour\r\n", "utf8");

    const result = await files.readText("tabs.txt");

    expect(result.content).toContain("1: one\ttwo");
    expect(result.content).toContain("2: three");
  });

  it("writes text and creates parent directories", async () => {
    const files = await loadFiles();

    await files.writeText("nested/dir/file.txt", "hello");

    await expect(fs.readFile(path.join(workspaceDir, "nested", "dir", "file.txt"), "utf8")).resolves.toBe("hello");
  });

  it("rejects stale expectedEtag writes", async () => {
    const files = await loadFiles();
    await fs.mkdir(workspaceDir, { recursive: true });
    await fs.writeFile(path.join(workspaceDir, "etag.txt"), "first", "utf8");
    const initial = await files.stat("etag.txt");
    await files.writeText("etag.txt", "second");

    await expect(files.writeText("etag.txt", "third", { expectedEtag: initial.etag })).rejects.toThrow("ETag mismatch");
  });

  it("roundtrips binary data through writeBuffer", async () => {
    const files = await loadFiles();
    const bytes = Buffer.from([0x50, 0x4b, 0x03, 0x04, 0x00, 0xff, 0x7f, 0x01]);

    const result = await files.writeBuffer("bin/data.xlsx", bytes);

    expect(result.isInsideWorkspace).toBe(true);
    expect(result.etag).toBeTruthy();
    await expect(fs.readFile(path.join(workspaceDir, "bin", "data.xlsx"))).resolves.toEqual(bytes);
  });

  it("rejects stale expectedEtag buffer writes", async () => {
    const files = await loadFiles();
    const initial = await files.writeBuffer("etag.bin", Buffer.from("first"));
    await files.writeBuffer("etag.bin", Buffer.from("second-longer"));

    await expect(
      files.writeBuffer("etag.bin", Buffer.from("third"), { expectedEtag: initial.etag })
    ).rejects.toThrow("ETag mismatch");
  });

  it("readBuffer reports the etag a guarded write can be keyed on", async () => {
    const files = await loadFiles();
    const written = await files.writeBuffer("etag.bin", Buffer.from("first"));

    const read = await files.readBuffer("etag.bin");

    expect(read.buffer).toEqual(Buffer.from("first"));
    expect(read.etag).toBe(written.etag);
    expect(read.stat.size).toBe(5);
    // A read → modify → write round-trip keyed on that etag goes through...
    await expect(
      files.writeBuffer("etag.bin", Buffer.from("second"), { expectedEtag: read.etag })
    ).resolves.toMatchObject({ isInsideWorkspace: true });
    // ...and the stale etag is refused afterwards.
    await expect(
      files.writeBuffer("etag.bin", Buffer.from("third"), { expectedEtag: read.etag })
    ).rejects.toBeInstanceOf(files.EtagMismatchError);
  });

  // A file that vanished between the caller's read and its guarded write is
  // the same conflict as a file that changed — NOT a raw ENOENT. The raw error
  // left the renderer's editor unable to save forever (every retry lstat'd the
  // missing file and threw), since only the mismatch marker raises the
  // conflict banner that offers a way out.
  it("a missing file under expectedEtag is an ETag mismatch, not ENOENT (writeBuffer)", async () => {
    const files = await loadFiles();
    const initial = await files.writeBuffer("gone.bin", Buffer.from("first"));
    await fs.unlink(path.join(workspaceDir, "gone.bin"));

    const attempt = files.writeBuffer("gone.bin", Buffer.from("second"), { expectedEtag: initial.etag });
    await expect(attempt).rejects.toBeInstanceOf(files.EtagMismatchError);
    await expect(attempt).rejects.toThrow("ETag mismatch");
    await expect(attempt).rejects.toMatchObject({ reason: "missing", code: "ETAG_MISMATCH" });
    // The guard refused: the file was not silently recreated.
    await expect(fs.access(path.join(workspaceDir, "gone.bin"))).rejects.toMatchObject({ code: "ENOENT" });
    // An unguarded write (the editor's explicit "keep mine") recreates it.
    await files.writeBuffer("gone.bin", Buffer.from("second"));
    await expect(fs.readFile(path.join(workspaceDir, "gone.bin"), "utf8")).resolves.toBe("second");
  });

  it("a missing file under expectedEtag is an ETag mismatch, not ENOENT (writeText)", async () => {
    const files = await loadFiles();
    const initial = await files.writeText("gone.txt", "first");
    await fs.unlink(path.join(workspaceDir, "gone.txt"));

    const attempt = files.writeText("gone.txt", "second", { expectedEtag: initial.etag });
    await expect(attempt).rejects.toBeInstanceOf(files.EtagMismatchError);
    await expect(attempt).rejects.toMatchObject({ reason: "missing" });
    await expect(fs.access(path.join(workspaceDir, "gone.txt"))).rejects.toMatchObject({ code: "ENOENT" });
  });

  it("a changed file under expectedEtag reports reason 'changed'", async () => {
    const files = await loadFiles();
    const initial = await files.writeBuffer("changed.bin", Buffer.from("first"));
    await files.writeBuffer("changed.bin", Buffer.from("second-longer"));

    await expect(
      files.writeBuffer("changed.bin", Buffer.from("third"), { expectedEtag: initial.etag })
    ).rejects.toMatchObject({ reason: "changed", code: "ETAG_MISMATCH" });
  });

  it("requires unique editText matches unless replaceAll is true", async () => {
    const files = await loadFiles();
    await fs.mkdir(workspaceDir, { recursive: true });
    await fs.writeFile(path.join(workspaceDir, "edit.txt"), "one two one", "utf8");

    const ambiguous = await files.editText("edit.txt", "one", "ONE");
    expect(ambiguous).toEqual({ error: "oldString found 2 times. Use replaceAll: true or provide more context to make it unique." });

    const replaced = await files.editText("edit.txt", "one", "ONE", true);
    expect(replaced).toMatchObject({ success: true, replacements: 2 });
    await expect(fs.readFile(path.join(workspaceDir, "edit.txt"), "utf8")).resolves.toBe("ONE two ONE");
  });

  it("runs glob relative to the requested cwd", async () => {
    const files = await loadFiles();
    await fs.mkdir(path.join(workspaceDir, "src"), { recursive: true });
    await fs.writeFile(path.join(workspaceDir, "src", "a.ts"), "export {};", "utf8");
    await fs.writeFile(path.join(workspaceDir, "src", "b.md"), "# b", "utf8");
    await fs.writeFile(path.join(workspaceDir, "c.ts"), "export {};", "utf8");

    const result = await files.glob("*.ts", "src");

    expect(result.files).toEqual(["a.ts"]);
    expect(result.resolvedFiles).toEqual([path.join(workspaceDir, "src", "a.ts")]);
    expect(result.resolvedCwd).toBe(path.join(workspaceDir, "src"));
  });

  it("greps text files and skips binary files", async () => {
    const files = await loadFiles();
    await fs.mkdir(workspaceDir, { recursive: true });
    await fs.writeFile(path.join(workspaceDir, "match.txt"), "needle\n", "utf8");
    await fs.writeFile(path.join(workspaceDir, "binary.dat"), Buffer.from([0x6e, 0x65, 0x65, 0x64, 0x6c, 0x65, 0x00]));

    const result = await files.grep({ pattern: "needle", searchPath: "." });

    expect(result.count).toBe(1);
    expect(result.matches).toEqual([
      expect.objectContaining({
        file: "match.txt",
        line: 1,
        content: "needle",
      }),
    ]);
  });
  it("rename of a knowledge HTML concept rewrites links in notes and in concepts", async () => {
    const files = await loadFiles();
    const k = path.join(workspaceDir, "knowledge");
    await fs.mkdir(path.join(k, "Konzepte"), { recursive: true });
    await fs.writeFile(path.join(k, "Konzepte", "k.html"), "<p>x</p>", "utf8");
    await fs.writeFile(path.join(k, "Notiz.md"), "[[Konzepte/k.html|Konzept]]", "utf8");
    await fs.writeFile(path.join(k, "Anderes.html"), "<p>[[Konzepte/k.html]]</p>", "utf8");

    await files.rename("knowledge/Konzepte/k.html", "knowledge/Konzepte/neu.html");

    expect(await fs.readFile(path.join(k, "Notiz.md"), "utf8")).toBe("[[Konzepte/neu.html|Konzept]]");
    expect(await fs.readFile(path.join(k, "Anderes.html"), "utf8")).toBe("<p>[[Konzepte/neu.html]]</p>");
  });

  it("writeText and editText return workspaceRelPath only for files inside the workspace", async () => {
    const files = await loadFiles();
    const rel = "knowledge/Konzepte/k.html";
    const written = await files.writeText(rel, "<p>eins</p>");
    expect(written.workspaceRelPath).toBe(rel);
    const edited = await files.editText(rel, "eins", "zwei");
    expect(edited).toMatchObject({ success: true, workspaceRelPath: rel });

    const outside = path.join(tmpDir, "outside.html");
    const outWritten = await files.writeText(outside, "<p>x</p>");
    expect(outWritten).not.toHaveProperty("workspaceRelPath");
    const outEdited = await files.editText(outside, "x", "y");
    expect(outEdited).toMatchObject({ success: true });
    expect(outEdited).not.toHaveProperty("workspaceRelPath");
  });

  describe("per-path write queue", () => {
    it("concurrent editText calls on one file all apply", async () => {
      const files = await loadFiles();
      const target = path.join(workspaceDir, "knowledge", "Konzepte", "q.html");
      const n = 12;
      const body = Array.from({ length: n }, (_, i) => `<h${i}>MARK${i}</h${i}>`).join("\n") + "\n" + "x".repeat(200_000);
      await files.writeText(target, body);

      const results = await Promise.all(
        Array.from({ length: n }, (_, i) => files.editText(target, `MARK${i}<`, `DONE${i}<`)),
      );

      const content = await fs.readFile(target, "utf8");
      for (let i = 0; i < n; i++) {
        expect(content).toContain(`DONE${i}<`);
        expect(content).not.toContain(`MARK${i}<`);
        expect(results[i]).toMatchObject({ success: true, replacements: 1 });
      }
    });

    it("a failing edit does not block the next one on the same path", async () => {
      const files = await loadFiles();
      const target = path.join(workspaceDir, "q2.html");
      await files.writeText(target, "<p>eins</p>");

      const [first, second] = await Promise.all([
        files.editText(target, "gibtsnicht", "x"),
        files.editText(target, "eins", "zwei"),
      ]);

      expect(first).toEqual({ error: "oldString not found in file" });
      expect(second).toMatchObject({ success: true, replacements: 1 });
      expect(await fs.readFile(target, "utf8")).toBe("<p>zwei</p>");
    });

    it("a rejecting step does not block later steps and still rejects for its caller", async () => {
      const files = await loadFiles();
      const target = path.join(workspaceDir, "missing.html");
      const [bad, good] = await Promise.allSettled([
        files.editText(target, "a", "b"),
        files.writeText(target, "<p>a</p>"),
      ]);
      expect(bad.status).toBe("rejected");
      expect(good.status).toBe("fulfilled");
      expect(await fs.readFile(target, "utf8")).toBe("<p>a</p>");
    });

    it("editText right after an un-awaited writeText sees the written content", async () => {
      const files = await loadFiles();
      const target = path.join(workspaceDir, "q3.html");

      const [, edited] = await Promise.all([
        files.writeText(target, "<p>neu</p>"),
        files.editText(target, "neu", "bearbeitet"),
      ]);

      expect(edited).toMatchObject({ success: true });
      expect(await fs.readFile(target, "utf8")).toBe("<p>bearbeitet</p>");
    });

    it("different files do not block each other", async () => {
      const files = await loadFiles();
      const a = path.join(workspaceDir, "a.html");
      const b = path.join(workspaceDir, "b.html");
      await files.writeText(a, "<p>a</p>");
      await files.writeText(b, "<p>b</p>");

      const [ra, rb] = await Promise.all([
        files.editText(a, "a", "A"),
        files.editText(b, "b", "B"),
      ]);

      expect(ra).toMatchObject({ success: true });
      expect(rb).toMatchObject({ success: true });
    });

    it("holds no queue entry once all calls settled", async () => {
      const files = await loadFiles();
      const target = path.join(workspaceDir, "q5.html");
      await Promise.all([
        files.writeText(target, "<p>x</p>"),
        files.editText(target, "x", "y"),
        files.editText(target, "nix", "z"),
      ]);
      expect(files._pendingWriteQueueSize()).toBe(0);
    });
  });

  describe("per-path write queue: key and writers", () => {
    it("relative and absolute spelling of one file share one queue", async () => {
      const files = await loadFiles();
      const rel = "knowledge/Konzepte/s.html";
      const abs = path.join(workspaceDir, "knowledge", "Konzepte", "s.html");
      const body = Array.from({ length: 8 }, (_, i) => `<p>M${i}</p>`).join("\n") + "\n" + "x".repeat(200_000);
      await files.writeText(rel, body);
      const results = await Promise.all(
        Array.from({ length: 8 }, (_, i) => files.editText(i % 2 ? rel : abs, `M${i}<`, `D${i}<`)),
      );
      const content = await fs.readFile(abs, "utf8");
      for (let i = 0; i < 8; i++) {
        expect(content).toContain(`D${i}<`);
        expect(results[i]).toMatchObject({ success: true });
      }
      expect(files._pendingWriteQueueSize()).toBe(0);
    });

    it.runIf(process.platform === "win32")("different case of one path shares one queue on win32", async () => {
      const files = await loadFiles();
      const abs = path.join(workspaceDir, "case.html");
      await files.writeText(abs, "<p>M0</p><p>M1</p>" + "x".repeat(200_000));
      await Promise.all([
        files.editText(abs, "M0<", "D0<"),
        files.editText(abs.toUpperCase(), "M1<", "D1<"),
      ]);
      const content = await fs.readFile(abs, "utf8");
      expect(content).toContain("D0<");
      expect(content).toContain("D1<");
    });

    it("writeBuffer is ordered with editText", async () => {
      const files = await loadFiles();
      const abs = path.join(workspaceDir, "buf.html");
      const [, edited] = await Promise.all([
        files.writeBuffer(abs, Buffer.from("<p>roh</p>", "utf8")),
        files.editText(abs, "roh", "fertig"),
      ]);
      expect(edited).toMatchObject({ success: true });
      expect(await fs.readFile(abs, "utf8")).toBe("<p>fertig</p>");
    });

    it("a rejected step keeps its usual error text", async () => {
      const files = await loadFiles();
      const abs = path.join(workspaceDir, "nofile.html");
      const [bad] = await Promise.allSettled([
        files.editText(abs, "a", "b"),
        files.writeText(abs, "<p>a</p>"),
      ]);
      expect(bad.status).toBe("rejected");
      if (bad.status === "rejected") expect(String(bad.reason.message)).toMatch(/ENOENT/);
    });

    it("a write guard runs inside the slot against the real result and refuses with its text", async () => {
      const files = await loadFiles();
      const abs = path.join(workspaceDir, "guard.md");
      await files.writeText(abs, "a");
      const seen: Array<[string | null, string]> = [];
      const guard = (current: string | null, next: string) => {
        seen.push([current, next]);
        return next.includes("verboten") ? "nein" : null;
      };
      const results = await Promise.allSettled([
        files.editText(abs, "a", "ab", false, guard),
        files.editText(abs, "ab", "verboten", false, guard),
        files.writeText(abs, "neu", undefined, guard),
      ]);
      expect(seen).toEqual([["a", "ab"], ["ab", "verboten"], ["ab", "neu"]]);
      expect(results[1]).toMatchObject({ status: "rejected" });
      expect(String((results[1] as PromiseRejectedResult).reason.message)).toBe("nein");
      expect(await fs.readFile(abs, "utf8")).toBe("neu");
    });
  });
});
