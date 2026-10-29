# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""One pattern for every choice the Box doctor offers: the hub checks it before it reaches root, and root checks
it again. Both import this, so a new choice is added once and cannot be accepted by one and refused by the other."""

import re

CHOICE_RE = re.compile(
    r"^(unit-restart|unit-enable|kiwix-quarantine):[A-Za-z0-9@._-]{1,80}$"
    r"|^(kiwix-rebuild|kiwix-off|rerun-install|rerun-install-port-80|net-scan)$"
    r"|^other-restart:[A-Za-z0-9@_][A-Za-z0-9@_.:\\-]{0,200}$"
    r"|^crashwatch-(snapshots|panic|watchdog|undervoltage):(on|off)$|^crashwatch-undervoltage-mv:(3000|4630|11000)$|^crashwatch-power-action:(log|sync|shutdown)$"
    r"|^clock-set:\d{10}$|^(rtc-find|rtc-look|rtc-save|rtc-remove)$|^rtc-setup:[a-z0-9]{3,12}:\d{1,3}:0x[0-9a-f]{2}$"
    r"|^clock-sources:(image|chrony):[01]:[01]:[01]:[01]:(-|[A-Za-z0-9][A-Za-z0-9.:-]{0,252})(:(gps|local|pool|module)(,(gps|local|pool|module)){0,3})?$|^clock-undo$"
    r"|^uplink-skip:[A-Za-z0-9_][A-Za-z0-9._-]{0,14}:(reconnect|restart|radio|reboot)$"
    r"|^idle-(off|on):(meshtasticd|ModemManager|bluetooth)\.service$"
    r"|^led-show:(leave|alive|network|all)$|^led-(led|flash):[A-Za-z0-9:._@-]{1,64}$|^armbian-led:(on|off)$"
    r"|^rfkill:(wifi|bluetooth):(on|off|leave)$|^rfkill-apply$"
    r"|^uplink-reconnect:[A-Za-z0-9_][A-Za-z0-9._-]{0,14}$"
    r"|^card-accounting-on$|^arch-remove:[a-z0-9-]{1,20}$"
    r"|^card-set:(apt_cache:(card|memory)|apt_languages:(download|none)|journal:(card|memory|flash)|writeback:(standard|longer)|box_state:(card|flash)|flash_crc:(on|off)|filesystem:(ext4|btrfs|f2fs|btrfs[+]f2fs)|ab:(off|tryboot|uboot)|card_accounting:(on|off))$"
    r"|^card-memcg-on$|^card-speed-test$|^card-scan$|^flash-read$|^recommended-apply$"
    r"|^health-timer:(on|off)$|^lost-writes:(on|off)$|^canary:(on|off)$|^bootkey:(on|off)$|^readonly:(on|off)$|^retain:(on|off)$|^retain-store:(on|off):[A-Za-z0-9%._~-]{1,300}$"
    r"|^card-ram:[A-Za-z0-9@._-]{1,128}\.service:[0-7]$|^card-ram-off:[A-Za-z0-9@._-]{1,128}\.service:[0-7]$|^card-ram-apply$"
)
