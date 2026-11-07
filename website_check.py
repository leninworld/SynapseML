"""Actual pinned middleware fixture and full owned npm-start deep-route witness."""
from pathlib import Path
import json
import http.client
import os
import re
import selectors
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def document_request(url):
    return urllib.request.Request(url, headers={"Accept": "text/html"})


class LocalRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        assert target.scheme == "http" and target.hostname == "127.0.0.1" and target.port == 3000, "NONLOCAL_DEV_REDIRECT"
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def stop_owned(proc):
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
    assert proc.poll() is not None


def fixture():
    """Exercise the real installed 2.0.0 middleware and actual urllib request helper."""
    module = Path("/runtime/source/website/node_modules/connect-history-api-fallback")
    assert json.loads((module / "package.json").read_text())["version"] == "2.0.0"
    code = """
const http = require('http');
const path = require('path');
const wds = path.dirname(require.resolve('webpack-dev-server/package.json', {paths: [process.cwd()]}));
const resolved = require.resolve('connect-history-api-fallback', {paths: [wds]});
if (path.dirname(resolved) !== require('fs').realpathSync(process.argv[1] + '/lib')
    || require(path.join(path.dirname(resolved), '..', 'package.json')).version !== '2.0.0') {
  throw new Error('MIDDLEWARE_RESOLUTION_MISMATCH');
}
const middleware = require(resolved)();
const server = http.createServer((req,res) => middleware(req,res,() => {
  const rewritten = req.url === '/index.html';
  res.statusCode = rewritten ? 200 : 404;
  res.setHeader('Content-Type', rewritten ? 'text/html' : 'text/plain');
  res.end(rewritten ? '<!doctype html>actual middleware rewrite' : 'no rewrite');
}));
server.listen(0, '127.0.0.1', () => console.log(JSON.stringify({port: server.address().port, resolved})));
"""
    proc = subprocess.Popen(["node", "-e", code, str(module)], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, start_new_session=True)
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)
    try:
        assert sel.select(5), "MIDDLEWARE_FIXTURE_START_TIMEOUT"
        line = proc.stdout.readline(8193)
        assert len(line) <= 8192
        endpoint = json.loads(line)
        port = endpoint["port"]
        assert 1024 <= port <= 65535 and proc.poll() is None
        url = f"http://127.0.0.1:{port}/SynapseML/docs/next/deep-route/"
        opener = urllib.request.build_opener()
        try:
            opener.open(url, timeout=2)
        except urllib.error.HTTPError as error:
            assert error.code == 404
        else:
            raise AssertionError("MISSING_ACCEPT_NEGATIVE_NOT_REJECTED")
        with opener.open(document_request(url), timeout=2) as response:
            data = response.read(4097)
            assert response.status == 200 and data == b"<!doctype html>actual middleware rewrite"
        result = {"real_middleware_version": "2.0.0", "resolved_module": endpoint["resolved"], "real_loopback_transport": True,
                  "without_accept_http_status": 404, "actual_document_request_http_status": 200}
        Path("/runtime/website-header-fixture.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
    finally:
        sel.close()
        stop_owned(proc)
        stderr = proc.stderr.read(16385)
        Path("/runtime/website-header-fixture-process.json").write_text(json.dumps({
            "terminal": proc.poll() is not None, "exit": proc.returncode,
            "stderr": stderr[:16384].decode(errors="replace"), "stderr_truncated": len(stderr) > 16384}, indent=2))
        proc.stdout.close()
        proc.stderr.close()


def main():
    from website_contract import inspect_build
    web = Path("/runtime/source/website")
    page, overview, route = inspect_build(web / "build")
    assert route.startswith("/SynapseML/"), route
    opener = urllib.request.build_opener(LocalRedirect)
    proc = subprocess.Popen(["npm", "start", "--", "--host", "127.0.0.1", "--port", "3000", "--no-open"],
                            cwd=web, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)
    start = time.monotonic()
    logs = bytearray()
    result = None
    compiled = False
    counts = {}
    last_errors = []
    requests = 0
    try:
        while time.monotonic() - start < 480:
            assert proc.poll() is None, "OWNED_DEV_SERVER_EXITED"
            for key, _ in sel.select(.2):
                chunk = os.read(key.fd, 8192)
                if chunk:
                    assert len(logs) + len(chunk) <= 1024**2, "WEBSITE_DEV_LOG_CAP"
                    logs.extend(chunk)
            clean = re.sub(rb'\x1b\[[0-9;]*[A-Za-z]', b'', bytes(logs))
            assert not any(marker in clean for marker in [b'Failed to compile', b'Module not found', b'[ERROR]']), "DEV_COMPILATION_ERROR"
            compiled = bool(re.search(rb'Client:\s+Compiled successfully', clean) or
                            re.search(rb'webpack[^\n]*compiled successfully', clean, re.I))
            if not compiled:
                continue
            requests += 1
            url = "http://127.0.0.1:3000" + urllib.parse.quote(urllib.parse.unquote(route), safe="/")
            try:
                with opener.open(document_request(url), timeout=1) as response:
                    data = response.read(2 * 1024**2 + 1)
                    assert len(data) <= 2 * 1024**2 and response.status == 200
                    assert b'<!DOCTYPE html>' in data or b'<!doctype html>' in data
                    result = {"route": route, "http_status": response.status, "bytes": len(data),
                              "ssr_file": str(page), "ssr_bytes": page.stat().st_size,
                              "dev_client_compilation_success": compiled, "request_accept": "text/html",
                              "http_evidence": "actual deep-route loopback liveness; production SSR is rendered-page evidence"}
                    break
            except (OSError, http.client.HTTPException) as error:
                status = getattr(error, "code", None)
                key = str(status) if status is not None else type(error).__name__
                counts[key] = counts.get(key, 0) + 1
                last_errors.append({"status": status, "type": type(error).__name__, "reason": str(error)[:256]})
                last_errors = last_errors[-8:]
        assert result, "NO_SUCCESSFUL_DEV_COMPILE_AND_LIVENESS"
        Path("/runtime/website-route.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
    finally:
        sel.close()
        stop_owned(proc)
        proc.stdout.close()
        Path("/runtime/website-dev.log").write_bytes(logs)
        Path("/runtime/website-http-observations.json").write_text(json.dumps({
            "route": route, "compiled": compiled, "requests": requests,
            "error_counts": counts, "last_errors": last_errors,
            "result": result, "owned_process_terminal": True}, indent=2))


if __name__ == "__main__":
    fixture() if sys.argv[1:] == ["--fixture"] else main()
