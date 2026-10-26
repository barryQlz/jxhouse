# -*- coding: utf-8 -*-
"""
最小化 localtunnel 客户端（适配只允许 HTTP 代理出网的沙箱）
- 通过 loca.lt API 申请公网地址
- 通过 HTTP CONNECT 代理建立到 loca.lt 分配端口的原始 TCP 隧道
- 将公网请求转发到本机 127.0.0.1:8000
用法: python lt_client.py [--subdomain name] [--local-port 8000]
"""
import argparse
import os
import select
import socket
import sys
import time

import requests

PROXY = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or "http://127.0.0.1:18080"
PROXY_HOST, PROXY_PORT = "127.0.0.1", 18080
if "://" in PROXY:
    _p = PROXY.split("://", 1)[1]
    PROXY_HOST, _, _pp = _p.partition(":")
    PROXY_PORT = int(_pp.split("/")[0] or 18080)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def connect_via_proxy(host, port, timeout=30):
    s = socket.create_connection((PROXY_HOST, PROXY_PORT), timeout=timeout)
    req = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\nProxy-Connection: Keep-Alive\r\n\r\n"
    s.sendall(req.encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        d = s.recv(4096)
        if not d:
            raise RuntimeError("proxy closed during CONNECT")
        buf += d
    head = buf.split(b"\r\n\r\n", 1)[0].decode("latin1")
    if " 200 " not in head.split("\r\n")[0]:
        raise RuntimeError("proxy CONNECT failed: " + head.split("\r\n")[0])
    return s


def relay(remote, local_port):
    try:
        local = socket.create_connection(("127.0.0.1", local_port), timeout=10)
    except Exception as e:
        log("local connect failed", e)
        remote.close()
        return
    remote.setblocking(True)
    local.setblocking(True)
    try:
        while True:
            r, _, _ = select.select([remote, local], [], [], 120)
            if not r:
                break
            for src in r:
                data = src.recv(65536)
                if not data:
                    return
                dst = local if src is remote else remote
                dst.sendall(data)
    except Exception:
        pass
    finally:
        for s in (remote, local):
            try:
                s.close()
            except Exception:
                pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subdomain", default="")
    ap.add_argument("--local-port", type=int, default=8000)
    args = ap.parse_args()

    api = "https://loca.lt/" + (args.subdomain if args.subdomain else "?new")
    r = requests.get(api, timeout=30)
    r.raise_for_status()
    info = r.json()
    host = "loca.lt"
    port = int(info["port"])
    max_conn = int(info.get("max_conn_count") or 2)
    url = info["url"]
    open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "public_url.txt"), "w").write(url)
    log(f"TUNNEL URL: {url}  ({host}:{port}, conns={max_conn})")

    import threading
    stop = False

    def worker():
        while not stop:
            try:
                remote = connect_via_proxy(host, port)
                log("tunnel connection established")
                relay(remote, args.local_port)
            except Exception as e:
                log("tunnel error:", e)
            time.sleep(1)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(max_conn)]
    for t in threads:
        t.start()
    try:
        while True:
            time.sleep(30)
    except KeyboardInterrupt:
        stop = True


if __name__ == "__main__":
    main()
