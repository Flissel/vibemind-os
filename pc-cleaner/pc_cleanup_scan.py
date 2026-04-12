"""Quick PC cleanup scan — safe, read-only analysis."""
import os


def dir_size(path):
    total = 0
    try:
        for root, dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except (PermissionError, OSError):
                    pass
    except (PermissionError, OSError):
        pass
    return total


def fmt(b):
    if b > 1024**3:
        return f"{b / 1024**3:.1f} GB"
    if b > 1024**2:
        return f"{b / 1024**2:.0f} MB"
    return f"{b / 1024:.0f} KB"


def main():
    temp = os.environ.get("TEMP", "")
    localappdata = os.environ.get("LOCALAPPDATA", "")
    appdata = os.environ.get("APPDATA", "")
    home = os.path.expanduser("~")

    targets = [
        (temp, "Windows Temp", True),
        (os.path.join(localappdata, "Temp"), "User Temp", True),
        (os.path.join(localappdata, "Microsoft", "Windows", "INetCache"), "IE/Edge Cache", True),
        (os.path.join(localappdata, "pip", "cache"), "pip Cache", True),
        (os.path.join(localappdata, "npm-cache"), "npm Cache", True),
        (os.path.join(appdata, "npm-cache"), "npm Cache (Roaming)", True),
        (os.path.join(localappdata, "pnpm", "store"), "pnpm Store", True),
        (os.path.join(localappdata, "yarn", "Cache"), "Yarn Cache", True),
        (os.path.join(localappdata, "NuGet", "Cache"), "NuGet Cache", True),
        (os.path.join(localappdata, "Google", "Chrome", "User Data", "Default", "Cache"), "Chrome Cache", True),
        (os.path.join(localappdata, "Google", "Chrome", "User Data", "Default", "Code Cache"), "Chrome Code Cache", True),
        (os.path.join(localappdata, "Docker", "wsl"), "Docker WSL Data", False),
        (os.path.join(home, ".cache"), ".cache", True),
        (os.path.join(home, ".pyenv", "pyenv-win", "install_cache"), "pyenv Install Cache", True),
        (os.path.join(localappdata, "Packages"), "UWP App Packages", False),
        (os.path.join(home, "Downloads"), "Downloads", False),
        (os.path.join(localappdata, "CrashDumps"), "Crash Dumps", True),
        (os.path.join(home, "AppData", "Local", "Temp", "vscode-stable-user-x64"), "VSCode Update Cache", True),
        (os.path.join(localappdata, "Microsoft", "Windows", "Explorer"), "Thumbnail Cache", True),
    ]

    print()
    print("=" * 60)
    print("  PC CLEANUP ANALYSE (read-only, loescht nichts)")
    print("=" * 60)
    print()
    print(f"  {'Ordner':<42} {'Groesse':>10} {'Safe?':>6}")
    print("  " + "-" * 60)

    total_safe = 0
    total_risky = 0
    cleanable = []

    for path, name, safe in targets:
        if os.path.exists(path):
            size = dir_size(path)
            if size > 1024 * 1024:  # >1MB
                tag = "JA" if safe else "NEIN"
                print(f"  {name:<42} {fmt(size):>10} {tag:>6}")
                if safe:
                    total_safe += size
                    cleanable.append((path, name, size))
                else:
                    total_risky += size

    print()
    print(f"  Sicher loeschbar:     {fmt(total_safe)}")
    print(f"  Nicht antasten:       {fmt(total_risky)}")
    print()

    if cleanable:
        print("  Top 5 zum Aufraeumen:")
        cleanable.sort(key=lambda x: x[2], reverse=True)
        for path, name, size in cleanable[:5]:
            print(f"    {fmt(size):>10}  {name}")
            print(f"              {path}")

    print()
    return cleanable


if __name__ == "__main__":
    cleanable = main()
    if cleanable:
        print("  Soll ich diese Ordner leeren? (Starte mit --clean)")
