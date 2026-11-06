# -*- coding: utf-8 -*-
"""Валидация шаблонов VIU/VO Yeelight Pro (формат wb-loxone)."""
import json
import os
import tempfile
import xml.etree.ElementTree as ET

from yeelight_bridge import Bridge, DEFAULTS, render_config_saved_page, save_config

BS_V = chr(92) + "v"  # \v — маркер значения Loxone

SAMPLES = [
    "yeelight.lamp.p=1", "yeelight.lamp.l=50", "yeelight.lamp.ct=4000",
    "yeelight.lamp.o=1", "yeelight.vdevice.1-p=0",
    "yeelight.vdevice.2-p=1", "yeelight.vdevice.o=1",
]


def extract(check, payload):
    pre, sep, post = check.partition(BS_V)
    if not sep or not payload.startswith(pre):
        return None
    rest = payload[len(pre):]
    if post and not rest.endswith(post):
        return None
    val = rest[: len(rest) - len(post) if post else len(rest)]
    try:
        float(val)
    except ValueError:
        return None
    return val


def test_smart_ww():
    cases = [
        (0, {"p": False}),
        (201002700, {"p": True, "l": 100, "ct": 2700}),
        (200504000, {"p": True, "l": 50, "ct": 4000}),
        (200256500, {"p": True, "l": 25, "ct": 6500}),
        (200012700, {"p": True, "l": 1, "ct": 2700}),
        (200501000, {"p": True, "l": 50, "ct": 2700}),
        (2005012000, {"p": True, "l": 50, "ct": 6500}),
    ]
    for raw, expected in cases:
        actual = Bridge.decode_smart_ww(raw, 2700, 6500)
        assert actual == expected, "%s: %r != %r" % (raw, actual, expected)
    for raw in (1, 100, 20050400, 209992700):
        try:
            Bridge.decode_smart_ww(raw, 2700, 6500)
        except ValueError:
            continue
        raise AssertionError("принят неверный Smart WW: %s" % raw)
    print("Smart WW: %d кодов и %d ошибочных значений OK" % (len(cases), 4))


def test_lumitech():
    cases = [
        (29029029, {"p": True, "l": 29, "ct": 4600}),
        (100000000, {"p": True, "l": 100, "ct": 6500}),
        (100100, {"p": True, "l": 100, "ct": 2700}),
        (100100100, {"p": True, "l": 100, "ct": 4600}),
        (50050050, {"p": True, "l": 50, "ct": 4600}),
    ]
    for raw, expected in cases:
        actual = Bridge.decode_smart_ww(raw, 2700, 6500)
        assert actual == expected, "%s: %r != %r" % (raw, actual, expected)
    for raw in (1, 100, 100000, 101000000):
        try:
            Bridge.decode_smart_ww(raw, 2700, 6500)
        except ValueError:
            continue
        raise AssertionError("принят неверный Lumitech: %s" % raw)
    print("Lumitech: %d кодов и %d ошибочных значений OK" % (len(cases), 4))


def test_config_save():
    bridge = Bridge(dict(DEFAULTS))
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "config.json")
        save_config(bridge, path, {
            "gateway_ip": ["192.0.2.1"], "poll_interval": ["31"],
            "aliases": ["4162314=vdevice"],
        })
        with open(path, encoding="utf-8") as fh:
            saved = json.load(fh)
    assert saved["gateway_ip"] == "192.0.2.1"
    assert saved["poll_interval"] == 31
    assert saved["aliases"] == {"4162314": "vdevice"}
    page = render_config_saved_page(bridge)
    assert b'<meta http-equiv="refresh" content="8;url=/">' in page
    print("Сохранение конфигурации и страница перезапуска: OK")


def main():
    test_smart_ww()
    test_lumitech()
    test_config_save()
    tree = ET.fromstring(open("VIU_Yeelight_Pro.xml", encoding="utf-8-sig").read())
    ok = True
    print("VIU (все команды аналоговые):")
    for cmd in tree:
        if cmd.tag != "VirtualInUdpCmd":
            continue
        check = cmd.get("Check")
        assert BS_V in check, "нет маркера \\v: %r" % check
        assert cmd.get("Analog") == "true", "команда не аналоговая: %s" % cmd.get("Title")
        hits = [v for v in (extract(check, s) for s in SAMPLES) if v is not None]
        good = len(hits) == 1
        ok &= good
        print("  %-32s -> %s %s" % (cmd.get("Title"), hits, "OK" if good else "FAIL"))
    print("VIU:", "OK" if ok else "FAIL")

    tree2 = ET.fromstring(open("VO_Yeelight_Pro.xml", encoding="utf-8-sig").read())
    print("\nVO (HTTP VirtualOut, подстановка v=80):")
    sww_found = False
    for cmd in tree2:
        if cmd.tag != "VirtualOutCmd":
            continue
        on = (cmd.get("CmdOn") or "").replace("<v>", "80")
        off = (cmd.get("CmdOff") or "").replace("<v>", "80")
        sww_found |= cmd.get("CmdOn") == "/set/lamp/sww/<v>"
        print("  %-32s analog=%-5s on=%-24s off=%s" % (cmd.get("Title"),
                                                       cmd.get("Analog"), on, off))
    assert sww_found, "в VO нет команды /set/lamp/sww/<v>"


if __name__ == "__main__":
    main()
