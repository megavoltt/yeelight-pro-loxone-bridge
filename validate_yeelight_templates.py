# -*- coding: utf-8 -*-
"""Валидация шаблонов VIU/VO Yeelight Pro (формат wb-loxone)."""
import xml.etree.ElementTree as ET

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


def main():
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
    for cmd in tree2:
        if cmd.tag != "VirtualOutCmd":
            continue
        on = (cmd.get("CmdOn") or "").replace("<v>", "80")
        off = (cmd.get("CmdOff") or "").replace("<v>", "80")
        print("  %-32s analog=%-5s on=%-24s off=%s" % (cmd.get("Title"),
                                                       cmd.get("Analog"), on, off))


if __name__ == "__main__":
    main()
