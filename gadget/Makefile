# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
#
# The USB gadget functions the Lyra's vendor kernel does not build: NCM (networking) and ACM
# (serial), with the two helper modules they use. The sources are fetched from the pinned vendor
# tree by root/usbgadget.py; this Makefile builds them out of tree against the kernel's headers.
obj-m := u_ether.o usb_f_ncm.o u_serial.o usb_f_acm.o
usb_f_ncm-y := f_ncm.o
usb_f_acm-y := f_acm.o
ccflags-y += -I$(src)
