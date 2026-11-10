"""Optional Docker acceptance check. Uses an isolated simulator with NO network adapter.

Run from the repository root after building: docker build -t subghz-gui ./gui
Then: python gui/tests/docker_smoke.py
Only randomly named containers/volumes created by this script are removed.
"""
import json
import subprocess
import time
import uuid


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True, stderr=subprocess.STDOUT)


def request(container, path, data=None):
    # Requests originate INSIDE the network-isolated container, never toward a LAN device.
    script = """
import json, sys, urllib.request
data = None if sys.argv[2] == 'null' else json.loads(sys.argv[2])
req = urllib.request.Request('http://127.0.0.1:8000' + sys.argv[1],
    data=None if data is None else json.dumps(data).encode(),
    headers={'Content-Type': 'application/json', 'X-Requested-With': 'subghz-gui'})
with urllib.request.urlopen(req, timeout=2) as response:
    print(response.read().decode())
"""
    return json.loads(docker("exec", container, "python", "-c", script, path, json.dumps(data)))


def start(container, volume):
    docker("run", "-d", "--name", container, "--network", "none", "--read-only",
           "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m", "--cap-drop", "ALL",
           "--security-opt", "no-new-privileges:true", "-e", "DEVICE_MODE=simulated",
           "-v", f"{volume}:/app/data", "subghz-gui")
    deadline = time.monotonic() + 15
    while True:
        try:
            cfg = request(container, "/api/config")
            assert cfg["simulated"] is True and isinstance(cfg.get("version"), str) and cfg["version"]
            return
        except subprocess.CalledProcessError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)  # Bounded readiness wait, not a fixed startup assumption.


def main():
    suffix = uuid.uuid4().hex
    volume, first, second = f"subghz-test-data-{suffix}", f"subghz-test-a-{suffix}", f"subghz-test-b-{suffix}"
    containers = []
    docker("volume", "create", volume)
    try:
        containers.append(first)
        start(first, volume)
        assert docker("exec", first, "id", "-u").strip() == "10001"
        config = json.loads(docker("inspect", first))[0]
        assert config["HostConfig"]["NetworkMode"] == "none"
        assert config["HostConfig"]["ReadonlyRootfs"]
        saved = request(first, "/api/signals", {"name": "Persistent simulator fixture", "pulses": [350, -10850]})
        docker("rm", "-f", first)
        containers.remove(first)
        containers.append(second)
        start(second, volume)
        records = request(second, "/api/signals")
        assert len(records) == 1 and records[0]["id"] == saved["id"]
        assert records[0]["pulses"] == [350, -10850]
        assert request(second, "/api/device/status")["simulated"]
        print("PASS: non-root/read-only/network-none simulator and volume persistence across recreation")
    finally:
        for container in containers:
            subprocess.run(["docker", "rm", "-f", container], check=False, capture_output=True)
        subprocess.run(["docker", "volume", "rm", volume], check=False, capture_output=True)


if __name__ == "__main__":
    main()
