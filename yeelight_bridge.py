#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Мост Yeelight Pro <-> Loxone. Версия 1.1 (веб-интерфейс).

Подключается к LAN-шлюзу Yeelight Pro (TCP 65443, JSON-кадры через CRLF),
пересылает состояния устройств на Miniserver по UDP (VirtualInUdp) и
принимает команды от Miniserver по UDP (VirtualOutUdp) -> gateway_set.prop.
Дополнительно поднимает HTTP-страницу статуса и настроек (по умолчанию :8082).

Формат датаграмм (в обе стороны):
    yeelight.<alias>.<param>=<value>

Параметры светильника: p (0/1), l (0-100 %), ct (2700-6500 K), ctp (0-100 %
цветовой температуры), sww (составной Smart Actuator WW или Lumitech DMX от Lighting Controller),
o (online 0/1). Конфигурация — JSON, см. DEFAULTS.
"""
import argparse
import html
import io
import json
import logging
import os
import re
import socket
import sys
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse
from xml.sax.saxutils import escape as xesc

LOG = logging.getLogger("yeelight-bridge")

DEFAULTS = {
    "gateway_ip": "192.168.1.53",
    "gateway_port": 65443,
    "loxone_ip": "192.168.1.91",
    "loxone_port": 7002,          # сюда шлём статусы (порт VirtualInUdp на Miniserver)
    "command_port": 7002,         # здесь слушаем команды от VirtualOutUdp
    "poll_interval": 30,          # период контрольного опроса, с
    "transition_ms": 500,         # плавность перехода l/ct
    "aliases": {},                # {"4253247": "lamp"} — переопределение имён узлов
    "ct_min": 2700,
    "ct_max": 6500,
    "key": "",                    # если задан, /set/<key>/... требует этот ключ
    "http_bind": "0.0.0.0",       # веб-интерфейс (None/false — выключить)
    "http_port": 8082,
}

PRETTY_PARAM = {
    "p": "Вкл/выкл", "l": "Яркость", "ct": "Цветовая температура",
    "ctp": "Цветовая температура, %", "sww": "Smart WW (яркость + CCT)",
    "c": "Цвет", "m": "Режим", "o": "Online",
}


def pretty_param(param):
    """Человекочитаемое имя параметра для заголовков шаблонов."""
    if param in PRETTY_PARAM:
        return PRETTY_PARAM[param]
    match = re.match(r"^(\d+)-p$", param or "")
    if match:
        return "Канал %s" % match.group(1)
    return param


def sanitize(name):
    """Имя узла -> безопасный для UDP-строки алиас."""
    alias = re.sub(r"\s+", "_", str(name).strip())
    alias = re.sub(r"[^0-9A-Za-zА-Яа-яЁё_.-]", "", alias)
    return alias or "node"


def fmt(value):
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return str(int(round(value)))
    return str(value)


def fmt_param(key, value):
    if isinstance(value, bool):
        return "вкл" if value else "выкл"
    if key == "ct":
        return "%s К" % fmt(value)
    if key == "l":
        return "%s %%" % fmt(value)
    return fmt(value)


class Bridge:
    def __init__(self, cfg):
        self.cfg = cfg
        self.nodes = {}           # id -> {"alias","name","params":{}, "o": bool|None}
        self.by_alias = {}        # alias -> id
        self.sock_tcp = None
        self.sock_udp = None
        self.send_lock = threading.Lock()
        self.msg_id = 0
        self.connected = threading.Event()
        self.stop = threading.Event()
        self.connected_at = None

    # ---------- UDP к Loxone ----------

    def send_loxone(self, alias, param, value):
        try:
            data = "yeelight.%s.%s=%s" % (alias, param, fmt(value))
            self.sock_udp.sendto(data.encode("utf-8"),
                                 (self.cfg["loxone_ip"], self.cfg["loxone_port"]))
            LOG.debug("-> loxone %s", data)
        except OSError as err:
            LOG.warning("UDP к Loxone не отправлено: %s", err)

    # ---------- TCP к шлюзу ----------

    def next_id(self):
        self.msg_id += 1
        return self.msg_id

    def gw_send(self, method, params=None, nodes=None):
        msg = {"version": "1.0", "id": self.next_id(), "method": method}
        if params is not None:
            msg["params"] = params
        if nodes is not None:
            msg["nodes"] = nodes
        frame = (json.dumps(msg, ensure_ascii=False) + "\r\n").encode("utf-8")
        with self.send_lock:
            self.sock_tcp.sendall(frame)
        return msg["id"]

    def request_sync(self):
        LOG.info("запрашиваю топологию")
        self.gw_send("gateway_get.topology")

    def request_nodes(self):
        for nid in list(self.nodes):
            try:
                self.gw_send("gateway_get.node", params={"id": nid})
            except OSError:
                break

    def force_reconnect(self):
        LOG.info("принудительное переподключение")
        try:
            if self.sock_tcp:
                self.sock_tcp.close()
        except OSError:
            pass

    # ---------- генерация шаблонов Loxone (формат wb-loxone) ----------

    def local_ip(self):
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect((self.cfg["gateway_ip"], int(self.cfg["gateway_port"])))
            ip = probe.getsockname()[0]
            probe.close()
            return ip
        except OSError:
            return "127.0.0.1"

    @staticmethod
    def writable_keys(params):
        """Какие параметры узла можно писать (p, l, ct, каналы N-p)."""
        keys = [k for k in ("p", "l", "ct") if k in params]
        keys += sorted(k for k in params if re.match(r"^\d+-p$", k or ""))
        return keys

    @staticmethod
    def unit_for(param):
        if param in ("l", "ctp"):
            return "<v> %"
        if param == "ct":
            return "<v> К"
        return "<v>"

    def tpl_viu(self, aliases=None):
        """VIU (VirtualInUdp) для узлов; aliases=None — все узлы одним блоком."""
        nodes = self._select(aliases)
        title = "Yeelight " + (nodes[0]["name"] if len(nodes) == 1 else "Pro — все устройства")
        ip = self.local_ip()
        lines = ['<?xml version="1.0" encoding="utf-8"?>',
                 '<VirtualInUdp Title="%s" Comment="Мост WB %s · Yeelight Pro" Address="%s" Port="%s">'
                 % (xesc(title), ip, ip, self.cfg["loxone_port"]),
                 '\t<Info templateType="1" minVersion="16000610"/>']
        for entry in nodes:
            params = list(entry["params"].keys()) + ["o"]
            for param in params:
                name = "%s — %s" % (pretty_param(param), entry["name"] or entry["alias"])
                lines.append(
                    '\t<VirtualInUdpCmd Title="%s" Comment="%s/%s" Address="" '
                    'Check="yeelight.%s.%s=\\v" Signed="true" Analog="true" '
                    'SourceValLow="0" DestValLow="0" SourceValHigh="100" DestValHigh="100" '
                    'DefVal="0" MinVal="-2147483648" MaxVal="2147483647" Unit="%s" HintText=""/>'
                    % (xesc(name), xesc(entry["alias"]), xesc(param),
                       xesc(entry["alias"]), xesc(param), xesc(self.unit_for(param))))
        lines.append("</VirtualInUdp>")
        return self._xml_bytes(lines), self._tpl_filename("VIU", title)

    def tpl_vo(self, aliases=None):
        """VO (VirtualOut HTTP) для узлов; None, если нечего писать."""
        nodes = self._select(aliases)
        chosen = [(e, self.writable_keys(e["params"])) for e in nodes]
        chosen = [(e, ks) for e, ks in chosen if ks]
        if not chosen:
            return None, None
        title = "Yeelight " + (chosen[0][0]["name"] if len(chosen) == 1
                               else "Pro — все устройства")
        http = "http://%s:%s" % (self.local_ip(), self.cfg["http_port"])
        lines = ['<?xml version="1.0" encoding="utf-8"?>',
                 '<VirtualOut Title="%s" Comment="Мост WB · Yeelight Pro" Address="%s" '
                 'CmdInit="" CloseAfterSend="true" CmdSep="">' % (xesc(title), xesc(http)),
                 '\t<Info templateType="3" minVersion="16000610"/>']
        for entry, keys in chosen:
            for param in keys:
                name = "%s — %s" % (pretty_param(param), entry["name"] or entry["alias"])
                base = "/set/%s/%s" % (entry["alias"], param)
                if param == "ct":
                    lines.append(self._vo_cmd(name, entry, param,
                                              base + "/&lt;v&gt;", ""))
                    lines.append(self._vo_cmd(
                        "%s — %s" % (pretty_param("ctp"), entry["name"] or entry["alias"]),
                        entry, "ctp",
                        "/set/%s/ctp/&lt;v&gt;" % entry["alias"], ""))
                elif param == "l":
                    lines.append(self._vo_cmd(name, entry, param,
                                              base + "/&lt;v&gt;", ""))
                else:  # p, N-p — цифровые
                    lines.append(self._vo_cmd(name, entry, param,
                                              base + "/1", base + "/0"))
            if "l" in keys and "ct" in keys:
                lines.append(self._vo_cmd(
                    "%s — %s" % (pretty_param("sww"), entry["name"] or entry["alias"]),
                    entry, "sww", "/set/%s/sww/&lt;v&gt;" % entry["alias"], ""))
        lines.append("</VirtualOut>")
        return self._xml_bytes(lines), self._tpl_filename("VO", title)

    def tpl_zip(self):
        """ZIP: VIU и VO каждого устройства отдельно + общие."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for entry in sorted(self.nodes.values(), key=lambda e: e["alias"]):
                data, fname = self.tpl_viu([entry["alias"]])
                zf.writestr(fname, data)
                data, fname = self.tpl_vo([entry["alias"]])
                if data:
                    zf.writestr(fname, data)
            data, fname = self.tpl_viu(None)
            zf.writestr(fname, data)
            data, fname = self.tpl_vo(None)
            if data:
                zf.writestr(fname, data)
        return buf.getvalue(), "loxone_yeelight_templates.zip"

    def _select(self, aliases):
        if aliases is None:
            return sorted(self.nodes.values(), key=lambda e: e["alias"])
        out = []
        for alias in aliases:
            nid = self.by_alias.get(alias)
            if nid is not None:
                out.append(self.nodes[nid])
        return out

    @staticmethod
    def _vo_cmd(title, entry, param, on, off):
        analog = "true" if on.endswith("&lt;v&gt;") else "false"
        return ('\t<VirtualOutCmd Title="%s" Comment="%s/%s" CmdOnMethod="GET" '
                'CmdOn="%s" CmdOnHTTP="" CmdOnPost="" CmdOffMethod="GET" CmdOff="%s" '
                'CmdOffHTTP="" CmdOffPost="" CmdAnswer="" HintText="" Analog="%s" '
                'Repeat="0" RepeatRate="0"/>'
                % (xesc(title), xesc(entry["alias"]), xesc(param), on, off, analog))

    @staticmethod
    def _tpl_filename(prefix, title):
        safe = re.sub(r'[\\/:*?"<>|]+', "", title).strip()
        safe = re.sub(r"\s+", "_", safe) or "Yeelight"
        return "%s_%s.xml" % (prefix, safe)

    @staticmethod
    def _xml_bytes(lines):
        return ("\ufeff" + "\n".join(lines) + "\n").encode("utf-8")

    # ---------- состояние ----------

    def alias_of(self, node):
        nid = str(node.get("id"))
        if nid in self.cfg["aliases"]:
            return self.cfg["aliases"][nid]
        return sanitize(node.get("n", nid))

    def remember(self, node, emit=True, emit_all=False):
        nid = int(node["id"])
        entry = self.nodes.get(nid)
        if entry is None:
            entry = {"alias": self.alias_of(node), "name": node.get("n", ""),
                     "params": {}, "o": None}
            self.nodes[nid] = entry
            self.by_alias[entry["alias"]] = nid
        changed = []
        params = node.get("params") or {}
        for key, val in params.items():
            old = entry["params"].get(key)
            # сравнение без учёта типа: True == 1, иначе пуши с int-поглощали бы
            # изменения bool-параметра p
            if emit_all or old is None or fmt(old) != fmt(val):
                entry["params"][key] = val
                changed.append((key, val))
        online = node.get("o")
        if isinstance(online, bool) and (emit_all or entry["o"] != online):
            entry["o"] = online
            changed.append(("o", online))
        if emit:
            for key, val in changed:
                self.send_loxone(entry["alias"], key, val)
        return changed

    # ---------- обработка кадров шлюза ----------

    def handle_frame(self, frame):
        method = frame.get("method", "")
        if method in ("gateway_post.topology", "getway_post.topology"):
            nodes = frame.get("nodes") or []
            for node in nodes:
                if node.get("nt") == 2 and "id" in node:
                    self.remember(node, emit=False)
            if nodes:
                LOG.info("топология: %d узлов", len(self.nodes))
                self.request_nodes()
        elif method in ("gateway_post.prop", "device_post.prop"):
            for node in frame.get("nodes") or []:
                if "id" in node:
                    self.remember(node, emit=True)
        elif isinstance(frame.get("nodes"), list) and "method" not in frame:
            # ответ на gateway_get.node (поля id/nodes без method):
            # контрольный опрос — пересылаем все параметры, а не только изменения
            for node in frame["nodes"]:
                if "id" in node:
                    self.remember(node, emit=True, emit_all=True)
        elif "result" in frame:
            if frame.get("result") != "ok":
                LOG.warning("ошибка шлюза: %s", frame)

    # ---------- поток TCP ----------

    def tcp_worker(self):
        backoff = 5
        while not self.stop.is_set():
            try:
                self.sock_tcp = socket.create_connection(
                    (self.cfg["gateway_ip"], self.cfg["gateway_port"]), timeout=10)
                self.sock_tcp.settimeout(5)
                self.connected.set()
                self.connected_at = time.time()
                LOG.info("подключено к шлюзу %s:%s",
                         self.cfg["gateway_ip"], self.cfg["gateway_port"])
                self.request_sync()
                backoff = 5
                buf = b""
                while not self.stop.is_set():
                    try:
                        chunk = self.sock_tcp.recv(65536)
                    except socket.timeout:
                        continue
                    if not chunk:
                        raise ConnectionError("соединение закрыто шлюзом")
                    buf += chunk
                    while b"\r\n" in buf:
                        line, buf = buf.split(b"\r\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            frame = json.loads(line.decode("utf-8"))
                        except ValueError:
                            LOG.warning("некорректный кадр: %r", line[:120])
                            continue
                        self.handle_frame(frame)
            except OSError as err:
                LOG.warning("шлюз недоступен: %s", err)
            finally:
                self.connected.clear()
                self.connected_at = None
                try:
                    if self.sock_tcp:
                        self.sock_tcp.close()
                except OSError:
                    pass
            if self.stop.wait(backoff):
                break
            backoff = min(backoff * 2, 60)

    # ---------- команды от Loxone и веба ----------

    def command_worker(self):
        self.sock_udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_udp.bind(("0.0.0.0", self.cfg["command_port"]))
        LOG.info("команды Loxone: слушаю UDP :%s", self.cfg["command_port"])
        while not self.stop.is_set():
            try:
                data, addr = self.sock_udp.recvfrom(4096)
            except OSError:
                break
            try:
                text = data.decode("utf-8").strip()
                alias, param, value = self.parse_command(text)
            except ValueError:
                LOG.warning("непонятная команда от %s: %r", addr, data[:120])
                continue
            self.execute(alias, param, value)

    @staticmethod
    def parse_command(text):
        match = re.match(r"^yeelight\.([A-Za-zА-Яа-яЁё0-9_.-]+)\.([A-Za-z0-9_-]+)=(-?[\d.]+)$",
                         text)
        if not match:
            raise ValueError(text)
        return match.group(1), match.group(2), match.group(3)

    @staticmethod
    def decode_smart_ww(num, ct_min, ct_max):
        value = int(round(num))
        if value == 0:
            return {"p": False}
        match = re.fullmatch(r"20(\d{3})(\d{4,5})", str(value))
        if match:
            level = int(match.group(1))
            if level > 100:
                raise ValueError("яркость Smart WW должна быть 0-100 %")
            ct = max(ct_min, min(ct_max, int(match.group(2))))
            if level == 0:
                return {"p": False}
            return {"p": True, "l": level, "ct": ct}
        blue, remainder = divmod(value, 1000000)
        green, red = divmod(remainder, 1000)
        if value < 1000 or max(red, green, blue) > 100:
            raise ValueError("ожидается код Smart WW или Lumitech DMX (R + G*1000 + B*1000000)")
        level = max(red, green, blue)
        if level == 0:
            return {"p": False}
        if red + blue == 0:
            raise ValueError("код Lumitech не описывает белый свет")
        warmth = red / (red + blue)
        ct = int(round(ct_max - warmth * (ct_max - ct_min)))
        return {"p": True, "l": level, "ct": ct}

    def execute(self, alias, param, raw):
        nid = self.by_alias.get(alias)
        if nid is None:
            LOG.warning("неизвестный узел '%s'", alias)
            return False, "неизвестный узель '%s'" % alias
        try:
            num = float(raw)
        except ValueError:
            return False, "некорректное значение '%s'" % raw
        ct_min, ct_max = self.cfg["ct_min"], self.cfg["ct_max"]

        if param == "p":
            props = {"p": num != 0}
        elif param == "l":
            level = max(0, min(100, int(round(num))))
            props = {"l": level, "p": level > 0}
        elif param == "ct":
            props = {"ct": max(ct_min, min(ct_max, int(round(num))))}
        elif param == "ctp":
            frac = max(0.0, min(100.0, num)) / 100.0
            props = {"ct": int(round(ct_min + frac * (ct_max - ct_min)))}
        elif param == "sww":
            try:
                props = self.decode_smart_ww(num, ct_min, ct_max)
            except ValueError as err:
                return False, "некорректный Smart WW: %s" % err
        elif re.match(r"^\d+-[A-Za-z0-9_-]+$", param):
            # каналы виртуальных устройств шлюза: "1-p", "2-p" и т.п.
            props = {param: num != 0}
        else:
            LOG.warning("неизвестный параметр '%s'", param)
            return False, "неизвестный параметр '%s'" % param

        node = {"id": nid, "nt": 2, "duration": self.cfg["transition_ms"], "set": props}
        if not self.connected.is_set():
            LOG.warning("шлюз не подключён, команда '%s.%s' не отправлена", alias, param)
            return False, "шлюз не подключён"
        try:
            self.gw_send("gateway_set.prop", nodes=[node])
            LOG.info("команда yeelight.%s.%s=%s -> set %s", alias, param, raw, props)
            return True, "отправлено: %s" % props
        except OSError as err:
            LOG.warning("сбой отправки команды: %s", err)
            return False, "сбой отправки: %s" % err

    # ---------- контрольный опрос ----------

    def run(self):
        threading.Thread(target=self.tcp_worker, daemon=True).start()
        threading.Thread(target=self.command_worker, daemon=True).start()
        interval = max(5, int(self.cfg["poll_interval"]))
        LOG.info("мост запущен: шлюз %s:%s -> Loxone %s:%s",
                 self.cfg["gateway_ip"], self.cfg["gateway_port"],
                 self.cfg["loxone_ip"], self.cfg["loxone_port"])
        try:
            while True:
                time.sleep(interval)
                if self.connected.is_set() and self.nodes:
                    self.request_nodes()
        except KeyboardInterrupt:
            self.stop.set()


# ---------- веб-интерфейс ----------

PAGE = """<!DOCTYPE html>
<html lang="ru"><head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="15">
<title>Yeelight Pro — мост</title>
<style>
body{{font-family:system-ui,Arial,sans-serif;margin:24px;color:#222}}
h1{{font-size:20px}} h2{{font-size:16px;margin-top:28px}}
table{{border-collapse:collapse;margin:8px 0}}
td,th{{border:1px solid #ccc;padding:4px 10px;font-size:14px}}
th{{background:#f4f4f4}}
.badge{{display:inline-block;padding:2px 10px;border-radius:10px;font-size:13px}}
.on{{background:#d6f5d6}} .off{{background:#f5d6d6}}
form{{margin:8px 0;font-size:14px}}
input,select{{padding:3px 6px;margin:2px}}
button{{padding:4px 14px}}
.msg{{background:#fff8d6;padding:6px 10px;border:1px solid #e0d080;margin:8px 0}}
.mono{{font-family:Consolas,monospace;font-size:13px}}
</style></head><body>
<h1>Мост Yeelight Pro &harr; Loxone</h1>
<div>Соединение со шлюзом: <span class="badge {conn_cls}">{conn}</span>
&nbsp; Шлюз: <b>{gw}</b> &nbsp; Loxone: <b>{lx}</b> &nbsp; UDP-команды: <b>:{cmd_port}</b></div>
{msg}
<h2>Узлы</h2>
<table><tr><th>Алиас</th><th>ID</th><th>Имя</th><th>Online</th><th>Параметры</th></tr>
{rows}
</table>
<h2>Шаблоны Loxone</h2>
<div>Общие: <a href="/tpl/viu/all">VIU всех устройств</a> ·
<a href="/tpl/vo/all">VO всех устройств</a> ·
<a href="/tpl/all.zip">ZIP со всеми шаблонами</a></div>
<p class="mono" style="color:#777;margin:4px 0">Импорт: Периферия → Виртуальные входы/выходы → «Импорт шаблона». Новые устройства шлюза появляются в списке сами.</p>
<table><tr><th>Устройство</th><th>Алиас</th><th>Каналов</th><th>Шаблоны</th></tr>
{tpl_rows}
</table>
<h2>Тестовая команда</h2>
<form method="get" action="/cmd">
Узел: <select name="alias">{alias_opts}</select>
Параметр: <select name="param">
<option value="p">p — вкл/выкл (0/1)</option>
<option value="l">l — яркость (0–100 %)</option>
<option value="ct">ct — температура (2700–6500 К)</option>
<option value="ctp">ctp — температура (0–100 %)</option>
<option value="sww">sww — Smart WW / Lumitech (200504000 или 29029029)</option>
</select>
Значение: <input name="value" size="12" value="1">
<button type="submit">Отправить</button>
</form>
<h2>Переподключиться к шлюзу</h2>
<form method="get" action="/reconnect"><button type="submit">Reconnect</button></form>
<h2>Настройки</h2>
<form method="get" action="/config">
<table>
<tr><td>IP шлюза Yeelight</td><td><input name="gateway_ip" value="{gateway_ip}"></td>
<td>Порт TCP шлюза</td><td><input name="gateway_port" value="{gateway_port}" size="6"></td></tr>
<tr><td>IP Miniserver</td><td><input name="loxone_ip" value="{loxone_ip}"></td>
<td>Порт статусов → Miniserver</td><td><input name="loxone_port" value="{loxone_port}" size="6"></td></tr>
<tr><td>Порт команд (UDP)</td><td><input name="command_port" value="{command_port}" size="6"></td>
<td>Порт веб-страницы</td><td><input name="http_port" value="{http_port}" size="6"></td></tr>
<tr><td>Опрос шлюза, с</td><td><input name="poll_interval" value="{poll_interval}" size="6"></td>
<td>Переход l/ct, мс</td><td><input name="transition_ms" value="{transition_ms}" size="6"></td></tr>
<tr><td>CCT мин, К</td><td><input name="ct_min" value="{ct_min}" size="6"></td>
<td>CCT макс, К</td><td><input name="ct_max" value="{ct_max}" size="6"></td></tr>
</table>
Алиасы узлов (по строке <span class="mono">id=alias</span>):<br>
<textarea name="aliases" rows="3" cols="40">{aliases}</textarea><br>
<button type="submit">Сохранить и перезапустить</button>
</form>
<p class="mono" style="color:#777">После сохранения сервис перезапускается systemd (~5 с),
страница обновится сама. Мост без авторизации — только для локальной сети.</p>
</body></html>"""


def render_page(bridge, msg=""):
    cfg = bridge.cfg
    conn = "подключено" if bridge.connected.is_set() else "нет соединения"
    conn_cls = "on" if bridge.connected.is_set() else "off"
    rows = []
    alias_opts = []
    for nid, entry in sorted(bridge.nodes.items()):
        params = ", ".join(
            "%s=%s" % (k, fmt_param(k, v)) for k, v in sorted(entry["params"].items()))
        online = "да" if entry["o"] else ("нет" if entry["o"] is False else "—")
        rows.append(
            "<tr><td class='mono'>%s</td><td>%s</td><td>%s</td><td>%s</td><td class='mono'>%s</td></tr>"
            % (html.escape(entry["alias"]), nid, html.escape(entry["name"]),
               online, html.escape(params)))
        alias_opts.append('<option value="%s">%s</option>'
                          % (html.escape(entry["alias"]), html.escape(entry["alias"])))
    tpl_rows = []
    for nid, entry in sorted(bridge.nodes.items()):
        alias_q = quote(entry["alias"])
        n_ch = len(entry["params"]) + 1
        links = ['<a href="/tpl/viu/%s">VIU</a>' % alias_q]
        if bridge.writable_keys(entry["params"]):
            links.append('<a href="/tpl/vo/%s">VO</a>' % alias_q)
        tpl_rows.append(
            "<tr><td>%s</td><td class='mono'>%s</td><td>%d</td><td>%s</td></tr>"
            % (html.escape(entry["name"] or entry["alias"]),
               html.escape(entry["alias"]), n_ch, " · ".join(links)))
    aliases_text = "\n".join("%s=%s" % (k, v) for k, v in sorted(cfg["aliases"].items()))
    body = PAGE.format(
        conn=conn, conn_cls=conn_cls,
        gw="%s:%s" % (cfg["gateway_ip"], cfg["gateway_port"]),
        lx="%s:%s" % (cfg["loxone_ip"], cfg["loxone_port"]),
        cmd_port=cfg["command_port"],
        msg="<div class='msg'>%s</div>" % html.escape(msg) if msg else "",
        rows="\n".join(rows) or "<tr><td colspan=5>узлов пока нет</td></tr>",
        tpl_rows="\n".join(tpl_rows) or "<tr><td colspan=4>узлов пока нет</td></tr>",
        alias_opts="\n".join(alias_opts),
        aliases=html.escape(aliases_text),
        gateway_ip=cfg["gateway_ip"], gateway_port=cfg["gateway_port"],
        loxone_ip=cfg["loxone_ip"], loxone_port=cfg["loxone_port"],
        command_port=cfg["command_port"], http_port=cfg["http_port"],
        poll_interval=cfg["poll_interval"], transition_ms=cfg["transition_ms"],
        ct_min=cfg["ct_min"], ct_max=cfg["ct_max"])
    return body.encode("utf-8")


def render_config_saved_page(bridge):
    return render_page(bridge, "Конфигурация сохранена. Перезапуск...").replace(
        b'<meta http-equiv="refresh" content="15">',
        b'<meta http-equiv="refresh" content="8;url=/">')


class WebHandler(BaseHTTPRequestHandler):
    bridge = None
    cfg_path = None

    def log_message(self, fmt_str, *args):  # noqa: A002 — сиглета urllib
        LOG.info("http %s", fmt_str % args)

    def send_html(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, code, text):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_download(self, body, filename, mime="application/octet-stream"):
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition",
                         "attachment; filename*=UTF-8''%s" % quote(filename))
        self.end_headers()
        self.wfile.write(body)

    def handle_tpl(self, parts):
        # /tpl/all.zip | /tpl/viu/<alias> | /tpl/vo/<alias> | /tpl/viu|vo (все)
        bridge = self.bridge
        if len(parts) >= 2 and parts[1] == "all.zip":
            body, fname = bridge.tpl_zip()
            self.send_download(body, fname, "application/zip")
            return
        kind = parts[1] if len(parts) >= 2 else ""
        aliases = None
        if len(parts) >= 3 and parts[2] != "all":
            aliases = [unquote(parts[2])]
        if kind == "viu":
            body, fname = bridge.tpl_viu(aliases)
            self.send_download(body, fname, "text/xml; charset=utf-8")
        elif kind == "vo":
            body, fname = bridge.tpl_vo(aliases)
            if body is None:
                self.send_text(404, "no writable channels")
            else:
                self.send_download(body, fname, "text/xml; charset=utf-8")
        else:
            self.send_text(404, "unknown template kind")

    def handle_set(self, segments):
        """/set/<alias>/<param>/<value> или /set/<key>/<alias>/<param>/<value>."""
        from urllib.parse import unquote
        segments = [unquote(s) for s in segments]
        key = self.bridge.cfg.get("key") or ""
        if key:
            if len(segments) != 5 or segments[1] != key:
                self.send_text(403, "error: bad key")
                return
            alias, param, value = segments[2], segments[3], segments[4]
        else:
            if len(segments) != 4:
                self.send_text(400, "usage: /set/<alias>/<param>/<value>")
                return
            alias, param, value = segments[1], segments[2], segments[3]
        ok, text = self.bridge.execute(alias, param, value)
        self.send_text(200 if ok else 400, ("ok: " if ok else "error: ") + text)

    def redirect_home(self, msg=""):
        dest = "/?msg=" + quote(msg) if msg else "/"
        self.send_response(303)
        self.send_header("Location", dest)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):  # noqa: N802 — API http.server
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path.startswith("/set/"):
            self.handle_set(url.path.split("/")[1:])
        elif url.path.startswith("/tpl/"):
            self.handle_tpl(url.path.split("/")[1:])
        elif url.path in ("/", "/index.html"):
            msg = query.get("msg", [""])[0]
            self.send_html(render_page(self.bridge, msg))
        elif url.path == "/cmd":
            alias = query.get("alias", [""])[0]
            param = query.get("param", [""])[0]
            value = query.get("value", [""])[0]
            ok, text = self.bridge.execute(alias, param, value)
            self.redirect_home(("OK: " if ok else "ОШИБКА: ") + text)
        elif url.path == "/reconnect":
            self.bridge.force_reconnect()
            self.redirect_home("переподключение...")
        elif url.path == "/config":
            try:
                save_config(self.bridge, self.cfg_path, query)
                body = render_config_saved_page(self.bridge)
                self.send_html(body)
                threading.Timer(1.0, os._exit, args=(0,)).start()
            except Exception as err:  # noqa: BLE001 — показать любую ошибку в вебе
                self.send_html(render_page(self.bridge, "Ошибка сохранения: %s" % err))
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()


def save_config(bridge, cfg_path, query):
    cfg = dict(bridge.cfg)
    int_keys = ("gateway_port", "loxone_port", "command_port", "http_port",
                "poll_interval", "transition_ms", "ct_min", "ct_max")
    str_keys = ("gateway_ip", "loxone_ip", "http_bind")
    for key in int_keys:
        if key in query and query[key][0].strip():
            cfg[key] = int(query[key][0])
    for key in str_keys:
        if key in query and query[key][0].strip():
            cfg[key] = query[key][0].strip()
    aliases = {}
    for line in query.get("aliases", [""])[0].splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if key and val:
            aliases[key] = val
    cfg["aliases"] = aliases
    with open(cfg_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    LOG.info("конфигурация сохранена: %s", cfg_path)


def web_worker(bridge, cfg_path):
    if not bridge.cfg.get("http_bind"):
        return
    try:
        server = ThreadingHTTPServer(
            (bridge.cfg["http_bind"], int(bridge.cfg["http_port"])), WebHandler)
    except OSError as err:
        LOG.warning("веб-интерфейс не поднят: %s", err)
        return
    WebHandler.bridge = bridge
    WebHandler.cfg_path = cfg_path
    LOG.info("веб-интерфейс: http://%s:%s/", bridge.cfg["http_bind"],
             bridge.cfg["http_port"])
    threading.Thread(target=server.serve_forever, daemon=True).start()


def main():
    parser = argparse.ArgumentParser(description="Yeelight Pro <-> Loxone bridge")
    parser.add_argument("--config", help="путь к JSON-конфигу")
    parser.add_argument("--verbose", action="store_true", help="подробный лог")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)

    cfg = dict(DEFAULTS)
    cfg_path = args.config
    if cfg_path:
        with open(cfg_path, "r", encoding="utf-8") as fh:
            user = json.load(fh)
        for key, val in user.items():
            if key == "aliases":
                cfg["aliases"].update({str(k): str(v) for k, v in val.items()})
            else:
                cfg[key] = val
    bridge = Bridge(cfg)
    web_worker(bridge, cfg_path)
    bridge.run()


if __name__ == "__main__":
    main()
