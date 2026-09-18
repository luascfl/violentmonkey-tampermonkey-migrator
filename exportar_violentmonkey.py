#!/usr/bin/env python3
"""Pipeline completo: Exporta, Migra e Sincroniza scripts do Violentmonkey para o Tampermonkey.

Uso:
    ./exportar_violentmonkey.py export  # Apenas exporta do Violentmonkey
    ./exportar_violentmonkey.py migrate # Apenas migra do ZIP exportado
    ./exportar_violentmonkey.py sync    # Executa o pipeline completo

Dependências:
    pip install python-snappy selenium webdriver-manager
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import struct
import sys
import tempfile
import time
import zipfile
from pathlib import Path

# --- DEPENDÊNCIAS ---
try:
    import snappy
except ImportError:
    sys.exit("Erro: python-snappy faltando. Instale com: pip install python-snappy")

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from webdriver_manager.chrome import ChromeDriverManager
    from selenium.webdriver.chrome.service import Service
except ImportError:
    sys.exit("Erro: selenium ou webdriver-manager faltando. Instale com: pip install selenium webdriver-manager")

# --- CONFIGURAÇÕES ---
SCRIPT_DIR = Path(__file__).parent.resolve()
VM_EXTENSION_ID = "{aecec67f-0d10-4fa7-b7c7-609a2db280cf}"
TM_ID = "dhdgffkkebhmkfjojejmpbldmpobfkfo"

BROWSER_PROFILES = {
    "librewolf": Path.home() / ".librewolf",
    "firefox": Path.home() / ".mozilla" / "firefox",
}

# --- FUNÇÕES DE EXPORTAÇÃO ---
def create_export_zip(scripts: list[dict], output_path: Path) -> None:
    """Cria um ZIP com os scripts extraídos como arquivos .user.js."""
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for script in scripts:
            filename = f"{script['safe_name']}.user.js"
            zf.writestr(filename, script["code"])

    print(f"ZIP criado: {output_path}")
    print(f"  {len(scripts)} scripts exportados")
    print(f"  Tamanho: {output_path.stat().st_size / 1024:.1f} KB")

def find_profile(browser: str | None) -> tuple[str, Path]:
    order = [browser] if browser else ["librewolf", "firefox"]
    for name in order:
        base = BROWSER_PROFILES.get(name)
        if base and base.is_dir():
            profiles = sorted(base.glob("*.default*"), key=lambda p: p.stat().st_mtime, reverse=True)
            if profiles: return name, profiles[0]
    sys.exit(f"Nenhum perfil encontrado para: {', '.join(order)}")

def find_vm_internal_uuid(profile: Path) -> str:
    prefs_path = profile / "prefs.js"
    with open(prefs_path) as f:
        for line in f:
            if "webextensions.uuids" in line:
                m = re.search(r'"({.*?})"', line)
                if m:
                    uuid_map = json.loads(m.group(1).replace('\\"', '"'))
                    if uuid_map.get(VM_EXTENSION_ID): return uuid_map[VM_EXTENSION_ID]
    sys.exit("Violentmonkey não encontrado no perfil.")

def find_vm_idb(profile: Path, internal_uuid: str) -> Path:
    patterns = [
        profile / "storage" / "default" / f"moz-extension+++{internal_uuid}^userContextId=4294967295" / "idb",
        profile / "storage" / "default" / f"moz-extension+++{internal_uuid}" / "idb",
    ]
    for idb_dir in patterns:
        if idb_dir.is_dir():
            dbs = sorted(idb_dir.glob("*.sqlite"), key=lambda p: p.stat().st_size, reverse=True)
            if dbs: return dbs[0]
    sys.exit(f"IndexedDB não encontrado.")

def decode_idb_key(blob: bytes) -> str | None:
    if not blob or not isinstance(blob, (bytes, bytearray)) or blob[0] != 0x30: return None
    return "".join(chr(b - 1) for b in blob[1:] if b != 0)

def decode_sc_string(raw_data: bytes) -> str | None:
    if not isinstance(raw_data, (bytes, bytearray)): return None
    try:
        data = snappy.decompress(raw_data)
        marker = b"\x04\x00\xff\xff"
        pos = data.find(marker)
        if pos < 4: return None
        length_raw = struct.unpack_from("<I", data, pos - 4)[0]
        is_latin1 = bool(length_raw & 0x80000000)
        str_length = length_raw & 0x7FFFFFFF
        str_start = pos + 4
        if is_latin1: return data[str_start:str_start + str_length].decode("latin-1", errors="replace")
        return data[str_start:str_start + str_length * 2].decode("utf-16-le", errors="replace")
    except Exception: return None

def load_scripts_from_idb(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT key, data FROM object_data")
    code_blobs = {decode_idb_key(row[0]).split(":")[1]: bytes(row[1]) 
                  for row in cursor.fetchall() if decode_idb_key(row[0]) and decode_idb_key(row[0]).startswith("code:")}
    conn.close()
    
    scripts = []
    for sid, blob in sorted(code_blobs.items(), key=lambda x: int(x[0])):
        code = decode_sc_string(blob)
        if code and len(code) > 10:
            name_match = re.search(r"@name\s+(.+?)(?:\n|$)", code)
            scripts.append({'id': sid, 'name': (name_match.group(1).strip() if name_match else f"script_{sid}"), 
                            'safe_name': re.sub(r'[<>:"/\\|?*]', "_", name_match.group(1).strip() if name_match else f"script_{sid}").strip(". "), 'code': code})
    return scripts

# --- FUNÇÕES DE MIGRAÇÃO ---
def safe_extract(zip_file: zipfile.ZipFile, destination: Path) -> None:
    for member in zip_file.infolist():
        target = (destination / member.filename).resolve()
        if destination.resolve() not in target.parents: sys.exit("ZIP inseguro")
    zip_file.extractall(destination)

def migrate_zip(zip_path: Path, output_dir: Path) -> Path:
    final_zip = output_dir.parent / "tampermonkey_import.zip"
    with zipfile.ZipFile(zip_path) as archive:
        with tempfile.TemporaryDirectory(prefix="vm_backup_") as temp_name:
            temp_dir = Path(temp_name)
            safe_extract(archive, temp_dir)
            scripts = sorted(list(temp_dir.rglob("*.user.js")), key=lambda p: p.name.casefold())
            
            if output_dir.exists(): shutil.rmtree(output_dir)
            output_dir.mkdir(parents=True)
            
            for script in scripts:
                dest = output_dir / script.name
                shutil.copy2(script, dest)
            
            with zipfile.ZipFile(final_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for path in output_dir.rglob("*.user.js"): zf.write(path, path.name)
    return final_zip

# --- FUNÇÕES DE SYNC ---
def sync_to_tm(zip_path: Path):
    chrome_options = Options()
    chrome_options.add_argument(f"user-data-dir={Path.home()}/.config/google-chrome")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
    try:
        driver.get(f"chrome-extension://{TM_ID}/options/index.html#import")
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='file']"))).send_keys(str(zip_path))
        print("Arquivo enviado. Aguardando 5s para processamento...")
        time.sleep(5)
    finally:
        driver.quit()

# --- MAIN ---
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["export", "migrate", "sync"], default="sync", nargs="?")
    args = parser.parse_args()

    base_export = SCRIPT_DIR / "violentmonkey_export.zip"
    migrated_dir = SCRIPT_DIR / "tampermonkey_import"

    if args.action in ["export", "sync"]:
        print("Exportando...")
        name, profile = find_profile(None)
        scripts = load_scripts_from_idb(find_vm_idb(profile, find_vm_internal_uuid(profile)))
        create_export_zip(scripts, base_export)
        
    if args.action in ["migrate", "sync"]:
        print("Migrando...")
        final_zip = migrate_zip(base_export, migrated_dir)
        print(f"Migração pronta: {final_zip}")
        
    if args.action == "sync":
        print("Sincronizando com Chrome...")
        sync_to_tm(SCRIPT_DIR / "tampermonkey_import.zip")
        print("Sincronização concluída.")

if __name__ == "__main__":
    main()
