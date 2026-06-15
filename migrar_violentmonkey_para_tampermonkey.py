#!/usr/bin/env python3
"""Migra scripts de um backup ZIP do Violentmonkey para um ZIP importável no Tampermonkey."""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

USER_SCRIPT_MARKER = "==UserScript=="
OUTPUT_DIR_NAME = "tampermonkey_import"
OUTPUT_ZIP_NAME = "tampermonkey_import.zip"
INSTRUCTIONS_NAME = "IMPORTAR_NO_TAMPERMONKEY.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migra userscripts de um backup ZIP do Violentmonkey para o Tampermonkey."
    )
    parser.add_argument(
        "zip_path",
        help="Caminho do arquivo .zip exportado pelo Violentmonkey.",
    )
    return parser.parse_args()


def fail(message: str) -> None:
    print(f"Erro: {message}", file=sys.stderr)
    sys.exit(1)


def safe_extract(zip_file: zipfile.ZipFile, destination: Path) -> None:
    """Extrai sem permitir caminhos que escapem da pasta temporária."""
    base = destination.resolve()
    for member in zip_file.infolist():
        target = (destination / member.filename).resolve()
        if base != target and base not in target.parents:
            fail(f"o ZIP contém caminho inseguro: {member.filename}")
    zip_file.extractall(destination)


def has_userscript_header(path: Path) -> bool:
    """Confirma scripts .js sem sufixo .user.js pelo cabeçalho padrão."""
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            return USER_SCRIPT_MARKER in handle.read(8192)
    except OSError:
        return False


def natural_name(name: str) -> str:
    """Mantém nomes legíveis e remove separadores problemáticos."""
    cleaned = re.sub(r"[\\/\0]+", "_", name).strip()
    return cleaned or "userscript.user.js"


def unique_destination(output_dir: Path, original_name: str) -> Path:
    base_name = natural_name(original_name)
    candidate = output_dir / base_name
    if not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    counter = 2
    while True:
        numbered = output_dir / f"{stem} ({counter}){suffix}"
        if not numbered.exists():
            return numbered
        counter += 1


def find_scripts(extracted_dir: Path) -> tuple[list[Path], bool]:
    """Procura .user.js primeiro; se não achar, aceita .js com cabeçalho userscript."""
    user_scripts = sorted(
        (path for path in extracted_dir.rglob("*.user.js") if path.is_file()),
        key=lambda path: str(path.relative_to(extracted_dir)).casefold(),
    )
    if user_scripts:
        return user_scripts, False

    header_scripts = sorted(
        (
            path
            for path in extracted_dir.rglob("*.js")
            if path.is_file() and has_userscript_header(path)
        ),
        key=lambda path: str(path.relative_to(extracted_dir)).casefold(),
    )
    return header_scripts, True


def write_instructions(output_dir: Path, zip_name: str) -> None:
    instructions = f"""# Importar no Tampermonkey

1. Abra o painel do Tampermonkey no navegador.
2. Entre em Utilities.
3. Procure Import, Zip ou Import from file, conforme a versão exibida.
4. Selecione o arquivo `{zip_name}`.
5. Revise a lista de scripts e confirme a importação.
6. Se a importação por ZIP não aparecer, extraia este arquivo e importe cada `.user.js` manualmente pelo painel do Tampermonkey.

O ZIP original do Violentmonkey não foi alterado.
"""
    (output_dir / INSTRUCTIONS_NAME).write_text(instructions, encoding="utf-8")


def prepare_output_dir(base_dir: Path) -> Path:
    output_dir = base_dir / OUTPUT_DIR_NAME
    if output_dir.exists():
        if not output_dir.is_dir():
            fail(f"já existe um arquivo chamado {output_dir}")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    return output_dir


def create_zip(output_dir: Path, final_zip: Path) -> None:
    if final_zip.exists():
        final_zip.unlink()
    with zipfile.ZipFile(final_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_dir.rglob("*"), key=lambda item: str(item.relative_to(output_dir)).casefold()):
            if path.is_file():
                archive.write(path, path.relative_to(output_dir))


def migrate(zip_path: Path) -> tuple[list[str], Path, bool]:
    if not zip_path.exists():
        fail(f"arquivo ZIP não encontrado: {zip_path}")
    if not zip_path.is_file():
        fail(f"o caminho informado não é um arquivo: {zip_path}")
    if zip_path.suffix.lower() != ".zip":
        fail("informe um arquivo com extensão .zip")

    base_dir = zip_path.parent.resolve()
    final_zip = base_dir / OUTPUT_ZIP_NAME

    try:
        with zipfile.ZipFile(zip_path) as archive:
            members = [member for member in archive.infolist() if not member.is_dir()]
            if not members:
                fail("o ZIP está vazio")

            with tempfile.TemporaryDirectory(prefix="violentmonkey_backup_") as temp_name:
                temp_dir = Path(temp_name)
                safe_extract(archive, temp_dir)

                scripts, used_header_fallback = find_scripts(temp_dir)
                if not scripts:
                    fail("nenhum userscript encontrado. Procurei por .user.js e por .js com cabeçalho ==UserScript==")

                output_dir = prepare_output_dir(base_dir)
                copied_names: list[str] = []
                for script in scripts:
                    destination = unique_destination(output_dir, script.name)
                    shutil.copy2(script, destination)
                    copied_names.append(destination.name)

                write_instructions(output_dir, final_zip.name)
                create_zip(output_dir, final_zip)
                return copied_names, final_zip, used_header_fallback
    except zipfile.BadZipFile:
        fail("o arquivo informado não é um ZIP válido")
    except OSError as error:
        fail(f"falha de leitura ou escrita: {error}")

    fail("falha inesperada durante a migração")


def main() -> None:
    args = parse_args()
    zip_path = Path(args.zip_path).expanduser()
    scripts, final_zip, used_header_fallback = migrate(zip_path)

    print("Migração concluída.")
    if used_header_fallback:
        print("Nenhum .user.js foi encontrado; foram copiados arquivos .js com cabeçalho ==UserScript==.")
    print("\nScripts encontrados:")
    for name in scripts:
        print(f"- {name}")
    print(f"\nZIP final: {final_zip}")


if __name__ == "__main__":
    main()
