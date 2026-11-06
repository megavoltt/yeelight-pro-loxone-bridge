# -*- coding: utf-8 -*-
"""Генерация шаблонов Loxone в формате, проверенном проектом wb-loxone.

VIU — виртуальный вход UDP: все команды аналоговые (0/1 для бинарных),
Address = IP WB (фильтр источника), templateType="1".
VO — виртуальный выход HTTP: GET на веб-сервер моста /set/<alias>/<param>/<value>,
templateType="3" (CmdOn/CmdOff, как у wbloxone.py).
"""
import io

WB_IP = "192.168.1.62"
HTTP = "http://%s:8082" % WB_IP
UDP_PORT = 7002

VIU_LINES = [
    '<?xml version="1.0" encoding="utf-8"?>',
    '<VirtualInUdp Title="Yeelight Pro — статусы" Comment="Мост WB {}:8082. Формат: yeelight.&lt;узел&gt;.&lt;параметр&gt;=&lt;значение&gt;. Все входы аналоговые; 0/1 подключать к цифровым входам блоков." Address="{}" Port="{}">'
    .format(WB_IP, WB_IP, UDP_PORT),
    '\t<Info templateType="1" minVersion="16000610"/>',
    '\t<VirtualInUdpCmd Title="Lamp — вкл/выкл" Comment="Состояние питания светильника lamp (0/1)" Address="" Check="yeelight.lamp.p=\\v" Signed="true" Analog="true" SourceValLow="0" DestValLow="0" SourceValHigh="100" DestValHigh="100" DefVal="0" MinVal="-2147483648" MaxVal="2147483647" Unit="" HintText=""/>',
    '\t<VirtualInUdpCmd Title="Lamp — яркость" Comment="Яркость 0-100 %" Address="" Check="yeelight.lamp.l=\\v" Signed="true" Analog="true" SourceValLow="0" DestValLow="0" SourceValHigh="100" DestValHigh="100" DefVal="0" MinVal="-2147483648" MaxVal="2147483647" Unit="&lt;v.1&gt;%" HintText=""/>',
    '\t<VirtualInUdpCmd Title="Lamp — цветовая температура" Comment="CCT 2700-6500 К" Address="" Check="yeelight.lamp.ct=\\v" Signed="true" Analog="true" SourceValLow="0" DestValLow="0" SourceValHigh="100" DestValHigh="100" DefVal="0" MinVal="-2147483648" MaxVal="2147483647" Unit="&lt;v.1&gt;К" HintText=""/>',
    '\t<VirtualInUdpCmd Title="Lamp — online" Comment="Светильник доступен (0/1)" Address="" Check="yeelight.lamp.o=\\v" Signed="true" Analog="true" SourceValLow="0" DestValLow="0" SourceValHigh="100" DestValHigh="100" DefVal="0" MinVal="-2147483648" MaxVal="2147483647" Unit="" HintText=""/>',
    '\t<VirtualInUdpCmd Title="VDevice — канал 1" Comment="Виртуальное устройство, канал 1 (0/1)" Address="" Check="yeelight.vdevice.1-p=\\v" Signed="true" Analog="true" SourceValLow="0" DestValLow="0" SourceValHigh="100" DestValHigh="100" DefVal="0" MinVal="-2147483648" MaxVal="2147483647" Unit="" HintText=""/>',
    '\t<VirtualInUdpCmd Title="VDevice — канал 2" Comment="Виртуальное устройство, канал 2 (0/1)" Address="" Check="yeelight.vdevice.2-p=\\v" Signed="true" Analog="true" SourceValLow="0" DestValLow="0" SourceValHigh="100" DestValHigh="100" DefVal="0" MinVal="-2147483648" MaxVal="2147483647" Unit="" HintText=""/>',
    '\t<VirtualInUdpCmd Title="VDevice — online" Comment="Виртуальное устройство доступно (0/1)" Address="" Check="yeelight.vdevice.o=\\v" Signed="true" Analog="true" SourceValLow="0" DestValLow="0" SourceValHigh="100" DestValHigh="100" DefVal="0" MinVal="-2147483648" MaxVal="2147483647" Unit="" HintText=""/>',
    '</VirtualInUdp>',
]

VO_LINES = [
    '<?xml version="1.0" encoding="utf-8"?>',
    '<VirtualOut Title="Yeelight Pro — управление" Comment="Мост WB {}. Формат: /set/&lt;узел&gt;/&lt;параметр&gt;/&lt;значение&gt;. p: 0/1, l: 0-100 %, ct: 2700-6500 К, ctp: CCT в % 0-100, sww: составной Smart Actuator WW или Lumitech DMX." Address="{}" CmdInit="" CloseAfterSend="true" CmdSep="">'
    .format(WB_IP, HTTP),
    '\t<Info templateType="3" minVersion="16000610"/>',
    '\t<VirtualOutCmd Title="Lamp — вкл/выкл" Comment="Питание светильника lamp" CmdOnMethod="GET" CmdOn="/set/lamp/p/1" CmdOnHTTP="" CmdOnPost="" CmdOffMethod="GET" CmdOff="/set/lamp/p/0" CmdOffHTTP="" CmdOffPost="" CmdAnswer="" HintText="" Analog="false" Repeat="0" RepeatRate="0"/>',
    '\t<VirtualOutCmd Title="Lamp — яркость" Comment="Яркость 0-100 % (0 = выкл)" CmdOnMethod="GET" CmdOn="/set/lamp/l/&lt;v&gt;" CmdOnHTTP="" CmdOnPost="" CmdOffMethod="GET" CmdOff="" CmdOffHTTP="" CmdOffPost="" CmdAnswer="" HintText="" Analog="true" Repeat="0" RepeatRate="0"/>',
    '\t<VirtualOutCmd Title="Lamp — цветовая температура" Comment="CCT 2700-6500 К" CmdOnMethod="GET" CmdOn="/set/lamp/ct/&lt;v&gt;" CmdOnHTTP="" CmdOnPost="" CmdOffMethod="GET" CmdOff="" CmdOffHTTP="" CmdOffPost="" CmdAnswer="" HintText="" Analog="true" Repeat="0" RepeatRate="0"/>',
    '\t<VirtualOutCmd Title="Lamp — CCT 0-100 %" Comment="Температура в процентах: 0=2700 К ... 100=6500 К" CmdOnMethod="GET" CmdOn="/set/lamp/ctp/&lt;v&gt;" CmdOnHTTP="" CmdOnPost="" CmdOffMethod="GET" CmdOff="" CmdOffHTTP="" CmdOffPost="" CmdAnswer="" HintText="" Analog="true" Repeat="0" RepeatRate="0"/>',
    '\t<VirtualOutCmd Title="Lamp — Smart WW (яркость + CCT)" Comment="Выход LC Lighting Controller: Smart WW 201002700 = 100 %, 2700 К; Lumitech DMX 29029029 = 29 %, 4600 К; 0 = выкл" CmdOnMethod="GET" CmdOn="/set/lamp/sww/&lt;v&gt;" CmdOnHTTP="" CmdOnPost="" CmdOffMethod="GET" CmdOff="" CmdOffHTTP="" CmdOffPost="" CmdAnswer="" HintText="" Analog="true" Repeat="0" RepeatRate="0"/>',
    '\t<VirtualOutCmd Title="VDevice — канал 1" Comment="Виртуальное устройство, канал 1" CmdOnMethod="GET" CmdOn="/set/vdevice/1-p/1" CmdOnHTTP="" CmdOnPost="" CmdOffMethod="GET" CmdOff="/set/vdevice/1-p/0" CmdOffHTTP="" CmdOffPost="" CmdAnswer="" HintText="" Analog="false" Repeat="0" RepeatRate="0"/>',
    '\t<VirtualOutCmd Title="VDevice — канал 2" Comment="Виртуальное устройство, канал 2" CmdOnMethod="GET" CmdOn="/set/vdevice/2-p/1" CmdOnHTTP="" CmdOnPost="" CmdOffMethod="GET" CmdOff="/set/vdevice/2-p/0" CmdOffHTTP="" CmdOffPost="" CmdAnswer="" HintText="" Analog="false" Repeat="0" RepeatRate="0"/>',
    '</VirtualOut>',
]


def write(path, lines):
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("\ufeff")
        fh.write("\n".join(lines))
        fh.write("\n")
    raw = open(path, "rb").read()
    print(path, "| bytes:", len(raw), "| BOM:", raw[:3] == b"\xef\xbb\xbf",
          "| CRLF:", raw.count(b"\r\n"), "| LF:", raw.count(b"\n"))


write("VIU_Yeelight_Pro.xml", VIU_LINES)
write("VO_Yeelight_Pro.xml", VO_LINES)
