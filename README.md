# Irrigation Controller

One page on the Pi 4 (over Tailscale): live video on demand plus garden valve control.
An ESP-01S drives the 24 V solenoid and caps every run at 90 s. Scheduled runs missed
because of a power cut catch up when the Pi is back, until 22:00. If the Pi is dead,
the ESP waters on its own at 08:00 for 60 s.

Design: [docs/specs/2026-10-14-irrigation-design.md](docs/specs/2026-10-14-irrigation-design.md)
Firmware: [firmware/README.md](firmware/README.md)

## Deploy on the Pi 4 (SSH as nilrasp)

```bash
sudo apt update && sudo apt install -y git mosquitto mosquitto-clients python3-paho-mqtt python3-picamera2
sudo timedatectl set-timezone Asia/Kolkata
git clone https://github.com/chakrabortyniladri/irrigation-controller.git ~/irrigation-controller
cd ~/irrigation-controller

sudo cp systemd/mosquitto-irrigation.conf /etc/mosquitto/conf.d/irrigation.conf
sudo systemctl enable mosquitto && sudo systemctl restart mosquitto

# Reuse the camera page login (12+ char password), or create a new one with nano
sed 's/^CAMERA_/IRRIGATION_/' ~/.camera.env > ~/.irrigation.env && chmod 600 ~/.irrigation.env

sudo systemctl disable --now camera        # old camera page: frees the camera and port 8000
sudo cp systemd/irrigation.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now irrigation
journalctl -u irrigation -f
```

Open `http://<pi-tailscale-name>:8000/`.

Update: `cd ~/irrigation-controller && git pull && sudo systemctl restart irrigation`

## Tests

```bash
cd server && python3 -m unittest -v
cd firmware/test && make test
```
