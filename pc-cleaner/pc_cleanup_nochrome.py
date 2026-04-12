"""PC Cleanup — without Chrome (Chrome is running)."""
import os
import shutil


def fmt(b):
    if b > 1024**3:
        return f"{b / 1024**3:.1f} GB"
    if b > 1024**2:
        return f"{b / 1024**2:.0f} MB"
    return f"{b / 1024:.0f} KB"


def clean_dir(path):
    freed = 0
    errors = 0
    try:
        items = os.listdir(path)
    except (PermissionError, OSError):
        return 0, 1
    for item in items:
        fp = os.path.join(path, item)
        try:
            if os.path.isfile(fp) or os.path.islink(fp):
                size = os.path.getsize(fp)
                os.remove(fp)
                freed += size
            elif os.path.isdir(fp):
                size = 0
                for r, d, files in os.walk(fp):
                    for f in files:
                        try:
                            size += os.path.getsize(os.path.join(r, f))
                        except (PermissionError, OSError):
                            pass
                shutil.rmtree(fp, ignore_errors=True)
                freed += size
        except (PermissionError, OSError):
            errors += 1
    return freed, errors


def main():
    la = os.environ.get("LOCALAPPDATA", "")
    home = os.path.expanduser("~")

    targets = [
        (os.path.join(la, "Temp"), "User Temp"),
        (os.path.join(la, "pip", "cache"), "pip Cache"),
        (os.path.join(la, "npm-cache"), "npm Cache"),
        (os.path.join(la, "pnpm", "store"), "pnpm Store"),
        (os.path.join(home, ".cache"), ".cache"),
        (os.path.join(home, ".pyenv", "pyenv-win", "install_cache"), "pyenv Cache"),
        (os.path.join(la, "CrashDumps"), "Crash Dumps"),
        (os.path.join(la, "Temp", "vscode-stable-user-x64"), "VSCode Update Cache"),
        (os.path.join(la, "Microsoft", "Windows", "Explorer"), "Thumbnail Cache"),
    ]

    print()
    print("  Cleaning (ohne Chrome)...")
    total = 0
    for path, name in targets:
        if os.path.exists(path):
            freed, errors = clean_dir(path)
            total += freed
            err = f" ({errors} locked)" if errors else ""
            print(f"    {name:<35} {fmt(freed):>10}{err}")

    print()
    print(f"  TOTAL FREED: {fmt(total)}")
    print()


if __name__ == "__main__":
    main()
