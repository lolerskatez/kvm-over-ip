"""
Curated catalog of network-bootable OS entries for the KVM-over-IP PXE server.

Each entry's kernel_url / initrd_url points directly to an official upstream
distribution mirror.  The iPXE client downloads these URLs at boot time —
nothing is stored locally on the KVM server.

Boot flow for catalog entries
==============================
1. The booting machine's PXE ROM obtains our iPXE bootloader via TFTP.
2. iPXE fetches boot.ipxe from our Flask server.
3. The menu script contains  ``kernel <upstream_url>``  stanzas for each
   enabled catalog entry.
4. iPXE downloads the kernel (and initrd / modloop if present) directly from
   the distro's CDN.
5. The installer / live environment starts and pulls additional packages from
   the same upstream mirror — the KVM server is not involved.

Requirements for the target machine
=====================================
- Internet access, or LAN access to a local mirror of the relevant distro.
- iPXE with HTTPS support (standard in most builds compiled after 2016).

Schema for each entry
======================
  id          str   Unique stable identifier used in config.json.
  name        str   Human-readable display name shown in the boot menu.
  family      str   Distribution family (Debian, RHEL, Alpine, …).
  category    str   Grouping header shown in the UI (e.g. 'Linux Installers').
  description str   Short description shown in the catalog UI.
  arch        str   Target architecture, currently always 'x86_64'.
  boot_type   str   'kernel' — standard kernel + initrd boot.
  kernel_url  str   Direct URL to the kernel binary.
  initrd_url  str   Direct URL to the initrd (empty string if none needed).
  kernel_args str   Extra kernel command-line parameters (empty string if none).
"""

BOOT_CATALOG = [

    # =========================================================================
    # Debian
    # =========================================================================
    {
        'id': 'debian_12',
        'name': 'Debian 12 (Bookworm)',
        'family': 'Debian',
        'category': 'Linux Installers',
        'description': 'Debian stable — minimal ~50 MB d-i network installer. '
                       'Fetches selected packages from official mirrors during setup.',
        'arch': 'x86_64',
        'boot_type': 'kernel',
        'kernel_url': (
            'http://deb.debian.org/debian/dists/bookworm/main/installer-amd64'
            '/current/images/netboot/debian-installer/amd64/linux'
        ),
        'initrd_url': (
            'http://deb.debian.org/debian/dists/bookworm/main/installer-amd64'
            '/current/images/netboot/debian-installer/amd64/initrd.gz'
        ),
        'kernel_args': '',
    },
    {
        'id': 'debian_11',
        'name': 'Debian 11 (Bullseye)',
        'family': 'Debian',
        'category': 'Linux Installers',
        'description': 'Debian oldstable — minimal d-i network installer. '
                       'Ideal when Bookworm compatibility is a concern.',
        'arch': 'x86_64',
        'boot_type': 'kernel',
        'kernel_url': (
            'http://deb.debian.org/debian/dists/bullseye/main/installer-amd64'
            '/current/images/netboot/debian-installer/amd64/linux'
        ),
        'initrd_url': (
            'http://deb.debian.org/debian/dists/bullseye/main/installer-amd64'
            '/current/images/netboot/debian-installer/amd64/initrd.gz'
        ),
        'kernel_args': '',
    },

    # =========================================================================
    # Ubuntu
    # =========================================================================
    {
        'id': 'ubuntu_2004',
        'name': 'Ubuntu 20.04 LTS (Focal Fossa)',
        'family': 'Ubuntu',
        'category': 'Linux Installers',
        'description': 'Ubuntu 20.04 LTS — traditional d-i network installer. '
                       'Ubuntu 22.04+ removed the legacy netboot images and '
                       'requires a separately hosted squashfs.',
        'arch': 'x86_64',
        'boot_type': 'kernel',
        'kernel_url': (
            'http://archive.ubuntu.com/ubuntu/dists/focal/main/installer-amd64'
            '/current/images/netboot/ubuntu-installer/amd64/linux'
        ),
        'initrd_url': (
            'http://archive.ubuntu.com/ubuntu/dists/focal/main/installer-amd64'
            '/current/images/netboot/ubuntu-installer/amd64/initrd.gz'
        ),
        'kernel_args': '',
    },

    # =========================================================================
    # Rocky Linux
    # =========================================================================
    {
        'id': 'rocky_9',
        'name': 'Rocky Linux 9',
        'family': 'RHEL',
        'category': 'Linux Installers',
        'description': 'Community RHEL 9-compatible enterprise Linux. '
                       'Anaconda installer fetches packages from the Rocky CDN.',
        'arch': 'x86_64',
        'boot_type': 'kernel',
        'kernel_url': (
            'https://dl.rockylinux.org/pub/rocky/9/BaseOS/x86_64/os'
            '/images/pxeboot/vmlinuz'
        ),
        'initrd_url': (
            'https://dl.rockylinux.org/pub/rocky/9/BaseOS/x86_64/os'
            '/images/pxeboot/initrd.img'
        ),
        'kernel_args': (
            'inst.repo=https://dl.rockylinux.org/pub/rocky/9/BaseOS/x86_64/os/ quiet'
        ),
    },
    {
        'id': 'rocky_8',
        'name': 'Rocky Linux 8',
        'family': 'RHEL',
        'category': 'Linux Installers',
        'description': 'Community RHEL 8-compatible enterprise Linux. '
                       'Anaconda installer fetches packages from the Rocky CDN.',
        'arch': 'x86_64',
        'boot_type': 'kernel',
        'kernel_url': (
            'https://dl.rockylinux.org/pub/rocky/8/BaseOS/x86_64/os'
            '/images/pxeboot/vmlinuz'
        ),
        'initrd_url': (
            'https://dl.rockylinux.org/pub/rocky/8/BaseOS/x86_64/os'
            '/images/pxeboot/initrd.img'
        ),
        'kernel_args': (
            'inst.repo=https://dl.rockylinux.org/pub/rocky/8/BaseOS/x86_64/os/ quiet'
        ),
    },

    # =========================================================================
    # AlmaLinux
    # =========================================================================
    {
        'id': 'almalinux_9',
        'name': 'AlmaLinux 9',
        'family': 'RHEL',
        'category': 'Linux Installers',
        'description': 'Community RHEL 9-compatible Linux by CloudLinux. '
                       'Anaconda installer fetches packages from the AlmaLinux CDN.',
        'arch': 'x86_64',
        'boot_type': 'kernel',
        'kernel_url': (
            'https://repo.almalinux.org/almalinux/9/BaseOS/x86_64/os'
            '/images/pxeboot/vmlinuz'
        ),
        'initrd_url': (
            'https://repo.almalinux.org/almalinux/9/BaseOS/x86_64/os'
            '/images/pxeboot/initrd.img'
        ),
        'kernel_args': (
            'inst.repo=https://repo.almalinux.org/almalinux/9/BaseOS/x86_64/os/ quiet'
        ),
    },
    {
        'id': 'almalinux_8',
        'name': 'AlmaLinux 8',
        'family': 'RHEL',
        'category': 'Linux Installers',
        'description': 'Community RHEL 8-compatible Linux by CloudLinux. '
                       'Anaconda installer fetches packages from the AlmaLinux CDN.',
        'arch': 'x86_64',
        'boot_type': 'kernel',
        'kernel_url': (
            'https://repo.almalinux.org/almalinux/8/BaseOS/x86_64/os'
            '/images/pxeboot/vmlinuz'
        ),
        'initrd_url': (
            'https://repo.almalinux.org/almalinux/8/BaseOS/x86_64/os'
            '/images/pxeboot/initrd.img'
        ),
        'kernel_args': (
            'inst.repo=https://repo.almalinux.org/almalinux/8/BaseOS/x86_64/os/ quiet'
        ),
    },

    # =========================================================================
    # Fedora
    # =========================================================================
    {
        'id': 'fedora_41',
        'name': 'Fedora 41',
        'family': 'Fedora',
        'category': 'Linux Installers',
        'description': 'Fedora Server — cutting-edge RPM-based Linux with the '
                       'Anaconda installer, fetching packages from Fedora mirrors.',
        'arch': 'x86_64',
        'boot_type': 'kernel',
        'kernel_url': (
            'https://dl.fedoraproject.org/pub/fedora/linux/releases/41'
            '/Server/x86_64/os/images/pxeboot/vmlinuz'
        ),
        'initrd_url': (
            'https://dl.fedoraproject.org/pub/fedora/linux/releases/41'
            '/Server/x86_64/os/images/pxeboot/initrd.img'
        ),
        'kernel_args': (
            'inst.repo=https://dl.fedoraproject.org/pub/fedora/linux'
            '/releases/41/Server/x86_64/os/ quiet'
        ),
    },

    # =========================================================================
    # Alpine Linux
    # =========================================================================
    {
        'id': 'alpine_321',
        'name': 'Alpine Linux 3.21',
        'family': 'Alpine',
        'category': 'Linux Installers',
        'description': 'Lightweight musl-based Linux — built for netboot. '
                       'Kernel, initrd, and modloop all stream directly from the Alpine CDN.',
        'arch': 'x86_64',
        'boot_type': 'kernel',
        'kernel_url': (
            'https://dl-cdn.alpinelinux.org/alpine/v3.21/releases/x86_64'
            '/netboot/vmlinuz-lts'
        ),
        'initrd_url': (
            'https://dl-cdn.alpinelinux.org/alpine/v3.21/releases/x86_64'
            '/netboot/initramfs-lts'
        ),
        'kernel_args': (
            'ip=dhcp '
            'alpine_repo=https://dl-cdn.alpinelinux.org/alpine/v3.21/main '
            'modloop=https://dl-cdn.alpinelinux.org/alpine/v3.21/releases/x86_64/netboot/modloop-lts '
            'modules=loop,squashfs,sd-mod,usb-storage quiet'
        ),
    },

    # =========================================================================
    # Arch Linux
    # =========================================================================
    {
        'id': 'archlinux',
        'name': 'Arch Linux (latest)',
        'family': 'Arch',
        'category': 'Linux Installers',
        'description': 'Rolling-release minimalist Linux. Boots a live install '
                       'environment — the archiso squashfs (~900 MB) is fetched '
                       'directly from geo.mirror.pkgbuild.com.',
        'arch': 'x86_64',
        'boot_type': 'kernel',
        'kernel_url': (
            'https://geo.mirror.pkgbuild.com/iso/latest'
            '/arch/boot/x86_64/vmlinuz-linux'
        ),
        'initrd_url': (
            'https://geo.mirror.pkgbuild.com/iso/latest'
            '/arch/boot/x86_64/initramfs-linux.img'
        ),
        'kernel_args': (
            'archisobasedir=arch '
            'archiso_http_srv=https://geo.mirror.pkgbuild.com/iso/latest/ '
            'ip=dhcp net.ifnames=0 quiet'
        ),
    },

    # =========================================================================
    # Utilities
    # =========================================================================
    # Linux Servers — server-oriented boot entries
    # =========================================================================
    {
        'id': 'debian_12_server',
        'name': 'Debian 12 (Bookworm) — Server',
        'family': 'Debian',
        'category': 'Linux Servers',
        'description': 'Debian 12 netinstall pre-configured for a headless server. '
                       'Skips desktop/GUI task selection and auto-installs standard '
                       'system utilities + SSH. Identical kernel to the generic entry, '
                       'server profile is determined by preseed kernel args.',
        'arch': 'x86_64',
        'boot_type': 'kernel',
        'kernel_url': (
            'http://deb.debian.org/debian/dists/bookworm/main/installer-amd64'
            '/current/images/netboot/debian-installer/amd64/linux'
        ),
        'initrd_url': (
            'http://deb.debian.org/debian/dists/bookworm/main/installer-amd64'
            '/current/images/netboot/debian-installer/amd64/initrd.gz'
        ),
        'kernel_args': 'priority=critical tasks=standard,ssh-server',
    },
    {
        'id': 'debian_11_server',
        'name': 'Debian 11 (Bullseye) — Server',
        'family': 'Debian',
        'category': 'Linux Servers',
        'description': 'Debian 11 netinstall pre-configured for a headless server. '
                       'Skips desktop/GUI task selection and auto-installs standard '
                       'system utilities + SSH.',
        'arch': 'x86_64',
        'boot_type': 'kernel',
        'kernel_url': (
            'http://deb.debian.org/debian/dists/bullseye/main/installer-amd64'
            '/current/images/netboot/debian-installer/amd64/linux'
        ),
        'initrd_url': (
            'http://deb.debian.org/debian/dists/bullseye/main/installer-amd64'
            '/current/images/netboot/debian-installer/amd64/initrd.gz'
        ),
        'kernel_args': 'priority=critical tasks=standard,ssh-server',
    },
    {
        'id': 'ubuntu_2004_server',
        'name': 'Ubuntu 20.04 LTS (Focal) — Server',
        'family': 'Ubuntu',
        'category': 'Linux Servers',
        'description': 'Ubuntu 20.04 LTS traditional d-i installer targeted at '
                       'server installations. Installs standard system + SSH, '
                       'skipping desktop packages.',
        'arch': 'x86_64',
        'boot_type': 'kernel',
        'kernel_url': (
            'http://archive.ubuntu.com/ubuntu/dists/focal/main/installer-amd64'
            '/current/images/netboot/ubuntu-installer/amd64/linux'
        ),
        'initrd_url': (
            'http://archive.ubuntu.com/ubuntu/dists/focal/main/installer-amd64'
            '/current/images/netboot/ubuntu-installer/amd64/initrd.gz'
        ),
        'kernel_args': 'net.ifnames=0 biosdevname=0',
    },
    {
        'id': 'ubuntu_2204_server',
        'name': 'Ubuntu 22.04 LTS (Jammy) — Server',
        'family': 'Ubuntu',
        'category': 'Linux Servers',
        'description': 'Ubuntu 22.04 LTS Server via the Subiquity live installer. '
                       'WARNING: Ubuntu 22.04+ removed the traditional d-i netboot '
                       'images. This entry requires the vmlinuz and initrd to be '
                       'extracted from the live-server ISO and served from a local '
                       'mirror — update the URLs accordingly. Use the netboot.xyz '
                       'chainload entry as an alternative.',
        'arch': 'x86_64',
        'boot_type': 'kernel',
        'kernel_url': (
            'http://YOUR-LOCAL-MIRROR/ubuntu-22.04/casper/vmlinuz'
        ),
        'initrd_url': (
            'http://YOUR-LOCAL-MIRROR/ubuntu-22.04/casper/initrd'
        ),
        'kernel_args': (
            'ip=dhcp url=http://YOUR-LOCAL-MIRROR/ubuntu-22.04/ubuntu-22.04.5-live-server-amd64.iso '
            'autoinstall net.ifnames=0 biosdevname=0 quiet'
        ),
    },
    {
        'id': 'ubuntu_2404_server',
        'name': 'Ubuntu 24.04 LTS (Noble) — Server',
        'family': 'Ubuntu',
        'category': 'Linux Servers',
        'description': 'Ubuntu 24.04 LTS Server via the Subiquity live installer. '
                       'WARNING: Ubuntu 22.04+ removed the traditional d-i netboot '
                       'images. This entry requires the vmlinuz and initrd to be '
                       'extracted from the live-server ISO and served from a local '
                       'mirror — update the URLs accordingly. Use the netboot.xyz '
                       'chainload entry as an alternative.',
        'arch': 'x86_64',
        'boot_type': 'kernel',
        'kernel_url': (
            'http://YOUR-LOCAL-MIRROR/ubuntu-24.04/casper/vmlinuz'
        ),
        'initrd_url': (
            'http://YOUR-LOCAL-MIRROR/ubuntu-24.04/casper/initrd'
        ),
        'kernel_args': (
            'ip=dhcp url=http://YOUR-LOCAL-MIRROR/ubuntu-24.04/ubuntu-24.04.1-live-server-amd64.iso '
            'autoinstall net.ifnames=0 biosdevname=0 quiet'
        ),
    },

    # =========================================================================
    # Utilities
    # =========================================================================
    {
        'id': 'memtest86plus',
        'name': 'Memtest86+ 7.x',
        'family': 'Memory',
        'category': 'Utilities',
        'description': 'Open-source memory testing utility — thoroughly tests RAM '
                       'for hardware errors. Legacy BIOS / CSM mode only.',
        'arch': 'x86_64',
        'boot_type': 'kernel',
        'kernel_url': (
            'http://deb.debian.org/debian/dists/bookworm/main/installer-amd64'
            '/current/images/netboot/debian-installer/amd64/boot-screens/memtest'
        ),
        'initrd_url': '',
        'kernel_args': '',
    },
]
