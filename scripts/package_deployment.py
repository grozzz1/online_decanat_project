import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
FILES = ("main.py", "requirements.txt", ".env.example", "rules.md", "README.md", "docs/SERVER.md")
DIRECTORIES = ("handlers", "utils", "webapp", "scripts", "deploy")
SUFFIXES = {".py", ".sh", ".html", ".css", ".js", ".svg", ".png"}


def package(target):
    target = Path(target).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    paths = [ROOT / name for name in FILES]
    paths.extend(
        path
        for directory in DIRECTORIES
        for path in (ROOT / directory).rglob("*")
        if path.is_file()
        and path.suffix in SUFFIXES
        and "__pycache__" not in path.parts
        and path.resolve().is_relative_to(ROOT)
    )
    with ZipFile(target, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(set(paths)):
            archive.write(path, path.relative_to(ROOT).as_posix())
    return len(set(paths))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / ".tmp" / "online-decanat-server.zip")
    args = parser.parse_args()
    count = package(args.output)
    print(f"Created {args.output}: {count} files")
