#!/usr/bin/env python3
"""Padroniza nomes de stems, pastas, regioes e tracks no projeto unificado.

GUITA -> GUITARRA, GAITA -> SANFONA, typos, caixa alta, track NAME sem extensao.
Dry-run por padrao; --apply executa renomes + atualiza .rpp e region_markers.json.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from backup_rpp import backup_rpp  # noqa: E402
from renomear_tracks_rpp import (  # noqa: E402
    FILE_PATTERN,
    classify_role,
    extract_track_blocks,
    format_rpp_name,
    parse_aberto_file,
    parse_quoted_name,
    replace_track_blocks,
    stem_display,
)

import projeto  # noqa: E402

WORKSPACE = projeto.WORKSPACE
RPP = projeto.RPP
ABERTO = WORKSPACE / "ABERTO"
REGION_DATA = WORKSPACE / "data" / "region_markers.json"
MANIFEST_PATH = WORKSPACE / "data" / "rename_manifest.json"

TRACK_NAME_PATTERN = re.compile(r"^(    NAME )(.+)$", re.MULTILINE)
ITEM_NAME_PATTERN = re.compile(r"^(      NAME )(.+)$", re.MULTILINE)
MARKER_PATTERN = re.compile(r'^(\s*MARKER \d+ [\d.]+ )"(.*?)"(.*)$', re.MULTILINE)

# Ordem importa: casos compostos antes dos simples.
FILENAME_RULES: list[tuple[str, str]] = [
    ("TECLADO E GAITA KATCHAQUEIRA", "TECLADO E SANFONA CATCHAQUEIRA"),
    ("SOPRO E GAITA", "SOPRO E SANFONA"),
    ("GAITA SOLO", "SANFONA SOLO"),
    ("GAITAS", "SANFONAS"),
    ("GAITA", "SANFONA"),
    ("GUITA SOLO.mp3.mpeg", "GUITARRA SOLO.mp3"),
    ("SOLO VIOLÃO GUITA", "SOLO VIOLÃO GUITARRA"),
    ("SOLOS GUITA", "SOLOS GUITARRA"),
    ("GUITAS", "GUITARRAS"),
    ("GUITA", "GUITARRA"),
    ("REGENCIA 2", "REGÊNCIA 2"),
    ("REGENCIA", "REGÊNCIA"),
    ("PERCUSSA BLOCO", "PERCUSSÃO BLOCO"),
    ("PERCUSSA", "PERCUSSÃO"),
    ("VIOLAO E BASE", "VIOLÃO E BASE"),
    ("CLICK .mp3", "CLICK.mp3"),
    ("VS  BLOCO", "VS BLOCO"),
    ("PIANO BASE ALO", "PIANO BASE ALÔ"),
    ("VS BLOCO PEGANDO ONIBUS", "VS BLOCO PEGANDO ÔNIBUS"),
    ("VS LAGRIMAS E PAGINA", "VS LÁGRIMAS E PÁGINA"),
]

SONG_TITLE_RULES: list[tuple[str, str]] = [
    ("SEM AMOR NINGUEM VIVE", "SEM AMOR NINGUÉM VIVE"),
    ("VOCE NAO VALE NADA", "VOCÊ NÃO VALE NADA"),
]


def stem_display(filename: str) -> str:
    """Nome do stem para track/item NAME (sem extensao, caixa alta)."""
    return _stem_display(filename)


def _stem_display(filename: str) -> str:
    stem = filename
    for ext in (".mp3.mpeg", ".mpeg", ".mp3", ".wav"):
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break
    return stem.upper()


def normalize_token(text: str, rules: list[tuple[str, str]]) -> str:
    result = text
    for old, new in rules:
        if old in ("GUITA", "GUITAS", "GAITA", "GAITAS"):
            result = re.sub(rf"\b{re.escape(old)}\b", new, result)
        else:
            result = result.replace(old, new)
    return result


def normalize_filename(name: str) -> str:
    return normalize_token(name, FILENAME_RULES)


def normalize_song_title(title: str) -> str:
    return normalize_token(title, SONG_TITLE_RULES)


def normalize_folder_name(folder: str) -> str:
    match = re.match(r"^(\d+\s*-\s*)(.+)$", folder)
    if not match:
        return folder
    return f"{match.group(1)}{normalize_song_title(match.group(2))}"


def build_track_name(order: int, filename: str, song_name: str, role: str | None) -> str:
    parts = [f"{order:02d}", stem_display(filename), song_name]
    if role:
        parts.append(role)
    return " - ".join(parts)


def rpp_path_to_disk(rpp_path: str) -> Path:
    return WORKSPACE / rpp_path.replace("\\", "/")


def disk_path_to_rpp(path: Path) -> str:
    rel = path.relative_to(WORKSPACE)
    return str(rel).replace("/", "\\")


@dataclass
class RenamePlan:
    folder_renames: list[tuple[Path, Path]] = field(default_factory=list)
    file_renames: list[tuple[Path, Path]] = field(default_factory=list)
    rpp_string_replacements: list[tuple[str, str]] = field(default_factory=list)
    region_title_updates: list[tuple[str, str]] = field(default_factory=list)


def collect_file_paths(rpp_text: str) -> list[str]:
    return [m.group(2) for m in FILE_PATTERN.finditer(rpp_text)]


def build_plan(rpp_text: str) -> RenamePlan:
    plan = RenamePlan()
    seen_folders: dict[str, str] = {}
    seen_files: dict[str, str] = {}

    for rpp_file in collect_file_paths(rpp_text):
        parts = rpp_file.replace("/", "\\").split("\\")
        try:
            aberto_idx = next(i for i, p in enumerate(parts) if p.upper() == "ABERTO")
        except StopIteration:
            continue
        if aberto_idx + 2 >= len(parts):
            continue

        old_folder = parts[aberto_idx + 1]
        old_filename = parts[aberto_idx + 2]
        new_folder = normalize_folder_name(old_folder)
        new_filename = normalize_filename(old_filename)

        if old_folder not in seen_folders:
            seen_folders[old_folder] = new_folder
        elif seen_folders[old_folder] != new_folder:
            raise ValueError(f"Conflito de pasta: {old_folder!r}")

        file_key = f"{old_folder}\\{old_filename}"
        if file_key not in seen_files:
            seen_files[file_key] = f"{new_folder}\\{new_filename}"
        elif seen_files[file_key] != f"{new_folder}\\{new_filename}":
            raise ValueError(f"Conflito de arquivo: {file_key!r}")

    for old_folder, new_folder in sorted(seen_folders.items()):
        if old_folder != new_folder:
            old_path = ABERTO / old_folder
            new_path = ABERTO / new_folder
            plan.folder_renames.append((old_path, new_path))
            plan.rpp_string_replacements.append(
                (f"ABERTO\\{old_folder}", f"ABERTO\\{new_folder}")
            )

    for file_key, new_key in sorted(seen_files.items()):
        old_folder, old_filename = file_key.split("\\", 1)
        new_folder, new_filename = new_key.split("\\", 1)
        if old_filename == new_filename:
            continue
        old_path = ABERTO / old_folder / old_filename
        new_path = ABERTO / new_folder / new_filename
        plan.file_renames.append((old_path, new_path))
        plan.rpp_string_replacements.append(
            (
                f"ABERTO\\{old_folder}\\{old_filename}",
                f"ABERTO\\{new_folder}\\{new_filename}",
            )
        )

    for old, new in SONG_TITLE_RULES:
        if old != new:
            plan.region_title_updates.append((old, new))

    return plan


def resolve_rename_src(old: Path, new: Path) -> Path | None:
    """Retorna origem do rename ou None se destino ja existe (resume)."""
    if new.is_file():
        return None
    if old.is_file():
        return old
    alt = new.parent / old.name
    if alt.is_file():
        return alt
    return old if old.is_file() else None


def validate_pre(rpp_text: str, plan: RenamePlan) -> None:
    file_paths = collect_file_paths(rpp_text)
    expected = 190
    if len(file_paths) != expected:
        raise SystemExit(f"Pre-flight: esperado {expected} FILE, encontrado {len(file_paths)}")

    missing: list[str] = []
    for rpp_file in file_paths:
        disk = rpp_path_to_disk(rpp_file)
        if disk.is_file():
            continue
        parts = rpp_file.replace("/", "\\").split("\\")
        aberto_idx = next(i for i, p in enumerate(parts) if p.upper() == "ABERTO")
        old_folder = parts[aberto_idx + 1]
        old_filename = parts[aberto_idx + 2]
        folder = ABERTO / normalize_folder_name(old_folder)
        if (folder / old_filename).is_file() or (
            folder / normalize_filename(old_filename)
        ).is_file():
            continue
        missing.append(rpp_file)
    if missing:
        raise SystemExit(f"Pre-flight: {len(missing)} FILE ausente(s):\n  " + "\n  ".join(missing[:10]))

    dests: set[Path] = set()
    for old, new in plan.folder_renames:
        if new.is_dir() and not old.is_dir():
            continue
        if not old.is_dir():
            raise SystemExit(f"Pre-flight: pasta ausente: {old}")
        if new.exists():
            raise SystemExit(f"Pre-flight: destino de pasta ja existe: {new}")
        if new in dests:
            raise SystemExit(f"Pre-flight: colisao de pasta: {new}")
        dests.add(new)

    file_dests: set[Path] = set()
    for old, new in plan.file_renames:
        src = resolve_rename_src(old, new)
        if src is None:
            continue
        if not src.is_file():
            raise SystemExit(f"Pre-flight: arquivo ausente: {old}")
        if new.exists():
            raise SystemExit(f"Pre-flight: destino de arquivo ja existe: {new}")
        if new in file_dests:
            raise SystemExit(f"Pre-flight: colisao de arquivo: {new}")
        file_dests.add(new)


def validate_post(rpp_text: str) -> None:
    file_paths = collect_file_paths(rpp_text)
    expected = 190
    if len(file_paths) != expected:
        raise SystemExit(f"Post-flight: esperado {expected} FILE, encontrado {len(file_paths)}")

    missing: list[str] = []
    for rpp_file in file_paths:
        if not rpp_path_to_disk(rpp_file).is_file():
            missing.append(rpp_file)
    if missing:
        raise SystemExit(
            f"Post-flight: {len(missing)} FILE quebrado(s):\n  " + "\n  ".join(missing[:10])
        )

    track_count = len(re.findall(r"^\s*<TRACK\b", rpp_text, re.MULTILINE))
    if track_count != expected:
        raise SystemExit(f"Post-flight: esperado {expected} tracks, encontrado {track_count}")


def apply_disk_renames(plan: RenamePlan) -> None:
    for old, new in plan.folder_renames:
        if new.is_dir() and not old.is_dir():
            print(f"  pasta (ja feita): {new.name}")
            continue
        old.rename(new)
        print(f"  pasta: {old.name} -> {new.name}")

    for old, new in plan.file_renames:
        src = resolve_rename_src(old, new)
        if src is None:
            print(f"  arquivo (ja feito): {new.name}")
            continue
        if not src.is_file():
            raise FileNotFoundError(f"Arquivo nao encontrado para renomear: {old}")
        new.parent.mkdir(parents=True, exist_ok=True)
        try:
            src.rename(new)
        except PermissionError:
            shutil.copy2(src, new)
            src.unlink()
        print(f"  arquivo: {src.name} -> {new.name}")


def update_track_block(track_text: str) -> str:
    file_match = FILE_PATTERN.search(track_text)
    if not file_match:
        return track_text

    parsed = parse_aberto_file(file_match.group(2))
    if not parsed:
        return track_text

    order, song_name, filename = parsed
    song_name = normalize_song_title(song_name)
    filename = normalize_filename(filename)
    parts = file_match.group(2).replace("/", "\\").split("\\")
    aberto_idx = next(i for i, p in enumerate(parts) if p.upper() == "ABERTO")
    new_folder = normalize_folder_name(parts[aberto_idx + 1])
    new_rpp_file = f"ABERTO\\{new_folder}\\{filename}"

    track_text = FILE_PATTERN.sub(
        lambda m: f'{m.group(1)}"{new_rpp_file}"{m.group(3)}',
        track_text,
        count=1,
    )

    name_match = TRACK_NAME_PATTERN.search(track_text)
    old_track_name = (
        parse_quoted_name(name_match.group(2).strip()) if name_match else ""
    )
    role = classify_role(filename, old_track_name)
    new_track_name = build_track_name(order, filename, song_name, role)
    track_text = TRACK_NAME_PATTERN.sub(
        lambda m: f"{m.group(1)}{format_rpp_name(new_track_name)}",
        track_text,
        count=1,
    )

    item_stem = stem_display(filename)
    track_text = ITEM_NAME_PATTERN.sub(
        lambda m: f"{m.group(1)}{format_rpp_name(item_stem)}",
        track_text,
        count=1,
    )
    return track_text


def update_rpp_text(rpp_text: str) -> str:
    for old, new in SONG_TITLE_RULES:
        rpp_text = MARKER_PATTERN.sub(
            lambda m, o=old, n=new: f'{m.group(1)}"{m.group(2).replace(o, n)}"{m.group(3)}'
            if o in m.group(2)
            else m.group(0),
            rpp_text,
        )

    blocks = extract_track_blocks(rpp_text)
    new_blocks = [update_track_block(b) for b in blocks]
    return replace_track_blocks(rpp_text, new_blocks)


def update_region_markers(plan: RenamePlan) -> int:
    data = json.loads(REGION_DATA.read_text(encoding="utf-8"))
    changed = 0
    for entry in data.get("starts", []):
        old_name = entry["name"]
        new_name = normalize_song_title(old_name)
        if new_name != old_name:
            entry["name"] = new_name
            changed += 1
            plan.region_title_updates.append((old_name, new_name))
    if changed:
        REGION_DATA.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return changed


def print_plan(plan: RenamePlan) -> None:
    print(f"Pastas ({len(plan.folder_renames)}):")
    for old, new in plan.folder_renames:
        print(f"  {old.name}\n    -> {new.name}")

    print(f"\nArquivos ({len(plan.file_renames)}):")
    for old, new in plan.file_renames:
        print(f"  {old.parent.name}/{old.name}\n    -> {new.name}")

    print(f"\nRegioes ({len(plan.region_title_updates)}):")
    for old, new in plan.region_title_updates:
        print(f"  {old}\n    -> {new}")

    print("\nTracks: 190 NAME atualizados (stem sem extensao + normalizacao)")


def write_manifest(plan: RenamePlan) -> None:
    manifest = {
        "folders": [
            {"old": disk_path_to_rpp(o), "new": disk_path_to_rpp(n)}
            for o, n in plan.folder_renames
        ],
        "files": [
            {"old": disk_path_to_rpp(o), "new": disk_path_to_rpp(n)}
            for o, n in plan.file_renames
        ],
        "regions": [{"old": o, "new": n} for o, n in plan.region_title_updates],
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Manifest: {MANIFEST_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Padroniza nomes stems/titulos/tracks.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Executa renomes e grava .rpp (padrao: dry-run)",
    )
    parser.add_argument(
        "--rpp",
        type=Path,
        default=RPP,
        help="Caminho do .rpp unificado",
    )
    args = parser.parse_args()

    rpp_path = args.rpp.resolve()
    if not rpp_path.is_file():
        raise SystemExit(f".rpp nao encontrado: {rpp_path}")
    if not ABERTO.is_dir():
        raise SystemExit(f"ABERTO/ nao encontrado: {ABERTO}")

    rpp_text = rpp_path.read_text(encoding="utf-8", errors="replace")
    plan = build_plan(rpp_text)
    validate_pre(rpp_text, plan)

    print("=== Padronizar nomes ===")
    print_plan(plan)

    if not args.apply:
        print("\n[dry-run] Nenhuma alteracao. Use --apply para executar.")
        return

    print("\n=== Aplicando ===")
    dest = backup_rpp(rpp_path)
    print(f"Backup: {dest}")

    apply_disk_renames(plan)

    new_rpp = update_rpp_text(rpp_text)
    validate_post(new_rpp)
    rpp_path.write_text(new_rpp, encoding="utf-8")
    print(f"Gravado: {rpp_path.name}")

    regions = update_region_markers(plan)
    print(f"region_markers.json: {regions} titulo(s) atualizado(s)")

    write_manifest(plan)
    print("\nPost-flight OK: 190/190 FILE resolvem.")


if __name__ == "__main__":
    main()
