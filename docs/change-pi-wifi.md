# Changing Wi-Fi without a screen

How to get the robot onto a new Wi-Fi network, or back into SSH, with just your phone. There are two safety
nets, each set up once.

## 1. Your phone's hotspot (do this first)

Save your phone's hotspot as a backup network. With the hotspot on, SSH in and run:

```bash
sudo nmcli connection add type wifi con-name "phone-hotspot" ssid "YourHotspotName" \
     wifi-sec.key-mgmt wpa-psk wifi-sec.psk "YourHotspotPassword" \
     connection.autoconnect yes connection.autoconnect-priority -10
```

This only saves the network; it doesn't switch to it, so your SSH session stays up. The low priority means
your normal Wi-Fi wins when both are in range. Now, anywhere: turn the hotspot on, the Pi joins it, and you can
`ssh <user>@<hostname>.local` (or use the IP from the phone's list of connected devices).

**Never run `nmcli device wifi connect` over SSH on your current Wi-Fi.** It switches networks at once and
freezes the session you're typing in. Also, some Android hotspots block device-to-device traffic, which makes
SSH impossible: test yours at home first.

## 2. The Comitup portal

When the Pi finds no network it knows, Comitup makes it start its own Wi-Fi network with a page for choosing
a new one. The installer sets it up (`ROBOT_INSTALL_COMITUP=1`) and stores no passwords.

1. Power the robot on somewhere new. It looks for your home Wi-Fi, then your hotspot.
2. Finding neither, it starts a network called `comitup-<id>`.
3. Join it from your phone. The portal page opens, or browse to `http://10.42.0.1`.
4. Pick the local Wi-Fi and enter its password. The Pi joins it and drops its own network.
5. `ssh <user>@<hostname>.local` from the new network.

To install it by hand: `sudo apt install -y comitup avahi-daemon`, then
`sudo systemctl enable --now comitup avahi-daemon` and reboot.

## Switching networks by hand

Once you have a shell:

```bash
sudo nmcli device wifi rescan; nmcli device wifi list   # networks in range
nmcli connection show                                   # networks it has saved
sudo nmcli connection up "YourHomeSSID"                 # switch to a saved one
sudo nmcli connection delete "SomeSSID"                 # forget one
```

`sudo nmtui` does the same from a menu. To make home always win over the hotspot:
`sudo nmcli connection modify "YourHomeSSID" connection.autoconnect-priority 10`.

## SSH from your phone

[ConnectBot](https://connectbot.org/) (Android) or Termius and Blink (iPhone) are free SSH apps. Join the same
network as the Pi, then connect to `<user>@<hostname>.local` on port 22. If `.local` doesn't resolve on a
hotspot, use the Pi's IP. In ConnectBot, volume-down works as Ctrl.

Typing passwords on a phone gets old, so use a key:

1. In the app's key section, create an **Ed25519** key with no passphrase and copy the public key (it starts
   with `ssh-ed25519`).
2. On the Pi, add it on a new line in `~/.ssh/authorized_keys`, then
   `chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys`.
3. Check in a new session that it logs in without a password.
4. Only then, if you like, turn off password logins: set `PasswordAuthentication no` in
   `/etc/ssh/sshd_config` and `sudo systemctl restart ssh`. Keep the hotspot as your way back in.

## Logs from earlier boots

The installer keeps logs across reboots. `journalctl -u robot.service -b` shows this boot, and
`journalctl -u robot.service -b -1` the one before, which is useful when it crashed.
