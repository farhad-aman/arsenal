#!/usr/bin/env python3
"""Convert a v2ray/xray subscription (or a file of share links) into one
sing-box config with a urltest group that auto-picks the fastest node.

Usage: sub2singbox.py <url-or-file-or-link> <output.json> [--port N]

Unsupported-by-sing-box transports (xhttp, kcp, quic) are skipped and counted.
"""
import base64, json, os, re, ssl, sys, urllib.parse as up, urllib.request

UA = "v2rayNG/1.8.5"
SKIP_TRANSPORTS = {"xhttp", "kcp", "quic", "splithttp"}


def _get(src, proxy, timeout):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    handlers = [urllib.request.HTTPSHandler(context=ctx)]
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        handlers.append(urllib.request.ProxyHandler({}))   # ignore env proxies
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(src, headers={"User-Agent": UA})
    return opener.open(req, timeout=timeout).read().decode()


def fetch(src):
    if os.path.exists(src):
        return open(src).read()
    if re.match(r"(vless|vmess|trojan)://", src):
        return src
    # Sub hosts are often filtered — try direct, then fall back through the
    # local sing-box proxy (mixed inbound on :2080 speaks HTTP CONNECT).
    proxy = os.environ.get("SUB_PROXY", "http://127.0.0.1:2080")
    attempts = [(None, 12), (proxy, 25)]
    last = None
    for p, t in attempts:
        try:
            return _get(src, p, t)
        except Exception as e:
            last = e
            sys.stderr.write(f"fetch via {p or 'direct'} failed: {e}\n")
    raise SystemExit(f"could not fetch subscription (tried direct + {proxy}): {last}")


def maybe_b64(text):
    t = "".join(text.split())
    if "://" in text:
        return text
    try:
        return base64.b64decode(t + "=" * (-len(t) % 4)).decode()
    except Exception:
        return text


def tls_block(q, host, default_sni=None):
    sec = q.get("security", "none")
    if sec not in ("tls", "reality"):
        return None
    t = {"enabled": True,
         "server_name": q.get("sni") or q.get("host") or default_sni or host}
    if q.get("fp"):
        t["utls"] = {"enabled": True, "fingerprint": q["fp"]}
    if q.get("alpn"):
        t["alpn"] = q["alpn"].split(",")
    if sec == "reality":
        if not q.get("pbk"):
            return "SKIP"
        t["reality"] = {"enabled": True, "public_key": q["pbk"],
                        "short_id": q.get("sid", "")}
    return t


def transport_block(q):
    t = q.get("type", "tcp")
    if t in SKIP_TRANSPORTS:
        return "SKIP"
    if t == "ws":
        tr = {"type": "ws", "path": q.get("path", "/")}
        if q.get("host"):
            tr["headers"] = {"Host": q["host"]}
        return tr
    if t == "httpupgrade":
        tr = {"type": "httpupgrade", "path": q.get("path", "/")}
        if q.get("host"):
            tr["host"] = q["host"]
        return tr
    if t == "grpc":
        return {"type": "grpc",
                "service_name": q.get("serviceName") or q.get("path", "")}
    if t == "tcp" and q.get("headerType") == "http":
        tr = {"type": "http", "path": q.get("path", "/")}
        if q.get("host"):
            tr["host"] = [q["host"]]
        return tr
    return None  # plain tcp


def parse_vless(link):
    u = up.urlparse(link)
    q = {k: v[0] for k, v in up.parse_qs(u.query).items()}
    ob = {"type": "vless", "server": u.hostname, "server_port": u.port,
          "uuid": u.username}
    if q.get("flow"):
        ob["flow"] = q["flow"]
    t = tls_block(q, u.hostname)
    if t == "SKIP":
        return None
    if t:
        ob["tls"] = t
    tr = transport_block(q)
    if tr == "SKIP":
        return None
    if tr:
        ob["transport"] = tr
    return ob


def parse_trojan(link):
    u = up.urlparse(link)
    q = {k: v[0] for k, v in up.parse_qs(u.query).items()}
    ob = {"type": "trojan", "server": u.hostname, "server_port": u.port,
          "password": up.unquote(u.username or "")}
    q.setdefault("security", "tls")          # trojan is TLS by default
    t = tls_block(q, u.hostname)
    if t == "SKIP":
        return None
    if t:
        ob["tls"] = t
    tr = transport_block(q)
    if tr == "SKIP":
        return None
    if tr:
        ob["transport"] = tr
    return ob


def parse_vmess(link):
    raw = link[len("vmess://"):]
    try:
        c = json.loads(base64.b64decode(raw + "=" * (-len(raw) % 4)).decode())
    except Exception:
        return None
    ob = {"type": "vmess", "tag": c.get("ps", ""),
          "server": c["add"], "server_port": int(c["port"]),
          "uuid": c["id"], "alter_id": int(c.get("aid", 0) or 0),
          "security": c.get("scy") or "auto"}
    q = {"type": c.get("net", "tcp"), "host": c.get("host", ""),
         "path": c.get("path", "/"), "headerType": c.get("type", ""),
         "security": "tls" if c.get("tls") == "tls" else "none",
         "sni": c.get("sni", ""), "serviceName": c.get("path", "")}
    t = tls_block({k: v for k, v in q.items() if v}, c["add"])
    if t == "SKIP":
        return None
    if t:
        ob["tls"] = t
    tr = transport_block(q)
    if tr == "SKIP":
        return None
    if tr:
        ob["transport"] = tr
    return ob


def slug(name, used):
    s = re.sub(r"[^A-Za-z0-9]+", "-", up.unquote(name)).strip("-").lower()
    s = re.sub(r"-+", "-", s)[:28] or "node"
    base, n = s, 2
    while s in used:
        s = f"{base}-{n}"; n += 1
    used.add(s)
    return s


def main():
    src, out = sys.argv[1], sys.argv[2]
    port = 2080
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])

    text = maybe_b64(fetch(src))
    links = [l.strip() for l in text.splitlines() if "://" in l]

    nodes, used, skipped, failed = [], set(), 0, 0
    for link in links:
        frag = link.split("#", 1)[1] if "#" in link else ""
        try:
            if link.startswith("vless://"):
                ob = parse_vless(link)
            elif link.startswith("trojan://"):
                ob = parse_trojan(link)
            elif link.startswith("vmess://"):
                ob = parse_vmess(link)
            else:
                ob = None
                skipped += 1
                continue
        except Exception:
            ob = None
        if ob is None:
            skipped += 1
            continue
        if not ob.get("server") or not ob.get("server_port"):
            failed += 1
            continue
        ob["tag"] = slug(frag or ob.get("tag") or ob["server"], used)
        nodes.append(ob)

    if not nodes:
        sys.exit("no usable nodes found")

    tags = [n["tag"] for n in nodes]
    cfg = {
        "log": {"level": "warn"},
        "inbounds": [{"type": "mixed", "tag": "mixed-in",
                      "listen": "0.0.0.0", "listen_port": port}],
        "outbounds": [
            # urltest MUST stay first: vpn.sh's TUN builder retags the first
            # non-direct outbound as "proxy".
            {"type": "urltest", "tag": "auto", "outbounds": tags,
             "url": "https://www.gstatic.com/generate_204",
             "interval": "5m", "tolerance": 50, "idle_timeout": "30m"},
            *nodes,
            {"type": "direct", "tag": "direct"},
        ],
        "route": {"auto_detect_interface": True, "final": "auto"},
    }
    with open(out, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")

    print(f"{len(nodes)} nodes -> {out}  (skipped {skipped} unsupported, {failed} malformed)")


if __name__ == "__main__":
    main()
