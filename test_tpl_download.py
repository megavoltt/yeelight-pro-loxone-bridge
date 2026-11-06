# -*- coding: utf-8 -*-
"""Проверка веб-выгрузки шаблонов с моста WB."""
import io
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

BASE = "http://192.168.1.62:8082"


def get(path, raw=False):
    r = urllib.request.urlopen(BASE + path, timeout=10)
    data = r.read()
    return (r.headers.get("Content-Disposition"), data) if raw else data.decode("utf-8", "replace")


page = get("/")
print("секция 'Шаблоны Loxone':", "OK" if "Шаблоны Loxone" in page else "НЕТ")
print("ссылки /tpl/viu/lamp:", "OK" if '/tpl/viu/lamp' in page else "НЕТ")

disp, data = get("/tpl/viu/lamp", raw=True)
print("\nVIU lamp:", disp, "| bytes:", len(data), "| BOM:", data[:3] == b"\xef\xbb\xbf")
root = ET.fromstring(data.decode("utf-8-sig"))
print("root:", root.tag, "|", root.get("Title"), "| Address:", root.get("Address"),
      "| Port:", root.get("Port"), "| cmds:", len(root) - 1)
for c in root:
    if c.tag.endswith("Cmd"):
        print("  ", c.get("Title"), "|", c.get("Check"), "| analog:", c.get("Analog"))

disp, data = get("/tpl/vo/lamp", raw=True)
print("\nVO lamp:", disp, "| bytes:", len(data))
root = ET.fromstring(data.decode("utf-8-sig"))
print("root:", root.tag, "|", root.get("Title"), "|", root.get("Address"), "| cmds:", len(root) - 1)
for c in root:
    if c.tag.endswith("Cmd"):
        print("  ", c.get("Title"), "| on:", c.get("CmdOn"),
              "| off:", c.get("CmdOff") or "∅", "| analog:", c.get("Analog"))

disp, data = get("/tpl/viu/all", raw=True)
root = ET.fromstring(data.decode("utf-8-sig"))
print("\nVIU all:", root.get("Title"), "| cmds:", len(root) - 1)
disp, data = get("/tpl/vo/all", raw=True)
root = ET.fromstring(data.decode("utf-8-sig"))
print("VO all:", root.get("Title"), "| cmds:", len(root) - 1)

disp, data = get("/tpl/all.zip", raw=True)
zf = zipfile.ZipFile(io.BytesIO(data))
print("\nZIP:", disp, "| файлов:", len(zf.namelist()))
for n in zf.namelist():
    print("  ", n)
for n in zf.namelist():
    ET.fromstring(zf.read(n).decode("utf-8-sig"))
print("все XML в ZIP валидны")

disp, data = get("/tpl/vo/vdevice", raw=True)
root = ET.fromstring(data.decode("utf-8-sig"))
print("\nVO vdevice:")
for c in root:
    if c.tag.endswith("Cmd"):
        print("  on:", c.get("CmdOn"), "| off:", c.get("CmdOff"), "| analog:", c.get("Analog"))
