#!/usr/bin/env python3
"""
AutoGreen - Lector Arduino -> Supabase (consola; Windows, macOS y Linux)

  * Detecta solo el puerto del Arduino. Si no hay uno, ofrece el SIMULADOR.
  * Muestra las lecturas en vivo y las guarda en Supabase (tabla 'lecturas').
  * Si se cae el internet guarda en memoria y sube todo al volver.
  * Si se desconecta el cable, reintenta solo.

Uso:
  python autogreen.py                 (modo normal, detecta el Arduino)
  python autogreen.py --sim           (forzar simulador)
  python autogreen.py --puerto COM3   (forzar puerto; en Mac: /dev/cu.usbmodem1101)
  python autogreen.py --sin-nube      (solo mostrar, sin guardar)
  python autogreen.py --listar        (listar puertos y salir)
  python autogreen.py --reconfigurar  (volver a pedir URL y clave)
"""
import argparse
import base64
import getpass
import json
import math
import os
import random
import re
import shutil
import sys
import time
from datetime import datetime, timezone

import requests
import serial
from serial.tools import list_ports

VERSION = "1.0"
URL_POR_DEFECTO = "https://ujpdaduyogwzkongatdl.supabase.co"
BAUDIOS = 9600
# Fabricantes tipicos: Arduino, Arduino.org, CH340 (clones), FTDI, CP210x
VIDS_ARDUINO = {0x2341, 0x2A03, 0x1A86, 0x0403, 0x10C4}
CLAVES = ("temperatura", "humedad_ambiente", "humedad_suelo", "lux",
          "ventilador", "bomba", "calefactor", "luz")


# ------------------------------------------------------------------ consola
def ancho():
    return max(40, shutil.get_terminal_size((100, 20)).columns - 1)


def en_vivo(texto):
    print("\r" + texto[:ancho()].ljust(ancho()), end="", flush=True)


def linea(texto=""):
    print("\r" + " " * ancho() + "\r" + texto, flush=True)


def preguntar(texto):
    try:
        return input(texto).strip()
    except EOFError:
        return "q"


def pausar():
    if getattr(sys, "frozen", False):        # abierto con doble clic: no cerrar la ventana
        try:
            input("\nPresiona Enter para cerrar...")
        except EOFError:
            pass


# --------------------------------------------------------------- deteccion
def detectar_puertos():
    """Devuelve (candidatos, todos). Candidatos = puertos que parecen un Arduino."""
    todos = list(list_ports.comports())
    cand = []
    for p in todos:
        txt = " ".join(str(x or "") for x in (p.description, p.manufacturer, p.product, p.hwid)).lower()
        dev = p.device.lower()
        if (p.vid in VIDS_ARDUINO or "arduino" in txt or "ch340" in txt or "usb serial" in txt
                or any(k in dev for k in ("usbmodem", "usbserial", "wchusbserial", "ttyacm", "ttyusb"))):
            cand.append(p)
    # macOS lista /dev/tty.* y /dev/cu.*: usar cu.*
    cand = [p for p in cand if not p.device.startswith("/dev/tty.")] or cand
    cand.sort(key=lambda p: (0 if p.vid in (0x2341, 0x2A03) or "arduino" in str(p.description).lower() else 1, p.device))
    return cand, todos


def etiqueta(p):
    return f"{p.device} - {p.description}"


def mostrar_consejos():
    print("    Revisa lo siguiente:")
    print("      - El Arduino esta conectado por USB con un cable de DATOS (no solo de carga).")
    print("      - Si es un clon con chip CH340, instala su driver (Windows/macOS).")
    print("      - Cierra el Serial Monitor del Arduino IDE si esta abierto.")


def elegir_fuente(args):
    """Devuelve ('serial', dispositivo) o ('sim', None). None si el usuario sale."""
    if args.sim:
        return ("sim", None)
    if args.puerto:
        return ("serial", args.puerto)
    while True:
        cand, todos = detectar_puertos()
        if len(cand) == 1:
            print(f"[OK] Arduino detectado en {etiqueta(cand[0])}")
            return ("serial", cand[0].device)
        if len(cand) > 1:
            print("[!] Se encontraron varios puertos que podrian ser el Arduino:")
            for i, p in enumerate(cand, 1):
                print(f"    [{i}] {etiqueta(p)}")
            op = preguntar("    Elige el numero, S = simulador, R = reintentar, Q = salir > ").lower()
            if op.isdigit() and 1 <= int(op) <= len(cand):
                return ("serial", cand[int(op) - 1].device)
        else:
            print("[!] No se detecto ningun Arduino conectado.")
            if todos:
                print("    Puertos serial vistos (no parecen Arduino):")
                for p in todos:
                    print(f"      - {etiqueta(p)}")
            else:
                print("    El sistema no reporta ningun puerto serial.")
            mostrar_consejos()
            print("    Sin Arduino se necesita EMULAR las lecturas con el simulador.")
            op = preguntar("    [S] Iniciar simulador   [R] Reintentar   [Q] Salir > ").lower()
        if op == "s":
            return ("sim", None)
        if op == "q":
            return None


# ------------------------------------------------------------------ parser
def numero(texto):
    m = re.search(r"-?\d+(?:\.\d+)?", texto.split(":", 1)[1])
    if not m:
        raise ValueError("sin numero")
    return float(m.group())


def procesar(txt, d):
    """Acumula valores en d. Al leer 'Luz:' con el bloque completo devuelve el registro."""
    try:
        if txt.startswith("Temperatura:"):
            d["temperatura"] = numero(txt)
        elif txt.startswith("Humedad ambiente:"):
            d["humedad_ambiente"] = numero(txt)
        elif txt.startswith("Humedad suelo:"):
            d["humedad_suelo"] = int(numero(txt))
        elif txt.startswith("Luminosidad:"):
            d["lux"] = numero(txt)
        elif txt.startswith("Ventilador:"):
            d["ventilador"] = "ON" in txt
        elif txt.startswith("Bomba:"):
            d["bomba"] = "ON" in txt
        elif txt.startswith("Calefactor:"):
            d["calefactor"] = "ON" in txt
        elif txt.startswith("Luz:"):
            d["luz"] = "ON" in txt
            if all(k in d for k in CLAVES):       # bloque completo
                reg = dict(d)
                d.clear()
                return reg
            d.clear()                              # bloque incompleto (arranque a medias)
    except (ValueError, IndexError):
        d.clear()                                  # linea corrupta: descartar el bloque
    return None


# ---------------------------------------------------------------- simulador
class Simulador:
    """Emula lo que imprime mostrarSerial() del sketch (mismo formato y umbrales)."""
    TEMP_MAX, TEMP_MIN = 35.0, 30.0
    HUM_MIN, HUM_MAX = 40, 50
    LUZ_ON, LUZ_OFF = 4000, 6000

    def __init__(self):
        self.t0, self.suelo = time.time(), 55.0
        self.bomba = self.luz = False
        self.ciclo = 0

    def bloque(self):
        self.ciclo += 1
        t = time.time() - self.t0
        out = []
        dht_ok = self.ciclo > 2          # como el sensor real: primeros ciclos sin lectura valida
        if dht_ok:
            temp = 25 + 13 * math.sin(t / 60) + random.uniform(-0.3, 0.3)
            hum = 60 + 15 * math.sin(t / 80 + 1) + random.uniform(-1, 1)
        else:
            temp = hum = 0.0
            out.append(f"[ERROR] Fallo en lectura DHT22 ({self.ciclo})")
        lux = max(0, min(10000, 5000 + 5500 * math.sin(t / 90)))
        self.suelo -= 0.15
        if self.suelo < self.HUM_MIN:
            self.bomba = True
        elif self.suelo > self.HUM_MAX:
            self.bomba = False
        if self.bomba:
            self.suelo += 1.5
        self.suelo = max(0, min(100, self.suelo))
        if lux < self.LUZ_ON:
            self.luz = True
        elif lux > self.LUZ_OFF:
            self.luz = False
        oo = lambda b: "ON" if b else "OFF"
        out += ["-----------------------------",
                f"Temperatura:      {temp:.2f} C",
                f"Humedad ambiente: {hum:.2f} %",
                f"Humedad suelo:    {int(self.suelo)} %",
                f"Luminosidad:      {lux:.2f} lux",
                "--- Actuadores ---",
                f"Ventilador: {oo(dht_ok and temp > self.TEMP_MAX)}",
                f"Bomba:      {oo(self.bomba)}",
                f"Calefactor: {oo(temp < self.TEMP_MIN)}",
                f"Luz:        {oo(self.luz)}",
                "-----------------------------"]
        return out


def flujo_sim():
    sim = Simulador()
    while True:
        for l in sim.bloque():
            yield ("linea", l)
        time.sleep(0.7)


def flujo_serial(dev):
    """Genera ('linea', txt), ('vacio', None) o ('aviso', txt). Reconecta solo."""
    ser = None
    while True:
        if ser is None:
            try:
                ser = serial.Serial(dev, BAUDIOS, timeout=2)
                yield ("aviso", f"Puerto {dev} abierto a {BAUDIOS} bps (el Arduino se reinicia al abrirlo)")
            except serial.SerialException as e:
                m = str(e)
                if any(k in m for k in ("denied", "denegado", "busy", "ocupado", "PermissionError", "Errno 13", "Errno 16")):
                    yield ("aviso", f"El puerto {dev} esta ocupado. Cierra el Serial Monitor del Arduino IDE u otro programa.")
                else:
                    yield ("aviso", f"No se pudo abrir {dev}: {m}")
                time.sleep(3)
                continue
        try:
            raw = ser.readline()
        except (serial.SerialException, OSError):
            yield ("aviso", "Se perdio la conexion con el Arduino (¿cable desconectado?). Reintentando...")
            try:
                ser.close()
            except Exception:
                pass
            ser = None
            time.sleep(2)
            continue
        if raw:
            yield ("linea", raw.decode(errors="ignore").strip())
        else:
            yield ("vacio", None)


# ---------------------------------------------------------------- Supabase
def ruta_config():
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "config.json")


def leer_config():
    try:
        with open(ruta_config(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def guardar_config(url, key):
    p = ruta_config()
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"url": url, "key": key}, f, indent=2)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def rol_jwt(key):
    try:
        p = key.split(".")[1]
        p += "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p)).get("role")
    except Exception:
        return None


def obtener_credenciales(args):
    cfg = leer_config()
    url = os.environ.get("SUPABASE_URL") or cfg.get("url")
    key = os.environ.get("SUPABASE_KEY") or cfg.get("key")
    if args.reconfigurar or not url or not key:
        print("\nConfiguracion de Supabase (se guarda en config.json junto al programa)")
        u = preguntar(f"  URL del proyecto [{url or URL_POR_DEFECTO}]: ")
        url = u or url or URL_POR_DEFECTO
        try:
            k = getpass.getpass("  SECRET key (no se muestra al escribir): ").strip()
        except EOFError:
            k = ""
        key = k or key
        if key:
            guardar_config(url, key)
    if not url.startswith("http"):
        url = "https://" + url
    return url.rstrip("/"), (key or "")


class Nube:
    def __init__(self, url, key):
        self.endpoint = url + "/rest/v1/lecturas"
        self.h = {"apikey": key, "Content-Type": "application/json", "Prefer": "return=minimal"}
        if key.count(".") == 2:                      # claves antiguas (JWT) tambien van en Authorization
            self.h["Authorization"] = f"Bearer {key}"
        self.pend = []

    def probar(self):
        try:
            r = requests.get(self.endpoint + "?select=id&limit=1", headers=self.h, timeout=8)
        except requests.RequestException:
            return False, "No hay conexion a internet o la URL es incorrecta."
        if r.ok:
            return True, "Conexion a Supabase OK. Tabla 'lecturas' encontrada."
        if r.status_code in (401, 403):
            return False, f"Supabase rechazo la clave (HTTP {r.status_code}). Revisa que sea la SECRET key vigente."
        if r.status_code == 404 or "PGRST205" in r.text:
            return False, "No existe la tabla 'lecturas'. Ejecuta schema.sql en Supabase > SQL Editor."
        return False, f"Supabase respondio HTTP {r.status_code}: {r.text[:150]}"

    def guardar(self, reg):
        """Devuelve (ok, cantidad_guardada, mensaje)."""
        self.pend.append(reg)
        self.pend = self.pend[-1000:]
        try:
            r = requests.post(self.endpoint, json=self.pend, headers=self.h, timeout=6)
        except requests.RequestException:
            return False, 0, f"sin internet ({len(self.pend)} lecturas en espera)"
        if r.ok:
            n = len(self.pend)
            self.pend = []
            return True, n, "guardado en Supabase" if n == 1 else f"guardadas {n} lecturas (se recupero la conexion)"
        if r.status_code in (400, 404, 409, 422):    # datos invalidos: no insistir
            self.pend = []
        return False, 0, f"Supabase rechazo el guardado (HTTP {r.status_code}): {r.text[:100]}"


# -------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="AutoGreen: Arduino -> Supabase")
    ap.add_argument("--puerto")
    ap.add_argument("--sim", action="store_true", help="usar el simulador")
    ap.add_argument("--sin-nube", action="store_true", help="solo mostrar, sin guardar")
    ap.add_argument("--intervalo", type=float, default=5.0, help="segundos entre lecturas guardadas")
    ap.add_argument("--listar", action="store_true", help="listar puertos y salir")
    ap.add_argument("--reconfigurar", action="store_true", help="volver a pedir URL y clave")
    args = ap.parse_args()

    print("=" * 52)
    print(f" AutoGreen - Lector Arduino -> Supabase   v{VERSION}")
    print("=" * 52)

    if args.listar:
        cand, todos = detectar_puertos()
        print("Puertos serial encontrados:" if todos else "No hay puertos serial.")
        for p in todos:
            print(f"  {'*' if p in cand else ' '} {etiqueta(p)}   (* = parece Arduino)")
        return

    fuente = elegir_fuente(args)
    if fuente is None:
        print("Saliendo.")
        return

    nube = None
    if not args.sin_nube:
        url, key = obtener_credenciales(args)
        if not key:
            print("[X] Falta la SECRET key. Ejecuta de nuevo con --reconfigurar.")
            return
        if key.startswith("sb_publishable_") or rol_jwt(key) == "anon":
            print("[X] Esa es la clave PUBLICA (publishable/anon). Aqui se necesita la SECRET (sb_secret_...).")
            print("    Ejecuta con --reconfigurar para cambiarla.")
            return
        nube = Nube(url, key)
        ok, msg = nube.probar()
        print(("[OK] " if ok else "[!] ") + msg)
        if not ok and preguntar("    ¿Continuar de todas formas? [s/N] > ").lower() != "s":
            return
    else:
        print("[i] Modo --sin-nube: las lecturas solo se muestran, no se guardan.")

    flujo = flujo_sim() if fuente[0] == "sim" else flujo_serial(fuente[1])
    if fuente[0] == "sim":
        print("[i] SIMULADOR activo: los datos son emulados, no vienen de un Arduino.")
    print("Ctrl+C para detener.\n")

    d, leidas, guardadas = {}, 0, 0
    ult_guardado, ult_dato, ult_aviso = 0.0, time.time(), 0.0
    esperando_dht = False
    try:
        for tipo, val in flujo:
            ahora = time.time()
            if tipo == "aviso":
                linea(f"[!] {val}")
                continue
            if tipo == "vacio":
                if ahora - ult_dato > 10 and ahora - ult_aviso > 10:
                    linea("[!] El Arduino no envia datos. Verifica que tenga cargado el sketch y que Serial.begin sea 9600.")
                    ult_aviso = ahora
                continue
            ult_dato = ahora
            if val.startswith("[ERROR]") or val.startswith("[INFO]"):
                linea(f"[Arduino] {val}")
                continue
            reg = procesar(val, d)
            if reg is None:
                continue
            if reg["temperatura"] == 0 and reg["humedad_ambiente"] == 0:    # DHT22 aun sin lectura valida
                if not esperando_dht:
                    linea("[i] Esperando la primera lectura valida del DHT22 (se ignoran los ceros iniciales)...")
                    esperando_dht = True
                continue
            esperando_dht = False
            leidas += 1
            oo = lambda b: "ON " if b else "OFF"
            en_vivo(f"T {reg['temperatura']:.1f}C | Hum {reg['humedad_ambiente']:.0f}% | Suelo {reg['humedad_suelo']}% | "
                    f"Luz {reg['lux']:.0f} lux | Vent {oo(reg['ventilador'])} Bomba {oo(reg['bomba'])} "
                    f"Calef {oo(reg['calefactor'])} Luz {oo(reg['luz'])}")
            if nube and ahora - ult_guardado >= args.intervalo:
                ult_guardado = ahora
                reg["created_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                ok, n, msg = nube.guardar(reg)
                guardadas += n
                linea(f"[{time.strftime('%H:%M:%S')}] {'OK ' if ok else 'ERROR '}{msg}")
    except KeyboardInterrupt:
        pass
    linea()
    print("-" * 52)
    print(f"Detenido. Lecturas validas: {leidas} | Guardadas en Supabase: {guardadas}"
          + (f" | Pendientes sin subir: {len(nube.pend)}" if nube and nube.pend else ""))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n[X] Error inesperado: {type(e).__name__}: {e}")
    pausar()
